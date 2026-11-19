"""Small-area walking graphs, with explainable scenic and street-life weights."""
from __future__ import annotations

import hashlib
import heapq
import json
import math
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, NamedTuple

USER_AGENT = "AliveMaps/0.1 (personal walking route planner)"
ALLOWED = {"footway", "pedestrian", "path", "steps", "living_street", "residential", "service", "unclassified", "tertiary", "tertiary_link", "secondary", "secondary_link", "primary", "primary_link", "cycleway", "track"}
FETCH_LOCK = threading.Lock()

class RouteError(ValueError):
    """A route cannot be offered with the available map data."""


def distance(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lat2 = math.radians(a[0]), math.radians(b[0])
    dlat, dlon = lat2-lat1, math.radians(b[1]-a[1])
    h = math.sin(dlat/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin(dlon/2)**2
    return 12742000 * math.asin(min(1, math.sqrt(h)))


def fetch_json(url: str) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            return json.load(response)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
        raise RouteError("Map data is unavailable right now. Try again, or choose a shorter walk.") from error


def save_record(data_dir: Path, kind: str, key: str, payload: dict[str, Any]) -> str:
    folder = data_dir / kind
    folder.mkdir(parents=True, exist_ok=True)
    identity = hashlib.sha256(key.encode()).hexdigest()[:24]
    target = folder / f"{identity}.json"
    temp = target.with_suffix(f".{threading.get_ident()}.tmp")
    temp.write_text(json.dumps(payload))
    temp.replace(target)
    return identity


def search_places(data_dir: Path, query: str) -> list[dict[str, Any]]:
    if not 3 <= len(query) <= 200:
        raise RouteError("Enter a place or address with a city (at least 3 characters).")
    source = "https://photon.komoot.io/api/?" + urllib.parse.urlencode({"q": query, "limit": 5, "lang": "en"})
    identity = hashlib.sha256(source.encode()).hexdigest()[:24]
    cached = data_dir / "geocodes" / f"{identity}.json"
    record: dict[str, Any]
    if cached.exists() and time.time()-cached.stat().st_mtime < 86400*7:
        record = json.loads(cached.read_text())
    else:
        record = {"source": source, "fetched_at": datetime.now(timezone.utc).isoformat(), "raw": fetch_json(source)}
        save_record(data_dir, "geocodes", source, record)
    output = []
    for feature in record["raw"].get("features", []):
        prop = feature.get("properties", {})
        lon, lat = feature["geometry"]["coordinates"][:2]
        name = prop.get("name") or " ".join(str(prop.get(k, "")) for k in ("housenumber", "street")).strip()
        context = ", ".join(dict.fromkeys(str(prop[k]) for k in ("city", "state", "country") if prop.get(k)))
        osm_type = {"N": "node", "W": "way", "R": "relation"}.get(prop.get("osm_type"), "node")
        output.append({"name": name or query, "context": context, "lat": lat, "lon": lon,
                       "source": f"https://www.openstreetmap.org/{osm_type}/{prop.get('osm_id', '')}"})
    return output


def load_region(data_dir: Path, start: tuple[float, float], end: tuple[float, float]) -> tuple[dict[str, Any], str]:
    if distance(start, end) > 2500:
        raise RouteError("This first version handles local walks up to 2.5 km apart. Choose two closer places.")
    if distance(start, end) < 40:
        raise RouteError("Choose places at least 40 metres apart.")
    if abs(start[0]) > 75 or abs(end[0]) > 75 or abs(start[1]-end[1]) > 1:
        raise RouteError("This location is outside the map area this version supports.")
    latpad = 0.0026
    lonpad = latpad / math.cos(math.radians((start[0]+end[0])/2))
    bbox = [min(start[1],end[1])-lonpad, min(start[0],end[0])-latpad,
            max(start[1],end[1])+lonpad, max(start[0],end[0])+latpad]
    with FETCH_LOCK:
        region_dir = data_dir / "regions"
        region_dir.mkdir(parents=True, exist_ok=True)
        for path in sorted(region_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
            cached = json.loads(path.read_text())
            b = cached["bbox"]
            if b[0]<=bbox[0] and b[1]<=bbox[1] and b[2]>=bbox[2] and b[3]>=bbox[3] and time.time()-path.stat().st_mtime < 86400*7:
                return cached, path.stem
        source = "https://www.openstreetmap.org/api/0.6/map.json?bbox=" + ",".join(f"{x:.6f}" for x in bbox)
        record = {"bbox": bbox, "source": source, "fetched_at": datetime.now(timezone.utc).isoformat(), "raw": fetch_json(source)}
        identity = save_record(data_dir, "regions", source, record)
        return record, identity

class Edge(NamedTuple):
    target: int
    metres: float
    seconds: float
    scenic: float
    lively: float
    name: str
    way_id: int
    tags: dict[str, str]


def inside(point: tuple[float, float], poly: list[tuple[float, float]]) -> bool:
    y,x = point
    result = False
    for (ay,ax),(by,bx) in zip(poly, poly[1:]):
        if (ay>y)!=(by>y) and x < (bx-ax)*(y-ay)/(by-ay)+ax:
            result = not result
    return result


class WalkingGraph:
    def __init__(self, raw: dict[str, Any]):
        elements = raw.get("elements", [])
        self.coords = {e["id"]:(e["lat"],e["lon"]) for e in elements if e["type"]=="node"}
        nodes = {e["id"]:e for e in elements if e["type"]=="node"}
        self.adj: dict[int, list[Edge]] = {}
        self.places: list[dict[str, Any]] = []
        green_polys = []
        signals: dict[tuple[int,int], list[tuple[tuple[float,float],str]]] = {}
        def signal(p: tuple[float,float], kind: str) -> None:
            signals.setdefault((int(p[0]*1000),int(p[1]*1000)),[]).append((p,kind))
        ways = {e["id"]: e for e in elements if e["type"]=="way"}
        for e in elements:
            tags = e.get("tags", {})
            kind = None
            if tags.get("leisure") in {"park", "garden", "nature_reserve"} or tags.get("landuse") in {"forest", "grass", "meadow"} or tags.get("natural") in {"wood", "water", "tree", "coastline"}:
                kind = "scenic"
            if tags.get("amenity") in {"cafe", "restaurant", "marketplace", "arts_centre", "bar", "theatre"} or tags.get("shop") in {"bakery", "books", "greengrocer", "florist"}:
                kind = "lively"
            points = [self.coords[n] for n in e.get("nodes", []) if n in self.coords] if e["type"]=="way" else ([self.coords[e["id"]]] if e["type"]=="node" else [])
            if e["type"]=="relation" and kind:
                points = [self.coords[n] for member in e.get("members", []) if member.get("type")=="way" and member.get("role")!="inner" for n in ways.get(member["ref"], {}).get("nodes", []) if n in self.coords]
            if not kind or not points:
                continue
            center = (sum(x[0] for x in points)/len(points),sum(x[1] for x in points)/len(points))
            if kind=="scenic" and len(points)>3 and points[0]==points[-1]:
                green_polys.append((min(p[0] for p in points),min(p[1] for p in points),max(p[0] for p in points),max(p[1] for p in points),points))
            for point in points if kind=="scenic" else [center]:
                signal(point,kind)
            if tags.get("name"):
                self.places.append({"name":tags["name"],"lat":center[0],"lon":center[1],"kind":kind,"source":f"https://www.openstreetmap.org/{e['type']}/{e['id']}"})
        for way in elements:
            tags=way.get("tags", {})
            highway=tags.get("highway")
            if way["type"]!="way" or highway not in ALLOWED or tags.get("area")=="yes" or tags.get("indoor")=="yes":
                continue
            if tags.get("foot") in {"no", "private", "use_sidepath"} or (tags.get("access") in {"no", "private", "customers", "permit"} and tags.get("foot") not in {"yes", "designated", "permissive"}):
                continue
            if highway in {"primary","secondary","primary_link","secondary_link","cycleway"} and tags.get("foot") not in {"yes","designated","permissive"} and tags.get("sidewalk") not in {"yes","both","left","right"}:
                continue
            ns=way.get("nodes", [])
            for a,b in zip(ns, ns[1:]):
                if a not in self.coords or b not in self.coords:
                    continue
                if any(nodes[n].get("tags",{}).get("access") in {"no","private"} and nodes[n].get("tags",{}).get("foot") not in {"yes","designated"} for n in (a,b)):
                    continue
                pa,pb=self.coords[a],self.coords[b]
                metres=distance(pa,pb)
                if metres==0:
                    continue
                mid=((pa[0]+pb[0])/2,(pa[1]+pb[1])/2)
                green=0.18 if highway in {"footway","path","pedestrian","living_street"} else 0.04
                if tags.get("tree_lined") not in (None,"no"):
                    green+=0.35
                local={"scenic":0,"lively":0}
                cell=(int(mid[0]*1000),int(mid[1]*1000))
                for i in range(-1,2):
                    for j in range(-2,3):
                        for point,kind in signals.get((cell[0]+i,cell[1]+j),[]):
                            if distance(mid,point)<65:
                                local[kind]+=1
                if local["scenic"]:
                    green+=0.45
                if any(y1<=mid[0]<=y2 and x1<=mid[1]<=x2 and inside(mid,poly) for y1,x1,y2,x2,poly in green_polys):
                    green+=0.6
                lively=min(1,local["lively"]*0.17 + (0.15 if highway=="pedestrian" else 0))
                seconds=metres/(0.7 if highway=="steps" else 1.3)
                if highway=="steps":
                    seconds+=1
                directions=[(a,b),(b,a)]
                if tags.get("oneway:foot")=="yes":directions=[(a,b)]
                if tags.get("oneway:foot")=="-1":directions=[(b,a)]
                for source,target in directions:
                    self.adj.setdefault(source,[]).append(Edge(target,metres,seconds,min(1,green),lively,tags.get("name") or highway.replace("_"," "),way["id"],tags))
        self.coords = {n:self.coords[n] for n in self.adj.keys() | {e.target for es in self.adj.values() for e in es}}

    def snap(self, point: tuple[float,float]) -> tuple[int,float]:
        if not self.coords:
            raise RouteError("No connected walking streets were found here.")
        node=min(self.coords,key=lambda n:distance(point,self.coords[n]))
        gap=distance(point,self.coords[node])
        if gap>180:
            raise RouteError("That place is too far from a mapped walking path. Pick a nearby street entrance on the map.")
        return node,gap

    def shortest(self, start: int, end: int, mode: str, strength: float) -> list[tuple[int,Edge]]:
        heap=[(0.0,start)]
        best={start:0.0}
        previous: dict[int,tuple[int,Edge]]={}
        while heap:
            cost,node=heapq.heappop(heap)
            if cost!=best[node]:continue
            if node==end:break
            for edge in self.adj.get(node,[]):
                signal=edge.scenic if mode=="scenic" else edge.lively if mode=="lively" else (edge.scenic+edge.lively)/2
                new=cost+edge.seconds*(1-strength*signal)
                if new<best.get(edge.target,math.inf):
                    best[edge.target]=new
                    previous[edge.target]=(node,edge)
                    heapq.heappush(heap,(new,edge.target))
        if end not in previous:
            raise RouteError("No connected walking route was found in this area. Try street entrances closer together.")
        path=[]
        cursor=end
        while cursor!=start:
            previous_node,edge=previous[cursor]
            path.append((previous_node,edge))
            cursor=previous_node
        return list(reversed(path))

    def plan(self, start: tuple[float,float], end: tuple[float,float], extra: int) -> dict[str,Any]:
        a,agap=self.snap(start)
        b,bgap=self.snap(end)
        if a==b:raise RouteError("These places connect to the same walking point. Move one pin farther away.")
        base=self.shortest(a,b,"scenic",0)
        base_seconds=sum(e.seconds for _,e in base)
        connectors=(agap+bgap)/1.3
        result=[]
        for mode in ("direct","scenic","lively","mixed"):
            chosen=base
            def quality(path: list[tuple[int,Edge]]) -> float:
                total=sum(e.metres for _,e in path)
                return sum(e.metres*(e.scenic if mode=="scenic" else e.lively if mode=="lively" else (e.scenic+e.lively)/2) for _,e in path)/total
            if mode!="direct":
                for strength in (0.25,0.5,0.7,0.85,0.93,0.98):
                    candidate=self.shortest(a,b,mode,strength)
                    if sum(e.seconds for _,e in candidate)<=base_seconds+extra*60 and quality(candidate)>quality(chosen)+0.001:
                        chosen=candidate
            metres=sum(e.metres for _,e in chosen)
            seconds=sum(e.seconds for _,e in chosen)
            coordinates=[list(self.coords[n]) for n,_ in chosen]+[list(self.coords[b])]
            instructions: list[dict[str, Any]] = []
            for _,edge in chosen:
                if instructions and instructions[-1]["way_id"]==edge.way_id:
                    instructions[-1]["metres"]+=edge.metres
                else:
                    instructions.append({"name":edge.name,"metres":edge.metres,"way_id":edge.way_id,"source":f"https://www.openstreetmap.org/way/{edge.way_id}"})
            for instruction in instructions:instruction["metres"]=round(instruction["metres"])
            highlights=[]
            for place in self.places:
                if any(distance((place["lat"],place["lon"]),tuple(c))<80 for c in coordinates):
                    if place["name"] not in {p["name"] for p in highlights}:highlights.append(place)
            preferred = "lively" if mode=="lively" else "scenic"
            highlights.sort(key=lambda p: (p["kind"]!=preferred, min(distance((p["lat"],p["lon"]), tuple(c)) for c in coordinates)))
            result.append({"id":mode,"minutes":math.ceil((seconds+connectors)/60),"seconds":round(seconds+connectors),"distance_m":round(metres+agap+bgap),"extra_minutes":round(max(0,seconds-base_seconds)/60,1),"coordinates":coordinates,"scenic_percent":round(sum(e.metres*e.scenic for _,e in chosen)/metres*100),"lively_percent":round(sum(e.metres*e.lively for _,e in chosen)/metres*100),"highlights":highlights[:15],"directions":instructions,"same_as_direct":[n for n,_ in chosen]==[n for n,_ in base]})
        return {"routes":result,"snapped_start":list(self.coords[a]),"snapped_end":list(self.coords[b]),"start_gap_m":round(agap),"end_gap_m":round(bgap)}
