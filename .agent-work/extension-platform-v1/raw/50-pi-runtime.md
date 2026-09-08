# 50 — Pi Runtime Integration / Active Tool Surface / GIS Harness

Repo: `/home/kevin/projects/webgis/webgis-ai-agent-extension-platform-v1` (all paths relative to repo root).

## 1. What Pi is and its tool-surface mechanism

**Pi = vendored open-source coding-agent runtime (earendil-works/pi), wrapped, never forked.**
- Git submodule: `.gitmodules` → `vendor/pi` = `https://github.com/earendil-works/pi.git` (currently uninitialized — `vendor/pi/` is empty; build expected at `vendor/pi/packages/coding-agent/dist/rpc-entry.js`, `app/services/chat/pi_rpc_client.py:39-42,269-275`).
- ADR `docs/adr/0077-wrap-vendored-pi.md` (entire): host GIS on "official Pi extension + spawn-flag + RPC surface"; do NOT fork `vendor/pi`, do not add Pi-core Plan types.
- Spawned as a Node subprocess at API lifespan (`app/main.py:102-118`), gated by `USE_NEW_AGENT` (default True, `app/core/config.py:120-121`); on spawn failure the app falls back to the legacy ChatEngine.
- Spawn args: `node rpc-entry --mode rpc --no-session --no-builtin-tools` + one `--extension <path>` per extension (`pi_rpc_client.py:290-295`). `--no-builtin-tools` removes bash/read/write/edit — "Pi is the host, not a coding agent" (`pi_rpc_client.py:287-293`).
- Transport: JSON-RPC over stdin/stdout, one line per message; deep core `PiRpcClient` (`pi_rpc_client.py:84`, multiplexing `request()` at :557, bounded reader :525); event→SSE mapping in `app/services/chat/pi_event_mapper.py`.
- Orchestrator `PiBridge` (`app/agent_pi_bridge.py:1206`): turn lock w/ owner-checked leases (:1184-1292), abort (:1396), respawn (:1326). Pool `PiBridgePool` with stable session affinity `md5(sid) % N`, `PI_BRIDGE_POOL_SIZE` (`agent_pi_bridge.py:2465-2494,2525`).

**Tool surface = a dynamic PROJECTION of the live Python ToolRegistry, dumped to a file at spawn, registered by the JS extension, narrowed per-turn by an out-of-band prompt marker.**
- Frozen native names: 7 `NATIVE_TOOL_NAMES` + `EXECUTE_PROXY_NAME = "webgis_execute"` (`app/services/chat/pi_native_surface.py:20-31`).
- Spawn dump v2 `pi_surface_for_spawn()` (`pi_native_surface.py:296-318`): `tools` = native 7 (with promptSnippet) + every "registered surface" tool marked `dormant: true`; `default_active` = the native 7; `execute_proxy`. Schemas are taken LIVE from the registry (`native_tools_for_pi` :171-199 fail-fast if a native is missing from the registry). Written atomically to `WEBGIS_NATIVE_TOOLS_PATH` env before spawn (`pi_rpc_client.py:282-285`, `dump_surface_file` :349-352).
- Registered superset selection `registered_surface_names()` (`pi_native_surface.py:256-273`): `model_visible`, not `EXTERNAL_UNAVAILABLE`, tier < 3 and effective_security_tier < 3.
- Extension `app/extensions/webgis-tools/index.mjs` (the ONLY extension, wired at `app/main.py:111-112`): reads the dump (`loadNativeTools` :53-76), `pi.registerTool(...)` for each (:253-265 — all execute via HTTP POST back to the backend), registers `webgis_execute` proxy (:267-320), and on `before_agent_start` (:322-347) rewrites the system prompt to GIS identity and applies the per-turn active list.
- Per-turn dynamic activation (ADR-0103 Phase 3): Python computes `compute_turn_active_tools()` = `DynamicToolSurface.select()` (10-30 tools, `app/services/chat/tool_surface_v3.py:14-20,37-40`) + always the native 7 (`pi_native_surface.py:355-401`); injected into the turn prompt as `[WEBGIS_ACTIVE_TOOLS:[...]]` marker (`app/services/chat/pi_turn_context.py:227-246,27`); the extension parses the LAST marker and calls `pi.setActiveTools(active)` with a hard `MAX_ACTIVE=48` ceiling (`index.mjs:228-248,333-343`). Vendor semantics: "changes take effect on the next agent turn" (`pi_native_surface.py:243-248`).
- Call classification `resolve_pi_tool_call()` (`pi_native_surface.py:73-150`): `native` (direct), `execute` (proxy or registered-surface direct name → ToolRegistry pipeline), `reject` (unknown → "discover via list_available_tools").

## 2. Skill system

Two unrelated "skills" concepts coexist:

