# 05 — Context/Memory Runtime Audit (Wave 5 gap map)

Repo: webgis-ai-agent-harness-v4 @ 16d1c70. Read-only audit; all paths relative to repo root.
Headline: **the context policy today is measure-and-advise, not execute**. The only executed
operations are legacy-engine history DROP_OLDEST (turn-granular, 6000-token soft budget) and
intra-turn tool-result folding. CONDENSE/SUMMARIZE/RELOAD_REF have no executor; OFFLOAD_REF
already exists at tool-dispatch time (not as a context operation); `app/lib/runtime/context.py`
is **not** a context manager — it is a correlation-ID ContextVar holder.

## (a) Current context pipeline map

### A1. `app/lib/runtime/context.py` — correlation, NOT context management
- `RuntimeContext` frozen dataclass (context.py:30-62): request_id/session_id/turn_id/run_id/project_id.
  Propagated via ContextVar (`_CURRENT`, context.py:65-67); `bind_runtime_context` merges parent fields
  (context.py:98-125). No tokens, no layers, no trimming, no budget.
- Callers bind/consume it only for correlation: app/main.py:322-328 (request), app/api/routes/chat.py:612,745,1051,1122
  (SSE/chat), app/agent_pi_bridge.py:530,1640,1962 (turn/run), app/services/jobs/worker.py:410 (durable jobs),
  app/services/tool_metrics.py:248-249, app/services/chat/model_routing_bridge.py:113-116 (evidence stage),
  app/tools/upload_tools.py:30 (ownership).
- **Gap**: Wave-5 "context operations" cannot live here under the current name without a collision; a new
  module (e.g. `app/lib/runtime/context_policy.py`) is the clean seam.

### A2. Legacy engine prompt assembly (per LLM round)
Entry: `ExecutionEngine._compose_request_messages` (app/services/chat/execution_engine.py:692-720)
→ `ChatContextAssembler.assemble` (app/services/chat/context_assembler.py:206-467). Message head order:
1. system prompt + `[环境感知]` env summary + session overview + project block
   (context_assembler.py:276-325; env built by `build_map_state_summary` app/services/chat/context_builder.py:159-286;
   project block via `ProjectContextCache` fingerprint LRU, context_assembler.py:55-109).
