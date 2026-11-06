# PR1972 independent review and repair ledger

Status: **SOURCE_FROZEN**. Source owner finished; no further source edits planned.

- Worktree: `/home/kevin/projects/webgis/webgis-pr-1972-review`
- Branch: `codex/pr-1972-reviewed`
- Reviewed PR head: `f8b56009f20c890da9c91cf7345d27f85838d843`
- Changes remain uncommitted. No push, merge, application VERSION change, lockfile change, repository-wide generated refresh, or ADR renumbering.
- Parent findings source was read only: `/home/kevin/projects/webgis/webgis-all-issues-review/.planning/2026-11-05-all-issues-deep-review/pr1972-review-findings.json`. No source in that worktree was edited.
- Integration package: `.planning/pr1972-review-repair.patch` includes tracked changes **and both new untracked source files**. `.planning/pr1972-source-frozen.json` records SHA-256 for all 16 changed source/test/doc files.

## Findings and disposition

| ID | Priority | Disposition and verified behavior |
| --- | --- | --- |
| pr1972-1 | P1 | Fixed. Executor rejects `_routes_trimmed`, `routes_found > len(routes)`, and descriptor/missing geometry with `AWF_INCOMPLETE_NETWORK_RESULT` before storing an envelope or running aggregation. The exact 200-route population mean 75.25 versus 50-route preview mean 1.0 is reproduced; preview cannot become a final population statistic. |
| pr1972-2 | P2 | Fixed. Native raster goes directly to zonal statistics by default. Resample requires explicit finite positive `target_resolution`; workflow enforces `resolution_unit=crs` in destination CRS units. Public plan exposes `include_optional_steps`; recompute preserves optional-step exclusion. Resolution mode is forwarded through tool/analyzer/library, enters artifact identity, and survives raster evidence. Existing output resource guard and thresholds remain intact. |
| pr1972-3 | P2 | Fixed. Closest-facility converts each endpoint FeatureCollection to WGS84 using its declared CRS, always XY, before unpacking. Both incidents and facilities are covered through registry invocation. Invalid projections fail honestly, source payload stays unchanged, and coordinates must be finite and within WGS84 bounds. |
| pr1972-4 | P2 | Fixed. Local raster input identity combines canonical bounded V5 content fingerprint with live device/inode/size/mtime_ns/ctime_ns, checked before/after reading. Executor and public recompute use the same identity. Unchanged inputs reuse; same-path overwrite recomputes; a middle-window edit with original mtime restored is detected even when the canonical corner sample itself is unchanged. Unsafe/unverifiable paths disable reuse without opening remote/outside-sandbox inputs. |
| pr1972-5 | P2 | Fixed. Bounded measured network population facts survive as `envelope.network` and public run/status layer summaries; unmatched IDs are sampled to 16 IDs, 128 characters each with truncation flag. Missing matches produce `matched_demand_only`, workflow `partial`, final-gate disclosure, and existing product gate refusal of complete-product eligibility. |
| pr1972-extra-1 | P2 | Fixed during repair audit. A reused executor clears both hydrated role cache and upstream payload cache at each execute call, so role overwrites cannot preserve old data/identity across runs. |
| pr1972-extra-2 | P2 | Fixed during repair audit. Public recompute originally compared descriptor-only identities against new local-raster fingerprints and unnecessarily recomputed unchanged files; it now uses the same live-file helper. |
| pr1972-extra-3 | P2 | Fixed during repair audit. Replanning a public workflow created with `include_optional_steps=False` could reintroduce absent steps and reference missing state entries; recompute retains the declared optional-step selection. |

No unresolved finding remains within the reviewed repair scope. Existing ADR0282 and family risk notes now describe the actual raster opt-in and unmatched-population behavior.

## Coverage

`tests/unit/gis_harness/analysis_workflows/test_pr1972_integrity.py` adds 20 deterministic regression cases: biased 50/200 previews; route-count mismatch and descriptor geometry; unmatched population and product eligibility; native/default raster rewiring; explicit optional exclusion; invalid resolutions (0, negative, infinity, NaN, boolean); projected/geographic destination-unit behavior; projected endpoints and both tool roles; same-path file overwrite with actual raster reads; same-executor vector revision overwrite; public plan/run/recompute identity; public optional exclusion; unsampled middle-window file edits; remote/outside-sandbox no-read checks.

Legacy raster-chain tests now explicitly request resolution where they intend to test the resample chain. Their assertions on numerical finiteness, east/west ordering, and coverage disclosure were retained.

