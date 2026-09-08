# 60 — Data Provider / Data Source abstractions & remote GIS data access

Worktree: `/home/kevin/projects/webgis/webgis-ai-agent-extension-platform-v1` (read-only investigation, 2026-09-08). Paths below are relative to `app/`.

## 1. File inventory

| Path | Purpose |
|---|---|
| `core/network.py` | aiohttp shared `ClientSession` pool (per event loop), SSL ctx (certifi / `ALLOW_INSECURE_SSL` gate), base UA headers. No SSRF, no body cap. |
| `services/provider_health.py` | `ProviderHealthTracker` (60/min window + 5-error circuit, 300 s cool-down) and `tracked_provider_get()` for amap/baidu/tianditu/overpass/nominatim. |
| `services/data_fabric/base_adapter.py` | `GeospatialDataSourceAdapter` ABC — the provider interface. |
| `services/data_fabric/registry.py` | `AdapterSpec` / `AdapterRegistry`: single source of truth `source_type → adapter` (11 types). |
| `services/data_fabric/adapters/*.py` | 10 concrete adapters: postgis, ogc_api, wfs, wms_wmts, arcgis, stac, geoparquet, flatgeobuf, pmtiles, s3 (6978 LOC total). |
| `services/data_fabric/connection_manager.py` | In-memory per-owner profile store + `GenericDataSourceAdapter` (explicit demo, synthetic features). |
| `services/data_fabric/security.py` | SSRF `validate_url`, `SSRFSafeHTTPAdapter`, `make_safe_session`, `bounded_get`, `safe_json_get`, `sanitize_profile_dict`, `redact_url`, `parse_safe_xml` (defusedxml), `resolve_safe_local_path`, `ensure_same_origin_url`. |
| `services/data_fabric/limits.py` | Clamped budgets (features / bytes / pages / timeouts) with non-zero floors. |
| `services/data_fabric/manager.py` | `DataFabricManager`: `create_data_source` (DB persist), `sync_catalog`, `query_catalog_item(_async)`, `materialize_catalog_item`, `explain_catalog_item`. |
| `services/data_fabric/materialization_service.py` | `QueryResult` → session `ref:` (prefix `data-fabric`); result modes; `is_demo` labelling; `query_evidence`. |
| `services/data_fabric/query/` | V2 query plane: `capabilities.py` (`AdapterCapabilitiesV2` defaults per source_type), planner, compilers, predicates, federation, pushdown, statistics. |
| `services/data_fabric/{circuit_breaker,reliability,health,metadata_cache,spatial_catalog}.py` | Per-source breaker, `RetryPolicy`+`is_transient`, health truthfulness, tenant-scoped TTL cache, catalog index. |
| `schemas/data_fabric_schema.py` | `ConnectionProfile`, `DatasetDescriptor`, `QuerySpec`, `QueryResult`, `DataFabricHealth`. |
| `models/data_fabric.py` | ORM `DataSource` (`connection_profile` JSON), `DataFabricDataset`, `DataMaterializationRecord`, `DatasetStatisticsRecord`. |
| `services/data_catalog/catalog.py` | Federated read-only `DataCatalog` over session refs + uploads + fabric descriptors. |
| `services/data_ingest/pipeline.py` | `IngestPipeline`: FeatureCollection → session ref (fingerprint dedup). No HTTP. |
| `services/data_lifecycle/{service,gc}.py` | V3 lifecycle / staleness propagation over `artifact_registry`. |
| `lib/data/artifact_contract.py` | `ArtifactContract` V3 read-only projection + `from_*` bridges. |
| `lib/data/vocabulary.py` | `ArtifactCategory`, `LogicalRole`, `LifecycleState`, `PersistenceTier`, `QualityStatus`. |
| `tools/data_fabric_tools.py` | Agent tools: `connect_data_source`, `inspect_data_source`, `search_spatial_catalog`, `describe_dataset`, `query_dataset`, `materialize_dataset`, `refresh_data_source`, `plan_data_query`, `aggregate_dataset`, `query_federated_data`. |
| `tools/data_discovery.py` | Read-only tools over `DataCatalog` / profiler / lifecycle: `list_datasets`, `search_datasets`, `profile_dataset`, `describe_artifact`, `find_artifacts_by_role`, `get_lineage`. |
| `tools/osm.py`, `tools/geocoding.py` | Overpass / Nominatim via `tracked_provider_get` (aiohttp path). |
| `tools/chinese_maps/{protocol,http,amap,baidu,tianditu}.py` | Second provider abstraction: `ChineseMapsProvider` Protocol + fallback dispatch + API-key injection. |
| `tools/local_admin.py` | Fallback `httpx.get` straight to Amap district API (bypasses tracker / shared client) at :179-190. |
| `services/rs/stac_client.py` | `StacClientPrimitive`: pystac_client to hard-coded earth-search + windowed COG band reads. |
| `lib/geo_raster/{env,remote,cog,source,reader}.py` | GDAL env knobs, remote window-read retry/budget policy, COG write/validate/range-probe, `RasterSource` descriptor. |
| `services/mvt.py` | MVT encoder that *serves* session-ref vectors as tiles (not a consumer of remote tiles). |
| `services/raster_tile_service.py` | XYZ PNG tiles rendered from local GeoTIFF (windowed rasterio reads). |
| `core/base_layers.py` | Basemap provider name catalog (12 entries) — LLM-facing names only; tile URLs live in frontend. |
| `services/mapspec_source.py` | MapSpec source shapes; `DATAFABRIC_SOURCE_TYPES = {data_fabric, wms, wmts, pmtiles}`. |
| `services/layer_service.py` | DB `Layer` CRUD (`source_url` column). Pure persistence; no fetch. |
| `services/data_parser.py` | Local upload parsing (geopandas/pyogrio, shapefile zip validation). No HTTP. |

