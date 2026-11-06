# Pi Agent Host review

Reviewer: read-only adversarial pass. Branch `zcode/pi-gis-harness-usability-20261106`, HEAD `a678d0005`. Vendor pin `f9a263655` (v1.5.0), not edited. Open issues: none. Open PRs: dependabot only. None of the findings below duplicate an open issue. Already-merged PRs #1973/#1974/#1975/#1976/#1972/#1965 were not re-litigated.

Confirmed defects only. Anything not reproduced from the call sequence is under unconfirmed hypotheses and is not a finding.

## Call chain

Streaming turn (the path a map user actually hits):

1. `app/api/routes/chat.py` `chat_stream` (`:1153`) mints `_affinity_sid`, then `await _ensure_pi_bridge_available` (`:698`). False means the stream never starts on Pi; it falls through to `ChatEngine.chat_stream` (`:1448`) whose `task_start` is `TurnEventEmitter.task_start` with default `agent_runtime="chatengine"` (`app/services/chat/sse_contracts.py:139-144`, `execution_engine.py:2178`).
2. Pi path: `_pi_turn_bridge` → `PiBridgePool.bridge_for_session` (`agent_pi_bridge.py:3138`). Default pool size is 1 (`pi_bridge_tasks.py:32-33`), so every session gets the same worker.
3. `PiBridge.stream_prompt` (`agent_pi_bridge.py:2407`) mints `turn_id`, `issue_turn_token`, `register_active_pi_turn`, drains the event queue, then `bind_turn_prompt` (`pi_turn_context.py:216`).
4. `bind_turn_prompt` → `assemble_turn_context` (typed, default) or `legacy_bind_turn_prompt`. Both end with the active-tools marker then `[WEBGIS_TURN_CONTEXT:…]`. The marker is built by `compute_turn_active_tools` (`pi_native_surface.py:558`) and `_active_tools_block_for` (`pi_turn_context.py:395`).
5. `PiRpcClient.request("prompt", data)` (`agent_pi_bridge.py:2099` and `:2620`). Spawn argv is `node <rpc-entry> --mode rpc --no-session --no-builtin-tools` (`pi_rpc_client.py:402-404`). The vendor command type is `{ type: "prompt"; message; images?; streamingBehavior? }` (`vendor/pi/packages/coding-agent/src/modes/rpc/rpc-types.ts:22`). `handleCommand` calls `session.prompt(command.message)` and does not read `sessionId` (`rpc-mode.ts:394-407`). `new_session` exists (`rpc-types.ts:27`) and the bridge never sends it.
6. Extension `before_agent_start` (`app/extensions/webgis-tools/index.mjs:380`) resets `setActiveTools` to the turn marker or the default native seven, then the model calls a registered tool.
7. Registered-tool `execute` (`index.mjs:302`) runs only after Pi `validateToolArguments` (`vendor/pi/packages/ai/src/utils/validation.ts:54-59`), which `parse()`s with `coerce: true`. Integer nodes `Math.trunc` (`vendor/pi/packages/schema/src/coerce.ts:201-203`). The extension then `postToBridge`s the already-coerced `params`.
8. `webgis_execute.execute` (`index.mjs:343`) rejects any `toolName` in the spawn dump before HTTP.
9. `POST /pi-tools/execute` (`app/api/routes/pi_tools.py:51`): secret, `verify_turn_token`, `is_active_pi_turn` (`:75`). Inactive turn → 409, no dispatch. Active turn → `dispatch_tool` → `_dispatch_tool_bound` (`agent_pi_bridge.py:519`).
10. `_dispatch_tool_bound`: `resolve_pi_tool_call` (`pi_native_surface.py:76`) unwraps the proxy for non-frozen names; `stale_surface_gate`; `validate_pi_tool_arguments`; `ToolDispatchService.dispatch` under `use_token`; `apply_post_dispatch_disclosure` (`pi_post_dispatch.py:78`) writes SessionPlan evidence unless the callback is late.
11. `tool_execution_end` → `map_event_to_sse` (`pi_event_mapper.py:192`) reads the dispatch cache and appends `take_session_plan_sse` (`agent_pi_bridge.py:2801`). Turn end is `agent_settled`, not `agent_end` (`pi_event_mapper.py:274-287`).

