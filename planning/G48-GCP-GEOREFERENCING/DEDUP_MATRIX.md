# G48-GCP-GEOREFERENCING — Dedup Matrix

Every reusable capability or boundary requirement is traced to real codebase modules and status (`present` / `partial` / `missing` / `owned-by-other`).

| Capability / Seam | Status | Existing Authoritative Anchor | G48 Action / Decision |
|---|---|---|---|
| Bounded PNG/JPEG Pillow decode | `partial` | `app/services/visual_context/decoder.py` | Reuse/share decode bounds helper without changing G36 semantics; add strict EXIF parser, single-page TIFF validator, explicit uint8/uint16 capability check, corrupt-EXIF fail-closed rule |
| Untrusted raster magic/driver check | `partial` | `app/services/data_parser.py` (`_check_untrusted_raster_content`) | Reuse magic/driver allowlist for TIFF (`GTiff` only, reject VRT/multi-page) alongside PIL/rasterio inspection |
| Immutable source & artifact blob storage | `present` | `app/services/durable_blob_store.py` (`BlobStore`) | Store immutable original + normalized bytes in `BlobStore` with dual SHA256 digests and digest re-verification on warp start/read |
| GC & session cleanup protection | `partial` | `app/services/data_lifecycle/gc.py` | Protect active G48 blobs/refs during session lifetime; release explicitly on delete/reset |
| GeoTIFF / COG writing & range probe | `present` | `app/lib/geo_raster/cog.py` (`to_cog`, `validate_cog`, `range_read_probe`) + `env.py` (`rasterio_env`, `open_raster`) | Reuse directly inside warp pipeline; record `cog_verified=True` only after real validation + probe |
| 2D general affine GCP solver + checkpoints | `missing` | `None` (`raster_math.resample_raster` only takes raw gcps) | Build `app/services/georeferencing/solver.py` + `crs.py` (centered/scaled SVD/QR, condition/determinant/reflection, holdout checkpoints, hull coverage, honest 3-pt disclosure) |
| Direct 6-param warp & straight-alpha bilinear | `missing` | `app/lib/geo_raster/cog.py`, `rasterio.warp.reproject` | Build `app/services/georeferencing/warp.py`: freeze `Affine(a1,a2,a0,b1,b2,b0)` directly; premultiplied alpha weighted bilinear; zero black halo; preflight memory guard |
| Live XYZ raster tile rendering & MapSpec CAS | `partial` | `app/services/raster_tile_service.py`, `app/services/raster_authoring/service.py` | Keep G20 numeric raster path untouched; add RGBA / gray+alpha / internal mask + unstretched raw uint8 adapter |
| Print PNG/PDF raster publication window | `partial` | `app/services/raster_authoring/render.py`, `publication_window.py`, `mapspec_to_svg.py` | Add matching raw uint8 RGBA + mask passthrough adapter so print PNG/PDF matches live MapLibre tiles |
| Durable async job runner + cancel + recovery | `partial` | `app/services/jobs/lifecycle.py`, `app/services/data_operations/runner.py` | Add `JobKind.georeference`; build bounded chunked job execution, cancel fencing, digest check, and lifespan startup recovery |
| Session ownership & tenant isolation | `present` | `app/services/auth_history_bridge.py` (`require_owned_session`) | Apply on all HTTP routes & verify caller ownership in Pi tools |
| MapLibre map click picker mutual exclusion | `partial` | `frontend/lib/store/slices/toolSlice.ts`, `frontend/components/map/map-panel.tsx` | Add `'georef_pick_map'` to `MapToolId` & exclude sketch/feature-select while forwarding `publishMapClick` |
| Dual-view GCP frontend workbench UI | `missing` | `frontend/components/sidebar/data-sources-tab.tsx` | Build clean React workbench components + pure coordinate helper under `frontend/components/georeferencing/` & `frontend/lib/georeferencing/` |
| Deferred Pi tools & completion evidence | `missing` | `app/tools/__init__.py` (`ToolRegistry`), `app/services/gis_harness/completion/` | Add 6 deferred tools in `app/tools/georeferencing_tools.py`; keep `NATIVE_TOOL_NAMES` frozen; verify with `pi_doctor.py` & conformance checker |
| G40 symbol/texture assets | `owned-by-other` | `app/services/cartographic_assets/*`, `app/tools/cartographic_asset_tools.py` | Do not touch or re-implement |
| G18 arbitrary file delivery pipeline | `owned-by-other` | `app/services/data_delivery/*`, `app/api/routes/data_delivery.py` | Do not bypass G18 guards or couple G48 GeoTIFF download to G18 vector/zip contracts; implement standalone G48 authorized GeoTIFF/report download adapter |