**(a) GIS skills (`app/skills/`) — runtime-callable, registry-integrated:**
- `.md` files: YAML frontmatter `name`/`description` + markdown body (`app/skills/heatmap.md:1-3`); loaded into in-memory `_md_skills` (`app/tools/skills.py:106-153`). Body injected as a system message on the legacy engine (`app/services/chat/execution_engine.py:589-599,787-800`); listed via API `chat.py:1850-1851`. NOTE: the Pi-path turn prompt does NOT inject md-skill bodies — `bind_turn_prompt` carries plan/surface/active-tools blocks only (`pi_turn_context.py:147-195`).
- `.py` files: must define `register_skills(registry)` (or `register`); exec'd with restricted builtins (`_load_single_skill`, `app/tools/skills.py:412-436`; whitelist `_restricted_skill_builtins` :377-409). `load_skills()` scans the dir at startup (`app/tools/__init__.py:75-76`).
- `create_new_skill` — tier-3 meta tool (RCE-class; AST deny-list + dry-run; disabled unless `ALLOW_DYNAMIC_SKILLS=true`) (`app/tools/skills.py:274-366`).
- `refresh_skill_surface` — tier-3 tool; hot-reloads the REGISTRY layer (new skills immediately callable via `webgis_execute`) and truthfully reports the native schema layer as frozen-at-spawn; never auto-respawns (`app/tools/skill_surface_refresh.py:34-90`; ADR-0100 §4).

**(b) `skills-lock.json` + `agent/skills/` — coding-assistant prompt skills, NOT GIS runtime:**
- Format: `{"version": 1, "skills": {"<name>": {"source", "sourceType": "github", "skillPath", "computedHash"}}}` (skills-lock.json:1-9). Sources: `mattpocock/skills`, `garrytan/gstack`. The `agent/skills/` tree holds the cloned SKILL.md files (tdd, research, wayfinder, ...). No Python code loads these; they are dev-workflow prompts for the human/CLI assistant, not part of the FastAPI runtime.

## 3. Harness overview

**(a) GIS Harness deterministic product runtime — `app/services/gis_harness/`** (ADRs 0079/0080/0088/0091-0103):
- No classic finite state machine; state truth lives in **SessionPlan** (ADR-0076 single plan truth). Everything else is a pure derived projection:
  - `workflow_compiler.py:1-27` — 12→15-stage deterministic compile pipeline (normalize_intent → … → produce_map_product_plan / completion_contract). Zero LLM, zero IO; "compiles, never executes".
  - `plan_graph.py:1-33` — dependency-aware analysis DAG derived from MapProductPlan; node states merged from SessionPlan rows; **"LLM does not maintain DAG state"** — advanced deterministically by SessionPlan + tool-result bindings (`_mark_progress`).
  - `product_graph.py:42-52` — node statuses `pending/ready/failed/done` (+ fallback equivalence).
  - `planner_runtime.py:1-53` — process-wide shared planner, bounded memo, no session state.
  - `tool_surface.py:1-33,99` — `compile_tool_surface(plan)` → phase model `planning/data/analysis/assembly/final` with preferred_tools/allowed_domains (pure projection, feeds the turn prompt "tool surface hint" via `pi_turn_context.py:198-224`).
  - `trace.py:1-24` — bounded in-memory event rings/counters; never enters LLM context.
- Harness-native tools registered into the registry (`gis_harness/tools.py:357`): `webgis_map_intent` (tier=1, :370), `webgis_map_product` (tier=2, :537), `webgis_component_update` (:1177), `webgis_component_catalog` (:1548), `webgis_world_state` (:1667).

**(b) `PiAgentHarness` — evidence-driven evaluation loop — `app/lib/harness/pi_agent_harness.py:279`:**
- Per-session bounded registry `cartography_runtime._get_session_harness` (`app/services/cartography_runtime.py:81-118`), built with real SessionStore ref resolver, MapSpec validator, cartography state reader.
- Records `ToolCallEvent`s and map actions from the bridge (`agent_pi_bridge.py:587-619,870-879`); computes evidence-tiered verdicts (MapSpecValidity, ref resolution, camera/ACK convergence; `pi_agent_harness.py:1-24`); verdict block is fed back into the NEXT turn prompt as `cartography_context` (`agent_pi_bridge.py:2051-2053` → `bind_turn_prompt`). Interactive closure recomputed server-side, "never trust hint alone" (:65-74).

**Tool invocation path (Pi turn):**
1. `stream_prompt` mints turn_id, HMAC turn token (`pi_turn_context.issue_turn_token` :39-58), registers active turn (`agent_pi_bridge.py:1079`), binds prompt via `bind_turn_prompt` (:2051).
2. Pi extension tool call → `POST /pi-tools/execute` (`app/api/routes/pi_tools.py:66-`): `verify_bridge_secret` (X-Pi-Bridge-Secret, hmac.compare_digest :47-54) + `verify_turn_token` + `is_active_pi_turn` (409 if turn superseded).
3. `dispatch_tool` (`agent_pi_bridge.py:427`): classify via `resolve_pi_tool_call` → registry existence check (:481-493) → **tier≥3 rejection** (:495-506) → `ToolDispatchService.dispatch` (:570) wrapped in cancellation token + `JobOrigin`.
4. `ToolDispatchService` (`app/services/tool_dispatch_service.py:296`): dedup (executed_tools set), per-session wave gate + global semaphore (`_SessionWaveGate` :247-293), ref minting to session_data, LLM result slimming, event log, WS broadcast.
5. Result cached per (session, toolCallId) (`cache_dispatch_result` :299) → SSE adapter reads it on `tool_execution_end` and emits `step_result` with `geojson_ref`; SessionPlan `apply_tool_result` appends plan SSE (:699-733); harness/map-action evidence recorded (:807-879).