Non-streaming is the same prompt RPC (`chat.py:970` → `PiBridge.prompt`) without an `agent_runtime` field on `ChatResponse`.

## Confirmed findings

| ID | Sev | Title | Duplicates open issue |
| --- | --- | --- | --- |
| PH-01 | P1 | One Pi worker transcript is shared by every GIS session | no |
| PH-02 | P1 | Chengdu heatmap proxy is rejected by the extension that instructs it | no |
| PH-03 | P1 | Cancel does not stop an in-flight MapSpec write | no |
| PH-04 | P2 | Pi truncates continuous meter fields to int before the server gate | no |

### PH-01 — Cross-session model context on the shared Pi subprocess

**Where**

- `app/services/chat/pi_rpc_client.py:402-404` spawn is one long-lived process, `--no-session` (no disk file; the in-memory `AgentSession` stays).
- `app/services/chat/pi_bridge_tasks.py:32-33` default `PI_BRIDGE_POOL_SIZE=1`. `PiBridgePool.bridge_for_session` (`agent_pi_bridge.py:3138-3140`) returns that single worker for every session.
- Bridge RPCs are only `prompt` and `abort` (`agent_pi_bridge.py:1817`, `:2099`, `:2620`). No `new_session`.
- Vendor prompt command has no session field (`vendor/pi/packages/coding-agent/src/modes/rpc/rpc-types.ts:22`). Handler (`rpc-mode.ts:398-407`) appends `command.message` onto the process's one session. A `sessionId` key on the JSON line is not part of the command and is ignored. `tests/integration/test_pi_vendor_contract.py:157` sends `sessionId` and only checks that the prompt still runs.

**Reproduction**

1. Start the API with default pool size 1.
2. Stream session A: "成都市小学分布情况". Wait until `query_local_poi` / `webgis_map_intent` tool results have been appended (Pi `message_end` for `toolResult`).
3. Stream session B, different `session_id`, same process: "只回答：上一条用户问的是哪座城市？"
4. B's model context includes A's user text and tool summaries, because both prompts were `session.prompt` on the same `AgentSession`.

Tool side effects stay isolated: `/pi-tools/execute` binds `session_id` from the turn token (`pi_tools.py:72-91`), and `currentTurnToken` reads the latest user entry (`index.mjs:109-115`). B cannot write A's MapSpec through the callback. B's model can still quote A's query, POI summary, and plan text, and can continue A's GIS task.

**Impact.** Multi-user (or two browser sessions) on the default single worker leak prior geographic questions and tool results into the next turn, and the model can mix cities. Pool size > 1 only shards the leak; sessions that hash to the same worker still share a transcript (`bridge_for_session` does not reset it).

**Root cause.** GIS session identity lives in the turn token and SessionPlan. The vendored coding-agent session is a different object, and the host never starts a new one when `session_id` changes. `--no-session` only disables persistence.

**Minimal fix.** When the worker's last GIS `session_id` differs from the incoming one, send `{"type":"new_session"}` and wait for it before `prompt`. Do not treat a `sessionId` field on `prompt` as isolation; v1.5.0 ignores it. Same-session follow-ups should keep the transcript.

### PH-02 — Extension forbids the Chengdu heatmap call its own prompt requires

**Where**