## 2. Existing provider / data-source interfaces

**Primary: `GeospatialDataSourceAdapter(ABC)`** — `services/data_fabric/base_adapter.py:18-85`. Sync methods; adapters are run via `asyncio.to_thread` (`tools/data_fabric_tools.py:154`).
```python
def __init__(self, connection_profile: ConnectionProfile)                 # :24
@abstractmethod def probe(self) -> bool                                   # :28
@abstractmethod def capabilities(self) -> List[str]                       # :33
@abstractmethod def list_datasets(self) -> List[Dict[str, Any]]           # :38
@abstractmethod def describe(self, dataset_id: str) -> DatasetDescriptor  # :43
@abstractmethod def preview(self, dataset_id: str, limit: int = 10) -> Dict[str, Any]   # :48
@abstractmethod def query(self, dataset_id: str, query_spec: QuerySpec) -> QueryResult  # :53
@abstractmethod def health(self) -> DataFabricHealth                      # :58
def sync(self, owner=None) -> Dict[str, Any]   # concrete; registers describe() results into spatial_catalog_service  :62-85
```
Optional V2 hook: `capabilities_v2()` override consumed by `query/capabilities.py:237 get_capabilities(source_type, overrides)`; declared fields are `AdapterCapabilitiesV2` (`query/models.py:197-216`: bbox/filter/projection/sort pushdown, offset/cursor pagination, spatial_predicates, temporal_filter, aggregation, group_by, count, statistics, server_reprojection, vector_tiles, range_requests, streaming, max_page_size, server_side_spatial_join).

**Registry** — `services/data_fabric/registry.py`: `AdapterSpec` dataclass `:31-53` (canonical, adapter_cls, aliases, supports_bbox/filter/pagination/datetime/projection, is_raster_tile, is_demo, notes); `AdapterRegistry.register/resolve/is_supported/build_adapter` `:56-101` (alias rebinding raises `ValueError` `:67-71`; unknown type raises `UnsupportedSourceError` `:79-85`, never mock fallback). Registration table is a closed literal list in `_build_registry()` `:105-169`; lazily built singleton `get_registry()` `:175-179`.

**Profile model** — `ConnectionProfile` `schemas/data_fabric_schema.py:9-55`: id, source_type, provider_type, name, url/endpoint/endpoint_url, host, port, database, username, password, access_key, secret_key, region, `credentials: dict`, `options: dict`, `allow_private: bool`; `model_post_init` parses postgres DSN into structured fields `:43-55`.

**Secondary: `ChineseMapsProvider(Protocol, runtime_checkable)`** — `tools/chinese_maps/protocol.py:30-79`: async `search_poi`, `search_poi_around`, `search_poi_polygon`, `geocode`, `reverse_geocode`, `route`, `input_tips`, `district`, `distance_matrix`. Dispatch/fallback in `tools/chinese_maps/http.py:125-206 with_fallback()`; key presence gate `_has_provider` `:28-35`.

**Third: `ProviderHealthTracker`** — `services/provider_health.py:26-159` (`can_call`, `record_attempt`, `record_success`, `record_error`, `snapshot`); `PROVIDER_NAMES` frozenset `:12` is label-only — any string key works.

## 3. Network egress safety

Two disjoint HTTP stacks:

**(a) aiohttp path — `core/network.py`** (used by provider_health, osm, geocoding, chinese_maps, web_crawler):
- `get_ssl_context(verify)` `:12-42` — certifi CA; `verify=False` only when `ALLOW_INSECURE_SSL` env set.
- `get_shared_client()` `:166-200` — per-loop `ClientSession`, `TCPConnector(ttl_dns_cache=300, limit=20, limit_per_host=10)`, `ClientTimeout(total=10)`.
- **No SSRF check, no private-range block, no redirect re-validation (aiohttp default follows redirects), no response-size cap.** `tracked_provider_get` `services/provider_health.py:225-286` adds rate limit + breaker, proxy from `settings.HTTPS_PROXY/HTTP_PROXY` `:251`, per-call `timeout=10.0`, but reads `resp.json()` unbounded `:271`.
- Targets are operator-configured constants (`config.py:105,123`; `chinese_maps/http.py:23-25`) so user URLs never reach this path today. `config.py:329` validates `OVERPASS_API_URL`/`NOMINATIM_URL`.

**(b) requests path — `services/data_fabric/security.py`** (all fabric adapters):
- `DataFabricSecurity.validate_url(url, allow_private=False)` `:95-191`: scheme allowlist http/https/s3/minio/postgresql/postgres `:117`; hostname blocklist + `.local`/`.internal` `:153-159`; literal IP or **all** `getaddrinfo` A/AAAA records checked against `BLOCKED_NETWORKS` `:55-65` (RFC1918, loopback, link-local, 0/8, ::1, fc00::/7, fe80::/10) + explicit metadata IPs `:40-43`; IPv4-mapped IPv6 unwrapped `:87-89`. Unresolvable hostname is **allowed with a warning** `:172-183`.
- `SSRFSafeHTTPAdapter.send` `:316-331` re-runs `validate_url` on every hop (redirect-SSRF fix, narrows DNS-rebind TOCTOU); `make_safe_session` `:334-342` mounts it on http/https.
- `bounded_get` `:350-387`: `stream=True`, Content-Length pre-check, decompressed byte cap (floor 16 MiB `:347`), raises `SourceBadResponseError`. `safe_json_get` `:390-396`. `ensure_same_origin_url` `:464-489` prevents next-link cursors from exfiltrating creds to another origin.
- `parse_safe_xml` `:277-293` — defusedxml + `defuse_stdlib()` `:22-24` (XXE for WFS/WMS capabilities).
- `resolve_safe_local_path` `:399-447` — realpath, sensitive dirs, `DATA_FABRIC_LOCAL_FILE_ROOTS` allow-list, size cap.
- Budgets `services/data_fabric/limits.py:20-40` from `core/config.py:186-190`: features 50k (floor 1k), response 256 MiB (floor 16 MiB), pages 200 (floor 10), per-request 30 s (floor 5), total 120 s. Per-source `CircuitBreaker` + `RetryPolicy` (transient-only).
- `allow_private` is **not** an LLM tool parameter (`tools/data_fabric_tools.py:113-135`); only REST route / server-side.

**(c) GDAL path — raster remote reads**: `lib/geo_raster/env.py:39-44` `GDAL_HTTP_TIMEOUT=5`, `GDAL_HTTP_MAX_RETRY=0`, `GDAL_DISABLE_READDIR_ON_OPEN`; `lib/geo_raster/remote.py:63-104` `RemoteReadPolicy` / `RemoteReadSession` byte+count budget, host-keyed breaker. `remote_uri()` `:89-92` treats `http(s)://` and `/vsi*` as remote. **No SSRF validation anywhere in `lib/geo_raster/` or `services/rs/`** (grep confirmed) — `stac_client.py:212 RasterReader.open(href)` opens any STAC asset href.

**Secrets**: provider keys are `settings` fields (`core/config.py:126-154`: `TIANDITU_TOKEN`, `AMAP_API_KEY`, `BAIDU_MAP_AK`, `BING_MAP_KEY`, `TENCENT_MAP_KEY`, `OPENTOPOGRAPHY_API_KEY`) injected as query params at `tools/chinese_maps/http.py:213,222,232`. Fabric credentials live in `ConnectionProfile` and are **persisted plaintext** in `DataSource.connection_profile` JSON (`models/data_fabric.py:22`; deliberate, see `manager.py:153-159`). Adapters inject `options["headers"]` verbatim into the session (`adapters/ogc_api_adapter.py:53-54`, `adapters/wfs_adapter.py:114-115`). Egress redaction: `sanitize_profile_dict` `security.py:242-274` (recursive key match incl. headers subtree) applied in `api/routes/data_fabric.py:358-431` and `tools/data_fabric_tools.py:139`; `redact_url` strips userinfo `:218-239`. Trace side: `services/chat/decision_log.py:55-66` redacts `api_key/token/password/secret/apikey/Authorization` in logged tool args only. No generic redaction of tool *results* (e.g. WMS `tile_url_template` echoing a URL) — relies on adapters not echoing creds.

