# BASELINE — G12-PI-NATIVE-INTEGRATION

## Identity

- Repo: `WindWang2/webgis-ai-agent` (origin verified via `git remote -v` + `gh repo view`)
- BASE_BRANCH: `master` (defaultBranchRef confirmed)
- BASE_SHA: `816705218a5aa6202bf15d745cb31cddb3437f44` (= origin/master at task start; local
  `master` ref was 11 commits behind — irrelevant, all work keyed to origin/master)
- Worktree: `webgis-worktrees/goal-g12-pi-native-integration-20261110T133034Z`
- Branch: `zcode/goal-g12-pi-native-integration-20261110T133034Z`
- Task snapshot claimed master=816705218a → **matches current origin/master exactly**.

## Pi upstream state (re-verified at execution time — 2026-11-10)

- `.gitmodules` → `https://github.com/earendil-works/pi.git`; gitlink at BASE_SHA =
  `f9a26365568127461c47a07a37dcb42c140a3ac5` = tag **v1.5.0** (verified `git describe`).
- Tags fetched at execution: **v1.5.1** = `dd30101cfc5b0609411dd80b6f93adbc14db14cf`
  (2026-11-10 00:07:37 +0100), **v1.5.2** = `b4afdddf44e059c67ccf5097e5317d913521b470`
  (2026-11-10 13:01:48 +0100, 22 commits after v1.5.1, incl. conversation-switch refetch
  fix, worker young-generation memory cap, views-beside-transcript P2 features).
- Pin decision: pending upstream-diff review (W01) — headline target is v1.5.1; v1.5.2
  will be evaluated for host-relevant risk before choosing.

## Core seam facts (personally verified at BASE_SHA)

### Extension (`app/extensions/webgis-tools/index.mjs`, 416 lines)
- Loads spawn dump `WEBGIS_NATIVE_TOOLS_PATH` (v2 `{tools, default_active, execute_proxy}`
  + v3 additive `abi`/`registry_generation`/`surface_fingerprint`/per-tool `spec`).
- Registers every dumped tool via `pi.registerTool({name,label,description,promptSnippet,
  parameters, execute})`; execute → HTTP `POST {WEBGIS_API_BASE}/pi-tools/execute` with
  `X-Pi-Bridge-Secret`, body `{toolCallId,name,arguments,turnToken[,toolFingerprint]}`.