2. plan block — `render_plan_block` (context_assembler.py:329-335), subagents excluded via `include_plan_block` (#436).
3. `[CARTOGRAPHY_VERDICT]` bounded block (context_assembler.py:169-204,345-350) + project memory block (ADR-0069,
   context_assembler.py:112-142,358-366).
4. `[最近对话上下文]` extraction (context_assembler.py:368-370 ← context_builder.py:292-341; last user 200 chars /
   assistant 300 chars + ref cursors; covers only turns *before* the history window).
5. History: `fold_intra_turn_tool_results` then `truncate_history_by_budget` + truncation notice
   (context_assembler.py:375-380 ← app/services/chat/context/history_compression.py:140-192,84-121,77-81).
6. Token sum + budget measurement (context_assembler.py:386-459): `measure_assembled_context`
   (context_budget.py:208-254) + `GisBudgetAdvisor.advise` (context_budget.py:266-349) → `budget_report` dict on
   `ContextAssemblyResult` (context_assembler.py:145-156). **Advice is logged (`[CONTEXT-BUDGET]` warning,
   context_assembler.py:453-457) but nothing executes it.**
- Layers actually present: immediate conversation (history), workflow state (plan block), map state (env block),
  artifact summaries (last-analysis + ref lines), project long-term memory (memory block), science evidence
  (verdict block). Tool-result *references* appear only inside history text / env "近期工具调用" lines.

### A3. Pi path (per-turn prompt, Pi subprocess owns its own history)
- `bind_turn_prompt` (app/services/chat/pi_turn_context.py:147-195) → `attach_turn_context`
  (pi_turn_context.py:110-144): user message, then cartography block, SessionPlan projection, env block
  (`_build_environment_turn_context` app/api/routes/chat.py:312-330, frontend snapshot only), tool-surface hint,
  `[WEBGIS_ACTIVE_TOOLS:...]` marker, HMAC turn marker last.
- Pi subprocess session/history management is vendored/out-of-tree (`vendor/pi` empty submodule). Python-side
  history compression does **not** apply to Pi's internal history; Python only slims each tool result handed back.
- `app/lib/harness/pi_agent_harness.py` is the **evaluation** harness (evidence/verdict/ref-resolution metrics,
  pi_agent_harness.py:1-16), not runtime assembly. It bounds its own argument projections (FIFO byte budget,
  pi_agent_harness.py:242-276) and scans ref cursors (REF_CURSOR_PATTERN pi_agent_harness.py:47).

### A4. Tool-result payload path (both engines)
`ToolDispatchService` (app/services/tool_dispatch_service.py):
- ref mint: `session_data_manager.store(session_id, target_data, prefix="geojson")` (:583), uncertainty (:594),
  heatmap (:606); raster products use `ref:raster/<id>` registered at :1123-1146.
- LLM payload: `slim_tool_result(result, result_str, geojson_ref)` (:830) with a cheap `_estimate_json_bytes` gate
  so ≤4096-byte payloads skip full dumps (:809-829).
- `slim_tool_result` (app/services/llm_result_formatter.py:264-329): summary branch keeps `summary`+`ref_id`+
  preserved meta (:265-282); oversized non-summary results → `geojson_summary` (feature_count, typed properties,
  samples, "请将 geojson 参数设为 ref:..." hint :309-319); hard caps `MSG_MAX_CHARS=2500`, `VALUE_MAX_CHARS=80`
  (llm_result_formatter.py:18-19); `_serialize_under_budget` total gate (#439).
- Pi bridge returns `llm_payload` as tool content and slims `details` separately
  (app/agent_pi_bridge.py:364-399 `_slim_pi_details_payload`: strips geojson/image, keeps ref fields).
- Tool-result stores: `app/services/session_data.py:77-144` — in-process store, ref `ref:<prefix>-<hex16>` (:86),
  **LRU + byte-budget eviction** (capacity / SESSION_STORE_MAX_BYTES=50MB, :88-125) which also drops alias+descriptor
  (:120-124); alias map (:223-231); `get_ref_descriptor` (:530), `ref_exists` (:542). Redis variant
  `app/services/session_data_redis.py` with unavailable-ref sentinels (dispatch :611-618).
- Later reference: model passes `ref:geojson-...`/alias as a parameter; tools resolve via
  `session_data_manager.get` (session_data.py:267); evaluation-grade resolution incl. type contracts at
  `app/lib/harness/ref_resolver.py:38-106` (MALFORMED/NOT_FOUND/TYPE_MISMATCH statuses; cheap descriptor path :60-80).

### A5. History persistence
- DB conversations/messages via `AsyncHistoryService` (app/services/history_service_async.py):
  windowed tail load `_HISTORY_WINDOW=200` messages (:126,136-205) with a 6000-token early check (:188);
  `_strip_orphaned_tool_calls` on replay (:86-94, #376). No LLM summarization of old turns anywhere;
  the only LLM-summary-adjacent feature is cheap title generation (`LLM_TITLE_MODEL`, app/core/config.py:102).
- Session-scoped "memory": env/event log + refs (Redis/session store), project cartographic memory
  (ADR-0069 block), plan state. Repo-root `MEMORY.md` is dev-process notes, not runtime memory.

### A6. Overflow behavior today
- Planning: `LLM_CONTEXT_WINDOW` default **None** (app/core/config.py:100,227-234) → `plan_budget` plans
  against a conservative 8192 (context_budget.py:147-148) with `LLM_MAX_TOKENS=16384` reserved capped at
  window/2 → usable=4096, while history soft budget alone is 6000 → **chronic false `over_budget` violations
  when the window is unconfigured** (measure-only, so impact is log noise + eval metrics, app/evaluation/runtime_metrics.py:292-305).
- Pre-flight: model routing skips models whose window < est×0.9 (app/services/chat/model_runtime/routing.py:117-119);
  `context_too_large` marks capability mismatch, no cooldown (health.py:106-118).
- Reaction: provider 400/413/422 with context hints → `FailureKind.CONTEXT_TOO_LARGE` (provider.py:44-62);
  llm_client does **no** mid/post-send retry (app/services/chat/llm_client.py:181-182). There is **no
  deterministic overflow recovery**: no re-trim + retry, no graceful degradation — the turn fails honestly
  (`HonestTurnFailure` family, app/services/chat/turn_recovery.py:24-40). Tool-layer mitigations that keep
  payloads out of the prompt in the first place: dispatch slimming, `network_tools` explicit last-resort trim with
  honest notice (app/tools/network_tools.py:145-256, "never silently truncated"), `temporal_tools` 40k-char
  feature truncation (app/tools/temporal_tools.py:87-99), user_action JSON 1200-char cap
  (context_builder.py:63,276-284).

## (b) Per-operation gap table

| Operation | Exists? | Where / evidence | Gap for Wave 5 |
|---|---|---|---|
| KEEP | Partial | Implicit default: everything not trimmed is kept; advisor `_UNTOUCHABLE` = RESERVED_OUTPUT/USER_PROMPT/SYSTEM_INSTRUCTIONS (context_budget.py:282-286) | No per-item KEEP pinning. Safety/verdict facts inside HISTORY have no pin: `truncate_history_by_budget` and `fold_intra_turn_tool_results` are content-blind — a tool message carrying a Tier-3 confirmation echo or cartography verdict can be folded/dropped. |
| CONDENSE | Advice only | `GisBudgetAdvisor` returns `action:"condense"` for TRACE_SUMMARIES/DATA_PROFILE/ALGORITHM_METADATA/CARTOGRAPHY_METADATA/SESSION_PLAN/MAP_STATE (context_budget.py:270-279); "执行权在调用方组件" (context_budget.py:257-263) | **No executor anywhere.** Schema side has real compression (`app/services/chat/schema_compression.py:1-135`, none/compact/minimal) but it is not wired to advice. Bounded projection primitives exist (`app/services/chat/context_projections.py:36-115`) but only layer/ref/tool lists are projected; no trace/plan/map-state condenser consumes the advice. |
| OFFLOAD_REF | Executed at dispatch, not as policy | Every geometry/raster/heatmap result stored as ref before the LLM sees it (tool_dispatch_service.py:583,594,606,1123-1146); LLM payload slimmed ≤2500 chars with ref hint (llm_result_formatter.py:264-329); advisor "offload_ref" for TOOL_RESULTS is advice-only (context_budget.py:271) | Offload is unconditional per-tool-shape, not budget-triggered; no offload of *already-inline* text (e.g. giant tool JSON in legacy history, user uploads in messages). No re-offload pass over an over-budget assembled prompt. |
| SUMMARIZE | **Missing** | No LLM summarization of old turns; only extraction snippets `build_last_analysis_context` (context_builder.py:292-341: last user 200 / assistant 300 chars) and one-line fold placeholder (history_compression.py:133-137) | No summarizer service, no fingerprinted summaries, no cache of "condensed turn" artifacts. Dropped turns are replaced by a static count notice (history_compression.py:77-81), not a summary. |
| DROP_OLDEST | Executed (legacy engine only) | `truncate_history_by_budget` — turn-granular reverse accumulation, 6000-token budget, min 2 turns (history_compression.py:10-11,84-121); intra-turn fold keeps last 8 tool results after 12 (history_compression.py:130-137,140-192); DB tail window 200 msgs (history_service_async.py:126) | Not applied on the Pi path (Pi owns its history); no priority beyond recency (no importance/safety weights); dropped-count notice is the only traceability; min-turns exemption can itself blow the budget (#980 note history_compression.py:124-129). |
| RELOAD_REF | Partially | Refs resolvable while resident: session store get/alias/descriptor (session_data.py:267,530,542,223); typed resolution ref_resolver.py:38-106; truncation notice tells the model refs stay usable (history_compression.py:77-81) | **Silent ref loss**: store LRU/byte eviction permanently deletes payload+alias+descriptor (session_data.py:117-125) — later tool calls fail with "引用的 ref/alias 不存在" (prompt.py:27). No reload-from-durable-store (workspace/artifact) path, no ref→artifact spill, no eviction-aware hint to the model, no fingerprint to detect payload drift on reload (only ref *revisions* counter, session_data.py:130,180-181). |

## (c) Payload-by-ref status

Already by-reference (good):
- All vector/raster/heatmap tool results: stored in session store, LLM sees `ref_id` + summary only
  (tool_dispatch_service.py:583-606,830; llm_result_formatter.py:265-282,309-321). Frontend mounts via ref
  (agent_pi_bridge.py:199-200 comment).
- Artifact contracts: `ArtifactContract.storage_ref` "不内联载荷" + fingerprint/profile refs
  (app/lib/data/artifact_contract.py:173-198,566).
- Project context: fingerprint-keyed cached *rendered text* (context_assembler.py:55-109); bounded projections
  with content fingerprints (`content_fingerprint` context_projections.py:61-68, `ProjectionCache` :128-197).
- Map/plan/verdict blocks: bounded projections, never raw dicts (context_projections.py:1-15;
  pi_turn_context.py:121-125; format_session_plan_projection).
- Harness evaluation: argument projections FIFO-bounded (pi_agent_harness.py:242-276); no_progress fingerprints
  params deterministically (app/services/chat/no_progress.py:34-59).

Still inlined / at risk:
- Small (≤4096-byte) tool results are sent as full JSON (tool_dispatch_service.py:809-818) — fine, but they then
  live in history forever and count against the 6000 history budget with no later offload.
- Non-GeoJSON big keys (data/rows/chart) only gated by `_serialize_under_budget` after the fact
  (llm_result_formatter.py:323-325); pure string results hard-truncated at 2500 chars (:327-329).
- user_action events: bounded at 1200 chars but truncated content is still inline each turn (context_builder.py:276-284).
- Legacy-engine history replays keep folded *placeholders* but never re-inline/reload; oversized `summary` strings
  (up to 600 chars/value clamp, llm_result_formatter.py:58) accumulate.
- Pi `details` payload capped 64KB (agent_pi_bridge.py:364-399) — front-channel only, not prompt channel.

Summaries/fingerprints: fingerprint infrastructure exists (`content_fingerprint`, ref revisions, MapSpec/
cartographic fingerprints) but **tool-result summaries handed to the LLM are not fingerprinted**, so a future
RELOAD_REF cannot verify the payload behind an old summary is unchanged (only live refs have revisions).

## (d) Token estimator status
- Single shared estimator `_estimate_tokens` (history_compression.py:18-46): CJK/non-ASCII 1 char ≈ 1.5 tokens,
  ASCII 4 chars ≈ 1 token, +1; encode-diff CJK counting (#729), per-string memo capped 8192 entries
  (memo key = `hash(content)` — id()-based intent, actual `hash()` of str; collision-safe-ish but unbounded-false-hit
  risk is nil for str). zh/en handled; deliberate over-estimate bias ("不长期偏离 30%... 宁可高估").
  No tiktoken anywhere (grep: 0 hits).
- Consumers: context_budget (all categories), assembler totals (context_assembler.py:386-397 incl. CJK-aware
  tools_payload; legacy chars/4 fallback :395-397), routing `_estimate_context_tokens` handles multimodal text
  parts at 16 tokens each (model_routing_bridge.py:30-56), history DB early check (history_service_async.py:185-188).
- Error guards: non-str coerced via json.dumps/str; routing wrapper returns 0 on failure (router treats as unknown).
- **Gap**: no accuracy test vs any real tokenizer; no per-model calibration; message-level overhead only in
  `_message_tokens` (+4, history_compression.py:49-55) — `measure_assembled_context` sums content-only
  (context_budget.py:229-249), undercounting tool_calls JSON. No synthetic 32K/64K/128K/256K tests exist
  (grep across tests/: 0 hits for 32768/65536/131072/262144-scale context fixtures).

## (e) Overflow + safety specifics (audit asks 6/7)
- Overflow: measure→log only (context_assembler.py:453-457); routing capability-skip (routing.py:117-119);
  honest turn failure on provider 400 (provider.py:44-62, llm_client.py:181-182, turn_recovery.py:24-40).
  Tool-layer pre-emptive slims (network_tools.py:145-256, temporal_tools.py:87-99) are the only executed
  "recovery", and they are per-tool, not deterministic end-to-end policy.
- Safety/confirmation facts:
  - System-level safety boundary text: SYSTEM_PROMPT (prompt.py:35-42) and `[安全]` line in env block
    (context_builder.py:194) — rebuilt every turn as system content; assembler never trims system blocks and the
    advisor marks SYSTEM_INSTRUCTIONS untouchable (context_budget.py:282-286). Safe.
  - Tier-3 destructive confirmations are HTTP-request-scoped (`confirm_destructive` chat.py:1999-2011;
    plan_mode.py:772-780,998-1008 via `confirm_tier3()` contextvar) — **not stored in conversation at all**, so
    trimming cannot drop them, but there is also no durable in-context record the model can cite later.
  - At risk: tool *error/self-healing* messages (`construct_self_healing_message` prompt.py:11-32) and
    `_cartographic_review` verdicts are ordinary tool/message content — subject to `fold_intra_turn_tool_results`
    and `truncate_history_by_budget`. A confirmation echo or safety-relevant verdict older than the window
    (6000 tokens / 200-msg DB window) silently disappears from the prompt while its effects persist in
    map state. No KEEP pin exists.

## (f) Tests today
- tests/unit/test_context_budget_v2.py (13 tests: plan determinism, output reserve, schema cap, unknown-window
  conservative, violations not silent, near-budget warn, projections, fingerprint cache).
- tests/unit/test_context_budget_gis_v2.py (7 tests: V2 categories, section caps, offload/condense advice,
  untouchable classes, determinism, assembler carries advice).
- tests/test_history_compression.py (estimate ASCII/CJK, turn grouping, drop-oldest, min-turns, truncation notice).
- tests/test_chat_context_builder*.py, test_context_builder_injection/_round1/_round2, tests/test_chat_engine_history.py,
  tests/test_chat_history_persistence.py, tests/test_subagent_context_isolation_436.py.
- Missing: any ≥32K synthetic-window test; any test that executes an advised action; no reload-after-eviction test.

## (g) Recommended additive integration seam
1. **Do not extend `app/lib/runtime/context.py`** (correlation-only by design, ADR docstring context.py:1-20).
   Add `app/lib/runtime/context_policy.py` (or `app/services/chat/context_policy.py`) hosting the executed
   `ContextOp` enum (KEEP/CONDENSE/OFFLOAD_REF/SUMMARIZE/DROP_OLDEST/RELOAD_REF) + a deterministic
   `apply_advice(...)` that consumes the existing `GisBudgetAdvice.actions` (context_budget.py:343-349).
2. **Single choke point**: `ChatContextAssembler.assemble` already computes budget_report + advice *after*
   assembly (context_assembler.py:399-459). Move advice computation before the history step and let
   `apply_advice` drive: `truncate_history_by_budget` (DROP_OLDEST), `fold_intra_turn_tool_results` (CONDENSE),
   a new inline→ref re-offload pass for oversized history tool messages (OFFLOAD_REF), and a summarizer hook
   (SUMMARIZE) writing fingerprinted summaries keyed by turn range (`content_fingerprint` reuse).
3. **Pi parity**: expose the same policy over the Pi path by post-processing `ToolDispatchResult.llm_payload`
   (already the single tool→prompt seam, tool_dispatch_service.py:804-835) and by attaching a bounded
   "evicted-refs tombstone" line via `attach_turn_context` (pi_turn_context.py:110-144).
4. **RELOAD_REF durability**: spill evicted/oversized ref payloads to the artifact store
   (`ArtifactContract.storage_ref`, artifact_contract.py:186) inside `session_data.store` eviction
   (session_data.py:117-125); resolver then falls back ref→artifact before returning NOT_FOUND
   (ref_resolver.py:64-67) and stamps reload provenance into evidence (`RefResolution`).
5. **Safety pin**: add a `pinned` flag to `BudgetItem` (context_budget.py:92-105) consumed by
   `truncate_history_by_budget`/`fold_intra_turn_tool_results` so confirmation/verdict-bearing messages are
   KEEP-protected; optionally persist a compact in-conversation confirmation record at confirm time
   (chat.py:1999-2011) so the fact survives windows.
6. **Config**: require a real `LLM_CONTEXT_WINDOW` (or per-model descriptors) before enforcing anything from
   advice; today None ⇒ 8k plan ⇒ guaranteed false `over_budget` noise (config.py:100, context_budget.py:147-148).
7. **Tests to add**: synthetic 32K/64K/128K/256K assembled-context fixtures asserting determinism of
   `apply_advice`, ordering vs `Category` priority (context_budget.py:30-50), safety-pin survival,
   reload-after-eviction, and estimator error bounds vs a reference tokenizer.
