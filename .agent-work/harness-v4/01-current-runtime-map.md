# 01 — Current Runtime Map: Pi/Chat → Tool → SessionPlan → MapSpec → Verdict (repo @16d1c70, worktree harness-v4)

All claims are static-read from code; `file:line` citations are exact as of HEAD `16d1c70` (branch `feat/gis-harness-autonomous-runtime-v4`). Paths relative to repo root.

---

## 0. One-paragraph truth

The production agent path is **not** an in-Python LLM loop: the server spawns the vendor **Pi** coding-agent as a subprocess over JSON-RPC (`app/services/chat/pi_rpc_client.py:38-41,84`), and the LLM loop (model calls, tool-call parsing, context/compaction) lives entirely inside that subprocess. The Python side owns: per-turn prompt decoration (HMAC turn token + bounded context blocks), the tool registry + dispatch, a **SessionPlan envelope** (durable capability DAG in the session Redis store), MapSpec desired-state storage on disk, a frontend render-observation loop, and two post-hoc evaluators (map-product finalizer + cartography harness gate). `app/services/gis_harness/workflow_compiler.py` is **evaluation-only today** (see §5.4).

---

## 1. Entry points

| Entry | Location | Notes |
|---|---|---|
| `POST /api/v1/stream` (SSE) | `app/api/routes/chat.py:869-1158` | Primary chat entry; `StreamingResponse` SSE. Pi path vs legacy chosen at `chat.py:911` (`_ensure_pi_bridge_available`) / `chat.py:479-502` (`_use_pi_bridge` = `USE_NEW_AGENT` + live bridge). |
| `POST /api/v1/completions` (JSON) | `app/api/routes/chat.py:726` | Non-stream; same selection at `chat.py:746,831`. |
| `POST /pi-tools/execute` | `app/api/routes/pi_tools.py:51-92` | HTTP callback from the Pi extension (secret + signed turn token, no `/api/v1` prefix — `app/main.py:447`). |
| `POST /chat/tools/execute` | `app/api/routes/chat.py:1983-1984` | Direct admin-only tool execution. |
| Observation/ACK (frontend → server) | `chat.py:1446` (`/sessions/{sid}/cartographic-observation`), `chat.py:1780` (`/sessions/{sid}/map-action-ack`), `chat.py:1395` (`/sessions/{sid}/map-state`) | Renderer feedback loop, see §7. |
| SessionPlan read | `chat.py:1345` (`GET /sessions/{sid}/plan`) | |
| WebSocket | `app/api/routes/ws.py` (mounted `app/main.py:437`) | Not part of the Pi chat chain (progress broadcast). |

Lifespan wiring (`app/main.py:38-121`): `ToolRegistry()` + `init_tools` (`main.py:68-69`; gis_harness tools registered from the module list at `app/tools/__init__.py:47`), runtime-manifest compile/validate (`main.py:75-86`), registry injection into the bridge `set_tool_registry` (`main.py:93-94` → `app/agent_pi_bridge.py:173-185`, which also recompiles the runtime manifest), `ChatEngine`+`ToolCatalog` legacy singletons (`main.py:96-101`), and the **Pi bridge pool** start with extension path `app/extensions/webgis-tools/index.mjs` (`main.py:104-112`). Pi failure at spawn → logged fallback to ChatEngine (`main.py:113-118`).

**Pool**: `PiBridgePool` (`agent_pi_bridge.py:2465-2494`) — `PI_BRIDGE_POOL_SIZE` (default 1) subprocesses; session→worker stable affinity via md5 (`:2484-2494`); lazy creation `_ensure_bridge_pool` (`:2515-2541`); `get_pi_bridge` (`:2445-2462`).

---

## 2. Pi runtime: what "Pi" and "turn" actually are

