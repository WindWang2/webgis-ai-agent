# Inventory: Pi vs ChatEngine host seams

**Ticket:** [#1019](https://github.com/WindWang2/webgis-ai-agent/issues/1019) (map [#1016](https://github.com/WindWang2/webgis-ai-agent/issues/1016))
**Question:** 把 Pi 宿主路径和 ChatEngine 回退路径在「回合规划 / 工具可见性 / SSE / 计划证据」上的缝列成清单。
**Method:** Read-only code + ADRs. Labels only — no architecture choice.

**Labels**

| Label | Meaning |
| --- | --- |
| **ChatEngine-only** | Exists on the legacy `ChatEngine` / `execution_engine` path; Pi turn does not run it. |
| **Pi-only** | Exists on `USE_NEW_AGENT` / `PiBridge` / `webgis_execute`; ChatEngine does not run it. |
| **Shared** | Both paths call the same module or persist to the same Session-keyed store. |
| **Missing** | Named in product language or map decisions, not present as code on that path. |

`USE_NEW_AGENT` defaults **True** (`app/core/config.py`); tests pin false. Live chat entry is `app/api/routes/chat.py` (`_use_pi_bridge()`).

This note does **not** decide wrap vs fork, native-tool wiring, SessionPlan persistence, or ChatEngine fallback bounds. Those are other tickets on [Pi as the agent host](https://github.com/WindWang2/webgis-ai-agent/issues/1016).

---

## Entry and identity

| Seam | Label | Where |
| --- | --- | --- |
| HTTP chat/stream fork | **Shared** entry, then split | `chat.py`: `use_pi = _use_pi_bridge()`; same route, two generators. |
| GIS world key `session_id` | **Shared** | ADR-0055. MapSpec, refs, checkpoints, map_state. Pi hosted with `--no-session`. |
| Pi subprocess session | **Pi-only** | `pi_rpc_client.py` `--mode rpc --no-session`; replaceable runtime, not GIS identity. |
| Conversation / history row | **Shared** store, **split** writers | ChatEngine saves via execution engine; Pi saves in `_persist_pi_transcript` (`chat.py` #818 / H-7). Same `conversations` table. |
| Turn token for `/pi-tools/execute` | **Pi-only** | `pi_turn_context.issue_turn_token` / `attach_turn_context`. ChatEngine dispatches in-process. |

---

## Round planning

| Seam | Label | Where |
| --- | --- | --- |
| `classify_followup` / `should_plan` / `make_plan` | **ChatEngine-only** | `execution_engine._maybe_plan` → `plan_orchestrator`. Comment in `chat.py:750-755` (#726): Pi **deliberately** skips this chain. |
| CanonicalPlan + `plan-current` | **ChatEngine-only** | `app/services/planning/`. Pi has no CanonicalPlan (gis-harness.md 「Pi 路径与规划链」). |
| Orchestrator `Plan` + `gis_intent` / `recipe_id` attachment | **ChatEngine-only** | `plan_orchestrator.py`: LLM plan plus GIS Harness projection as extra fields. |
| `decision_log` | **ChatEngine-only** | `execution_engine.py` → `decision_log.py`. |
| SessionPlan envelope | **Missing** both paths | Map decision on #1017; no type/store in code yet. ChatEngine still uses CanonicalPlan; Pi has no host-plan object. |
| Deterministic `webgis_map_intent` / `MapProductPlanner` | **Shared** tool | `app/services/gis_harness/tools.py`. ChatEngine can call it if ToolCatalog surfaces it; Pi can call it via `webgis_execute`. Neither path auto-opens a SessionPlan slot today. |
| H-1 deterministic plan short-circuit | **ChatEngine-only** (if present in orchestrator) | Not invoked from `pi_event_generator`. |

`docs/gis-harness.md` records the #726 fact: CanonicalPlan / decision_log are legacy-only; cartographic quality loop is shared; transplanting the planning chain onto Pi is a separate roadmap item.

---

## Tool visibility

| Seam | Label | Where |
| --- | --- | --- |
| `ToolRegistry` + `ToolDispatchService` | **Shared** | ADR-0006. ChatEngine `tool_pipeline`; Pi `agent_pi_bridge.dispatch_tool` → same service (ref, slim, repeat intercept, self-heal). |
| `ToolCatalog.select_schemas` (tier/domain subset per turn) | **ChatEngine-only** | `execution_engine.py` ~467. Pi LLM does not receive OpenAI tool schemas for GIS tools. |
| `SYSTEM_PROMPT` (意图先行、finalize_display、中国本地优先) | **ChatEngine-only** | `prompt.py`; `_build_system_prompt` in execution_engine. Pi never formats this string. |
| Single proxy `webgis_execute(toolName, arguments)` | **Pi-only** | `app/extensions/webgis-tools/index.mjs`. `additionalProperties: true` on `arguments` — no per-tool JSON schema at the Pi tool boundary. |
| Extension `promptSnippet` / `promptGuidelines` / `before_agent_start` GeoAgent rewrite | **Pi-only** | Same `index.mjs`. Coding-assistant opening replaced when `pi.on` is present. |
| `--no-builtin-tools` | **Pi-only** | `pi_rpc_client.py` spawn argv (GIS product must not fall through to bash). |
| Status-as-analysis reroute | **Pi-only** | `pi_tool_reroute.py` in `dispatch_tool`: `webgis_cartography_status` + city/topic/scope → `webgis_map_intent`. ChatEngine has per-tool pydantic and does not need this. |
| Native Pi tools for intent/POI/boundary/status | **Missing** | Map decision on #1018; live extension still exposes only `webgis_execute`. |
| `list_available_tools` | **Shared** registry tool | ChatEngine: in catalog when domain keywords fit. Pi: only if the model passes that name through `webgis_execute`. |
| Tier ≥3 refuse on Pi HTTP callback | **Pi-only** guard | `dispatch_tool` in `agent_pi_bridge.py`. ChatEngine has confirm_tier3 on registry. |

---

## Prompt / context injected into the model

| Seam | Label | Where |
| --- | --- | --- |
| `ChatContextAssembler` (`[环境感知]`, history budget, plan block, XML fence) | **ChatEngine-only** | `execution_engine` owns an assembler. Pi prompt is the raw user message plus attach_turn_context. |
| `[CARTOGRAPHY_VERDICT]` / `[CARTOGRAPHY_MEMORY]` next-turn inject | **Shared** builder, **Pi** consumer on this route | `_build_cartography_turn_context` in `chat.py`. Pi prepends it via `attach_turn_context`. Legacy assembler documents the same order (verdict then memory). |
| Turn marker `WEBGIS_TURN_CONTEXT:` | **Pi-only** | Must stay last in the user message (`pi_turn_context.py`). |
| Map-state observation write at turn start | **Shared** | `_record_frontend_cartographic_observation` before both Pi stream and (legacy uses assembler/observation elsewhere). Pi stream calls it explicitly (`chat.py:757`). |

---

## SSE and plan evidence

| Seam | Label | Where |
| --- | --- | --- |
| Independent SSE builders | **Split** (ADR-0022) | No shared `SseEventEmitter`. Pi: `pi_event_mapper.map_event_to_sse`. ChatEngine: `execution_engine` yields. |
| `task_start` | **Shared** event name, **split** payload | ChatEngine: `task_id` is TaskTracker id, `agent_runtime: chatengine`. Pi: `task_id := session_id`, `agent_runtime: pi`, plus `turn_id` (`agent_pi_bridge.py` stream_prompt). |
| `plan_ready` / `plan_step_done` / `plan_finalized` | **ChatEngine-only** | `execution_engine.py` ~1725, ~2108, `_maybe_plan_finalized_event`. Frontend `use-sse-stream.ts` handles them. Pi mapper never emits these names. |
| `step_result` / `step_error` | **Shared** names, **split** fill | ChatEngine from tool pipeline. Pi from mapper + ADR-0022 dispatch cache (`geojson_ref` rendezvous). Pi `step_index` historically 0 (ADR-0022: latent; frontend list uses array index). |
| Pi vendor events (thinking, tool_execution_*) | **Pi-only** | Mapped in `pi_event_mapper.py`. |
| Keepalives | **Shared idea, split impl** | ChatEngine planner pump `keep_alive`; Pi SSE comment `: keepalive` during lock/tool silence. |
| SessionPlan / capability-progress SSE | **Missing** | No events for SessionPlan. Map ticket [Plan evidence on the wire](https://github.com/WindWang2/webgis-ai-agent/issues/1023) owns that decision. |

Frontend plan UI (`plan_ready` in `use-sse-stream.ts`, `frontend/lib/types/agent-plan.ts`) is wired to ChatEngine events. On the default Pi host those events never arrive — a **Missing** plan-evidence surface, not a second implementation.

---

## Cartography (quality loop, not turn planning)

| Seam | Label | Where |
| --- | --- | --- |
| MapSpec lifecycle, evaluate, Observed Map, verdict store | **Shared** | ADR-0071 `app/services/cartography_runtime.py`; bridge re-exports. gis-harness.md: quality loop is shared; difference is turn-level task planning. |
| `webgis_cartography_status` | **Shared** tool | `cartography_tools.py`. Pull of stored harness verdict; not a data query. |
| Verdict inject policy | **Shared** function | `should_inject_verdict` used when building Pi turn context; legacy assembler has the same policy. |

---

## Dispatch cache and process (ADR-0022 / 0031)

| Seam | Label | Where |
| --- | --- | --- |
| `_dispatch_result_cache` rendezvous HTTP callback ↔ SSE mapper | **Pi-only** | Stays in `agent_pi_bridge.py` (ADR-0022, ADR-0031). ChatEngine has no HTTP tool callback. |
| `PiRpcClient` / `pi_event_mapper` split | **Pi-only** | ADR-0031. |

---

## Pointers (do not restate decisions)

- ADR-0006 unified dispatch (both agent loops).
- ADR-0022 do not extract shared SSE emitter; Pi cache is the HTTP↔SSE rendezvous.
- ADR-0031 split RPC client + mapper out of the bridge.
- ADR-0055 `session_id` owns the GIS world.
- ADR-0071 cartography runtime extracted from the bridge; both loops evaluate there.
- `docs/gis-harness.md` 「Pi 路径与规划链」: Pi skips CanonicalPlan; `webgis_map_intent` / `webgis_map_product` are the GIS planning tools on that path.

---

## Compression (facts only)

On today’s default Pi host: GIS **compute and map quality** go through shared dispatch + shared cartography runtime. **Turn planning, tool-schema visibility, SYSTEM_PROMPT, ChatContextAssembler, decision_log, and plan_* SSE** stay on ChatEngine. Pi sees one untyped proxy tool plus a snippet, plus optional verdict inject on the next user message. SessionPlan is a map decision without a code seam yet.
