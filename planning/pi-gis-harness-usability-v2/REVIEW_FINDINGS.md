# Review findings — V2

Confirmed on `99f6d1e89` before the fix. Status is after the local patch, before independent review.

## F1 — Bbox-only city reuse

| | |
|---|---|
| Severity | P1 |
| Reproduction | `scope_ref_mismatch("北京市", features=[{properties:{name:"无城市字段"}}])` is `None`. A descriptor bbox `[103.9, 30.5, 104.3, 30.8]` was accepted for scope 北京市. |
| Root cause | `scope_ref_guard.scope_ref_mismatch` only compares city-level name tokens. `webgis_map_product` treated `None` as success. |
| Journey | 3, switch city without reusing the previous city's data |
| Fix | `scope_geography_verdict`: name match/mismatch unchanged; no tokens + disjoint lon/lat boxes → `scope_bbox_disjoint`; missing evidence or partial overlap → `scope_unverified` warning, not a fake mismatch. Admin bbox is looked up only when place tokens are absent. |
| Test | `tests/unit/test_pi_gis_usability_v2.py` |
| Status | fixed; R1/R2 P0/P1 cleared |

## F2 — Constraint solver not on the export path

| | |
|---|---|
| Severity | P1 |
| Reproduction | `solve_layout_v5(` had no production caller. Cartographer always solved `viewport`. Vector PDF ignored page profile. |
| Root cause | ADR-0283 shipped the solver as a library and left MapSpec wiring for a later change. |
| Journey | 5, A4 landscape publication |
| Fix | Call the existing solver only when `page_profile` is a solver profile (`a4_landscape` and the other `page_profiles()` names). Otherwise disclose `constraint_layout=not_evaluated` and compile the original document. Browser `export_thematic_map` states that the raster chain did not run the solver. |
| Test | `prepare_publication_document` identity on the default path; cartographer screen vs `a4_landscape`; chrome SVG differs only when the evaluated flag is set. |
| Status | fixed; R1/R2 P0/P1 cleared |

## Checked and not reopened

| Item | Why it is not a V2 defect |
|---|---|
| Pi transcript across sessions | #1989 |
| Heatmap tool wrap rejection | #1989; distribution turns already pin `heatmap_data` |
| Cancelled turn writing MapSpec | #1989 checkpoint |
| `task_complete` without observation | #1989 |
| Heatmap recolor without re-query | #1989 |
| Euclidean nearest labeled as network | #1989 |
| SSE reconnect duplication | not reproduced; no patch |
| Playwright / live Pi extension / performance delta | environment not run; disclosed, not faked |