- **Vendor**: `vendor/pi` must contain the built `packages/coding-agent/dist/rpc-entry.js` (`pi_rpc_client.py:38-41`). **In this worktree `vendor/pi/` is empty** (build artifact, not in git) — without a local build the Pi path cannot start and the API silently degrades to legacy ChatEngine (`main.py:113-118`).
- **`PiRpcClient`** (`app/services/chat/pi_rpc_client.py:84`): owns spawn, readiness poll, JSON-RPC over stdin/stdout, a bounded event `asyncio.Queue` (`:53-59`), `process_died_event`, per-request timeout `PI_RPC_TIMEOUT=300s` (`:80`), commands incl. `prompt`/`abort` (`:557-599`).
- **`PiBridge`** (`app/agent_pi_bridge.py:1206`): thin orchestrator. `stream_prompt` (`:1902`) = one **turn**:
  1. mint `turn_id` `turn-<hex12>` (`:113-115`, `:1936`); mint HMAC turn token (`pi_turn_context.py:39-58`) at `:1937`.
  2. `RuntimeContext` bind (turn/run ids) + `TurnEvidence` register (`:1946-1963`; evidence module §10).
  3. Acquire turn lease (`#1108` invariants `:1184-1292`), register active turn in process table `_active_turns` + Redis (`register_active_pi_turn` `:1079-1098` → `PiTurnRegistry` `pi_turn_context.py:249-333`, Redis key `webgis:pi:active_turn:{sid}`).
  4. Decorate the prompt (`bind_turn_prompt`, §3), send RPC `prompt` (`:2058`), then pump the vendor event queue: each event → `map_event_to_sse` (`app/services/chat/pi_event_mapper.py`) → SSE yield (`:2184-2216`); `tool_execution_end` appends cached SessionPlan SSE (`:2206-2211`); `agent_settled` is the sole turn terminator (`:2231-2236`; `agent_end` is NOT terminal, `:2217-2230`).
  5. On `agent_settled`: `maybe_finalize_map_product(final_gate=True)` before `task_complete` (`:2151-2178`) and `map_finalization` SSE (`:2232-2235`).
  6. Finally: abort-on-disconnect/timeout (`:2313-2331`), drain leftovers (`:2335`), `_cleanup_turn_state` (`:2343`, def `:351-361`), release lease, settle `TurnEvidence`, `on_turn_result` sink for transcript persistence (`:2398-2412`).
- **Timeouts**: stall `PI_EVENT_STREAM_TIMEOUT=120s` default, total `PI_TURN_TOTAL_TIMEOUT=300s`, heartbeat `PI_HEARTBEAT_INTERVAL=8s` (`:85-101`).
- `prompt()` non-streaming twin exists (`:1579-1900`) with the same lifecycle.
- The route wraps the stream in SSE batching (`_sse_batched`, `chat.py:527`) and a resume ring buffer (`_recorded`, `chat.py:570`; `TurnEventBuffer` `app/services/chat/event_resume.py:116`); `Last-Event-ID` re-POST = replay-only, never re-executes (`chat.py:964-972`).

---

## 3. Prompt binding (per turn, Pi path)

Route assembles (Pi path `chat.py:974-1000`):
1. Pre-turn frontend snapshot persisted under lock: `_record_frontend_cartographic_observation` (`chat.py:241-310`) → map_state key `_cartographic_context_observation` (sequence++).
2. `cartography_context` = harness verdict block + project memory: `_build_cartography_turn_context` (`chat.py:385-440`) reads `_cartographic_review` from map_state, gates via `should_inject_verdict` + cartographic fingerprint (`app/lib/cartography/quality_loop.py`, `verdict_summary.render_verdict_for_llm`), appends `[CARTOGRAPHY_MEMORY]` via `_build_project_memory_block` (`chat.py:426-439`).
3. `environment_context` = bounded `[环境感知]` block from request `map_state` (`chat.py:313-382`).

`bind_turn_prompt` (`app/services/chat/pi_turn_context.py:147-195`) then appends, in order (`attach_turn_context` `:110-144`):
- user message with `WEBGIS_ACTIVE_TOOLS` markers neutralized (`:97-107`),
- `[CARTOGRAPHY_MEMORY]`/verdict block,
- `[SessionPlan]` bounded projection — `format_session_plan_projection` (`app/services/session_plan.py:177-268`): head line (recipe/open caps/replaced/superseded/STALE_PLAN `:193-208`), DAG block via `plan_graph.build_plan_graph` (`:222-229`), product-facets line via `product_graph` (`:234-242`), deterministic next-action line via `action_intent.action_intent_projection` (`:248-265`), map_product line (`:213-218`),
- `[环境感知]` block, `[工具面提示]` (surface_block, from `compile_tool_surface` — `pi_turn_context.py:198-224`),
- `[WEBGIS_ACTIVE_TOOLS:[...]]` dynamic-surface marker (`_active_tools_block_for` `:227-246` → `compute_turn_active_tools` `pi_native_surface.py:355-401`, k_max=30, native 7 always included),
- `[WEBGIS_TURN_CONTEXT:<hmac>]` marker, kept last (`pi_turn_context.py:142-143`).

