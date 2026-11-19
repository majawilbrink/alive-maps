# Alive Maps

A personal walking route planner that favors greenery, views, and street life within an extra-time budget.

## Use

Choose places by explicit address search or map pins. Compare direct, scenic, lively, and mixed routes, inspect mapped nearby places and walking segments, or export the selected route as a GPX track. Address search requires choosing a result; it does not silently assume the first match.

This initial version supports endpoints up to 2.5 km apart, at latitudes between -75 and 75. Dense map areas may exceed the upstream map download limit; choose closer endpoints when asked. It plans walks, rather than providing live navigation. Steps are allowed, so this is not an accessible-route planner.

## Data and ranking

Photon supplies address matches. Small, on-demand OpenStreetMap map extracts supply walking connections, access tags, parks, greenery, water, shops, cafes and cultural venues. Raw upstream documents, sources, and fetch dates persist under DATA_DIR and can be inspected in the walking route dialog. Region and geocoding snapshots are reused for seven days. No credentials or paid services are required. Background map tiles load directly from OpenStreetMap with attribution and ordinary browser caching.

Routes use positive-cost Dijkstra searches over mapped walking connections. Scenic weights favor walking paths, mapped tree-lined ways, nearby green/water features, and areas inside mapped park polygons. Lively weights favor proximity to mapped cafes, restaurants, shops, markets and cultural venues. Mixed weights average those signals. Several positive weighting strengths generate candidate paths; the highest average signal among candidates within the time limit wins. This is a heuristic candidate search, not a guarantee of the global best route under a time constraint. If no different route improves the signal within budget, the direct path is returned and labeled accordingly.

Travel estimates assume 1.3 m/s on ordinary segments and 0.7 m/s on stairs, plus a small stair segment delay. The detour budget compares exact seconds on paths in the downloaded region; pin-to-path connection estimates are identical across candidates. Unverified pin connectors are dotted and disclosed. GPX exports contain only the actual mapped walking path.

Mapped places are proxies, not subjective beauty ratings or live crowd information. Live closures, safety, gradients and accessibility are not evaluated. Walking access restrictions and explicit pedestrian direction tags are respected where mapped; completeness still depends on source data.

## Runtime

The service binds to loopback. ALIVE_MAPS_PORT and ALIVE_MAPS_DATA_DIR override its port and all stored state for isolated verification. The scaffold provides the API schema, health probe, shell integration, security policy and source-data inspection routes. Third-party browser code is bundled with its license in assets.