- Extension prompt (`index.mjs:314-317`) tells the model: distribution/density/heatmap, example 成都市小学分布, then `heatmap_data` or `h3_binning` **via this proxy**, then `webgis_map_product`.
- Turn surface line (`pi_turn_context.py:388-389`): "激活工具直调，其余经 webgis_execute".
- Stale-surface recovery (`pi_bridge_surface_gate.py:99-103`): "Re-discover via list_available_tools, then call webgis_execute."
- Extension wrap check (`index.mjs:254-256`, `:346-354`) rejects **every name in the spawn dump**, not the frozen seven. `nativeNames` is `surface.tools.map(name)`. The dump is `registered_surface_names` (`pi_native_surface.py:334-372`): every model-visible, non-tier-3 tool. `heatmap_data` is `@tool(..., tier=2)` (`app/tools/spatial.py:444`) and `model_visible` unless hidden/planned (`descriptor.py:253-255`), so it is in the dump.
- Python `resolve_pi_tool_call` wrap-rejects only `NATIVE_TOOL_NAME_SET` (`pi_native_surface.py:113-123`). `webgis_execute(heatmap_data)` is `kind="execute"` and is what `tests/test_pi_session_plan_host.py:97-107` (`test_chengdu_schools_scripted_chain`) uses. That test never loads `index.mjs`.

**Reproduction**

1. Spawn with `WEBGIS_NATIVE_TOOLS_PATH` pointing at `pi_surface_for_spawn` output (normal spawn, `pi_rpc_client.py:394-397`).
2. Model follows the extension description and calls `webgis_execute({toolName: "heatmap_data", arguments: {geojson, render_type: "native"}})`.
3. `index.mjs:346` returns `isError: true`, `native_wrap_rejected`, text "call it directly". No HTTP. No `dispatch_tool`. No SessionPlan progress. No MapSpec layer.
4. If the model obeys the error and calls `heatmap_data` directly, that works only while the name is in `setActiveTools`. Vendor contract: a `direct` tool is callable only while active (`vendor/pi/packages/coding-agent/src/core/extensions/types.ts:525-534`). Inactive registered tools are not declared.
5. If a direct call is stale, the server says use `webgis_execute` (`pi_bridge_surface_gate.py:99-103`). The extension then rejects that wrap and says call it directly. The two recoveries contradict. The loop does not reach `layer_upsert`.

`test_chengdu_schools_scripted_chain` stays green because it calls `dispatch_tool` in-process and skips the extension. `tests/unit/test_pi_surface_metrics.py:83-102` does not pin `heatmap_data` either: `must_have_any` is `{heatmap_data, query_local_poi}`, and `query_local_poi` is always in `NATIVE_TOOL_NAMES`.

**Impact.** The Chengdu heatmap step, as written in the tool the model is given, never mutates the map. A model that follows the stale-surface or "其余经 webgis_execute" text cannot reach a registered-but-inactive analysis tool at all: proxy is client-rejected, direct call is not in the active set. `webgis_map_product` / `webgis_component_update` are in the frozen native set and are not forced through the proxy. The drop is not inside `apply_post_dispatch_disclosure` — the live heatmap proxy call never arrives there. The Python proxy path, when invoked directly, does write SessionPlan (`test_chengdu_schools_scripted_chain` asserts `session_plan_progress`).

**Root cause.** ADR-0103 registered the superset and the extension started treating every dumped name as "native" for wrap-reject. The prompt, the surface line, the stale-surface guidance, and the Python classifier were not updated to the same rule.

**Minimal fix.** Pick one rule and make all four sites match it.

- If registered tools must be called directly: wrap-reject stays, but delete "via this proxy" / "其余经 webgis_execute" / "then call webgis_execute" for any dumped name. Stale recovery should say "call `<name>` directly" only when that name is in the current active set; otherwise it must not point at a path the extension rejects.
- If the Chengdu script is the contract: wrap-reject only `FALLBACK_NATIVE` / `NATIVE_TOOL_NAMES`, so `webgis_execute(heatmap_data)` reaches the Python path the host test already covers.

Do not leave both rules in production.

### PH-03 — A cancelled turn can still commit MapSpec

**Where**

