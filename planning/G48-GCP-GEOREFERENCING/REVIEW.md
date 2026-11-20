# G48-GCP-GEOREFERENCING — Independent Review & Fix Record

- **Independent Reviewer**: Slot 3 (`research` subagent `65e726a5-a1a8-4d27-ba44-7224876df16c`, did not author G48 implementation)
- **Initial Review Verdict**: `Changes Requested` (identified P0/P1/P2 items across API snapshots, raster alpha/EXIF/LA edge cases, CAS/idempotency/refcounts/cancellation cleanup, Redis session purge, CSV escaping, frontend job-status vocabulary, and tests)
- **Post-Fix Re-Review Verdict**: `Approved with minor P2 follow-ups` (`0` new P0/P1 regressions)

## Fixes Completed After Initial Review

### P0 Fixes
1. **API & Schema Contracts**:
   - Kept the 4 G48 binary/stream route exemptions in `tests/unit/api_contract/_contract_util.py`.
   - Updated `tests/quality/snapshots/api_contract_fields.json` (`API_CONTRACT_FIELDS_UPDATE=1`) and `tests/quality/snapshots/openapi.json` (`API_SNAPSHOT_UPDATE=1`, `BREAKING 变化数：0`).
2. **`LA` 2-band slice bug (`app/services/georeferencing/warp.py`)**:
   - Fixed `LA` color band extraction (`arr[:, :, 0][np.newaxis, :, :].astype(np.uint8)`) and covered non-square `40x24` `LA` warps in `tests/unit/georeferencing/test_warp_oracle.py`.
3. **Job status vocabulary (`frontend/components/georeferencing/GeoreferencingWorkbenchPanel.tsx`, tests)**:
   - Corrected `'succeeded'` -> `'completed'` in UI checks, Vitest mocks, and Playwright journey assertions.
4. **Imported GCP coordinate handling & validation**:
   - Added `detectImportedCoordsAreWgs84(format, content)` in `GeoreferencingWorkbenchPanel.tsx` so projected CSV/JSON imports do not falsely set `coords_are_wgs84=true`.
   - Updated `app/services/georeferencing/service.py` to strip UTF-8 BOM `\ufeff`, enforce finite `> 0` `target_resolution_m`, reject malformed CSV/JSON rows or null WGS84 coords with HTTP `422 INVALID_GCP_COORDS`, and escape CSV formula-injection string prefixes (`=`, `+`, `-`, `@`, `\t`, `\r`) via `_csv_cell_safe` without mutating numeric coordinates.

### P1 Fixes
1. **`target_resolution_m` validation across schemas/tools/service**:
   - Added `validate_positive_finite_resolution` validator in `app/schemas/georeferencing_schema.py` (`CreatePlanIn`, `DraftPlanUpdateIn`) and `app/tools/georeferencing_tools.py` (`GeorefPlanArgs`).
2. **Raster alpha & EXIF canonicalization**:
   - `app/services/raster_authoring/alpha_adapter.py`: Added `normalize_alpha_band` and `alpha_to_uint8` supporting `uint8`/`uint16`/bool/int/float alpha; aligned `reproject_straight_alpha` weight resampling (`dst_w`) with `color_resample`.
   - `app/services/raster_authoring/render.py`: Added straight-alpha premultiplied downscale path (`scale < 1.0`, non-nearest, `_has_alpha_sem`, `width*height <= 16_777_216`) in `render_snapshot` and narrowed `stretch = style.stretch or ()` for mypy cleanliness. Verified interior `[1..254]` alpha on both live XYZ tiles and mixed SVG embedded PNG export in `tests/integration/test_georeferencing_render_pub.py`.
   - `app/services/georeferencing/intake.py`: Added `_apply_exif_orientation_2d` (orientations `1..8`) for `uint16` TIFFs, accounted for Pillow `TiffImagePlugin` swapping `img.size` on orientations `5..8`, and reloaded raw GDAL bands when `ignore_exif=True` on `uint8` TIFFs.
