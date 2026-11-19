---
title: "Alive Maps"
description: "Walking routes that favor greenery, views, and street life within your extra-time budget."
thumbnail: "template.svg"
version: v1
format: v2
---

# Alive Maps

This file is the manifest for the **Alive Maps** template (slug:
`alive-maps`). It is the one document a future agent reads to understand,
present, and adapt this template. If you are an agent in a workspace that was
created from this template, this file is your script: read all of it, then
follow "How to adapt it" below.

## What it is

Walking routes that favor greenery, views, and street life within your extra-time budget.

Alive Maps is a walking route planner with a map and four route choices: direct, scenic, lively, and mixed. Choose addresses or map pins, allow up to 30 extra minutes, inspect nearby mapped greenery and venues, and download a GPX track. The included San Francisco example uses public locations; no personal route history is included.

## How it works

The snapshot includes these paths (each is a repo-root-relative path copied
from the original agent onto a clean default-workspace-template base):

- `system/apps/alive_maps/`
- `system/supervisord.conf.d/alive-maps.conf`

The app directory contains the Python web app, bundled Leaflet browser assets and license, icon, manifest, and tests. The launch settings run the `alive-maps` supervisord program on loopback port 8084 and register its URL with `forward_port.py`. Normal workspace Python package installation provides the `alive-maps` command. Photon supplies address search; on-demand OpenStreetMap extracts supply walking paths and mapped places, while OpenStreetMap tiles draw the background map. Runtime searches, map snapshots, and plans are stored only under `DATA_DIR` (default `data/.apps/alive-maps/`), which is excluded from this template.

## Recipe

This template is version `v1`. It is not a fork of the
workspace it came from -- it is DERIVED from it by a recipe: include these
paths, leave these out, apply these published-version rules. An update re-runs
the recipe against the current workspace and publishes the result as the next
version, so anything excluded stays excluded even though it still exists in the
source workspace.

The recipe is machine-read, so it lives in the sibling
[`template.toml`](template.toml) -- its `[recipe]` table -- along with
the structured requirements and the environment this template needs
installed. That file is authoritative for all of it; this one holds the prose.

## Requirements

Everything the adopting agent must deal with before this template is really
theirs. Two kinds of entry, handled at different times:

- **Activation** -- what must be SET UP before anything runs, in the
  machine-readable `requires_` forms below. The adopting agent acts on these
  ITSELF, first, before asking anything.
- **Adaptation** -- what must be DECIDED or REWIRED, in prose. Worked through
  interactively with the user, after activation.


No account permissions, secrets, paid keys, or LLM access are required. Internet access is needed for Photon search, OpenStreetMap extracts, and map tiles.

- Check that port 8084 is available. If occupied, assign a free port with `ALIVE_MAPS_PORT` and update the launch settings and forwarded URL to match.

## Environment

What this template needs INSTALLED, beyond what the template already has.
Declared in `template.toml`'s `[environment]` table; an adopting agent
converges it at ITS OWN pinned apt snapshot timestamp, so package versions come
out consistent with the rest of that agent's environment rather than frozen to
whatever this publisher happened to have.

Nothing extra -- runs on the stock workspace environment.

The included Python package declares its dependencies for normal workspace installation. No extra system packages or global tools are required.

## How to adapt it

Instructions for the NEXT agent -- the one adapting this template into a
new agent. This is the `use-template` skill's template path; in short:

1. Read this entire file first, especially "Requirements" below. It holds two
   kinds of entry and they are handled at different times: the machine-readable
   `requires_` lines are ACTIVATION (set them up before anything runs), and
   the prose bullets are ADAPTATION (decide or rewire them afterwards).
2. Present the template to the user in plain, non-technical language: what
   it is, what it does, and what it needs from them (name the activation
   requirements).
3. Ask whether they want to use the same connectors (e.g. their own Slack).
   If YES: ACTIVATE FIRST -- initiate every `requires_permission` line NOW
   via a latchkey permission request (see the `connect-external-service` skill; the request
   opens the approval/login flow in the Imbue Studio app), wire up any
   `requires_secret` values, start the services, and get the app showing
   THE USER'S OWN DATA. Done for a data-backed app means the user can open it
   and see their own data -- NOT that a service starts or an endpoint returns
   200. Then tell them it is live and to take a look.
4. Only AFTER that (or immediately, if they chose different connectors -- the
   swap is then the first adaptation) ask: "How do you want to adapt it?"
5. Work through each requirement interactively, one at a time. Translate each
   into plain language, ask for a decision only when you genuinely need one,
   and resolve the obvious ones yourself.
6. When done, append a dated entry to "Adaptation history" below (never
   rewrite earlier entries) and commit.

## Publication history

This template's changelog: what each published version changed. The PUBLISHER
appends one entry per version (newest last); earlier entries are never rewritten.
This is distinct from "Adaptation history" below, which is the ADOPTERS' log.

### v1 (2026-11-19) -- Publishes the walking route planner, public San Francisco example, bundled map assets, and launch settings.

## Adaptation history

Each agent that adapts this template appends one dated entry below. Earlier
entries are never rewritten.
