# G48-GCP-GEOREFERENCING — Requirement & Scenario Verification Matrix (R01–R40)

| ID | Scenario / Gate | Target Module / Test | Status |
|---|---|---|---|
| R01 | Normalized pixel upload + reload consistency (`BlobStore` + dual sha256) | `tests/unit/georeferencing/test_intake.py` | Verified |
| R02 | Valid PNG / JPEG / single-page un-georeferenced TIFF intake (uint8 & uint16 gray) | `tests/unit/georeferencing/test_intake.py` | Verified |
| R03 | Existing embedded TIFF CRS/transform warning without silent overwrite | `tests/unit/georeferencing/test_intake.py` | Verified |
| R04 | Existing TIFF embedded GCP tags warning without silent overwrite | `tests/unit/georeferencing/test_intake.py` | Verified |
| R05 | Reject SVG/PDF/animated/multi-page TIFF/VRT/misnamed payloads | `tests/unit/georeferencing/test_intake.py` | Verified |
| R06 | Reject decompression bombs / over-pixel / over-side / over-bytes uploads | `tests/unit/georeferencing/test_intake.py` | Verified |
| R07 | EXIF Orientation canonicalization (JPEG 3/6/8 & uint16 TIFF 2/4/5/7) + viewer & warp alignment | `tests/unit/georeferencing/test_intake.py` | Verified |
| R08 | Corrupt EXIF fail-closed unless explicit user override recorded | `tests/unit/georeferencing/test_intake.py` | Verified |
| R09 | Frontend canvas <-> normalized pixel coordinate transform across DPR/zoom/pan | `frontend/lib/georeferencing/__tests__/coords.test.ts` | Verified |
| R10 | Analytic affine oracle (translate/scale/rotate/shear/negative line axis + 0.5px center) | `tests/unit/georeferencing/test_solver_oracle.py` | Verified |
| R11 | `<3` fit points rejected; collinear / degenerate points fail rank/condition check | `tests/unit/georeferencing/test_solver_oracle.py` | Verified |
| R12 | Pure reflection negative determinant vs singular degenerate matrix distinction | `tests/unit/georeferencing/test_solver_oracle.py` | Verified |
| R13 | Exactly 3 non-collinear points disclose zero fit residuals + "not independently verified" | `tests/unit/georeferencing/test_solver_oracle.py` | Verified |
| R14 | Holdout checkpoint residuals separate from fit points & true RMSE/max error | `tests/unit/georeferencing/test_solver_oracle.py` | Verified |
| R15 | Clustered GCP convex-hull low coverage & extrapolation warning + large outlier flag | `tests/unit/georeferencing/test_solver_oracle.py` | Verified |
| R16 | Out-of-bounds `[0,W]x[0,H]` or non-finite pixel coords rejected before fitting | `tests/unit/georeferencing/test_solver_oracle.py` | Verified |
| R17 | Geographic degree / US-ft / compound-vertical CRS rejected; projected metric accepted | `tests/unit/georeferencing/test_crs.py` | Verified |
| R18 | WGS84 map click -> projected CRS conversion with `always_xy=True` & provenance | `tests/unit/georeferencing/test_crs.py` | Verified |
| R19 | Nearest-neighbor categorical mask preserves exact discrete labels (`LA` 2-band & RGBA covered) | `tests/unit/georeferencing/test_warp_oracle.py` | Verified |
| R20 | Straight-alpha premultiplied bilinear: 0 black halo + pure `(0,0,0,255)` & `uint16` internal mask kept | `tests/unit/georeferencing/test_warp_oracle.py` | Verified |
| R21 | Semi-transparent PNG alpha `[1..254]` preserved on COG, live XYZ tiles, and SVG print export | `tests/integration/test_georeferencing_render_pub.py` | Verified |
| R22 | Target resolution / pixel explosion preflight guard BEFORE allocation + API finite `> 0` check | `tests/unit/georeferencing/test_warp_oracle.py`, `tests/integration/test_georeferencing_api.py` | Verified |
| R23 | Plan CAS conflict (`expected_revision` mismatch returns 409) | `tests/integration/test_georeferencing_api.py` | Verified |
| R24 | Edits after preview mark preview stale & prevent stale preview export | `tests/integration/test_georeferencing_api.py` | Verified |
| R25 | Draft edits do not mutate running/queued frozen execution snapshot + scoped idempotency retry | `tests/integration/test_georeferencing_jobs.py` | Verified |
| R26 | Cancel mid-job triggers cancel fence; late completion never publishes usable artifacts | `tests/integration/test_georeferencing_jobs.py` | Verified |
| R27 | Restart recovery transitions interrupted running jobs & clears orphan staging files | `tests/integration/test_georeferencing_jobs.py` | Verified |
| R28 | GC protection while active (including superseded bound artifacts); explicit delete/reset releases refs cleanly | `tests/integration/test_georeferencing_gc.py` | Verified |
| R29 | Source swap mid-job caught by SHA256 digest re-check (no mixed output) | `tests/integration/test_georeferencing_jobs.py` | Verified |
| R30 | Multi-tenant ownership isolation (`404` for unauthorized session/source/plan/download/cancel/bind/delete) | `tests/integration/test_georeferencing_api.py` | Verified |
| R31 | Data generation succeeds even if MapSpec bind fails CAS conflict (partial disclosure + retry) | `tests/integration/test_georeferencing_api.py`, `frontend/components/georeferencing/__tests__/workbench.test.tsx` | Verified |
| R32 | Rasterio independent readback checks CRS, transform, bounds, nodata/alpha, center value | `tests/integration/test_georeferencing_render_pub.py` | Verified |
| R33 | `cog_verified=True` only after `to_cog` + `validate_cog` + `range_read_probe` | `tests/integration/test_georeferencing_render_pub.py` | Verified |
| R34 | Live MapLibre XYZ tile & print PNG/SVG/PDF publication render warped RGB/RGBA without stretch | `tests/integration/test_georeferencing_render_pub.py` | Verified |
| R35 | Real Pi journey (local LLM stub + production extension + dispatch) completes full workflow | `tests/integration/test_pi_g48_georeferencing_journey.py` | Verified |
| R36 | Real Pi journey blocked cleanly on insufficient GCPs without fabricated coords | `tests/integration/test_pi_g48_georeferencing_journey.py` | Verified |
| R37 | Playwright browser E2E: upload -> pick/edit GCPs -> preview -> execute -> bind -> reload | `frontend/e2e/journeys/g48-georeferencing.journey.ts`, `tests/integration/test_g48_browser_journey.py` | Verified |
| R38 | Tool capability conformance + bounded Pi receipts + frozen `NATIVE_TOOL_NAMES` | `scripts/check_tool_capability_conformance.py`, `scripts/pi_doctor.py` | Verified |
| R39 | >6 noisy GCPs use exact frozen 6-param affine (NOT GDAL auto-order refit) verified vs oracle | `tests/unit/georeferencing/test_warp_oracle.py` | Verified |
| R40 | GeoTIFF download strictly checks job complete (`409` while active) + digest + ownership | `tests/integration/test_georeferencing_api.py` | Verified |