## 4. Existing extension / seam points (runtime side)

- `set_tool_registry()` — inject/replace the live registry; recompiles the Runtime Manifest on injection (`agent_pi_bridge.py:173-185`). Runtime Manifest = immutable compiled snapshot of registry sources with content fingerprint (`app/lib/gis/runtime_manifest.py:1-30`); refresh via `refresh_runtime_manifest()`.
- Skill drop-in: add `.py` (registers tools) or `.md` (prompt skill) into `app/skills/`, then `load_skills` / `refresh_skill_surface`.
- Pluggable semantic retrieval: `TOOL_RETRIEVAL_SEMANTIC="module:callable"` env injection into DynamicToolSurface V3 (`tool_surface_v3.py:37-40`).
- Feature flags/env: `USE_NEW_AGENT`, `PI_DYNAMIC_TOOL_SURFACE=0` (reverts to frozen 7+proxy, `pi_native_surface.py:253`), `PI_BRIDGE_POOL_SIZE`, `PI_PROVIDER`/`PI_MODEL` (set_model RPC, `pi_rpc_client.py:328-335`), `PI_TURN_TOTAL_TIMEOUT`/`PI_EVENT_STREAM_TIMEOUT`/`PI_HEARTBEAT_INTERVAL` (`agent_pi_bridge.py:85-101`).
- Extension registration seam: `--extension` paths list passed to `get_pi_bridge(extension_paths=[...])` (`app/main.py:111-112`, `agent_pi_bridge.py:2515-2541`) — multiple extensions are structurally supported.
- Descriptor V3 contract fields + registry `descriptor()` projection (`docs/adr/0103` §Decisions 1-2; `app/tools/registry.py:625-666`).
- Subagents: `spawn_subagent` tier-2 tool (`app/tools/subagent.py:22`) → `SubagentDispatcher` runs an isolated ChatEngine micro-session (tier-1 + whitelisted domains; no spawn_subagent/propose_plan visible; recursion depth ≥2 fails, `app/services/subagent.py:52-54,84-100`); ADR-0100 §3 links child CancellationTokens.

## 5. Constraints an extension MUST respect (do not bypass)

1. **Do not fork vendor/pi.** Only the official extension API (`pi.registerTool`, `pi.on("before_agent_start")`, `pi.setActiveTools`), spawn flags, and RPC (`docs/adr/0077`; `skill_surface_refresh.py:3-9`).
2. **ToolRegistry is the single execution truth.** The Pi surface is a projection only; schemas must be taken live from the registry, never hand-written or compressed into a second form (`pi_native_surface.py:243-253,292-293`; `tool_surface_v3.py:17-20`). No second registry may execute tools (ADR-0101 Decision 2).
3. **Tier-3 never reaches the model-visible surface or the Pi bridge** (SEC-F1 ContextVar chokepoint `app/tools/registry.py:1208-1215`; bridge rejection `agent_pi_bridge.py:495-506`; superset filter `pi_native_surface.py:270-271`).
4. **Native schema layer is frozen at spawn.** New tools are callable only via `webgis_execute` until a worker respawn; never auto-respawn (kills active turns) (`skill_surface_refresh.py:70-88`).
5. **The active-tools marker is an out-of-band control plane**: user text must be neutralized against marker forgery (`pi_turn_context.py:97-107`); extension enforces `MAX_ACTIVE=48` and registered-superset membership (`index.mjs:236-242`); failures degrade to previous surface, never block the turn (`pi_native_surface.py:368-401`).
6. **Every callback must carry the signed, live turn capability** — bridge secret header + HMAC turn token + active-turn check; never route by mutable "current session" (`pi_turn_context.py:1-7`; `pi_tools.py:66-`).
7. **Determinism/no-second-truth invariants**: planner, plan graph, tool surface, runtime manifest are pure projections — they must not persist state or write back to the registry/SessionPlan (`planner_runtime.py:19-27`; `plan_graph.py:26-28`; `runtime_manifest.py:24-26`). SessionPlan (ADR-0076) and MapSpec store stay the plan/map truth.
8. **Cancellation/turn-ownership discipline**: use `abort_active_pi_turn` seam, session-keyed `_active_turns` table, lease-based bridge locks (ADR-0100 §1; `agent_pi_bridge.py:1184-1292`); subagent never sees meta tools (recursion guard).
