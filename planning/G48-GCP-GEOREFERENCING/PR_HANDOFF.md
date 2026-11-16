# PR Handoff — G48: GCP Image Georeferencing & Deliverable GeoRaster (`G48-GCP-GEOREFERENCING`)

## Summary
Implements end-to-end GCP-based raster image georeferencing in `WindWang2/webgis-ai-agent`:
- **Source Intake & EXIF Canonicalization** (`app/services/georeferencing/intake.py`): Strict magic-header + Pillow/GDAL validation for PNG, JPEG, and single-page TIFF (`uint8` 1/2/3/4-band and `uint16` grayscale); physical EXIF orientation (`1..8`) canonicalization with fail-closed corrupt EXIF guard and explicit `ignore_exif` override; existing embedded CRS/transform/GCP disclosure without silent reuse.
- **CRS & Coordinate Solver** (`app/services/georeferencing/crs.py`, `solver.py`): `always_xy=True` pyproj transforms between WGS84 and metric projected CRS (`EPSG:3857` / UTM / projected metric, rejecting geographic degree / vertical / non-metric units); unweighted OLS 6-parameter affine solver enforcing rank/condition checks, distinguishing negative-determinant reflections from singular fits, separating holdout checkpoints, and flagging clustered convex-hull coverage & large outliers without deleting user GCPs.
- **Direct Warp, COG Delivery & Alpha Publication** (`app/services/georeferencing/warp.py`, `app/services/raster_authoring/alpha_adapter.py`, `app/services/raster_authoring/render.py`, `app/services/raster_tile_service.py`, `app/services/raster_authoring/publication_window.py`): Frozen 6-parameter affine COG/GeoTIFF export preserving categorical nearest labels, straight-alpha premultiplied bilinear/cubic weights (zero black halos + preserving `[1..254]` alpha and `uint16` internal masks), `validate_cog` + `range_read_probe` verification, and live XYZ / mixed SVG/PNG/PDF map export parity.
- **Durable Execution, CAS, Atomic Refcounts & Session Lifecycle** (`app/services/georeferencing/jobs.py`, `refs.py`, `store.py`, `service.py`, `app/services/session_data.py`, `app/services/session_data_redis.py`, `app/services/jobs/lifecycle.py`, `app/services/artifact_registry.py`): Optimistic CAS (`expected_revision`), frozen snapshot execution with mid-warp cancel fence & pre-publish SHA256 digest guard, scoped idempotency (`{plan_id}:r{expected_revision}:{idempotency_key}`), atomic `_refs/` JSON refcounting alongside immutable `BlobStore` artifacts, and full session cleanup on both disk and Redis stores.
- **API & Pi Tool Surface** (`app/api/routes/georeferencing.py`, `app/schemas/georeferencing_schema.py`, `app/tools/georeferencing_tools.py`, `app/services/tool_catalog.py`, `docs/adr/0293-gcp-georeferencing.md`): REST routes with full `response_model` coverage (4 binary/stream endpoints registered in `tests/unit/api_contract/_contract_util.py` exclusions) and 6 Pi tools (`webgis_georef_inspect`, `webgis_georef_plan`, `webgis_georef_preview`, `webgis_georef_execute`, `webgis_georef_status`, `webgis_georef_report`).
- **Frontend GCP Workbench** (`frontend/components/georeferencing/`, `frontend/lib/georeferencing/`, `frontend/messages/{en-US,zh-CN}/georeferencing.json`, map/sidebar/run-panel integration): Dual-pane canvas/map picking, undo stack, zoom/pan/DPR coordinate conversion, CSV/JSON import with projected/WGS84 auto-detection & safe formula-escaped export, async polling + cancel/retry handling, partial MapSpec bind recovery, and authenticated COG download.

## API / Contract Snapshot Updates
- `tests/quality/snapshots/api_contract_fields.json`: Regenerated with `API_CONTRACT_FIELDS_UPDATE=1` (additive field signatures for G48 endpoints).
- `tests/quality/snapshots/openapi.json`: Regenerated with `API_SNAPSHOT_UPDATE=1` (`tests/quality/test_api_compatibility.py` confirmed `BREAKING 变化数：0`; snapshot on `origin/master` also lacked prior additive routes from recent merges).

## Verification Evidence (Worktree `/home/kevin/projects/webgis/webgis-worktrees/g48-gcp-georeferencing-20261115T125056Z`)
Ran **after final code edits** (`alpha_adapter.py`, `GeoreferencingWorkbenchPanel.tsx`):
- `mypy --ignore-missing-imports` (15 files: `app/schemas/georeferencing_schema.py`, `app/services/georeferencing/`, `app/api/routes/georeferencing.py`, `app/tools/georeferencing_tools.py`, `app/services/raster_authoring/alpha_adapter.py`, `app/services/raster_authoring/render.py`): **Success: no issues found in 15 source files**
- Backend & quality pytest (`tests/unit/georeferencing/`, `tests/integration/test_georeferencing_{api,jobs,gc,render_pub}.py`, `tests/unit/raster_authoring/`, `tests/unit/test_raster_tile_service.py`, `tests/quality/test_api_compatibility.py`, `tests/quality/test_contract_drift_gate.py`, `tests/quality/test_delete_ownership_matrix.py`, `tests/quality/test_f03_lifecycle_ownership_matrix.py`, `tests/unit/api_contract/test_field_contract.py`): **170 passed**
- Real Pi E2E (`PI_JOURNEY_E2E=1`) + Real Playwright Browser E2E (`REQUIRE_BROWSER=1`) (`tests/integration/test_pi_g48_georeferencing_journey.py`, `tests/integration/test_g48_browser_journey.py`): **2 passed in 103.59s**
- Frontend `eslint --max-warnings 0` on G48 files: **0 warnings**
- Frontend `vitest run` (`coords.test.ts`, `workbench.test.tsx`, `toolSlice.test.ts`): **3 files / 12 tests passed**
- Frontend `tsc --noEmit`: **0 errors in G48 files** (fails exit 2 solely on 2 pre-existing baseline errors in `components/chat/cartography-result-card.tsx:24,49`)

Ran prior to the final P2 edits on `alpha_adapter.py` & `GeoreferencingWorkbenchPanel.tsx` (unchanged tool/Pi/baseline scope):
- `scripts/pi_doctor.py --offline`: **PASS** (`497 tools; native 7/7; proxy=webgis_execute`)
- `scripts/check_tool_capability_conformance.py --fail-on warning` across all 6 `webgis_georef_*` tools: **PASS** (`overall=pass` on all 6)
- `tests/unit/test_{raster_tile_service,upload_raster_lane,visual_context_core,pi_extension_control_boundary,pi_native_surface,pi_tool_spec}.py`: **88 passed**
- `frontend`: `npm run build` (`next build --webpack`): webpack bundle compiled cleanly (`Compiled successfully in 26.7s`); TypeScript check failed solely on pre-existing `components/chat/cartography-result-card.tsx:24,49`
- `tests/unit/api_contract/` (`test_response_model_coverage.py::test_all_json_endpoints_have_response_model`): fails on pre-existing non-G48 routes only; 0 G48 routes unguarded (`test_schemathesis.py` skipped when optional dependency absent).

## Independent Review Status
- Audited by Slot 3 independent reviewer (`Approved with minor P2 follow-ups`); all identified P2 UI/resampling/doc items addressed in follow-up edits and re-verified via the post-edit checks above.
