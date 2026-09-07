# OGC / STAC Integration Status

The GIS Extension Platform also hardens the core data fabric's OGC/STAC
paths (ADR-0104 decisions 9; branch commit "fix(gis-standards): OGC/STAC
honesty + SSRF gates"). Everything below describes code that **exists** on
`feat/gis-extension-platform-v1`, pinned by
`tests/unit/extensions_platform/test_ogc_stac_hardening.py` (21 offline
tests with stubbed DNS/sessions).

## Per-protocol status

| Protocol | data_fabric source type | Status on this branch | Known gaps |
| --- | --- | --- | --- |
| OGC API – Features (OAPIF) | `ogc_api` (aliases `ogc_api_features`, `ogc`, `ogcapi`) | Full adapter: bbox/filter/pagination/datetime/projection pushdown, CQL2 predicate compile, cursor pagination, evidence metadata; all GETs via `safe_json_get` (size-capped, typed failures on non-200/bad JSON) | No OGC API **Tiles**/coverages support (see gaps below) |
| WFS | `wfs` (aliases `wfs1`, `wfs2`) | Vector features adapter with bbox pushdown | No datetime/pagination pushdown declared |
| WMS / WMTS | `wms` (aliases `wmts`, `wms_wmts`) | Raster-tile adapter; **hardened**: `describe()` parses CRS + bbox from GetCapabilities (Layer-chain `CRS`/`SRS` elements, `EX_GeographicBoundingBox`/`LatLonBoundingBox`/`WGS84BoundingBox`, parent-Layer inheritance), normalizes them, and **never fabricates EPSG:3857 or a world extent** — undeterminable values are honest `None`/empty with additive `metadata.notes`; bbox is WGS84 `[w, s, e, n]` with `metadata.bbox_crs` stating the source | GetCapabilities fetch/parse failure degrades metadata (notes + `describe_error`) rather than hard-failing; no raster byte fetch through data_fabric (tile URLs are metadata) |
| XYZ tiles | — | **No data_fabric adapter.** XYZ/WMTS basemaps are a frontend concern: `frontend/lib/providers.ts` `TILE_PROVIDERS` (raster `{z}/{x}/{y}` templates and vector GL style JSON) consumed client-side by the map stack | Server-side data fabric has no XYZ source type; extensions may register their own `is_raster_tile` provider if needed |
| COG | — | No dedicated adapter. COG **structural readiness** detection exists in `app/lib/geo_raster/reader.py` (`RasterInfo.is_cog`, `cog_structure_ok()`: tiled + overviews, advisory); remote COG reads go through `RasterReader.open`, now SSRF-gated | No COG-specific catalog source type or materialization pipeline |
| STAC | `stac` | V2 search adapter (normalize → plan → `POST /search`, result modes descriptor/statistics/sample/features, `links.next` cursor pagination, bbox+datetime pushdown) plus the `app/services/rs/stac_client.py` primitive. **Hardened**: the STAC API base URL is configurable (`settings.STAC_API_URL`, default `https://earth-search.aws.element84.com/v1` preserved) and **asset hrefs pass the SSRF gate before any raster open/download** | Asset **materialization** is a separate step, not part of `query`/`describe`; `stac` adapter declares bbox/datetime only |
| PostGIS | `postgis` (aliases `postgres`, `postgresql`) | Full adapter with bbox/filter/pagination/projection pushdown; the SSRF gate also accepts `postgresql`/`postgres` synthetic URLs so private/loopback DB hosts are blocked consistently | — |

Other registered source types for completeness: `arcgis` (+`featureserver`,
`mapserver` aliases), `geoparquet`, `flatgeobuf`, `pmtiles` (raster-tile,
metadata-only in catalog), `s3` (object-storage seam, metadata-only), and
`generic` (explicit `is_demo` synthetic adapter; never a fallback for
unknown types).

## The SSRF layer

One implementation, reused everywhere:
`app/services/data_fabric/security.py::DataFabricSecurity.validate_url`.

- **Scheme allowlist**: `http`, `https`, `s3`, `minio`, `postgresql`,
  `postgres`; anything else raises `DataFabricSecurityError` (a
  `ValueError` subclass).
- **Blocked hosts**: cloud metadata hostnames, `.local`, `.internal`.
- **Blocked networks** (checked on the literal IP *and* on every resolved
  A/AAAA record via `getaddrinfo`, IPv4 and IPv6): loopback (v4/v6), RFC1918
  private ranges, link-local (`169.254.0.0/16`, `fe80::/10`), IPv6
  unique-local (`fc00::/7`), IPv4-mapped IPv6 (e.g. `::ffff:127.0.0.1`),
  cloud metadata IPs. An unparseable IP is treated as blocked.
- An unresolvable hostname logs a warning rather than hard-failing
  (air-gapped deploys), but the resolved-IP gate still rejects the connection
  target if it later resolves private.
- Supporting hardening in the same module: `parse_safe_xml` (safe
  capabilities parsing), `bounded_get` / `safe_json_get`
  (Content-Length and decompressed-byte caps; typed failures), redirect-safe
  `SSRFSafeHTTPAdapter`, `redact_url` / `sanitize_profile_dict` so
  credential-bearing URLs never reach descriptors or traces.

### The validate_url-before-everything rule

All remote hrefs pass through `validate_url` **before** they reach GDAL or a
STAC fetch:

1. **GDAL `/vsicurl`** — `RasterReader.open(uri)`
   (`app/lib/geo_raster/reader.py`) calls `validate_remote_href(uri)`
   (`app/lib/geo_raster/env.py`) first. That gate delegates to
   `DataFabricSecurity.validate_url` (it never re-implements SSRF logic),
   strips a `/vsicurl/` prefix to check the embedded target, and passes
   local paths and non-http(s) schemes (`/vsi*`, `s3`, …) through untouched.
   Validation failure raises before `rasterio.open` is ever called.
2. **STAC assets** — `stac_client.py` runs every asset href through
   `_validate_asset_href` (same gate) before handing it to the raster
   reader; the catalog base URL comes from `settings.STAC_API_URL` instead
   of the previously hardcoded earth-search constant.
3. **Adapter HTTP** — data fabric adapters build their sessions with
   `make_safe_session` and fetch through `bounded_get` / `safe_json_get`,
   which validate per request.

Extension authors: if your provider fetches remote raster or vector data,
use the same entry points (`RasterReader.open`, `make_safe_session`) — do
not open raw sockets/clients around a validated URL and re-fetch it
yourself. (Note: within the trusted-code boundary these gates are
consistency and safety measures for the platform's own channels, not
confinement — see [limitations.md](limitations.md).)

## The no-fabricated-CRS rule

Project red line: **never silently assume a CRS**. Concrete guarantees after
the hardening:

- `WMSWMTSAdapter.describe()` returns `srs` / `bbox` only from explicit
  GetCapabilities declarations (including Layer-chain inheritance). If the
  service does not declare them, the descriptor carries `srs=None`,
  empty `normalized_crs`, `bbox=None`, and machine-readable `metadata.notes`
  explaining exactly which element was missing ("never a fabricated world
  extent"). It does **not** default to `EPSG:3857` or a global extent.
- Normalized CRS names (`EPSG:XXXX`, URN, CRS84 → canonical form) are
  exposed additively as `metadata.advertised_crs` / `metadata.normalized_crs`
  with `metadata.bbox_crs="EPSG:4326"` when a geographic bbox is present.
- Raster-tile sources (core or extension) report `feature_count=None` — an
  honest unknown — rather than a fabricated vector count.

The example extension provider (`extdemo-pack/tile_catalog.py`) mirrors
these semantics exactly; see [authoring-providers.md](authoring-providers.md).
