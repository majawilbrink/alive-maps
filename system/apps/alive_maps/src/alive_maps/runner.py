"""Find walking routes that favor greenery, views, and street life within your extra-time budget..

Services run from /home/user/workspace (the repo root). Conventions:

- Persistent state (anything written and read across runs -- cursors,
  caches, snapshots, user records): read and write it under ``DATA_DIR``
  (defined below), never a hardcoded ``data/.apps/alive-maps/`` at the
  call site. ``DATA_DIR`` defaults to ``data/.apps/alive-maps/`` but
  honors the ``ALIVE_MAPS_DATA_DIR`` env var, so an editing agent can point a
  throwaway instance at a *copy* of the data instead of the live store
  (see the update-app skill). Do NOT use ``Path(__file__)``-based
  paths for state -- the bug to avoid is one process writing to
  ``/home/user/workspace/data/.apps/...`` while another reads from
  ``/home/user/workspace/system/apps/<pkg>/data/...``.
- Static assets shipped alongside this file (templates, default
  configs, bundled JSON): ``Path(__file__).parent / "assets/..."`` is
  fine and is the right pattern.
- Listen port: bind ``PORT`` (defined below), which defaults to this
  app's assigned port but honors the ``ALIVE_MAPS_PORT`` env var, so
  an editing agent can boot a throwaway instance on a *spare* port
  alongside the live one (see the update-app skill). Never hardcode
  the port at the ``run_simple`` call.

This is a synchronous Flask app served by the threaded Werkzeug server.
The app owns its own browser origin (the forwarder routes
``http://alive-maps.<workspace-host>/`` straight to this port), so it serves
at ``/`` and root-absolute URLs, cookies, and service workers all work
unmodified -- nothing rewrites anything. Use ``flask_sock`` if you need
WebSockets.
"""

import base64
import hashlib
import os
import json
import uuid
from typing import Any
from xml.etree import ElementTree as ET
from pathlib import Path

from app_schema.api_schema import (
    DEFAULT_API_SCHEMA_PATH,
    RouteModels,
    RouteTable,
    SchemalessRoute,
    register_api_schema_route,
)
from app_schema.routes import model_response, parse_request_body, error_response
from flask import Flask, Response, abort, request, send_file
from imbue.imbue_common.frozen_model import FrozenModel
from imbue.imbue_common.logging import setup_logging
from loguru import logger
from pydantic import Field
from werkzeug.serving import run_simple
from alive_maps.engine import RouteError, WalkingGraph, load_region, search_places, save_record

# Persistent state for this app lives under DATA_DIR. It defaults to
# ``data/.apps/alive-maps/`` but is overridable via the ``ALIVE_MAPS_DATA_DIR`` env var
# so a throwaway instance can run against a *copy* of the data while editing --
# see the update-app skill. Always read/write state through DATA_DIR;
# never hardcode ``data/.apps/alive-maps/`` at a call site, or the override is
# bypassed. A writing call site should ``DATA_DIR.mkdir(parents=True,
# exist_ok=True)`` before writing.
DATA_DIR = Path(os.environ.get("ALIVE_MAPS_DATA_DIR", "data/.apps/alive-maps"))

# Listen port. Defaults to this app's assigned port but is overridable via
# the ``ALIVE_MAPS_PORT`` env var so an editing agent can boot a throwaway
# instance on a spare port next to the live one (see the update-app skill).
# Never hardcode the port at the ``run_simple`` call, or the override is bypassed.
PORT = int(os.environ.get("ALIVE_MAPS_PORT", "8084"))

# The browser-side modules the workspace shell builds and every app serves from
# its own origin: the app contract (how a page talks to the shell framing it) and
# the element context menu (the right-click menu whose last rows hand the
# clicked element to a chat). A module import is a fetch without cookies, which
# the forwarder refuses across origins, so they are served here rather than from
# the shell. Relative to the repo root the service runs from, like DATA_DIR.
SHELL_STATIC_MODULES_DIR = Path("system/apps/system_interface/imbue/system_interface/static/_static")
SHELL_STATIC_MODULE_NAMES = ('app_contract.js', 'context_menu.js')

# The script every page serves (keep it on every page): it connects the page to
# the shell, reports where the page is on the handshake so the shell can reopen
# this app's window at the same place, and installs the element context menu.
# A page visited outside the shell runs it harmlessly: nothing arrives, and the
# menu's Explain and Modify rows grey out.
SHELL_PAGE_SCRIPT_BODY = """
  import { connectToShell } from "/_static/app_contract.js";
  import { installElementContextMenu } from "/_static/context_menu.js";
  let handshake = null;
  const connection = connectToShell({
    onHandshake: (received) => {
      handshake = received;
      connection.location(location.pathname + location.search, document.title);
    },
  });
  installElementContextMenu({ connection, handshake: () => handshake });
"""
SHELL_PAGE_SCRIPT = f'<script type="module">{SHELL_PAGE_SCRIPT_BODY}</script>'