**Tool-schema injection is spawn-time, not per-turn**: `dump_surface_file`/`pi_surface_for_spawn` (`pi_native_surface.py:296-352`) writes `{version:2, tools, default_active, execute_proxy}` JSON from the live registry (fail-fast `:304`); the extension registers every tool and calls `pi.setActiveTools(default_active)` at load (`index.mjs:226-246,249-261`); per-turn the extension parses the LAST `WEBGIS_ACTIVE_TOOLS` marker in the prompt at `before_agent_start` and re-projects the active set, hard-capped at 48 (`index.mjs:327-345,214-246`). The extension also rewrites the vendor system prompt, replacing the coding-assistant identity with `GIS_IDENTITY` ("You are GeoAgent…never bash/files") (`index.mjs:36-37,323-334`).

**Budgeting/trimming on the Pi path**: none for LLM context (vendor-owned, compaction inside Pi). Python-side bounds are: `session_id` stripped from schemas (`_pi_parameters` `pi_native_surface.py:153-168`), dormant tools NOT compressed by design (`:250-253`), SSE details payload capped 64KB (`_slim_pi_details_payload` `agent_pi_bridge.py:364-399`), trace args bound 512B (`:951`). Byte-budgeting (`plan_budget`/`GisBudgetAdvisor`, `app/services/chat/context_budget.py:129,266`) and `ChatContextAssembler` (`app/services/chat/context_assembler.py:206,405-449`) are **legacy-path only**.

---

## 4. Tool surface & dispatch

**Canonical ToolRegistry**: `app/tools/registry.py:387` (`ToolRegistry`; `register` `:459`; ADR-0101/0103 descriptor kwargs incl. `input_artifacts`, `map_mutations`, `security_tier` at `:442-457`; tier semantics `:396-401`). Single process-wide instance injected at `main.py:68-94`.

**Model-facing surface** (`app/services/chat/pi_native_surface.py`):
- Frozen native 7: `NATIVE_TOOL_NAMES` `:20-28`; execute proxy `webgis_execute` `:30`.
- Classifier `resolve_pi_tool_call` `:73-150`: `webgis_execute(toolName,args)` unwraps (`:92-114`), native-wrap rejected (`:104-113`), zero-arg guard for `webgis_cartography_status` (`:116-130`), registered-surface bare names dispatch like execute (ADR-0103, `:135-138`), unknown → reject with discover guidance (`:142-150`).

**HTTP callback hop**: extension `postToBridge` → `fetch /pi-tools/execute` with `X-Pi-Bridge-Secret` + `turnToken` (`index.mjs:103-140`) → route verifies secret (`pi_tools.py:36-48`), verifies HMAC token and liveness `is_active_pi_turn` (`:61-89`; delayed/cross-pod callbacks get 409) → `dispatch_tool` (`agent_pi_bridge.py:427`).

**`dispatch_tool`** (`agent_pi_bridge.py:427-937`) in order: resolve/classify (`:460-471`); `ensure_session_plan_slot` (`:473-478`); registry existence + guidance (`:481-493`); tier≥3 hard reject (`:495-506`); shared `ToolDispatchService` (`:512`, singleton `:228-236`); cancel token + `JobOrigin` contextvar binding (`:540-569`); `service.dispatch` (`:570`); TaskTracker step (`:634-666`); cache result for SSE adapter (`:669`, cache `:210,299-338`); SessionPlan apply (success `:672-716`, failure `:755-803`, both retry-once on lock contention); map finalization attempt + `map_finalization` SSE cache (`:717-754`); harness evidence (`:805-879`); cartography evaluation (`:884-892`); gis_trace stages (`:896-915`); no-progress diagnosis appending `no_progress_hints` to details (`:917-930`, tracker `:941-1033` → `app/services/chat/no_progress.py:176`).

