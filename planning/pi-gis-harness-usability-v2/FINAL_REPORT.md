# Final report — Pi × GIS Harness usability V2

Branch: `zcode/pi-gis-harness-usability-v2`
Base: `99f6d1e89` (merge of #1989)
PR: not merged.

## What changed

A city switch can no longer silently draw a ref whose lon/lat box is disjoint from the requested scope when place names are missing. Name mismatches still reject. Missing evidence is `scope_unverified` in the product summary, not a fake mismatch and not a silent success.

An explicit solver page profile such as `a4_landscape` now runs the existing `solve_layout_v5` on the vector publication path and on the cartographer print path. The default path compiles the original document and reports `constraint_layout=not_evaluated`. Browser `export_thematic_map` uses that same disclosure and does not claim a publication-grade layout.

## What was not claimed

- No Playwright browser acceptance.
- No live Pi extension heatmap journey.
- No performance before/after.
- No SSE reconnect fix. That failure was not reproduced.
- No second layout solver.
- Default PDF compile input is unchanged unless `page_profile` is an explicit solver profile.

## Local evidence

`/home/kevin/projects/webgis/webgis-ai-agent/.venv/bin/python -m pytest tests/unit/test_pi_gis_usability_v2.py tests/unit/test_pi_gis_usability_fixes.py tests/unit/agent_swarm/test_cartographer_ledger.py --override-ini='addopts='`

68 passed after the R2 fixes. Earlier related product-host tests (15) also passed.

## Review

R1 and R2 read the code. P0/P1 from both rounds are fixed. Details are in `REVIEW_R1.md` and `REVIEW_R2.md`.
