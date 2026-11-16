# G48-GCP-GEOREFERENCING — Implementation Plan

## Scope & Acceptance Targets

Deliver an end-to-end **GCP Image Georeferencing & Deliverable GeoRaster** workbench across intake, numerical affine fitting & checkpoint diagnostics, straight-alpha premultiplied warp & COG export, durable G23 job execution with cancellation & startup recovery, MapSpec CAS layer registration, live/print PNG+PDF rendering, authorized download, React dual-view UI, and deferred Pi tool orchestration covering scenarios **R01–R40**.

## Work Packages

- **WP01 (Baseline, Lock, ADR & Planning)**: Reserve `ADR-0293` (`docs/integration/ownership.json` -> `293`), initialize planning ledger (`planning/G48-GCP-GEOREFERENCING/`), verify `pi_doctor.py` and `alembic heads`.
- **WP02 (Source Intake, EXIF Policy, Immutable BlobStore & GC Hook)**:
  - `app/services/georeferencing/intake.py`: PNG / JPEG / single-page un-georeferenced TIFF intake (uint8 gray, uint8 RGB/RGBA, explicit capability-matrix uint16 single-page gray TIFF); strict magic + driver allowlist; decompression bomb & pixel/side limits; explicit EXIF orientation parse & fail-closed on corrupt EXIF unless explicit override; normalized raster materialization + dual SHA256 (`original_sha256`, `normalized_sha256`); preserve existing TIFF CRS/transform/GCP metadata as warning without silent overwrite.
  - `app/services/data_lifecycle/gc.py`: retain active G48 source/artifact refs while in active session, clean on reset/delete.
- **WP03 (Contracts, Atomic CAS Draft/Frozen Store & Projection Resolver)**:
  - `app/schemas/georeferencing_schema.py`: typed Pydantic schemas & API request/response models (R01–R40 error codes included).
  - `app/services/georeferencing/crs.py`: projected 2D metric CRS validation (reject degree/feet/vertical/compound CRS with typed error), `pyproj.Transformer(..., always_xy=True)` WGS84 map-pick conversion with provenance (`source_crs`, `raw_x`, `raw_y`, `target_crs`, `map_x`, `map_y`), area-of-use check.
  - `app/services/georeferencing/store.py`: `fcntl.flock` atomic JSON per-session store (`DATA_DIR/georeferencing/<session_id>/`), CAS `revision`, GCP CRUD / enable-disable / fit-vs-checkpoint role, plan freeze & preview/result invalidation semantics.
- **WP05 (Centered/Scaled SVD Affine Solver & Honest Diagnostics)**:
  - `app/services/georeferencing/solver.py`: 2D general 6-param affine fit (`x_map = a0 + a1*px + a2*py`, `y_map = b0 + b1*px + b2*py`) on `enabled && role=="fit"` points using centroid-shift + scale normalization, rank/condition/determinant/reflection/degenerate-inverse detection, independent holdout `role=="check"` residual computation (x, y, radial error, checkpoint RMSE, max error in target CRS metric units), exact-3-point zero-fit-residual honest disclosure ("not independently verified"), convex-hull coverage & outlier identification without silent point dropping.
- **WP06 (Direct 6-Param Warp, Straight-Alpha Bilinear, Preview & Verified COG)**:
  - `app/services/georeferencing/warp.py`: build frozen `src_transform = Affine(a1, a2, a0, b1, b2, b0)` directly (NEVER pass GCPs to `rasterio.warp.reproject`), preflight pixel/byte budget checks BEFORE allocation, premultiplied alpha weighted bilinear reprojection + exact nearest resampling, 4-band RGBA / 2-band gray+alpha / 1-band+internal-mask GeoTIFF output, downsampled preview PNG + footprint GeoJSON, optional COG build via `to_cog` + `validate_cog` + `range_read_probe`.
- **WP07 (G23 Durable Job Runner, Cancel Fencing, Digest Guard & Lifespan Recovery)**:
  - `app/services/jobs/lifecycle.py`: add `JobKind.georeference = "georeference"`.
  - `app/services/georeferencing/jobs.py` & `recovery.py`: windowed/chunked cancellable execution via `submit_async_job`, cancel fencing before publish, digest verification before/during warp (anti-swap R29), startup recovery marking stale running jobs interrupted/reconciling staging files (`app/main.py` lifespan).
- **WP08 (Live Tiles, Print PNG/PDF Adapter, HTTP Routes & Authorized Download)**:
  - `app/services/raster_tile_service.py`, `app/services/raster_authoring/render.py`, `app/services/raster_authoring/publication_window.py`, `app/services/raster_authoring/source.py`, `app/services/mapspec_source.py`, `app/services/mapspec_to_svg.py`: additive raw-uint8 / RGBA / internal-mask passthrough adapter without breaking existing numeric raster contracts.
  - `app/services/georeferencing/service.py` & `app/api/routes/georeferencing.py`: MapSpec CAS registration via `apply_gis_mutation(UpsertLayerIntent)` separated from data-generation success; authorized GeoTIFF & CSV/JSON report download adapter; register router in `app/main.py`, add binary routes to `_contract_util.EXCLUSIONS`, regenerate `docs/api-docs.md`.
- **WP04 (Dual-View Frontend Workbench UI & Map Click Integration)**:
  - `frontend/lib/store/slices/toolSlice.ts`, `frontend/components/map/map-panel.tsx`, `frontend/lib/api/jobs.ts`.
  - `frontend/lib/georeferencing/` (pure DPR/zoom/pan/rotate-agnostic coordinate transform + typed API client) & `frontend/components/georeferencing/` (upload intake, dual-view zoom/pan normalized pixel viewer + MapLibre `georef_pick_map`, GCP table + undo + CSV/JSON import-export, solver & checkpoint diagnostics banner, warped footprint + opacity preview, async job cancel/retry controls, bind-to-MapSpec layer, download GeoTIFF/report, EN/ZH copy).
- **WP09 (Pi Deferred Tool Chain & Completion Evidence)**:
  - `app/tools/georeferencing_tools.py` & `app/tools/__init__.py`: 6 deferred tools passing `check_tool_capability_conformance.py` and `pi_doctor.py`.
- **WP10 & WP11 (Oracle Tests, Fault Injections, Real Playwright E2E, Real Pi Subprocess Journey & Scale Ledger)**:
  - Unit + integration tests covering R01–R40 (external analytic oracle, GDAL trap R39 check, straight-alpha halo check, Rasterio readback, print PNG+PDF export readback, isolated Playwright browser E2E, isolated real Pi + local LLM stub E2E, performance/memory benchmark).
- **WP12 (Independent Reviewer Audit, Fix, Re-Review, Rebase/Merge Master, Push & PR)**:
  - Independent reviewer subagent (Slot 3) reviews full diff + test ledger -> fix all findings -> re-verify -> merge latest `origin/master`, run gates, commit cleanly, push, create PR.
