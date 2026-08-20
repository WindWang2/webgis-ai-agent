# 0047. Data Plane: MVT vector tiles for large POI display

**Date:** 2026-08-07
**Status:** Accepted
**Updated:** 2026-08-20 — V2 encoder / index / cache landed; doc was Point-only/stdlib/no-LRU.

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

2. **Backend V2** (`app/services/mvt.py`, `GET
   /api/v1/layers/data/{ref_id}/tiles/{z}/{x}/{y}.mvt`):
   - MVT 2.1 encoder for **all vector geometries** — Point / MultiPoint /
     LineString / MultiLineString / Polygon / MultiPolygon (extent 4096,
     Web-Mercator projection, zigzag/varint delta, quantization/clip,
     ClosePath + correct winding for holes, per-tile key/value dedup, typed
     properties). GeometryCollection is skipped (never encoded). Antimeridian-
     crossing lines/polygons are split at ±180 per RFC 7946 §3.1.9. z<14
     line/polygon simplification via `shapely.simplify` (half-pixel tolerance)
     with `is_valid` self-intersection gate; z≥14 preserves detail.
   - **Spatial index:** one `STRtree` per `(session_id, ref_id)` via
     `SpatialIndexEntry` / `build_spatial_index_entry` — dual shapely geometries
     (lon/lat + z0 projected), bounded LRU (`max_refs=256 / max_bytes=256 MB`,
     `OrderedDict`, thread-safe). Without shapely falls back to pure-Python
     bbox full-scan. Hot path `encode_tile_from_index` reuses `query_candidates`
     — no per-tile GeoJSON→Shapely rebuild.
   - **Tile LRU cache:** `TileLRUCache` keyed by `(session_id, ref_id, z, x, y)`,
     `max_tiles=4096 / max_bytes=256 MB / max_entry_bytes=4 MB`, thread-safe,
     session-isolated. `SpatialIndexCache` + `TileLRUCache` invalidated on
     `MemorySessionStore.store` eviction and `clear_session`; `overwrite` clears
     both.
   - **Single-flight:** `SingleFlightManager` (`asyncio.Future`, `max_inflight=512`)
     dedupes concurrent same-tile requests (`single_flight.run(cache_key, _compute)`).
   - **HTTP semantics:** `Content-Type: application/vnd.mapbox-vector-tile` +
     `Content-Encoding: gzip` + `Cache-Control: private, max-age=300` (ref data
     immutable per session) + `ETag=sha256(gzip)[:16]` + 304 conditional (`If-None-Match`).
     Endpoint uses the exact `/layers/data` auth (`require_owned_session` +
     `owner_token`); tile LRU checked first, then single-flight + `asyncio.to_thread`
     encode+gz.
   - **Descriptor fast-path:** `RefDescriptor` computed once at `store()` via
     `asyncio.to_thread(compute_descriptor)` and persisted in `_descriptors` /
     Redis `session:{sid}:meta:{ref}` — `GET /layers/descriptor/{ref_id}` is
     metadata-only (`get_ref_descriptor_authorized`) and never hydrates the full
     payload. Legacy fallback recomputes off-loop and caches.
   - Measured (100k synthetic POI, city viewport): 24,788 KiB raw /
     2,580 KiB gzipped GeoJSON → **4 MVT tiles = 22 KiB gzip** (117× vs
     gzipped, ~1,100× vs raw; ~280 ms encode cold, 0 ms warm via LRU).

3. **Frontend** (`VectorMapSpecSource`, adapter threshold, runtime, renderer):
   - Layers with `_tileUrl` (minted in `use-sse-stream` with the session id)
     whose FeatureCollection exceeds `VECTOR_TILE_THRESHOLD = 5000` are
     rendered from MVT tiles (minzoom 1, maxzoom 16); smaller layers keep the
     inline-GeoJSON path with viewport culling.
   - `addVectorTileSource` replaces a same-id non-vector source (the empty
     pre-fetch GeoJSON upgrades to vector when the big FC arrives); sublayers
     get `source-layer: "data"` (MapLibre requirement).
   - MVT decision uses `descriptor.mvt_capable && feature_count > threshold`
     (descriptor short-circuit, 0 feature scans); `descriptor.bbox` / `geometry_types`
     reused without scanning FC.

## Consequences

- Per-render wire bytes for large layers drop ~100–1000×; the 100 ms
  `setData` parse jank and 26 MB HUD-store retention disappear for tile-served
  layers.
- Session id travels in the tile URL (established pattern in this app; a
  `transformRequest`-based token header is a possible follow-up).
- Encoder now covers all vector types; pure-Python fallback kept for non-shapely envs.
- Tile LRU + STRtree + single-flight + ETag make repeated viewport moves and
  concurrent tile fetches O(1) after the first encode.
