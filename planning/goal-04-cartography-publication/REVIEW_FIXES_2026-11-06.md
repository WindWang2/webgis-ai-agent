# PR #1975 independent review fixes

Reviewed base: `codex/pr-1975-reviewed` at `b5f937a98`. This ledger records the
uncommitted review patch, including the fixes inherited from the first reviewer.
No version, dependency lockfile, generated catalog, ADR number or golden file was
changed by this patch.

## Findings and disposition

| Finding | Severity | Resolution and regression evidence |
|---|---|---|
| Publication wrap projection never reaches actual PDF title/subtitle drawing. | P2 | `runExport` forwards frozen `PublicationText` entries; `exportToPDF` consumes their lines, reflows them with the current jsPDF font and physical page width, and reserves title/subtitle height before fitting the map. Tests inspect actual `doc.text` calls and map placement; a real jsPDF font-metrics test verifies a portrait page with 180 wide Latin glyphs preserves content and stays within 190 mm. |
| Semantic line budgets alone still permit horizontal overflow on narrow Canvas/SVG frames. | P2 | Canvas reflows with `measureText` after setting the drawing font; SVG reflows with a conservative glyph-width bound. Subtitle displacement uses the actual Canvas title line count. Narrow-frame tests verify preserved text and bounded line width. |
| Module-global tile-miss counts survive maps, sessions and source replacement, and the first eight sources permanently fill capacity. | P2 | A map-owned WeakMap stores source-generation accounts; runtime construction, session changes, style invalidation, source removal and disposal retire state. Export snapshots filter absent/replaced sources. The bounded account rotates old entries so new sources can be recorded. Tests exercise every lifecycle boundary and two maps with the same source ID. Snapshot ordering uses code-unit comparison, without locale dependence. |
| Production MapLibre tile errors do not provide the mocked `tile.id` string, so samples remain empty. | P2 | Verified against installed MapLibre 6.10.0 `TileManager._loadTile` and `Tile`: coordinates are read from `tile.tileID.canonical`. Older string IDs remain supported. Regression tests cover ordinary coordinates, `0/0/0`, malformed coordinates and count-only events. |
| The first 1,000 features are incorrectly treated as authoritative negative field evidence. | P2 | Both Python and TypeScript guards return unevaluated evidence for an incomplete scan. Tests put the requested field on feature 1,001 and verify the real compilers render its label without a false missing-field warning; a complete caller-selected scan still discovers it. |
| V5 silently discards an explicit minimum width above the participant's starting width. | P2 | Raise the effective width to the declared minimum before setting the solver's minimum-width bound, while retaining the declared maximum. A regression test starts at one column and requires at least three. |
| The PR's Canvas wrap test imports a private type and subtracts `unknown` values. | P2 | Derive the test type from the public function's parameter and convert recorded coordinates before arithmetic. Both application and test TypeScript configurations pass. |
| Same-source circle/symbol obstacles suppress the label at its own point. | Accepted contract | Existing ADR and explicit tests accept visible circle ink as an obstacle, with suppression disclosed when all candidates are blocked. Only misleading comments claiming automatic own-anchor exclusion were corrected. |

## Verification

Commands run from the isolated worktree, using its source files. Python uses
`/home/kevin/projects/webgis/webgis-ai-agent/.venv/bin/python`; frontend uses
Node 24.21.0 / pnpm 10.34.9. No commit or push was performed.

| Check | Result | Evidence |
|---|---|---|
| Python cartography lane, `pytest -m cartography --no-cov --timeout=180 -q` | 2,684 passed, 8 skipped, 3 expected failures; exit 0. The final source changes after this lane are frontend-only. | `/tmp/pr1975-cartography-final.log` |
| Python boundary suite: field guard, V5 constraints, layout golden/wrap, publication cases, label obstacles and semantic layout findings | 115 passed; exit 0. | `/tmp/pr1975-python-boundaries-final.log` |
| Frontend final related lane: `pnpm exec vitest run lib/export lib/map-kit lib/mapspec-compiler lib/mapspec-runtime --maxWorkers=2` | 111 files / 1,228 passed / 1 expected failure; no unhandled errors, exit 0 (107.38 s). | `/tmp/pr1975-export-final-frozen.log` |
| Frontend new-boundary tests: tile-miss account, PDF text layer and Canvas wrap | 3 files / 26 passed; exit 0. | `/tmp/pr1975-final-focused.log` |
| `pnpm run typecheck` (`tsc --noEmit` and `tsc -p tsconfig.test.json --noEmit`) | Passed; exit 0. | `/tmp/pr1975-types-final.log` |
| ESLint, all 15 changed/new frontend source and test files, `--max-warnings 0` | Passed; exit 0. | `/tmp/pr1975-lint-final.log` |
| Ruff, all 5 changed Python source and test files | All checks passed; exit 0. | Run in final review. |
| Publication golden generator, `--check` | Up to date, 36 entries; read-only, exit 0. | `/tmp/pr1975-golden-readonly.log` |
| `git diff --check` | Passed. | Run in final review. |

The earlier frontend run (`/tmp/pr1975-export-final4.log`) passed 713 assertions
but had two worker startup/exit errors; it is not counted as a clean result.
An intermediate bounded run had no worker errors but started before the final
MapLibre source/test edit finished and failed the new payload test. Only the
completed frozen-source run above is the final related-lane result.

Browser/full-stack E2E, production build, screenshot/pixel goldens and the entire
frontend suite were not run in this isolated review. Structural goldens and
jsdom/real-library metrics cover the listed boundaries; they do not establish
visual approval of a live exported page. MapLibre's error stream does not emit
every failed HTTP request (for example its internal 404 fallback), so the account
describes emitted source errors, not an independent network request census.