- Per-turn surface via `pi.setActiveTools` driven by `[WEBGIS_ACTIVE_TOOLS:[...]]` marker
  on the turn prompt; reset to `surface.defaultActive` every `before_agent_start`
  (S2's "surface reset" anchor). `MAX_ACTIVE=48` hard ceiling; `alwaysActive` =
  FALLBACK_NATIVE + `webgis_execute`.
- `webgis_execute` = long-tail proxy; wrap-rejects registered names.
- Identity rewrite in `before_agent_start` (GIS_IDENTITY; vendor opening strings listed
  per pin — **must be re-checked against new pin's system prompt**, W01/W02 risk).
- No native exposure/namespace/annotations/deferred handling yet (S2 gap confirmed).

### RPC client (`app/services/chat/pi_rpc_client.py`, 770 lines)
- Spawn: `node <rpc-entry> --mode rpc --no-session --no-builtin-tools --extension ...`
  Entry candidates: `vendor/pi/packages/coding-agent/dist/bundle/rpc-entry.js` (preferred),
  `dist/rpc-entry.js` (fallback).
- Env allowlist (F16); `PI_OFFLINE=1`, `PI_SKIP_VERSION_CHECK=1`, bridge secret injected.
- Model config written to `.pi/agent/models.json` + `settings.json` at spawn from
  `app.core.config.settings.LLM_*` (**runtime overrides NOT consulted — S5/#2061 root cause**).
  `PI_PROVIDER`+`PI_MODEL` env → best-effort `set_model` after readiness.
- Surface dump written per spawn; `spawn_surface_generation` read back (ADR-0286 D4).
- stop(): terminate → sleep(0.5) → kill → wait(2). atexit hook covers process exit (S6).
- `request()` flattens data into the request object (`{id, type: command, **data}`).

### Native surface (`app/services/chat/pi_native_surface.py`, 953 lines)
- `resolve_pi_tool_call()` → native | execute | reject (+test-only passthrough).
- `registered_surface_names()`: model-visible, non-tier-3, non-EXTERNAL_UNAVAILABLE;
  ABI on → evict DESTRUCTIVE + per-tool schema > PI_SPEC_MAX_SCHEMA_BYTES (16KB default).
- `pi_surface_for_spawn()`: v2 shape + v3 additive (per-tool `spec` = PiToolSpecV1 dump).
- `compute_turn_active_tools()`: V3/V4 selector + situation qualification (ADR-0224) +
  32KB active-surface byte budget (ADR-0253) + SurfaceReceipt mint; abstain path keeps
  native front door. Distribution tools re-pinned after every trim (product path).
- `apply_surface_byte_budget()` — schema bytes per registry.schema_size.

### Dispatch boundary (`app/agent_pi_bridge.py` 3482L + `app/api/routes/pi_tools.py`)
- Route `/pi-tools/execute`: bridge secret → `verify_turn_token` (HMAC sid/tid/iat,
  15 min) → `is_active_pi_turn` live-turn check (409 TURN_CONTEXT_INACTIVE) →
  `dispatch_tool`.
- `dispatch_tool` → bind runtime ctx → `_dispatch_tool_bound`: resolve_pi_tool_call →
  `stale_surface_gate` (fingerprint echo vs SurfaceReceipt; scope gate for bare names) →
  session plan slot → existence/tier gates → (further gates read in review pass).
- Abort: session-scoped, TOCTOU-guarded, token snapshot cancel, `fail_pending_ids`
  snapshot-only, abort source recorded (user/system/policy) (F03/F5/CONC-F1/F24).
- Pool: `PiBridgePool.bridge_for_session` stable affinity (S7).

### Event mapper (`app/services/chat/pi_event_mapper.py`, 419 lines)
- `message_update` (text/thinking deltas; toolcall_end → `tool_call` SSE),
  `tool_execution_start/end` → step_start/step_result/step_error (cache rendezvous),
  `agent_end` NOT final, `agent_settled` → task_complete (+map_product,
  goal_satisfaction bounded), auto_retry, compaction_start/end → content notes.
- `_base_step_payload` = {task_id, step_id, session_id} — **no parentToolCallId** (S8 gap).

### Tool spec ABI (`app/services/chat/pi_tool_spec.py`, 467 lines)
- PiToolSpecV1: deterministic canonical JSON fingerprint, tier/side-effect/resource
  classification, schema keyword whitelist (fail-honest; never rewrites constraints),
  description truncation ≤512 chars with findings, over-budget → whole-tool eviction.

### Input gate (`app/services/chat/pi_input_gate.py`)
- `validate_pi_tool_arguments`: unknown-field, required, structural issues; oversized
  exemption parity with registry #699 bypass.

### Model config (`app/services/chat/model_config.py` + `execution_engine.py`)
- `update_config()` (engine) + `set_runtime_override()` (single resolution point) —
  but Pi spawn reads `Settings` only → #2061 (S5 confirmed in code).

## Rules honored

- Read-only on main workspace + master; separate worktree/branch (done).
- ≤3 subagents: 3 research agents now; will free before independent review.
- Heavy builds: `-j2`/lockfile discipline via `webgis-heavy-stage.lock` when applicable.
- No CI watching; local verification only.

## Open questions being resolved by research agents

- PR/issue dedup state (#2061 fix ownership; sibling G13–G16 PRs; any in-flight
  vendor/pi upgrade).
- Upstream v1.5.1 exact surface (tool_search/codemode registration, deferred exposure,
  nested-call event shapes, reload/shutdown semantics) + v1.5.1 vs v1.5.2 pin choice.
- Repo conventions (ADR numbering, docs to update, test commands).