The final targeted run covers the entire analysis_workflows unit directory, raster tool/math/resource tests, network context trimming, scientific honesty, and snapping. The sole default skip was the existing isolated `perf` marker policy; its 100,001-feature budget test was then run explicitly and passed.

## Exact verification commands and logs

All commands ran from this worktree. No network or browser dependency was needed.

1. Inherited interrupted-state log (read only): `/tmp/pr1972-integrity1.log` — 6 failed, 147 passed, 1 skipped. Four failures were new test setup missing mandatory contract fingerprint; one expected ValueError instead of pyproj CRSError; one old chain test did not explicitly request resampling. The prior command itself was not available in the handed-over log.

2. Corrected initial focused suite:

   ```sh
   .venv/bin/python -m pytest -q tests/unit/gis_harness/analysis_workflows/test_pr1972_integrity.py tests/unit/gis_harness/analysis_workflows/test_r1_review_fixes.py > /tmp/pr1972-integrity2.log 2>&1
   ```

   Exit 0: **21 passed**, 4 warnings, 112.75 s. Default coverage reporting was enabled on this run; later targeted runs disable it explicitly.

3. Added public recompute identity regression:

   ```sh
   .venv/bin/python -m pytest --no-cov -q tests/unit/gis_harness/analysis_workflows/test_pr1972_integrity.py > /tmp/pr1972-integrity3.log 2>&1
   ```

   Exit 0: **13 passed**, 6.88 s. Additional safety/population cases were subsequently added and included in final verification below.

4. Final critical Python regression:

   ```sh
   .venv/bin/python -m pytest --no-cov -q tests/unit/gis_harness/analysis_workflows tests/unit/test_raster_tools.py tests/unit/test_raster_math_hardening.py tests/unit/test_raster_resource_guard.py tests/unit/test_network_issue582_trim_cap.py tests/unit/test_network_science_vnext.py tests/unit/test_network_snap_correctness.py > /tmp/pr1972-final-python.log 2>&1
   ```

   Exit 0: **224 passed, 1 skipped**, 18 warnings, 58.97 s. Warnings are existing Rasterio deprecations and an unregistered `unit` test marker.

5. Explicit isolated budget lane:

   ```sh
   .venv/bin/python -m pytest --no-cov -q -m perf tests/unit/gis_harness/analysis_workflows/test_perf_budget.py > /tmp/pr1972-budget-perf.log 2>&1
   ```

   Exit 0: **1 passed**, 11.55 s. Original 100,000 feature cap remains unchanged; 100,001 inputs are blocked.

6. Source hygiene:

   ```sh
   git diff --check
   .venv/bin/python -m ruff check app/services/gis_harness/analysis_workflows/runtime_integrity.py app/services/gis_harness/analysis_workflows/executor.py app/services/gis_harness/analysis_workflows/contracts.py app/services/gis_harness/analysis_workflows/families.py app/services/gis_harness/analysis_workflows/state.py app/services/gis_harness/analysis_workflows/envelope_facts.py app/services/gis_harness/analysis_workflows/evidence.py app/lib/geo_analysis/raster_math.py app/services/spatial_analyzer.py app/tools/analysis_workflow_tools.py app/tools/network_tools.py app/tools/raster_transform_tools.py tests/unit/gis_harness/analysis_workflows/test_pr1972_integrity.py tests/unit/gis_harness/analysis_workflows/test_r1_review_fixes.py tests/unit/gis_harness/analysis_workflows/test_chain2_raster.py > /tmp/pr1972-final-lint.log 2>&1
   ```

   Both exit 0. Ruff: **All checks passed!**

## Verified limits and remaining integration boundaries

- Large trimmed network results are blocked; this repair does not create a new full-route storage/transport protocol. Callers can narrow input or provide complete results.
- Local-file identity is bounded and robust against normal overwrites, including edits outside sampled corners. It is not a whole-file cryptographic digest or protection against a privileged actor restoring all filesystem identity fields. Session refs with content revision continue to rely on the store's revision contract; unverifiable inputs deliberately do not reuse.
- The public recompute regression uses the actual registry/state/executor boundary and real raster bytes, with a deterministic dispatch-service substitute; existing raster/network chain tests in the final suite exercise real raster computation and real Dijkstra. No claim is made about live Redis, third-party provider traffic, a deployed environment, or frontend browser behavior.
- Parent integration and repository-wide regression remain owned by the root agent. No CI waiting, commit, push, or merge was performed here.

**SOURCE_FROZEN** — all 16 source/test/doc files are ready for integration; the ledger and patch are metadata artifacts only.
