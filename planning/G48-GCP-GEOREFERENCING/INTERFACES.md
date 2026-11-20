# G48-GCP-GEOREFERENCING — Interfaces & Contracts

## 1. HTTP API Surface (`/api/v1/georeferencing/...`, Protected by `require_owned_session`)

| Method | Path | Response Model / Media Type | Purpose |
|---|---|---|---|
| `POST` | `/georeferencing/sources/upload` | `GeorefSourceResponse` (`application/json`) | Multipart PNG/JPEG/single-page TIFF intake, EXIF parse/normalize, dual SHA256 + BlobStore persist |
| `GET` | `/georeferencing/sources` | `GeorefSourceListResponse` | List sources for owned session |
| `GET` | `/georeferencing/sources/{source_id}` | `GeorefSourceResponse` | Inspect source capability, EXIF metadata, digests & existing georef warning |
| `GET` | `/georeferencing/sources/{source_id}/image` | Binary PNG (`_contract_util.EXCLUSIONS`) | Authorized normalized preview/source image stream for dual-view canvas |
| `POST` | `/georeferencing/plans` | `GeorefPlanResponse` | Create draft georeferencing plan for a `source_id` |
| `GET` | `/georeferencing/plans` | `GeorefPlanListResponse` | List plans in session |
| `GET` | `/georeferencing/plans/{plan_id}` | `GeorefPlanResponse` | Get plan state, GCPs, fit/checkpoint metrics, frozen snapshot & artifacts |
| `PUT` | `/georeferencing/plans/{plan_id}/draft` | `GeorefPlanResponse` | CAS-guarded update (`expected_revision`) of target CRS, resampling, output resolution, COG/EXIF override, GCP batch |
| `POST` | `/georeferencing/plans/{plan_id}/gcps/import` | `GeorefPlanResponse` | Validated CSV/JSON GCP table import with CAS revision |
| `GET` | `/georeferencing/plans/{plan_id}/gcps/export` | `text/csv` or `application/json` (`_contract_util.EXCLUSIONS`) | Export GCP table + fit/checkpoint residuals + affine transform JSON/CSV |
| `POST` | `/georeferencing/plans/{plan_id}/preview` | `GeorefPreviewResponse` | Compute live affine diagnostics & downsampled straight-alpha preview PNG + footprint polygon |
| `GET` | `/georeferencing/plans/{plan_id}/preview/image` | Binary PNG (`_contract_util.EXCLUSIONS`) | Authorized downsampled warped preview overlay PNG |
| `POST` | `/georeferencing/plans/{plan_id}/execute` | `GeorefExecuteResponse` | Freeze plan at `expected_revision` (idempotency supported) & launch G23 `JobKind.georeference` job |
| `POST` | `/georeferencing/plans/{plan_id}/cancel` | `GeorefJobStatusResponse` | Cancel running georeferencing job + set cancel fence |
| `GET` | `/georeferencing/plans/{plan_id}/status` | `GeorefJobStatusResponse` | Poll durable job status, stage, progress & failure details |
| `POST` | `/georeferencing/plans/{plan_id}/bind-layer` | `GeorefBindLayerResponse` | Bind completed GeoRaster artifact (`raster_ref`) to MapSpec via CAS (`expected_mapspec_revision`) |
| `GET` | `/georeferencing/artifacts/{artifact_id}/download` | `image/tiff` (`_contract_util.EXCLUSIONS`) | Authorized GeoTIFF/COG download after completed + digest-verified check |
| `DELETE` | `/georeferencing/sources/{source_id}` | `GeorefDeleteResponse` | Delete source/plans & release GC pins cleanly |

## 2. Backend Module Boundaries

- `app/schemas/georeferencing_schema.py`: Typed domain models, error codes, request/response schemas.
- `app/services/georeferencing/crs.py`: Projected 2D metric CRS validation, `EPSG:4326 -> target_crs` (`always_xy=True`) transform + area-of-use check.
- `app/services/georeferencing/intake.py`: Safe PNG/JPEG/single-page TIFF decode, EXIF fail-closed parsing, normalization, dual SHA256, `BlobStore` storage.
- `app/services/georeferencing/solver.py`: Centered/scaled SVD/QR 2D general affine fitter, holdout checkpoint evaluator, hull coverage & outlier metrics.
- `app/services/georeferencing/warp.py`: Direct 6-parameter `Affine` warp (`rasterio.warp.reproject`), preflight pixel/byte budget guard, straight-alpha premultiplied bilinear, preview generator, `cog.to_cog` + `validate_cog` + `range_read_probe`.
- `app/services/georeferencing/store.py`: `fcntl.flock` atomic JSON per-session store with CAS revision & plan freezing.
- `app/services/georeferencing/jobs.py` & `recovery.py`: G23 durable runner (`JobKind.georeference`), digest verification, cancel fencing, `session_data_manager` / `artifact_registry` registration, startup recovery.
- `app/services/georeferencing/service.py`: Facade orchestrating UI routes & Pi tools identically.
- `app/tools/georeferencing_tools.py`: 6 deferred tools registered in `app/tools/__init__.py` (`NATIVE_TOOL_NAMES` unchanged).

## 3. Shared Raster Tile & Publication Adapter Contract

- Warped uint8 RGBA GeoTIFFs carry `count=4`, Band 4 = `ColorInterp.alpha`, `nodata=None`.
- Warped uint8 grayscale + partial alpha GeoTIFFs carry `count=2` (`gray`, `alpha`), `nodata=None`; 1-band uint8/uint16 grayscale without partial alpha carries internal TIFF mask (`GDAL_TIFF_INTERNAL_MASK=YES`) or explicit nodata.
- `raster_tile_service.render_raster_tile`, `raster_authoring/render.py`, and `raster_authoring/publication_window.py` detect `raw_uint8_rgba` / `ColorInterp.alpha` / internal mask cleanly without disturbing numeric DEM/NDVI stretch behavior.