## 4. Existing OGC / STAC / tile support

| Capability | Where | Status |
|---|---|---|
| WFS 1.x/2.0 | `adapters/wfs_adapter.py` (596 LOC) | GetCapabilities + GetFeature, bbox pushdown; XML via defusedxml; POST body `:445`. |
| WMS / WMTS | `adapters/wms_wmts_adapter.py:20-170` | GetCapabilities layer listing only `:64-103`. `describe()` hard-codes `srs="EPSG:3857"`, world bbox `:113-114` and a GetMap `tile_url_template` `:120`; `query()` returns empty features + GetMap URL `:133-145`. No GetTile/GetMap proxying, no capability-derived CRS/bbox. |
| OGC API Features | `adapters/ogc_api_adapter.py` (448 LOC) | collections/items, CQL filter, `links.next` opaque cursor `:304-306`; same-origin cursor guard. |
| ArcGIS REST | `adapters/arcgis_adapter.py` (508 LOC) | FeatureServer/MapServer query pushdown. |
| STAC | `adapters/stac_adapter.py` (760 LOC) | POST `/search`, bbox+datetime, cursor; synthetic fixtures labelled `is_demo`. "asset materialization is separate" (`registry.py:145`). |
| STAC → COG bands | `services/rs/stac_client.py:48-` | pystac_client, catalog URL constant `:14` (earth-search); windowed rasterio read per asset href `:204-212`; used by `services/rs/spectral_engine.py`. Not wired to fabric STAC adapter. |
| COG tooling | `lib/geo_raster/cog.py` | `write_cog`, `validate_cog`, `range_read_probe`. |
| PMTiles | `adapters/pmtiles_adapter.py` (870 LOC) | Metadata-only in catalog (`registry.py:154`). |
| GeoParquet / FlatGeobuf / S3 | respective adapters | Local-file + range read seams; S3 metadata-only. |
| Vector tiles (serve) | `services/mvt.py` | MVT 2.1 encoder over session refs; STRtree + LRU + single-flight. |
| Raster XYZ (serve) | `services/raster_tile_service.py` | 256px EPSG:3857 PNG from local GeoTIFF. |
| Basemaps | `core/base_layers.py:15-28` | 12 named providers; URLs/keys owned by `frontend/lib/providers.ts`. |
| MapSpec | `services/mapspec_source.py:19,40-48` | Fabric/wms/wmts/pmtiles entries pass through as opaque dicts with `catalog_item_id`/`lazy`. |

Absent: WCS, OGC API Tiles/Maps/Coverages, WMTS GetTile or XYZ proxy for external tile URLs, STAC asset → raster artifact materialization bridge, remote GeoJSON URL adapter (removed, `registry.py:16-21`).

## 5. ArtifactContract / data ref model for external assets