def inline_script_source(body: str) -> str:
    """The Content-Security-Policy source admitting one inline <script> whose body is exactly ``body``."""
    return "'sha256-" + base64.b64encode(hashlib.sha256(body.encode()).digest()).decode() + "'"


# Every HTML page is served under this policy (the app is reachable from the
# internet once the workspace is shared): scripts only from this origin plus the
# inline blocks listed here by hash, fetches, sockets and images only from this
# origin, inline styles, frames of any http(s) page, no plugins. Anything else a
# page pulls in (a CDN script, Google Fonts, an image hosted elsewhere, a
# third-party API called from the browser) is blocked until its origin is added
# to the matching directive; a new inline <script> needs its
# ``inline_script_source`` added.
# The browser posts every block to ``/_csp-report`` below, which logs it on
# stderr (the supervisor log), so a blocked resource is never silent; the
# ``'report-sample'`` makes the browser include where a blocked inline script
# is and how it starts, since the report otherwise names it only as "inline".
CONTENT_SECURITY_POLICY = (
    "default-src 'self'; "
    f"script-src 'self' 'report-sample' {inline_script_source(SHELL_PAGE_SCRIPT_BODY)}; "
    "style-src 'self' 'unsafe-inline'; img-src 'self' data: blob: https://tile.openstreetmap.org; frame-src http: https:; "
    "object-src 'none'; base-uri 'self'; form-action 'self'; "
    "report-uri /_csp-report"
)
BROWSER_EXTENSION_SCHEMES = ("chrome-extension:", "moz-extension:", "safari-web-extension:")


class HealthResponse(FrozenModel):
    """What the health probe answers."""

    status: str = Field(description="Always 'ok'")


class Location(FrozenModel):
    lat: float = Field(ge=-75, le=75, allow_inf_nan=False)
    lon: float = Field(ge=-180, le=180, allow_inf_nan=False)
    name: str = Field(default="Map pin", max_length=250)


class PlanRequest(FrozenModel):
    start: Location
    end: Location
    extra_minutes: int = Field(default=10, ge=0, le=30)


class PlanResponse(FrozenModel):
    id: str
    region_id: str
    source: str
    fetched_at: str
    start: Location
    end: Location
    routes: list[dict[str, Any]]
    snapped_start: list[float]
    snapped_end: list[float]
    start_gap_m: int
    end_gap_m: int


class SearchResponse(FrozenModel):
    places: list[dict[str, Any]]


class RawResponse(FrozenModel):
    source: str
    fetched_at: str
    bbox: list[float]
    raw: dict[str, Any]


# What every route exchanges, which the app's API document publishes and every parse and answer
# is checked against (build-app skill, "The app describes its own API"): a route that reads or
# answers JSON names its pydantic models here and uses ``parse_request_body`` and
# ``model_response``; a page, a file or a stream is declared schemaless with the reason.
ROUTE_TABLE: RouteTable = {
    ("GET", "/"): SchemalessRoute(reason="the app's page"),
    ("GET", "/_static/{basename}"): SchemalessRoute(reason="a browser-side module the shell builds"),
    ("GET", "/health"): RouteModels(response_model=HealthResponse),
    ("GET", "/assets/{basename}"): SchemalessRoute(reason="bundled styles and browser code"),
    ("GET", "/api/search"): RouteModels(response_model=SearchResponse),
    ("POST", "/api/plan"): RouteModels(request_model=PlanRequest, response_model=PlanResponse),
    ("GET", "/api/raw/{identity}"): RouteModels(response_model=RawResponse),
    ("GET", "/api/export/{identity}/{mode}"): SchemalessRoute(reason="download a walking route as GPX XML"),
    ("POST", "/_csp-report"): SchemalessRoute(reason="the browser's report of what the page's policy blocked"),
}

app = Flask("alive_maps", static_folder=None)
# The app describes its own API: an OpenAPI document of every route below, served at the
# manifest's api_schema_path.
register_api_schema_route(
    app, DEFAULT_API_SCHEMA_PATH, title='Alive Maps', description='Find walking routes that favor greenery, views, and street life within your extra-time budget.', route_table=ROUTE_TABLE
)


@app.after_request
def add_content_security_policy(response: Response) -> Response:
    if response.mimetype == "text/html":
        response.headers.setdefault("Content-Security-Policy", CONTENT_SECURITY_POLICY)
    return response


@app.route("/")
def index() -> Response:
    """The app's page."""
    page = (Path(__file__).parent / "assets" / "index.html").read_text()
    return Response(page.replace("<!--SHELL-->", SHELL_PAGE_SCRIPT), mimetype="text/html")