- Gate is only at HTTP entry: `is_active_pi_turn` then `dispatch_tool` (`pi_tools.py:74-92`). A callback that arrives after unregister gets 409 and does not dispatch. That part is fine.
- Once inside `dispatch_tool`, cancel is a `CancellationToken` (`agent_pi_bridge.py:1845-1846`, and the dead-process path `:2291-2295`). The comment says in-flight callbacks "stop at their next checkpoint instead of running to completion against a dead turn."
- `checkpoint()` raises only if something calls it (`app/lib/cancellation.py:174-187`). `webgis_map_product` does not. It commits layers at `mapspec_store.layer_upsert` (`app/services/gis_harness/tool_map_product.py:322` and `:352`) and layout at `:661`. `MapSpecStore.layer_upsert` (`mapspec_store.py:443-456`) applies `UpsertLayerIntent` with no `is_cancelled` / `checkpoint` check. `app/tools/spatial.py` (heatmap tool) also has no cancellation checkpoint. `app/tools/**` `checkpoint(` hits are the unrelated `webgis_checkpoint` tool.

**Reproduction**

1. Pi turn calls `webgis_map_product` (Chengdu chain step; same arguments as `test_chengdu_schools_scripted_chain`).
2. After `POST /pi-tools/execute` has passed `is_active_pi_turn` and entered `webgis_map_product`, cancel the session (`abort_active_pi_turn` / disconnect abort).
3. `layer_upsert` still runs. The new layer is in MapSpec. Plan evidence may then be skipped if the turn is already inactive (`_late_for_plan` at `agent_pi_bridge.py:892-902`), so the map changes and the plan/SSE may not record it.

**Impact.** Stop / disconnect during product assembly or a heatmap that has already entered dispatch still changes the map. The UI can show the turn cancelled while a layer appears. Legacy `execution_engine.py:2691-2695` cancels in-flight tool tasks; this host path does not.

**Root cause.** Abort ignites a cooperative token and an RPC abort. The MapSpec write boundary never consults that token, so "next checkpoint" never happens on the write the user cares about.

**Minimal fix.** Call `cancellation.checkpoint()` (or refuse the apply when `is_cancelled()`) at the start of `MapSpecStore._apply` / `layer_upsert`, before the intent is committed. Do not rely on the extension aborting its fetch: the server handler is already past the 409 gate.

### PH-04 — Integer truncation of continuous meter fields happens before the server gate

**Where**

- Direct registered-tool calls are schema-checked in Pi before `execute` (`validation.ts:15`, `:54-59`). Integer conversion is `Math.trunc` (`coerce.ts:201-203`). Unions try conversion when no branch already accepts the value (`coerce.ts:240-246`), so `anyOf: [integer, null]` (pydantic `Optional[int]`) truncates too. Golden lock: `"3.5"` → `3` (`tests/fixtures/pi_schema_coercion_golden.json:95-97`).
- The extension posts `params` after that coercion (`index.mjs:302-306`). `validate_pi_tool_arguments` then `model_validate`s what it received (`pi_input_gate.py:259-268`). It cannot see the pre-truncation value. Pydantic v2 would have rejected a non-integer for an `int` field; it accepts the truncated int.
- Fields typed `int` whose descriptions are continuous meters, and which are on the registered surface (so the direct-call path is the one the extension allows):
  - `HeatmapDataArgs.cell_size` meters, `ge=10` (`app/tools/spatial.py:275`)
  - `HeatmapDataArgs.bandwidth_m` meters (`:280-282`)
  - `HeatmapDataArgs.radius` legacy meters (`:283-287`)
  - `search_poi_around.radius_m` meters, `1~50000` (`app/tools/chinese_maps/__init__.py:608-619`)
- `radius_px` is pixels. H3 `resolution` and GWR neighbor-count `bandwidth` are discrete counts. Those integer types match the science. They are not this finding.
- `buffer_analysis.distance` is `float` (`spatial.py:270`). `kde_surface` `bandwidth` / `cell_size` are `float` (`spatial_stats.py:587`). Pi will not trunc those. `webgis_execute` inner `arguments` is `additionalProperties: true` with no per-field schema (`index.mjs:335-338`), so a proxy call would not trunc either — but PH-02 blocks that proxy for these registered names. The live path is the truncating one.