**`ToolDispatchService.dispatch`** (`app/services/tool_dispatch_service.py:296-865`): dedup `(tool, normalized-args)` per-session with in-flight vs completed semantics (`:354-386`); deterministic analysis-reuse via `ArtifactRegistry` + algorithm registry (`:388-452`); wave semaphore (global 5, session 2, heavy=2 slots) (`:313-321,462-481`); `registry.dispatch` inside arg-lineage capture (`:478-479`); error-shape folding (`:504-553`); big GeoJSON/heatmap/uncertainty → `session_data_manager.store` refs (`:555-636`); artifact registration `register_tool_artifact` (`:637-680`); map-action minting `_mint_map_action_ids` (`:1305`). `ToolDispatchResult` fields incl. `map_actions`, `geojson_ref`, `background_job_ids` (`:156-207`).

**Confirmation/gating today**: tier≥3 reject at the Pi boundary (`agent_pi_bridge.py:495-506`); legacy path tier/confirm gates live in ToolCatalog/tool_pipeline (`app/services/chat/tool_pipeline.py`, `tool_catalog.py`). There is **no generic user-confirmation gate on the Pi path** — gating is tier-based rejection only.

---

## 5. SessionPlan + planning chain

### 5.1 Model & storage
`SessionPlan` (`app/services/session_plan.py:67-78`): `envelope_id`, `session_id`, `user_goal`, `gis_chapter` (embedded MapProductPlan dump), `progress: list[CapabilityProgress]` (`:59-65`, status pending/complete/voided/unavailable/failed `:56`), `replaced/superseded`. Stored in the session store (Redis or memory) as a ref under alias `session-plan` (`save_session_plan :307-320`, `load_session_plan :284-304`, `CURRENT_ALIAS :23`). Opened lazily per turn and per callback (`ensure_session_plan_slot :323-353`, double-checked locking under the distributed session lock).

### 5.2 Creation (intent → plan)
LLM calls `webgis_map_intent` (`app/services/gis_harness/tools.py:370-533`, registered tier=1 at `:357-370`): deterministic intent parse `resolve_map_request_intent` (`app/services/gis_harness/intent.py:660`; keyword/token tables `_match_scope :390`, `_match_subject :434`, task intents `:465`) → candidate recipes with project-verified prior (`tools.py:455-457`) → `MapProductPlanner.plan_from_intent` (shared `PlannerRuntime`, `app/services/gis_harness/planner_runtime.py:39-50`; memo `planner.py:267-269`) → `MapProductPlan` (`app/services/gis_harness/planner.py:191`) with `data_requirements`/`analysis_steps`/`algorithm_selections` (capability→tool resolution `:56-90`). Result returns `plan` dump + bounded `guidance` lines (`tools.py:491-528`). The SessionPlan chapter is then written by `apply_tool_result` on the dispatch path (below), **not** inside the tool.

### 5.3 Update (dispatch → plan)
`apply_tool_result` (`session_plan.py:481-675`, under `session_lock_registry.lock`): intent tool ⇒ supersede/replace chapter (archived `_archive_envelope :356`); product tool ⇒ `merge_map_product_result` (`:518`); other tools ⇒ capability completion via `capabilities_hit_by_tool` (`:417-439`, from `algorithm_registry.tool_to_capability` + `resolved_tool` matches) and `_mark_progress` (`:442-478`, mirrors status into chapter rows). Failure marks hit capabilities `failed` (v3 Phase E). SSE events emitted for updated/progress/superseded (`:368-405`, serialized `events_to_sse :271-281`).

### 5.4 Workflow compile vs planner (IMPORTANT correction to the assumed architecture)
`compile_workflow` — the 15-stage deterministic compiler (`app/services/gis_harness/workflow_compiler.py:130`, stage list `:38-55`) — is consumed **only** by the evaluation harness (`app/evaluation/runner.py:377`, `app/evaluation/anti_claim.py:230,240`) and tests. The live chain uses `MapProductPlanner.plan_from_intent` instead. `workflow_families.py`, `plan_candidates.py`, `data_qualification.py` are compiler-stage modules (compiled into the evaluation path), not a production runtime. `planner.py` uses `TemplateSelector` (`planner.py:47`) and recipe registry, not the workflow compiler.

---

## 6. Artifacts & MapSpec

