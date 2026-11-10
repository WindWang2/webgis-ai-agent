# TEST_MATRIX — G21

Every result must name exact command, revision, environment and evidence. `pending` is not a pass.

| Layer | Scenario / oracle | Status | Evidence |
|---|---|---|---|
| Legacy backend | Existing `tests/unit/test_od_flow_runtime.py` straight lines, weight paint, refs | pending | — |
| Legacy frontend | `frontend/lib/mapspec-compiler/flow-layer.test.ts` | pending | — |
| Custom binding | `trips` drives standard weight, widths and legend | pending | — |
| Directed arithmetic | A→B=8/B→A=3: directed 8/3, gross 11, net +5 A→B | pending | — |
| Equal reverse | Equal values yield zero net without arbitrary arrow; original pair queryable | pending | — |
| Coverage | 8/3 fully retained then net: gross retained 100%, net magnitude 5 | pending | — |
| Missing reverse | Unobserved is not zero unless explicit source contract | pending | — |
| Node totals | Origin with multiple destinations becomes node totals, never fake first-target total edge | pending | — |
| Duplicates/dimensions | Declared time/category aggregation and exclusions | pending | — |
| Binding | Nondefault fields/coords; missing coords never geocode | pending | — |
| Cost semantics | Time/distance matrices rejected/disclosed as non-flow | pending | — |
| Identity | Same lon/different lat, arrow-containing IDs, row permutations | pending | — |
| Coordinate conflicts | Same node ID at conflicting coordinates rejects with correction path | pending | — |
| Invalid weights | NaN/Inf/negative/missing excluded or rejected; valid JSON/no negative widths | pending | — |
| Display scope | top-N 20→200 retains total/domain evidence and deterministic ties | pending | — |
| Geometry | Directed curves, focus, antimeridian, self-loop, zero-length | pending | — |
| Authority | UI/Pi interleaving, CAS/user-wins, stale result cannot overwrite | pending | — |
| Persistence | Save/refresh/requery restores same config/ref/statistics | pending | — |
| Cancellation | Late detail after cancel/session switch has no cross-session effect | pending | — |
| Performance | 50k edges input, output/vertices/markers/query caps, no O(nodes²) | pending | — |
| Export | Real PNG/SVG/PDF, 96/300 DPI and rotation; directions/widths/nodes/legend | pending | — |
| Partial failure | Missing layer/direction markers makes verification incomplete | pending | — |
| UI journey | Create→node focus→directed→filter→save→refresh in real page | pending | — |
| Pi journey | Natural-language create/local modify via real Pi + local stub, no od_matrix/provider | pending | — |
| Review | Independent review→fix→rereview closes blocking findings | pending | — |

## Commands and results
Pending baseline commands.
