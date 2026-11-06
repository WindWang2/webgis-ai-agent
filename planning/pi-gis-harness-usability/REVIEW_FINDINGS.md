# Review findings — Pi × GIS Harness usability

Base: `a678d0005`. Confirmed by reading the cited lines, not by the review notes alone.
Open issues: none. None of these duplicate an open issue.

Severity uses the task scale: P0 data/permission/fake success/cross-session/wrong map; P1 cannot finish or cannot sync; P2 efficiency and feedback.

## Confirmed and fixed in this branch

| ID | Sev | Where | User impact | Fix | Test |
|---|---|---|---|---|---|
| PH-01 | P0 | `pi_bridge_tasks.py` pool default 1; `agent_pi_bridge.py` prompt path; vendor `rpc-mode.ts` prompt ignores `sessionId` | Next GIS session on the same worker sees the previous session's questions and tool text | `_isolate_vendor_session` sends `new_session` when the GIS session id changes. Same-session follow-ups keep the transcript | `test_pi_worker_resets_session_when_gis_session_changes` |
| PH-02 | P1 | `index.mjs` prompt said heatmap via proxy; wrap-reject used every dumped name | Model that follows the tool text never reaches `heatmap_data` | Wrap-reject only `FALLBACK_NATIVE`. Prompt says call heatmap directly if active, else `webgis_execute`. Stale-surface text matches | `test_extension_wrap_rejects_only_frozen_natives` |
| PH-03 | P0 | `mapspec_store.py` `_apply` did not read the cancellation token | Stop after the HTTP gate still committed a layer | `checkpoint()` before `apply_gis_mutation`. Dispatch already treats `OperationCancelled` as cancel, not success | `test_cancelled_turn_does_not_apply_mapspec` |
| PH-04 | P1 | `HeatmapDataArgs.cell_size/bandwidth_m/radius` and `search_poi_around.radius_m` were `int`. Pi `Math.trunc` runs before the server gate. Follow-up: `_coerce_int` then dropped a non-integer float and substituted 1000 m | `250.5` m became `250`, or after the float schema change became the 1000 m default | Meter fields are `float`. `normalize_heatmap_radius` keeps finite meter floats. Pixel radius stays `int` | `test_fractional_bandwidth_is_not_replaced_with_default` |
| GH-01 | P0 | `completion/pipeline.py` `_is_task_complete`; `goal_satisfaction/evaluator.py` `_eval_map_requirement` | Turn could say the map was done when nothing had been observed | `render_status` must be `verified` or `not_applicable` (legacy `final_map_status=verified` still passes). Unknown render is `not_evaluated`, not fulfilled | `test_unknown_render_is_not_task_complete`, goal evaluator test, updated completion fold |
| GH-02 | P1 | component update and paint whitelist cannot set heatmap color | "改成蓝色" either no-ops or the model re-queries POI | `palette` / `options.heatmap_palette` recolors existing heatmap layers via `heatmap_restyle`. No feature fetch. `blues` added and passes the screen ΔE gate | `test_heatmap_blue_restyle_does_not_touch_source` |
| GH-03 | P1 | `tool_map_product.py` trusted `primary_ref` | Beijing title could be drawn from Chengdu points | `scope_ref_guard` rejects a city-level place token that does not match `intent.scope.name`. Missing place evidence is not invented as a mismatch | `test_scope_ref_rejects_chengdu_points_for_beijing` |
| GH-05 | P0 | `algorithms/network.py` listed `nearest_facility` under Dijkstra `closest_facility`; result had no method | Straight-line metres could be recorded as network closest-facility | Removed from `tool_candidates`. Result stamps `distance_method=euclidean` | `test_nearest_facility_is_not_a_dijkstra_candidate` |
| GH-04 | P1 | `nearest_facility` ignored selection | "这些学校" analyzed the whole layer | Optional `feature_ids`. If the session has a validated selection, filter to it. Unusable selection is an error, not a full-layer analysis | helper covered by dispatch path; unit filter is in `advanced_spatial.py` |
| GH-06 | P2 | `local_stats.py` online fallback returned a successful FeatureCollection | Missing local POI looked like a finished city layer | Returns `success: false`, `code: partial_online_fallback`, and an error string. Features remain for inspection | disclosure is on the return dict |
| GH-07 | P2 | `settle.py` reattached the stored complete block on a no-op settle | A non-map turn SSE nested `map_product.status=complete` | Reused block drops `status` / `task_complete` / `goal_satisfaction` and sets `cartography_this_turn=not_evaluated`. Mapper skips reused status | mapper guard |
| UX-01 | P1 | `useMapBridge.cancel` only aborted the SSE reader | Cancel or the next question still fetched features and flew the camera | `bumpTurnEpoch` + `clearActions` on cancel and send. Layer fetches and in-flight observations abort | `frontend/lib/map/turn-epoch.ts` |
| UX-03 | P1 | brush selection was not in `map_state` | "这些学校" missed the box | `handleSend` flushes the interaction bus and sends `selection` plus `selected_ids`. Chat route stores them on the pre-turn observation | observation merge in `chat.py` |

## Not confirmed — not filed as bugs

- SSE reconnect duplicating layers. Id dedup plus `addLayer` id dedup already cover the chat stream.
- Silent Pi success on streaming fallback. Dead worker falls through to ChatEngine and badges `chatengine`.
- `solve_layout_v5` unwired. ADR-0283 deferred that MapSpec wiring. Not reopened.
- Late Pi events after drain. Plausible, not reproduced as a failing sequence. Tool writes from a late callback are still 409'd.

## Known limits after this pass

- Scope mismatch only fires when the ref carries a city-level place token (`cityname` / `city` / `市` / `省`). A ref with only a bbox is not rejected. That avoids inventing a false mismatch.
- Heatmap blue is a named ramp (`blues`), not an arbitrary CSS color. Unknown names fail closed.
- Selection filtering on `nearest_facility` depends on the pre-turn observation or explicit `feature_ids`. A selection that never reaches the server is still unusable, and the tool says so instead of analyzing the whole layer only when a claimed selection exists and fails validation. No selection still analyzes the given source.
- Publication constraint solver remains off the MapSpec production path by ADR. Export still uses the existing publication modules.
- No live LLM journey was run. No model credential was used, and none was invented.
- Browser E2E was not executed in this pass. Frontend changes are source-level plus the turn-epoch module. They are not claimed as a Playwright run.

## Local verification

Python: `/home/kevin/projects/webgis/webgis-ai-agent/.venv/bin/python` (this worktree has no venv).

```text
pytest tests/unit/test_pi_gis_usability_fixes.py \
  tests/unit/gis_harness/test_observation_completion_v4.py \
  tests/unit/test_pi_native_surface.py \
  tests/unit/test_new_geo_tools.py \
  tests/unit/gis_harness/test_goal_satisfaction_v1.py \
  tests/unit/gis_harness/test_goal_satisfaction_wiring.py \
  tests/unit/gis_harness/test_map_verification.py \
  tests/unit/test_symbology_consistency_matrix.py::test_heatmap_context_validated_on_native_stops
```

Result: the focused sets above passed (12 + 46 + 61, with overlap). `ruff check` on the edited Python files passed after removing unused completion imports.

Not run: full pytest, Playwright, Pi subprocess vendor contract, real LLM. Those are recorded as not run, not as passed.