@app.route("/_csp-report", methods=["POST"])
def content_security_policy_report() -> Response:
    """Where the browser reports what the page's security policy blocked."""
    body = request.get_json(force=True, silent=True)
    report = body.get("csp-report") if isinstance(body, dict) else None
    if not isinstance(report, dict):
        return Response(status=204)
    blocked, page, source, directive, line, sample = (
        str(report.get(key, ""))[:500]
        for key in ("blocked-uri", "document-uri", "source-file", "effective-directive", "line-number", "script-sample")
    )
    # A block caused by a browser extension is the policy doing its job and is not logged.
    if blocked.startswith(BROWSER_EXTENSION_SCHEMES) or source.startswith(BROWSER_EXTENSION_SCHEMES):
        return Response(status=204)
    # An inline script or style is reported as "inline": say where it is and how it starts instead.
    where = f" at {source}:{line}" if source and line else ""
    starting = f" starting {sample!r}" if sample else ""
    remedy = (
        "add its inline_script_source to CONTENT_SECURITY_POLICY if the page needs it"
        if blocked == "inline"
        else "add its origin to CONTENT_SECURITY_POLICY if the page needs it"
    )
    logger.error(
        "Content-Security-Policy blocked {!r} on {!r} ({}){}{}; {}", blocked, page, directive, where, starting, remedy
    )
    return Response(status=204)


@app.route("/_static/<basename>")
def shell_module(basename: str) -> Response:
    """One of the two browser-side modules the workspace shell builds and every app serves from its own origin."""
    # The two shell-built modules and nothing else: a name that is not one of
    # them is a 404, so this route can never read outside that directory.
    if basename not in SHELL_STATIC_MODULE_NAMES:
        abort(404)
    module_path = SHELL_STATIC_MODULES_DIR / basename
    if not module_path.is_file():
        abort(404)
    # Flask resolves a relative path against the app's own directory, not the cwd.
    return send_file(module_path.absolute(), mimetype="text/javascript")


@app.route("/health")
def health() -> Response:
    """Whether the app is up."""
    return model_response(HealthResponse(status="ok"))


@app.errorhandler(RouteError)
def route_error(error: RouteError) -> Response:
    return error_response(str(error), 422)


@app.route("/assets/<basename>")
def asset(basename: str) -> Response:
    """The map library, styles and page behavior bundled with Alive Maps."""
    if basename not in {"leaflet.js", "leaflet.css", "app.js", "style.css"}:
        abort(404)
    return send_file(Path(__file__).parent / "assets" / basename)


@app.route("/api/search")
def search() -> Response:
    """Find up to five places matching an address or a name and city, on explicit search."""
    return model_response(SearchResponse(places=search_places(DATA_DIR, request.args.get("q", "").strip())))


@app.route("/api/plan", methods=["POST"])
def plan() -> Response:
    """Compare direct, scenic, lively and mixed walking routes within a detour budget."""
    body = parse_request_body(PlanRequest)
    start, end = (body.start.lat, body.start.lon), (body.end.lat, body.end.lon)
    record, region_id = load_region(DATA_DIR, start, end)
    result = WalkingGraph(record["raw"]).plan(start, end, body.extra_minutes)
    identity = uuid.uuid4().hex
    response = PlanResponse(id=identity, region_id=region_id, source=record["source"], fetched_at=record["fetched_at"], start=body.start, end=body.end, **result)
    save_record(DATA_DIR, "plans", identity, {"request": body.model_dump(), "response": response.model_dump()})
    return model_response(response)


@app.route("/api/raw/<identity>")
def raw(identity: str) -> Response:
    """The original OpenStreetMap document used to calculate a route, with its source and date."""
    if len(identity)!=24 or any(c not in "0123456789abcdef" for c in identity):
        abort(404)
    path = DATA_DIR / "regions" / f"{identity}.json"
    if not path.exists():
        abort(404)
    return model_response(RawResponse(**json.loads(path.read_text())))


@app.route("/api/export/<identity>/<mode>")
def export_route(identity: str, mode: str) -> Response:
    """Download the selected route as a GPX track for a compatible walking app."""
    if len(identity)!=32 or any(c not in "0123456789abcdef" for c in identity):
        abort(404)
    key = hashlib.sha256(identity.encode()).hexdigest()[:24]
    path = DATA_DIR / "plans" / f"{key}.json"
    if not path.exists():
        abort(404)
    result = json.loads(path.read_text())["response"]
    selected = next((r for r in result["routes"] if r["id"]==mode), None)
    if selected is None:
        abort(404)
    root = ET.Element("gpx", version="1.1", creator="Alive Maps", xmlns="http://www.topografix.com/GPX/1/1")
    track = ET.SubElement(root, "trk")
    ET.SubElement(track, "name").text = f"Alive Maps: {result['start']['name']} to {result['end']['name']} ({mode})"
    segment = ET.SubElement(track, "trkseg")
    for lat,lon in selected["coordinates"]:
        ET.SubElement(segment, "trkpt", lat=str(lat), lon=str(lon))
    response = Response(ET.tostring(root, encoding="utf-8", xml_declaration=True), mimetype="application/gpx+xml")
    response.headers["Content-Disposition"] = f'attachment; filename="alive-maps-{mode}.gpx"'
    return response


def main() -> None:
    setup_logging()
    run_simple(
        "127.0.0.1", PORT, app, threaded=True, use_reloader=False, use_debugger=False
    )


if __name__ == "__main__":
    main()
