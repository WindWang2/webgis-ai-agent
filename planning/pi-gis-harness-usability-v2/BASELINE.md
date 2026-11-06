# Baseline — Pi × GIS Harness usability V2

Date: 2026-11-06
Branch: `zcode/pi-gis-harness-usability-v2`
Base: `origin/master` `99f6d1e89735dbf08d0b960372111258e447d8a9`
Subject: Merge pull request #1989
Vendor pin: `vendor/pi` `f9a26365568127461c47a07a37dcb42c140a3ac5` (v1.5.0)
Worktree: `/home/kevin/projects/webgis/webgis-wt-pi-gis-harness-v2`
Master was not modified.

## Already merged — do not reimplement

| PR | What it already shipped |
|---|---|
| #1972 | AnalysisWorkflow v2 |
| #1973 | Pi v1.5.0 host, extension load, tool ABI |
| #1974 | Situated selection / interaction IR |
| #1975 | Publication layout modules, label governance |
| #1976 | Cross-module integration |
| #1987 | Deterministic basemap styles for browser journeys |
| #1989 | Pi session isolation, heatmap dispatch, completion gate, restyle, name-token scope guard, euclidean honesty, cancel clears map actions |
| #1990 | Redis / PDF / workflow test fixes |

Open PRs at baseline are dependabot only. Open issues: none.

## #1989 leftovers rechecked on this HEAD

| Leftover | Verdict on `99f6d1e89` |
|---|---|
| Constraint solver not on the MapSpec production path | Confirmed. `solve_layout_v5` was test-only. Cartographer called `solve_layout_v4` with `page_profile="viewport"`. `publication_export.py` did not call a solver. |
| Bbox scope verification incomplete | Confirmed. `scope_ref_mismatch` returns `None` when place tokens are absent, including a Chengdu bbox reused for Beijing. |
| No real Playwright acceptance | Still true. Not invented as a code defect. |
| No full Pi-extension heatmap journey | Still true. Not invented as a new dispatch bug. #1989 already pins heatmap tools. |
| No performance before/after | Still true. Not used as a reason to change hot paths. |
| SSE reconnect bug | Not reproduced. Not patched. |

## Invariants kept

- No second layout solver. `solve_layout_v5` with no explicit profile is not run on the default PDF path.
- Default publication compile receives the caller's document object when `page_profile` is absent.
- Name-token mismatch behavior from #1989 is unchanged.
- Missing geography is `scope_unverified`, not an invented mismatch.
- Browser raster export is not called a publication success.