- **Refs**: every large tool output is stored via `session_data_manager.store` → `ref:geojson-*` / `ref:heatmap-*` etc. (`tool_dispatch_service.py:583,605-610`; store = `app/services/session_data.py:19` memory or `app/services/session_data_redis.py` Redis, TTL 4h `session_data_redis.py:21-26`, L1 2s `:36`).
- **ArtifactRegistry**: `app/services/artifact_registry.py` — `ArtifactRecord :84`, `register_artifact :361`, `register_tool_artifact :456` (dispatch seam; plan-apply seam later enriches capability/lineage), `ArtifactGraph :125`, `build_artifact_graph :197`, `probe_ref :323`, sweeps `:679`. Records persist in the same session store; read-only projection contract `ArtifactContract` at `app/lib/data/artifact_contract.py:173` (factories `:351,453,508,551`). Workspace snapshotting: `app/services/workspace/snapshot.py:199` (`WorkspaceSnapshotService`, JSON on disk under session dir `:134-161`).
- **MapSpec (desired-state single truth)**: `mapspec_store` singleton (`app/services/mapspec_store.py:408`) delegating to `MapSpecLifecycleEngine` (`app/services/mapspec/lifecycle_engine.py:522`) with typed intents (`:188-345`). Storage on disk: `DATA_DIR/<session_id>/mapspec.json` + `.fp` fingerprint sidecar + `.rev` revision sidecar, atomic writes (`app/services/mapspec/store.py:31-39,148-152,72-126`). Checkpoints/rollback with content-hash blobs (`app/services/mapspec/checkpoint.py:253,380`). CLI compile/validate (`mapspec/coordinator.py:23,108`).
- **Production tool**: `webgis_map_product` (`app/services/gis_harness/tools.py:535-1162`, tier=2): eligibility re-check, role binding by actual spec layer types (`:678`), missing-layer authorization via `convert_analysis_to_mapspec_layer` + `mapspec_store.layer_upsert` (`:776-841`), components/layout via `component_composer` + `layout_set` (`:862-903`), returns `mapspec_fingerprint` + per-mutation `map_actions` (action ids minted at dispatch, `tool_dispatch_service.py:1305-1357`) which the frontend ACKs. Component-only edits: `webgis_component_update` (`tools.py:73-292`).
- **Provenance**: `app/services/gis_world_state/provenance.py:31` (`ProvenanceEntry`, append/read `:52,71`), consumed by harness identity matching (`pi_agent_harness.py:89-104`) and `product_lineage.py`. Mutation guard for user-vs-agent presentation ownership: `gis_world_state/mutation.py:60-119,192,297` (`apply_gis_mutation[_batch]`), surfaced to users via `POST /sessions/{sid}/mapspec/mutations` (`app/api/routes/mapspec_mutations.py:176`).

---

## 7. Renderer observation (desired vs observed)

- Frontend POSTs runtime observation → `_cartographic_observation` map_state key (`chat.py:1446-1706`; key const `render_observation.py:49`). Server stamps `mapspec_revision` only after a content-fingerprint acceptance gate (module docstring `render_observation.py:12-20`); ACKs (map actions) POST to `chat.py:1780` → stored map-action events (`session_data` `get_map_action_events`).
- `validate_render_observation` (`render_observation.py:139-288`): revision match required else `stale`; per result-layer presence/visibility vs observation entries (`:200-247`), required component slots (`:249-271`), bounded runtime errors (`:273-283`); returns status `verified|issues|stale|unknown|not_applicable`.
- Observation is read-only evidence: no repair path writes from observation directly; repairs go through desired-state mutations (module boundary `render_observation.py:5-25`).

---

## 8. Completion / verdict — where "task complete" is decided today

Two independent evaluators, stored in two places:

1. **Map-product finalizer** (`app/services/gis_harness/completion/pipeline.py`): `maybe_finalize_map_product` (`:434-637`) — dedup gate (stored `gis_chapter["map_product"]` + revision + rows fingerprint + render-obs seq, `:487-499`) → `run_map_finalization` (`:72-260`): execution-DAG gate via `validators/execution.py` (pending ⇒ `pending`, blocked ⇒ `failed`, `:96-118`), then ≤`MAX_FINALIZATION_PASSES` validate→repair→revalidate over `validators/{layers,components,layout,semantics,artifacts,viewport_export}.py` (`:121-160`), render validation (`:162-181`), export parity/bbox (`:183-201`), V3 final map verification (`:204-224`), status roll-up (`:229-260`). Persisted into `gis_chapter["map_product"]` under the session lock with supersede/revision/rows/observation drift guards (`:537-602`). Disclosure: `map_finalization` SSE payload (`:667-695`) emitted from the bridge (per-dispatch `agent_pi_bridge.py:717-754`; turn-settle `:2151-2178`) and read back via `read_stored_map_product` (`:640-664`).
2. **Cartography harness gate** (`app/services/cartography_runtime.py` + `app/lib/harness/`): per-session `PiAgentHarness` (`app/lib/harness/pi_agent_harness.py:279`; registry `_build_session_harness`/`_get_session_harness` `cartography_runtime.py:54,81`; evidence persisted/restored `:151,261`). `evaluate_cartographic_session` (`:427-458`, session-locked, eval cache keyed by observation seq + evidence revision `:517-569`) → `harness.evaluate_with_evidence()` (`pi_agent_harness.py:673`) → `HarnessEvaluator.evaluate_evidence` gate (`app/lib/harness/evaluator.py:94-248`; thresholds `:16-29`; missing evidence = fail, `:172-176`). Result (+ runtime repair advance `_advance_runtime_cartographic_repair` `:632`) persisted to map_state `_cartographic_review` (`:614-616`) — which is what the next turn's prompt verdict block reads (`chat.py:414-419`) and what `webgis_cartography_status` returns (`app/tools/cartography_tools.py:829-866`). Dispatch-side evidence recording shared with legacy: `record_cartographic_dispatch_evidence` (`cartography_runtime.py:461-514`).

Note: `map_completion.py` at `gis_harness/` root is a **compat shim** re-exporting `completion/` (`app/services/gis_harness/map_completion.py:1-41`).

---

## 9. Context management (per turn)

- Pi path = additive bounded text blocks (§3) + vendor-owned conversation/compaction. The server does not re-send or trim Pi's transcript; it persists user/assistant turns to DB only (`_persist_pi_transcript` `chat.py:1005-1047`; H-7: user msg saved regardless of completion).
- Legacy path = `ChatContextAssembler.assemble` (`context_assembler.py:206`) + byte budget `plan_budget` (`context_budget.py:129`) + `GisBudgetAdvisor` (`:266`, consumed `context_assembler.py:405-449`).
- `app/lib/runtime/context.py` is **not** conversation context — it is the correlation ContextVar `RuntimeContext` (`:30-45`, bind `:98-125`, id minting `:130-141`); there is no "budget advisor" in this file (goal-prompt assumption corrected).

---

## 10. Trace emission points (all in-process, bounded)

| Registry | Unit | Where written |
|---|---|---|
| `TurnEvidence` (`app/lib/runtime/evidence.py:119`, registry `:361`) | per-turn outcome/SSE count/map actions | bridge turn lifecycle (`agent_pi_bridge.py:1620-1625,2213,2362-2379`); summary = `logger.info("[turn] …")` (`evidence.py:391-401`) |
| `TurnTrace` 17-kind event ring (`app/lib/runtime/trace.py:28-53,117,153`) | turn event stream | event kinds defined for the full chain (turn_start…turn_settled); emission sites are per-consumer (e.g. no_progress, dispatch) — registry is a debug/replay projection, not persisted |
| `GisTraceChain` 18 canonical stages (`app/lib/runtime/gis_trace.py:27-47,166`) | evidence chain Intent→UserOutput | dispatch stages written in `dispatch_tool` (`agent_pi_bridge.py:896-915`: TOOL_CALLS/ARGUMENTS/TOOL_RESULTS/MAP_MUTATIONS) |
| `GISRuntimeTrace` counters/stages (`app/services/gis_harness/trace.py:78,155`) | finalization/runtime-repair/action-intent counters | finalizer (`completion/pipeline.py:608-629`), runtime repair |
| `tool_metrics` (ADR-0044) | per-dispatch rows | `ToolDispatchService._record_event` (`tool_dispatch_service.py:1359`) |
| TaskTracker (in-memory, `app/services/task_tracker.py:145`) | /api/tasks steps | bridge (`agent_pi_bridge.py:634-666`) |
| Session events (`session_data_manager.append_event`) | `tool_failed` etc. | `tool_dispatch_service.py:540-544` |

Nothing here persists beyond process life except tool_metrics/session events (Redis) and DB transcripts — no durable end-to-end trace store exists.