`lib/data/artifact_contract.py`:
- `ArtifactContract` `:173-209` — `artifact_id`, `stable_identity`, `version`, `artifact_type` (vocab-validated `:212-221`), `artifact_subtype`, `logical_role`, `source: SourceInfo`, `storage_ref` (pointer, never payload), `media_type`, `format`, `data_schema` (≤64), `geometry_kind`, `feature_count`, `crs` (empty = unknown, never fabricated), `extent`, `temporal_extent`, `raster: RasterShape`, `units`, `statistics` (≤32), `profile_ref`, `lineage: LineageInfo`, `produced_by: ProducedBy`, `fingerprint: FingerprintSet`, `size_bytes`, `lifecycle`, `persistence`, `cacheability`, `reproducibility`, timestamps, `diagnostics` (≤8). `summary()` `:270-327` is the LLM-facing bounded view (≤1600 chars, progressive drop).
- `SourceInfo` `:69-77` — `source_type` (comment enumerates `upload / session_ref / raster_disk / db / fabric:<type>`), `source_ref` (upload_id / url / source_key), `source_revision`, `display_name`.
- `ProducedBy` `:100-108` (tool, capability, algorithm, node, workflow_run_id, operation_version); `LineageInfo` `:111-139` (parents ≤16, dependencies, replaces); `Cacheability` `:142-148`; `Reproducibility` `:151-157`.
- Bridges: `from_artifact_record` `:351`, `from_ref_descriptor` `:453`, `from_db_artifact` `:508`, `from_raster_descriptor` `:551`. **No `from_fabric_descriptor`** — fabric datasets reach the LLM only via `DataCatalog._entry_from_fabric_descriptor` (`services/data_catalog/catalog.py:270-289`: `entry_id="fabric:<id>"`, `scope="fabric"`, `source_type="fabric:<type>"`, `source_ref=source_id`).
- Vocabulary `lib/data/vocabulary.py`: `ArtifactCategory` `:31-48` (vector, raster, table, point_cloud, network, timeseries, matrix, model, statistics, chart_data, report, map_export, archive, metadata, collection); `LogicalRole` `:79-97`; `LifecycleState` `:100-119`; `PersistenceTier` `:255-261`.
- Remote-side descriptor: `DatasetDescriptor` `schemas/data_fabric_schema.py:72-99` (id, source_type, source_id, title, geometry_type, feature_type, data_type, srs/crs, bbox, feature_count, fields, schema_fields, query_capabilities, style_hints, free-form `metadata`). `QueryResult` `:127-148` carries `ref_id`, `result_mode`, `is_demo`, `next_cursor`, `metadata` (query_evidence). Materialization audit row: `DataMaterializationRecord` `models/data_fabric.py:81-100` (query_fingerprint, result_mode, record_count).

## 6. Extension seams and gaps for a Data Provider SDK (OGC/STAC)

Seams already usable:
1. `AdapterRegistry.register(AdapterSpec)` `registry.py:63-73` — append-only, alias-safe; `GeospatialDataSourceAdapter` ABC is the contract; `manager.get_adapter` and tools already resolve via registry `manager.py:86-93`.
2. `get_capabilities(source_type, overrides)` `query/capabilities.py:237` — adapters may override truthful capability defaults.
3. Egress primitives: `make_safe_session` / `bounded_get` / `safe_json_get` / `parse_safe_xml` / `ensure_same_origin_url` (`security.py`) + `limits.py` budgets + `circuit_breaker.py` / `reliability.py`.
4. `MaterializationService.materialize` → session ref + `query_evidence`; `DataCatalog` auto-lists fabric descriptors; `mapspec_source.store_data` `:40-48` passes fabric payloads to MapSpec.
5. `ProviderHealthTracker` accepts arbitrary provider keys; `tracked_provider_get` wraps aiohttp calls.
6. Tool descriptor `network: Optional[bool]` (`tools/descriptor.py:197`) already marks egress-bearing tools.

Gaps to close for an SDK:
- **Closed registry**: `_build_registry()` is a literal list `registry.py:105-169`; no entry-point / manifest-driven discovery, no per-org enablement. `mapspec_source.DATAFABRIC_SOURCE_TYPES` `:19` and `capabilities.py` per-type defaults must be edited by hand for new types.
- **Sync-only ABC**; no `fetch_tile(z,x,y)`, `fetch_asset(href)`, `stream(...)` or raster methods — raster/tile providers degrade to metadata-only (WMS/PMTiles/S3). `query()` returns in-memory feature lists.
- **Split egress policy**: SSRF/size caps exist only on the `requests` stack; `core/network.py` (aiohttp) and GDAL `/vsicurl` reads have neither private-range blocking nor byte caps. `local_admin.py:179` uses raw `httpx.get`. An SDK needs one `SafeHttp` facade (async + sync + GDAL href pre-validation).
- **Credential model**: plaintext JSON persistence (`models/data_fabric.py:22`), header injection via `options["headers"]`; no secret-reference indirection (env/vault id), no per-provider auth strategy (API-key param vs bearer vs basic vs signed S3). Trace redaction covers tool *args* only (`decision_log.py:63`).
- **Truthfulness violations in WMS adapter**: fabricated `EPSG:3857` + world bbox `wms_wmts_adapter.py:113-114` conflict with contract "never fabricate CRS" (`artifact_contract.py:192`).
- **STAC asset materialization** missing (`registry.py:145`); `rs/stac_client.py` hard-codes one catalog `:14` and does not consume `ConnectionProfile`.
- **No `ArtifactContract` bridge for fabric descriptors** — provenance (`source_revision`, `produced_by`, remote URL) for external assets is not projected into the V3 contract; only `CatalogEntry` and `QueryResult.metadata`.
- `validate_url` permits unresolvable hosts `security.py:172-183` (documented trade-off) and connect-time IP pinning is a stated follow-up `:309-313`.
