# 06 — OGC / STAC / Tile Integration Gaps

Baseline: fabric adapters (10, 6,978 LOC, raw 60 §1), safe requests stack (`data_fabric/security.py`),
GDAL raster path, MVT/XYZ serving. Paths relative to `app/` unless noted.

## 1. Per-protocol status and gaps

### OGC API — Features (OAPIF)
- Status: `adapters/ogc_api_adapter.py` (448 LOC) — collections/items, CQL filter, `links.next` opaque
  cursor (:304-306), same-origin cursor guard (`security.py:464-489`).
- Gaps: none blocking for V1 vector read. CQL extent and pagination already push down; capabilities
  defaults per `query/capabilities.py:237`.

### WFS 1.x/2.0
- Status: `adapters/wfs_adapter.py` (596 LOC) — GetCapabilities + GetFeature, bbox pushdown, POST body
  (:445), XML via defusedxml.
- Gaps: schema/property resolution is shallow vs full WFS XSD; no stored-query support. Acceptable for V1;
  keep behind the existing ABC.

### WMS / WMTS
- Status: `adapters/wms_wmts_adapter.py:20-170` — GetCapabilities layer listing only (:64-103).
- **Gaps (truthfulness violations)**: `describe()` hard-codes `srs="EPSG:3857"` + world bbox
  (:113-114) — conflicts with "never fabricate CRS" (`lib/data/artifact_contract.py:192`); `query()`
  returns empty features + a GetMap `tile_url_template` (:120,:133-145) with no GetMap/GetTile proxying
  and no capability-derived CRS/bbox.
- Required fix (core additive): derive CRS + bbox from GetCapabilities; report raster coverage honestly
  (metadata-only like PMTiles/S3, `registry.py:154`) until a tile-fetch path exists.

### WMTS GetTile / XYZ / TMS (external)
- Status: absent for external tile URLs. Serving side exists only for local data: MVT over session refs
  (`services/mvt.py`) and XYZ PNG from local GeoTIFF (`services/raster_tile_service.py`); basemap tile
  URLs live in frontend (`core/base_layers.py:15-28`, `frontend/lib/providers.ts`).
- Gap: no `fetch_tile(z,x,y)` on the adapter ABC; `mapspec_source.DATAFABRIC_SOURCE_TYPES` accepts wms/
  wmts/pmtiles as opaque lazy entries (`mapspec_source.py:19,40-48`) with no backend fetch path.

### COG / raster remote reads
- Status: windowed rasterio reads (`services/rs/stac_client.py:204-212`), GDAL env knobs
  (`lib/geo_raster/env.py:39-44`), `RemoteReadPolicy` byte+count budget + host breaker
  (`lib/geo_raster/remote.py:63-104`), COG write/validate/probe (`lib/geo_raster/cog.py`).
- **Gap: SSRF** — no `validate_url` anywhere in `lib/geo_raster/` or `services/rs/`;
  `stac_client.py:212 RasterReader.open(href)` opens any asset href via `/vsicurl` (raw 60 §3c).

### STAC
- Status: fabric adapter `adapters/stac_adapter.py` (760 LOC) — POST `/search`, bbox+datetime, cursor,
  synthetic fixtures labelled `is_demo`; **"asset materialization is separate" and unimplemented**
  (`registry.py:145`). Separate primitive client `services/rs/stac_client.py` — pystac_client to a
  **hard-coded earth-search catalog URL (:14)**, ignores `ConnectionProfile`; used by
  `services/rs/spectral_engine.py`; **not wired to the fabric STAC adapter**.
- Gaps: (a) earth-search hardcode `rs/stac_client.py:212` (catalog constant at :14); (b) STAC asset →
  raster artifact materialization missing (`registry.py:145`); (c) asset hrefs bypass the SSRF layer (§COG).

### PostGIS
- Status: `adapters/postgis` adapter among the 10 (raw 60 §1); `ConnectionProfile.model_post_init` parses
  postgres DSNs into structured fields (`schemas/data_fabric_schema.py:43-55`).
- Gaps: credentials plaintext in `DataSource.connection_profile` (`models/data_fabric.py:22`); no
  secret-reference indirection; read-only scope fine for V1.

### Absent protocols
- WCS, OGC API Tiles/Maps/Coverages, external XYZ proxy, remote GeoJSON URL adapter (deliberately removed,
  `registry.py:16-21`) — out of V1 scope (§3).

## 2. Cross-cutting structural gaps

1. **Closed adapter registry**: literal `_build_registry()` (`registry.py:105-169`); no manifest/entry-point
   discovery; new source types need hand edits in `mapspec_source.py:19` and `query/capabilities.py` defaults.
2. **Sync-only ABC**: no `fetch_tile`, `fetch_asset(href)`, `stream`, or raster methods (raw 60 §6);
   raster/tile providers degrade to metadata-only.
3. **Split egress policy**: SSRF+caps only on the requests stack; `core/network.py` aiohttp path has none
   (unbounded `resp.json()` at `provider_health.py:271`); GDAL path none; `tools/local_admin.py:179` raw
   `httpx.get`. Need one `SafeHttp` facade (sync + async + GDAL href pre-validation).
4. **Missing `ArtifactContract.from_fabric_descriptor` bridge**: fabric datasets reach the LLM only via
   `DataCatalog._entry_from_fabric_descriptor` (`services/data_catalog/catalog.py:270-289`); provenance
   (`source_revision`, `produced_by`, remote URL) is not projected into the V3 contract
   (`artifact_contract.py` bridges :351/:453/:508/:551 have no fabric variant).
5. **`validate_url` open items**: unresolvable hosts allowed with warning (`security.py:172-183`);
   connect-time IP pinning is a stated follow-up (:309-313) — needed before untrusted extension providers
   run in-process.

## 3. Recommended V1 scope

**Core additive fixes (no new protocols):**
1. WMS/WMTS honesty: capability-derived CRS/bbox; remove fabricated `EPSG:3857` + world bbox
   (`wms_wmts_adapter.py:113-114`); metadata-only descriptor until GetMap proxy lands.
2. STAC hardening: configurable catalog URL from `ConnectionProfile` (kill earth-search hardcode,
   `stac_client.py:14`); unify with the fabric STAC adapter; implement asset materialization
   (`registry.py:145`) as `ArtifactContract` raster artifacts via existing `from_raster_descriptor` path.
3. SSRF unification: `validate_url` before every `/vsicurl` open and every aiohttp call; route STAC asset
   hrefs through it (§COG/STAC gaps).
4. `ArtifactContract.from_fabric_descriptor` bridge projecting `DatasetDescriptor` → V3 contract with
   truthful (possibly empty) CRS, per `artifact_contract.py:192`.

**Extension Platform enablers:**
5. Manifest-driven adapter registration into the existing `AdapterRegistry` (namespaced source types
   `<ns>:<type>`), with `mapspec_source`/capabilities defaults extended dynamically instead of hand-edited.
6. Optional ABC extension `RasterTileCapableAdapter` (`fetch_tile`, `fetch_asset`) — additive protocol,
   default = unsupported (preserves the 10 existing adapters unchanged).

**Explicitly out of V1:** WCS, OGC API Tiles/Maps/Coverages, external XYZ/TMS proxying, new tile cache
infrastructure, secret vaulting (keep plaintext+redaction status quo, documented).