---

## 11. Where state lives (census)

**DB (SQLAlchemy, `app/models/db_model.py`)** — only chat/product identity: `conversations :209` (owner_token `:220`), `messages :226`, `layers :50`, `analysis_tasks :91`, `users :24`, `cartography_templates :249/251`. **No DB tables for plans, workflows, artifacts, maps, or traces.**

**Redis (when `USE_REDIS`; else `MemorySessionStore` `session_data.py:19`; singleton `:659`)** — via `session_data_manager`: ref payloads (`ref:geojson-*`…), `map_state` keys (`_cartographic_observation`, `_cartographic_review`, `_cartographic_mutation_revision`, `_cartographic_context_observation`, `owner_token_digest`), session event log, map-action ACK events, aliases (incl. SessionPlan `session-plan` envelope + history), ArtifactRegistry records. TTL 4h (`session_data_redis.py:21-26`); L1 per-process 2s cache (`:36`). Active Pi turn registry (`webgis:pi:active_turn:{sid}`, `pi_turn_context.py:288-292`). Distributed session lock (`app/services/distributed_lock.py`).

**Disk (`DATA_DIR`, `MAPSPEC_STORAGE_DIR`)** — `mapspec.json` + `.fp`/`.rev` sidecars (`mapspec/store.py:39,148-152`), checkpoints+blobs (`checkpoint.py:135-143`), raster PNGs (`artifact_registry.py:295`), workspace snapshots (`workspace/snapshot.py:134`).

**Process memory (lost on restart/pod move)** — bridge dispatch-result + SessionPlan-SSE caches + executed-sets + `_active_turns` + `_gis_progress_trackers` (`agent_pi_bridge.py:210-244,941-944,1069`); harness registry + eval cache (`cartography_runtime.py` `_HARNESS_REGISTRY_LIMIT` use `:566-601`); planner + memo (`planner.py:267-269`, `planner_runtime.py:36`); ToolRegistry; TurnEvidence/TurnTrace/GisTraceChain registries; TaskTracker; TurnEventBuffer resume rings (`event_resume.py:304`); module globals `chat.pi_bridge`/`chat.engine`/`chat.registry` (`chat.py:56,93`).

---

## 12. Extension seams V4 can plug into (already present)

1. **Registry injection + descriptor contract**: `set_tool_registry` (`agent_pi_bridge.py:173`), `init_tools` module list (`app/tools/__init__.py:47`), rich descriptor kwargs (`registry.py:442-457`) incl. V3 `input_artifacts`/`map_mutations`/`security_tier`.
2. **Dynamic tool surface**: `pi_surface_for_spawn` / `registered_surface_names` / `compute_turn_active_tools` (`pi_native_surface.py:296,256,355`) with env kill-switch `PI_DYNAMIC_TOOL_SURFACE` (`:253`) and vendor seam `pi.setActiveTools` + `before_agent_start` systemPrompt rewrite (`index.mjs:214-246,323-345`).
3. **Single dispatch owner**: `ToolDispatchService` (all agent paths converge; legacy shares it via `tool_pipeline`), map-action minting, `JobOrigin`/cancel-token contextvars (`agent_pi_bridge.py:556-569`).
4. **SessionPlan seams**: `apply_tool_result` post-dispatch hook, `format_session_plan_projection` (per-turn prompt projection), `plan_graph`/`product_graph`/`product_lineage`/`action_intent` pure projections (`session_plan.py:222-265`).
5. **Finalizer triggers**: `maybe_finalize_map_product(session_id, reason=…, final_gate=…)` callable from any event source (already invoked from dispatch + turn-settle; observation POST also feeds it via drift guards `completion/pipeline.py:575-582`).
6. **Harness evidence API**: `record_cartographic_dispatch_evidence` shared seam (`cartography_runtime.py:461`), `HarnessEvaluator` thresholds dict (`evaluator.py:16`), `evaluate_with_evidence` pluggable reader seams (`pi_agent_harness.py:59-69`: RefResolver/MapSpecValidator/MapActionReader/CartographyStateReader).
7. **Trace vocabularies**: `TurnTrace` closed event kinds (`trace.py:28-53`) and `gis_trace.Stage` (`gis_trace.py:27-47`) are explicitly designed extension points with `record_stage`/`emit` APIs.
8. **Bridge pool**: session-affinity multi-subprocess routing (`agent_pi_bridge.py:2465-2494`) — V4 subagents could rent workers.
9. **Workflow compiler**: 15-stage deterministic pipeline with machine-readable reason codes exists and is unit-tested — currently evaluation-only; wiring it into the live chain is a seam swap at `webgis_map_intent`/planner, not new construction (`workflow_compiler.py:38-130`; consumers `app/evaluation/runner.py:377`).

