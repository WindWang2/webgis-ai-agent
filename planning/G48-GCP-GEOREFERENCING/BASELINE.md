# G48-GCP-GEOREFERENCING — Baseline Snapshot

- **Task ID**: `G48-GCP-GEOREFERENCING`
- **Task Slug**: `g48-gcp-georeferencing`
- **Base Branch / SHA**: `master` @ `655f769ca17f17c5797da13b4c299b8fa600e370` (synced after #2122 and G40 #2124 merge)
- **Execution Branch**: `zcode/g48-gcp-georeferencing-20261115T125056Z`
- **Execution Worktree**: `/home/kevin/projects/webgis/webgis-worktrees/g48-gcp-georeferencing-20261115T125056Z`
- **ADR Allocated**: `ADR-0293` (`docs/adr/0293-gcp-georeferencing.md`, `adr_watermark=293`)
- **Pi Runtime Pin**: `vendor/pi` @ `b4afdddf44e059c67ccf5097e5317d913521b470` (`v1.5.2`, `npm run build:offline` -> 84 files / 9.3 MiB; `.venv/bin/python scripts/pi_doctor.py` -> PASS: 491 tools, 7/7 native, 484 deferred)
- **Alembic Head**: `g40_cartographic_symbol_assets (head)` (G48 reuses existing `AnalysisTask`, `Conversation`, `BlobStore`, `session_data_manager`, `artifact_registry`, and per-session `DATA_DIR/georeferencing` atomic JSON storage without adding a conflicting migration head)

## Key Architectural Findings & Seam Inventory

1. **Image & Raster Intake Seams**:
   - `app/services/visual_context/decoder.py`: bounded Pillow decoder for PNG/JPEG (`n_frames == 1`, `MAX_DECODE_PIXELS`, `ImageOps.exif_transpose`), but silently catches EXIF errors (G48 must parse orientation explicitly & fail closed on corrupt EXIF unless user explicitly overrides).
   - `app/services/data_parser.py`: `_check_untrusted_raster_content` enforces TIFF/BigTIFF magic numbers (`_RASTER_DRIVERS = ("GTiff",)`), rejecting VRT/camouflaged payloads.
   - `app/services/durable_blob_store.py`: `BlobStore` (`put_blob`, `put_blob_from_path`, `get_blob`, `get_blob_stream`, 64 MiB default max read).
   - `app/services/data_lifecycle/gc.py`: Session cleanup & orphan blob/ref sweeping — must respect active G48 source/normalized/artifact blobs and release them cleanly on delete/reset.
2. **Warp, COG, Live Tile & Print Publication Seams**:
   - `app/lib/geo_raster/cog.py`: `write_cog`, `to_cog`, `validate_cog`, `range_read_probe`, `CogWriteError`.
   - `app/services/raster_tile_service.py`: XYZ PNG tile renderer & `register_raster_ref` — currently reads 1 or 3 bands ignoring RGBA alpha / internal masks, stretches RGB channels independently, and outputs binary alpha `255`/`0`. Needs additive raw uint8 RGBA/gray+alpha/internal mask passthrough adapter while keeping G20 numeric raster rendering backward compatible.
   - `app/services/raster_authoring/render.py`, `publication_window.py`, and `mapspec_to_svg.py`: print PNG/PDF window renderer — same 1/3-band stretch gap; needs aligned raw uint8 RGBA + mask passthrough so PNG/PDF exports match live MapLibre tiles without black borders or color distortion.
3. **Durable Jobs, Ownership & Pi Surfaces**:
   - `app/services/jobs/lifecycle.py`: `JobKind` enum (add `JobKind.georeference = "georeference"`), `app/services/data_operations/runner.py` (`submit_async_job`, `run_async_job`, `AsyncJobHandle`).
   - `app/main.py`: FastAPI router registration + lifespan startup recovery hooks (reuse G38 recovery pattern).
   - `app/services/auth_history_bridge.py`: `require_owned_session` & `verify_session_owner` (SEC-08).
   - `app/tools/__init__.py`: `_TOOL_MODULES` (add `app.tools.georeferencing_tools`, deferred only; zero changes to `NATIVE_TOOL_NAMES`).
   - `frontend/lib/store/slices/toolSlice.ts` + `frontend/components/map/map-panel.tsx`: add `'georef_pick_map'` mutual exclusion while keeping `publishMapClick([lng, lat])`.
   - `tests/unit/api_contract/_contract_util.py` + `scripts/gen_api_docs.py`: all JSON routes require `response_model=...`, binary download/preview routes added to `EXCLUSIONS`, `docs/api-docs.md` regenerated.