**Reproduction**

1. Active surface includes `heatmap_data` (or call it as a registered tool the way PH-02's error tells the model to).
2. Model sends `heatmap_data` with `cell_size: 250.5` or `bandwidth_m: "1500.9"` (number or numeric string; both hit `tryNumber` + `Math.trunc`).
3. Extension `execute` receives `250` / `1500`. Server gate accepts them (`ge=10` still holds). Raster/kernel uses the truncated meter value. The model is not told the value changed.
4. Contrast: the same payload through in-process `dispatch_tool(webgis_execute, …)` (no Pi schema pass) still has the fraction, and the int field fails pydantic. The two host entries disagree.

`radius_m: 0.9` becomes `0`, then `search_poi_around` rejects `<= 0` (`chinese_maps/__init__.py:618`). The user asked for a sub-meter radius and gets a validation error about zero, not about type. `radius_m: 1.9` becomes `1` and is searched.

**Impact.** Chengdu heatmap grid size and analysis bandwidth, and nearby-POI search radius, silently change before any server check. The golden fixture's stated countermeasure ("server schema uses integer and re-checks") cannot work: the server never sees the original number.

**Root cause.** Tool args models use `int` for continuous meter quantities, and Pi coerce runs in the extension before `/pi-tools/execute`. The server gate is not a second look at the model output.

**Minimal fix.** Type those meter fields as `float` (JSON Schema `number`) with the same `gt`/`ge`/`le` bounds, and keep pixel radii and H3 resolution as `int`. Do not add a second coercion layer. Until the schema change ships, do not document server re-check as protection against Pi truncation.

## Non-findings

Checked and not defects. Do not re-fix these.

- **Silent Pi success on fallback (streaming).** Dead worker: `_ensure_pi_bridge_available` returns false (`chat.py:698-718`) and the stream is `ChatEngine.chat_stream`. `task_start` defaults to `agent_runtime="chatengine"` (`sse_contracts.py:140`, `execution_engine.py:2178`). Pi `task_start` hardcodes `"pi"` only after a live prompt RPC (`agent_pi_bridge.py:2670-2675`). Health probe fail-closes to `chatengine` if the bridge is not alive (`health.py:93-107`). Pool partial outage is disclosed as `pi_workers_alive`, not as a per-turn lie.
- **Unbounded event queue.** `PiRpcClient._event_queue` is `maxsize=1024`. Overflow drops incremental events or fails the turn (`pi_rpc_client.py:493-523`). Stdout lines are byte-capped.
- **Late HTTP callback mutating the next session.** `is_active_pi_turn` is before `dispatch_tool` (`pi_tools.py:74-92`). Mismatch is 409. Dispatch cache keys are `(session_id, tool_call_id)` (`agent_pi_bridge.py:342-348`). Plan evidence is skipped when `verifiedTurnId` is not the active turn (`:892-902`).
- **Turn terminator.** `agent_end` maps to nothing (`pi_event_mapper.py:274-287`). `task_complete` is `agent_settled`.
- **Oversized GIS body in the model context.** Provider adapters send tool `content`, not `details` (`vendor/pi/packages/ai/src/api/openai-completions.ts` tool-result text from content). `llm_payload` is `slim_tool_result` / `MSG_MAX_CHARS=2500` (`llm_result_formatter.py:20`, `:232-253`). Result ABI is ref-first and 4KB (`pi_result_abi.py:33-35`, `:94-109`). `details` can still hold a stripped dict up to 64KB (`agent_pi_bridge.py:411-443`); that is not what the model is sent.
- **Proxy dropping MapProductPlan / component mutation / selection, once the call arrives.** `resolve_pi_tool_call` unwraps to the inner name and the same `apply_post_dispatch_disclosure` runs. `test_chengdu_schools_scripted_chain` shows `webgis_execute(heatmap_data)` emits `session_plan_progress`, and `webgis_map_product` emits `session_plan_updated`. `webgis_component_update` is a frozen native (`pi_native_surface.py:23-31`); wrapping it is rejected on both sides. Selection for analysis scope is read from the interaction ledger, then `_cartographic_context_observation.selected_feature` (`analysis_scope.py:124-137`, `interaction_scope_tools.py:127-146`). The Pi route writes that observation (`chat.py:291-362`). The proxy does not strip it. Canonical `map_state["selection"]` / `selected_features` are legacy `_persist_map_state` keys (`execution_engine.py:1266`); no Pi tool reader of those keys showed up, so that split is not counted as a defect.
- **Frozen natives are not forced through the proxy.** `webgis_map_intent`, `get_local_admin_boundary`, `query_local_poi`, `list_available_tools`, `webgis_map_product`, `webgis_component_update`, `webgis_cartography_status` are registered and always activated (`index.mjs:24-32`, `:258`).
- **Float distance / KDE bandwidth already typed number.** `BufferAnalysisArgs.distance: float` (`spatial.py:270`). `kde_surface(..., bandwidth: float, cell_size: float)` (`spatial_stats.py:587`). Pi will not trunc those.
- **H3 resolution and neighbor-count bandwidth.** `h3_binning.resolution: int` and GWR `bandwidth: int = 30` are discrete. Integer truncation there does not change a continuous GIS quantity.
- **Missing turn token.** Extension returns `missing_turn_context` and does not POST (`index.mjs:119-125`).
- **Tier ≥ 3.** Rejected in `_dispatch_tool_bound` (`agent_pi_bridge.py:601-618`) and excluded from the dump (`pi_native_surface.py:364-365`).
- **Spawn without a dump.** `dump_surface_file` is fail-fast (`pi_rpc_client.py:388-397`). A raise aborts spawn; the route then uses ChatEngine rather than a Pi process with no GIS tools.
- **Status hallucination.** `webgis_cartography_status` with `city`/`topic`/`scope` is rejected before dispatch (`pi_native_surface.py:125-138`). Covered by `test_pi_status_fail_closed.py` and the Chengdu host test.
- **Dedup set cross-session wipe.** `_session_executed_sets` and the dispatch cache are popped by session id (`agent_pi_bridge.py:300-307`, `:2529-2530`), not globally, at turn start.

## Unconfirmed hypotheses

Not findings.

- **Late Pi events after `_drain_stale_events`.** Drain (`agent_pi_bridge.py:1870-1896`) drops only what is already queued, under the turn lock. An `tool_execution_end` that the reader has not enqueued yet can be consumed by the next turn, and Pi events carry no GIS session id. Plausible, not demonstrated with a failing sequence. Tool **writes** from that late callback are still blocked by the 409 gate (PH-03's opposite edge).
- **Client disconnect cancelling the ASGI handler before `layer_upsert`.** If Starlette cancels the request task at the next await, some cancels win by accident. PH-03 stands because the write path itself does not look at the token; it is not claimed that every cancel always lands after the commit.
- **Whether "成都市小学分布情况" always activates `heatmap_data`.** Not measured in this pass. The golden surface assertion does not require it (see test gaps). PH-02 does not depend on that measurement: the instructed proxy call is rejected either way.
- **Non-streaming `ChatResponse` omits `agent_runtime`.** The JSON success body does not claim `"pi"`. Not counted as a false Pi badge. Streaming and `/health` are the surfaces that speak.

## Test gaps (this layer)

A. **Session / cancel / fallback**

- No test runs two GIS `session_id`s through one `PiRpcClient` and asserts the second prompt's model messages do not contain the first session's user text or tool results.
- No test asserts a `new_session` RPC (or equivalent reset) when `bridge_for_session` is reused.
- No test cancels during `webgis_map_product` / `layer_upsert` and asserts MapSpec revision is unchanged. `tests/unit/test_pi_cancellation_unified.py` covers abort routing, not the map write.
- No test that a non-streaming fallback response is distinguishable from a Pi success. Streaming `agent_runtime` is covered indirectly by the emitter default; do not add a second badge.

B. **Registration / proxy**

- No test loads `index.mjs` `execute` for `webgis_execute({toolName:"heatmap_data"})` against a v3 dump and expects either success-with-SessionPlan or a prompt that does not instruct that call. `test_execute_heatmap_unwraps` (`tests/unit/test_pi_native_surface.py:40`) and `test_chengdu_schools_scripted_chain` both stop at Python.
- No test that stale-surface guidance and the extension wrap-reject agree on the next call.

C. **Event chain**

- No test injects a `tool_execution_end` onto the shared queue after `_drain_stale_events` and asserts it is not emitted as the next session's `step_result`.
- No test that `step_result` is withheld when `layer_upsert` returns `success: false` while Pi `isError` is false. Cache-hit mapping trusts `ToolDispatchResult.status` (`pi_event_mapper.py:202-224`); a fixture for "tool ok, map unchanged" is missing.
- `agent_settled` vs `agent_end` is covered (`pi_event_mapper.py` comments and unit tests). Do not reopen it.

D. **Chengdu journey through the extension**

- Host journey tests script `dispatch_tool`, not the extension. `tests/integration/test_pi_stub_llm_journeys.py` J1 stops at `query_local_poi` and does not require a heatmap layer.
- `test_golden_surface_quality_gate` must be tightened if direct `heatmap_data` is the intended Chengdu path: require `heatmap_data` itself for "成都市小学分布" / "密度热力图", not `query_local_poi` OR `heatmap_data`.

E. **Integer coercion on real GIS schemas**

- `tests/integration/test_pi_schema_coercion_golden.py` locks truncation on a synthetic `count` integer. It does not load `HeatmapDataArgs` or `search_poi_around` and does not assert the value that `/pi-tools/execute` receives.
- Add one vendor-contract case: register a dump of `heatmap_data`, send `cell_size: 250.5` and `bandwidth_m: "1500.9"`, and fail if `execute` params are `250` / `1500` unless the product explicitly accepts truncation. Today that failure is the production behavior (PH-04).

F. **Missing / null / unknown / hallucinated params**

- Covered for the Python classifier and the input gate (`test_pi_native_surface.py`, `test_pi_input_gate.py`, golden unknown-field / missing-required cases). No gap that rose to a defect. A gap remains for null-vs-missing on `Optional[int]` meter fields after Pi coerce (null stays null; omitted uses the default; a float string does not).

G. **Multi-tool turns and interrupt resume**

- No journey test aborts after `webgis_map_intent` and asserts the next prompt on the same session does not replay the aborted tool's Pi `toolResult` as success, and does not inherit another session's messages (overlaps PH-01).
- Resume (`Last-Event-ID`) is specified not to re-send `prompt` (`chat.py:1214-1221`). No test pairs that with an in-flight `layer_upsert`.

H. **Oversized results**

- Unit coverage exists for slim payload and result ABI. No vendor test asserts the next model request's tool message is the ref/summary and not `details` with a >100-feature collection. Code inspection says content is what providers send; a contract test would lock it so a future extension change (`JSON.stringify(result)` fallback at `index.mjs:205`) cannot inline the body.

## Suggested fix order

1. PH-01 — reset the Pi session on GIS session change. This is a privacy bug on the default single worker.
2. PH-02 — one wrap rule, matching prompts. Otherwise Chengdu heatmap never follows the text the model is given.
3. PH-03 — checkpoint at MapSpec apply. Otherwise Stop is a UI state, not a map state.
4. PH-04 — meter fields as `number`. Small diff, stops silent grid/radius changes. Do it with PH-02 so the direct-call path and the proxy path see the same numbers.