3. **CAS, scoped idempotency, mutable refcounts, artifact cleanup, and Redis session purge**:
   - `app/services/georeferencing/refs.py`: Moved mutable session refcounts out of content-addressed `BlobStore` into atomic JSON files under `DATA_DIR/georeferencing/_refs/...` (with legacy `georefref-*` migration/cleanup), added streaming `compute_file_sha256`, and protected `superseded_artifacts` in `active_georef_artifact_ids`.
   - `app/services/georeferencing/jobs.py`, `store.py`, `service.py`: Scoped idempotency keys as `{plan_id}:r{expected_revision}:{idempotency_key}` while consulting live `DurableJobStore` status so `failed`/`cancelled` allow retries; added `cleanup_artifact_resources` on post-cancel registration fence, unbound re-execution overwrite, MapSpec layer rebind replacement, and source deletion; added `purge_session_georef_state(session_id)` to `RedisSessionStore.clear_session` in `app/services/session_data_redis.py`.
4. **Frontend session isolation & workspace cleanliness**:
   - Added `resetSessionUiState` on session switch, rotated `:retryN` idempotency suffix after `failed`/`cancelled` in `GeoreferencingWorkbenchPanel.tsx`, added `window` `mouseup`/`blur` pan listeners, removed unused `"noPlan"` keys from `en-US`/`zh-CN` `georeferencing.json`, reverted unrequested `frontend/AGENTS.md` edit, and removed stray `data/sess-*`.

### P2 Follow-Up Fixes Applied After Slot 3 Re-Review
- `app/services/raster_authoring/alpha_adapter.py`: Mapped `"cubic"` resampling in the non-`uint8` mask branch of `render_alpha_aware_window` instead of degrading to bilinear, and aligned `valid_u8` with `dst_alpha_u8 > 0` to match the quantized output alpha channel.
- `frontend/components/georeferencing/GeoreferencingWorkbenchPanel.tsx`:
  - `handleCancel`: keeps polling active when backend returns non-terminal `'cancelling'` and clears `executing` only on terminal status (`'completed' | 'failed' | 'cancelled'`).
  - `georef-source-select`: stops prior polling and resumes polling only if the newly selected source plan has `active_job_id`.
  - `resetSessionUiState`: resets form defaults (`targetCrs`, `resampling`, `targetResM`, `buildCog`, `ignoreExif`) on session changes.
- Reverted residual `frontend/AGENTS.md` working-tree diff (`git checkout -- frontend/AGENTS.md`) and updated `planning/G48-GCP-GEOREFERENCING/TEST_MATRIX.md`.
- Note on `bind_mapspec_layer`: `layer_name` is validated and returned in the response `message` (`service.py`), while `RasterLayerSpec` fields in MapSpec remain unchanged so frozen MapSpec contracts stay intact.

## Final Post-Edit Verification Results
- `mypy --ignore-missing-imports` on 15 G48 + raster_authoring files: `Success: no issues found in 15 source files`
- Backend pytest (G48 unit/integration + `raster_authoring/` + `test_raster_tile_service.py` + `tests/quality/{test_api_compatibility,test_contract_drift_gate,test_delete_ownership_matrix,test_f03_lifecycle_ownership_matrix}.py` + `tests/unit/api_contract/test_field_contract.py`): **170 passed**
- Real Pi E2E (`PI_JOURNEY_E2E=1`) + Real Playwright Browser E2E (`REQUIRE_BROWSER=1`): **2 passed** (`test_pi_g48_georeferencing_journey.py`, `test_g48_browser_journey.py`)
- Frontend `eslint --max-warnings 0` on G48 files: **0 warnings**; `vitest run` (`coords.test.ts`, `workbench.test.tsx`, `toolSlice.test.ts`): **3 files / 12 tests passed**; `tsc --noEmit`: **0 G48 errors** (only 2 pre-existing baseline errors in `components/chat/cartography-result-card.tsx:24,49`)
- Tool/Pi checks (run after tool/extension files were final): `scripts/pi_doctor.py --offline` **PASS** (`497 tools; native 7/7`) and `scripts/check_tool_capability_conformance.py --fail-on warning` **PASS** on all 6 `webgis_georef_*` tools; downstream `tests/unit/test_{raster_tile_service,upload_raster_lane,visual_context_core,pi_extension_control_boundary,pi_native_surface,pi_tool_spec}.py` **88 passed**; `tests/unit/api_contract/` pre-compaction run failed strictly on `test_response_model_coverage.py::test_all_json_endpoints_have_response_model` for non-G48 routes (0 G48 routes missing `response_model`).