---

## 13. Fragilities (for V4 design)

1. **Reasoning is re-derived from natural language every turn**: no durable reasoning ledger exists. Plan truth is the `gis_chapter` dump + row statuses; *why* (recipe choice, rejections, model rationale) survives only as prompt text and DB transcripts. Each turn the model must re-assemble intent from `[SessionPlan]`+verdict+env blocks (`pi_turn_context.py:147-195`); projections are recomputed per turn from stored rows (`session_plan.py:177-268`).
2. **Intent/planning is hardcoded lexical** (Chinese token tables, `_match_scope/_match_subject/_task_specific_intents` `intent.py:390-660`); planner is deterministic rules + memo (`planner.py:352`), and the "real" compiler is not in the live path (§5.4) — two divergent notions of "compile" coexist.
3. **Fragmented per-session state** across Redis keys, disk MapSpec, and process-local harnesses/caches; harness evidence persistence can be silently skipped on lock degradation (`agent_pi_bridge.py:843-857`) — validity ladder "starves" and self-heals only on the next event.
4. **Process-global mutable singletons** (`_tool_registry`, dispatch caches, `_active_turns`, `chat.pi_bridge/engine`): multi-pod safety relies on the Redis turn registry + HMAC turn tokens (`pi_tools.py:61-89`); dispatch caches and no-progress trackers (`agent_pi_bridge.py:941-944`) are per-process and reset on pod move.
5. **The Pi runtime itself is not reproducible from the repo**: `vendor/pi` is an empty (gitignored) build dir; missing build ⇒ silent ChatEngine fallback (`main.py:113-118`). The "chain" audited here only runs when `USE_NEW_AGENT` and a local vendor build exist.
6. **Vendor-owned LLM loop** = the server cannot see model requests/token usage; turn integrity is inferred from `agent_settled` (`agent_pi_bridge.py:2151-2236`); abort semantics are best-effort RPC (`:1396-1508`).
7. **Completion split-brain**: DAG completion (`SessionPlan` rows), map-product completion (`gis_chapter.map_product`), and cartography quality (`_cartographic_review`) are three separately-triggered, separately-stored verdicts stitched by the frontend finalizer; ADR-0081 states "DAG 完成 ≠ 地图成品完成" but reconciliation is via SSE payloads, not a unified decision record.
8. **Pervasive best-effort swallowing**: SessionPlan apply, finalization, evaluation, and trace writes all `except Exception: log` and never block the tool return (`agent_pi_bridge.py:686-716,717-754,750-754,896-930`) — evidence gaps are invisible to the model.
9. **No user-confirmation gate on the Pi path**: tier≥3 is rejected outright (`agent_pi_bridge.py:495-506`); there is no interactive confirm flow (legacy `confirm_destructive` exists off-path, `pi_tools.py:12` comment).
10. **Prompt-marker control plane is text**: dynamic tool activation travels inside the user prompt (`WEBGIS_ACTIVE_TOOLS`), requiring marker neutralization (`pi_turn_context.py:97-107`) and extension-side caps (`index.mjs:220-246`) — a fragile channel for a security-relevant projection.

## 14. Goal-prompt items that do NOT exist as assumed

- `app/services/gis_harness/map_completion.py` is a shim; logic lives in `completion/` (§8).
- No budget advisor in `app/lib/runtime/context.py` — that file is the correlation ContextVar; budgets are legacy-path only (`chat/context_budget.py`).
- `workflow_compiler` / `planner` divergence: compiler is not called by `planner.py` or the live chain (only `app/evaluation/*`).
- `ToolRegistry` canonical location is `app/tools/registry.py:387` (not under services).
- No WebSocket path for the chat chain (SSE POST only); `ws.py` is auxiliary.
- No persistent (DB/Redis) end-to-end trace store; all three trace registries are in-process rings.
