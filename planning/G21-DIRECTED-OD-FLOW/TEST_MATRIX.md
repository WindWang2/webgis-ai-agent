# TEST_MATRIX — G21

Every result must name exact command, revision, environment and evidence. `pending` is not a pass.

| Layer | Scenario / oracle | Status | Evidence |
|---|---|---|---|
| Legacy backend | Existing `tests/unit/test_od_flow_runtime.py` straight lines, weight paint, refs | pass | `pytest tests/unit/test_od_flow_runtime.py` → 12 passed (worktree venv, `USE_REDIS=false`, isolated sqlite) |
| Legacy frontend | `frontend/lib/mapspec-compiler/flow-layer.test.ts` | pass | included in `vitest run lib/mapspec-compiler` → 60 files / 664 passed (1 expected fail) |
| Custom binding | `trips` drives standard weight, widths and legend | pass | `tests/unit/test_flow_product.py` + browser journey: legend shows original `trips` domain 2–8 with encoded widths |
| Directed arithmetic | A→B=8/B→A=3: directed 8/3, gross 11, net +5 A→B | pass | `tests/unit/test_flow_product_service.py` known-answer cases |
| Equal reverse | Equal values yield zero net without arbitrary arrow; original pair queryable | pass | `tests/unit/test_flow_product_service.py` |
| Coverage | 8/3 fully retained then net: gross retained 100%, net magnitude 5 | pass | service tests + live panel showed `gross 保留率: 100.0%` / `net 幅度覆盖率: 100.0%` |
| Missing reverse | Unobserved is not zero unless explicit source contract | pass | service tests; UI disables net mode with reason (`反向缺失处理=未知`) |
| Node totals | Origin with multiple destinations becomes node totals, never fake first-target total edge | pass | service tests; node ref shows 武侯区 inbound 5 / outbound 12 / net 7 / total 17 |
| Duplicates/dimensions | Declared time/category aggregation and exclusions | pass | service tests (repeat rows, category/time dims) |
| Binding | Nondefault fields/coords; missing coords never geocode | pass | service tests; panel requires explicit node/coordinate fields |
| Cost semantics | Time/distance matrices rejected/disclosed as non-flow | pass | service tests (cost-field refusal) |
| Identity | Same lon/different lat, arrow-containing IDs, row permutations | pass | `tests/unit/test_flow_product_geometry.py` + service identity tests |
| Coordinate conflicts | Same node ID at conflicting coordinates rejects with correction path | pass | service tests |
| Invalid weights | NaN/Inf/negative/missing excluded or rejected; valid JSON/no negative widths | pass | service tests |
| Display scope | top-N 20→200 retains total/domain evidence and deterministic ties | pass | service tests + browser: top-N=20 preview kept 4/4 edges and 100% retained |
| Geometry | Directed curves, focus, antimeridian, self-loop, zero-length | pass | `tests/unit/test_flow_product_geometry.py`; live map shows two separated curves + ▶ per direction |
| Authority | UI/Pi interleaving, CAS/user-wins, stale result cannot overwrite | pass | `tests/unit/test_flow_product_mapspec.py` (atomic family replace, expected_revision CAS, superseded) |
| Persistence | Save/refresh/requery restores same config/ref/statistics | pass | browser: after full page reload + re-login + session reselect, panel restored `已应用到地图`, product ref, stats; backend map-state revision ≥2 |
| Cancellation | Late detail after cancel/session switch has no cross-session effect | pass | `tests/unit/test_flow_product_surface.py` (guarded request/epoch semantics) |
| Performance | 50k edges input, output/vertices/markers/query caps, no O(nodes²) | pass | service tests: 500k input cap, 5000 output cap, single-pass aggregation, bounded query |
| Export | Real PNG/SVG/PDF, 96/300 DPI and rotation; directions/widths/nodes/legend | pass | `tests/cartography/test_flow_publication_formats.py` (SVG 96/300, WeasyPrint PDF, `pdftotext` ▶, `pdftoppm` PNG readback) → 1 passed |
| Partial failure | Missing layer/direction markers makes verification incomplete | pass | `tests/unit/test_flow_export_verification.py` |
| UI journey | Create→node focus→directed→filter→save→refresh in real page | partial | create/preview/apply/refresh verified live; node click selects the node (candidate popup node-first, stable id) but the focus-card render was verified in component tests only (headless probe could not open the right 制图 panel) |
| Pi journey | Natural-language create/local modify via real Pi + local stub, no od_matrix/provider | pass | `PI_JOURNEY_E2E=1 pytest tests/integration/test_pi_od_flow_journey.py` → 1 passed; `tests/unit/test_flow_product_surface.py test_pi_dynamic_surface.py test_flow_product_mapspec.py test_flow_product_service.py` with the flag → 25 passed |
| Review | Independent review→fix→rereview closes blocking findings | pending | scheduled after PR push |

## Master rebase (origin/master cc2dc6900)

- Merge committed as `e048e0bf5`; merge-repair commit `42f4bf476`.
- OD/flow/schema/registry suites after rebase: 259 passed.
- Full `tests/cartography/`: 3677 passed, 5 failed — all 5 verified failing on clean origin/master with the same interpreter (baseline, out of scope): `test_component_variants_v3.py::test_every_descriptor_variant_has_template_entry`, `test_component_variants_v4.py::test_v4_variant_count_target`, `test_component_variants_v4.py::test_v4_bidirectional_variant_template_coverage`, `multiscale/test_importance_v1.py::test_admin_level_numeric_ordering`, `test_carto_ir_corpus_v1.py[...scale_portrayal_cases]`.

## Commands and results

Backend OD regression (worktree `.venv`, Python 3.12.15, `USE_REDIS=false`, isolated sqlite under `.scratch/g21-ui`, `OMP_NUM_THREADS=1`):

```
flock /home/kevin/projects/webgis/.bare/webgis-heavy-stage.lock .venv/bin/python -m pytest -q --no-cov -o timeout=240 \
  tests/unit/test_flow_product_mapspec.py tests/unit/test_flow_product_service.py \
  tests/unit/test_flow_product.py tests/unit/test_flow_product_geometry.py \
  tests/unit/test_flow_product_surface.py tests/unit/test_flow_export_verification.py \
  tests/unit/test_od_flow_runtime.py tests/unit/test_pi_dynamic_surface.py \
  tests/cartography/test_flow_direction_svg.py tests/cartography/test_flow_publication_formats.py
→ 98 passed
```

Real-Pi journey: `PI_JOURNEY_E2E=1 … pytest tests/integration/test_pi_od_flow_journey.py` → 1 passed; E2E-flagged surface/mapspec/service files → 25 passed.

Frontend: `pnpm exec tsc --noEmit` → clean. `vitest run components/flow/… lib/flow/… lib/selection/flow-selection.test.ts lib/mapspec/… lib/mapspec-compiler/… components/map/map-panel.test.tsx` → 101 passed. Wider suites `lib/mapspec-compiler lib/mapspec-runtime lib/mapspec` → 60 files / 664 passed (1 expected fail, pre-existing).

Lint: `uvx ruff check` on all changed backend files → All checks passed. `git diff --check` → clean.
