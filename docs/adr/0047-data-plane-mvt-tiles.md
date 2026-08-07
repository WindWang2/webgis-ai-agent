# 0047. Data Plane: MVT vector tiles for large POI display

**Date:** 2026-08-07
**Status:** Accepted

## Context

The display path for large vector results transferred the full
FeatureCollection to the browser: a city-wide `query_osm_poi` result
(10k–100k features, up to ~26 MB GeoJSON) was stored as a ref, fetched whole
by the frontend (`/api/v1/layers/data/{ref}`), `JSON.parse`d, held in the HUD
store, and only then viewport-culled client-side. Fetch-on-Demand already
kept the LLM context slim; the *display* path was the pain (goal Phase G).

Root cause found en route: the OSM query tools advertised `limit` (1–500)
but never applied it to the Overpass query — fixed separately so the producer
is bounded at the source.

## Decisions

1. **MVT as an additive display path; GeoJSON stays the contract for small
   results and the LLM.** Tool responses and `/layers/data` remain
   GeoJSON-shaped — no tool, endpoint, or agent-contract breakage.

2. **Backend** (`app/services/mvt.py`, `GET
   /api/v1/layers/data/{ref_id}/tiles/{z}/{x}/{y}.mvt`):
   - Stdlib-only MVT 2.1 encoder for Point features (Web-Mercator,
     zigzag/varint delta geometry, extent 4096, per-tile key/value dedup,
     typed properties string/int/double/bool). Empty tiles are valid empty
     messages; tile coordinates are bounds-checked.
   - Endpoint uses the exact `/layers/data` auth (`require_owned_session` +
     owner_token), gzips the body, `Cache-Control: private, max-age=300`
     (ref data is immutable per session).
   - Measured (100k synthetic POI, city viewport): 24,788 KiB raw /
     2,580 KiB gzipped GeoJSON → **4 MVT tiles = 22 KiB gzip** (117× vs
     gzipped, ~1,100× vs raw; ~280 ms encode).

3. **Frontend** (`VectorMapSpecSource`, adapter threshold, runtime, renderer):
   - Layers with `_tileUrl` (minted in `use-sse-stream` with the session id)
     whose FeatureCollection exceeds `VECTOR_TILE_THRESHOLD = 5000` are
     rendered from MVT tiles (minzoom 1, maxzoom 16); smaller layers keep the
     inline-GeoJSON path with viewport culling.
   - `addVectorTileSource` replaces a same-id non-vector source (the empty
     pre-fetch GeoJSON upgrades to vector when the big FC arrives); sublayers
     get `source-layer: "data"` (MapLibre requirement).

## Consequences

- Per-render wire bytes for large layers drop ~100–1000×; the 100 ms
  `setData` parse jank and 26 MB HUD-store retention disappear for tile-served
  layers.
- Session id travels in the tile URL (established pattern in this app; a
  `transformRequest`-based token header is a possible follow-up).
- The encoder is Point-only today; polygon/label layers are future work
  (quantization + per-zoom simplification).
- Pure-Python encode is ~280 ms per 4-tile viewport — a per-ref tile LRU is a
  follow-up if encode cost shows up in practice.
