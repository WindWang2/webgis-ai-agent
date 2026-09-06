# 0103. Pi GIS Runtime V3 — Dynamic Tool Platform, Model Routing, Context Budget, Trace/Replay

**Date:** 2026-09-07
**Status:** Accepted
**Branch:** `feat/pi-gis-runtime-v3`

## Context

After ADR-0101 (tool platform V2) and ADR-0102 (model runtime foundation),
four structural gaps remained:

1. **Descriptor richness ≈ 0.** The descriptor mechanism was complete but
   almost unused: of ~231 live tools, essentially none declared
   `capabilities`, `side_effect`, `tags`, or semantic I/O — retrieval
   corpora and fingerprints rested on tier/domains/description alone.
   Capability→tool mapping existed only inside the AlgorithmRegistry
   (`tool_candidates`), invisible to tool descriptors.
2. **Model runtime was an island.** Descriptors, role profiles, provider
   health and the deterministic router shipped in ADR-0102 but no
   production code path touched them; the engine still went
   `resolve_llm_config` → `llm_client` with no capability guards, no
   health observation, no fallback chain.
3. **The Pi surface was frozen.** Seven native tools + the
   `webgis_execute` proxy; the 200+ tool long tail was reachable only
   through a two-hop discovery (`list_available_tools` → proxy), so the
   model never saw relevant analysis tools.
4. **Context budgeting measured but didn't reason.** Wave 5 delivered
   measurement with 12 categories; no GIS-aware sections, no
   artifact-offload decisions, no GIS no-progress diagnostics
   (map/workflow stagnation), and no unified evidence chain from user
   intent to final map verdict.

## Decisions

1. **ToolDescriptor V3** (`app/tools/descriptor.py`): new optional
   contract fields — `input_artifacts`, `required_context`,
   `map_mutations`, `data_mutations`, `latency_class`, `memory_class`,
   `scale_class`, `crs_semantics`, `unit_semantics`, `idempotent`,
   `security_tier`, `required_permission`, `examples`, `anti_examples`,
   `failure_modes`, `fallback_tool` — all defaulting to
   unknown/None/empty. Registration-time validation covers every
   vocabulary field; unknown kwargs still fail hard.
2. **Capability provenance, never fabrication.** When a tool does not
   declare capabilities, `ToolRegistry.descriptor()` back-fills from the
   AlgorithmRegistry reverse index (`tool_to_capability`, new
   `tool_to_algorithms`) and stamps `capability_source =
   derived:algorithm_registry`. Declared wins; neither is mistaken for
   the other. Enrichment of all 231 tools was done module-by-module from
   actual tool code; fields that could not be judged honestly stay blank
   (9 tools with mixed local/online paths keep `deterministic` unset).
   `scripts/check_tool_descriptor_coverage.py` gates coverage as a
   pytest red line, including a derivation-consistency check.
3. **Dynamic Tool Surface V3** (`app/services/chat/tool_surface_v3.py`):
   context (message / task / workflow stage / SessionPlan capabilities /
   data profile / map summary) → capability retrieval → contract filter
   → rank → select 10–30 → compressed schema projection. Lexical
   retrieval remains the deterministic baseline; semantic retrieval is
   pluggable via `TOOL_RETRIEVAL_SEMANTIC=module:callable` and degrades
   to lexical on absence or failure, with the serving retriever recorded.
   Role side-effect policy filters mutation-class tools out of
   restricted surfaces (corpus_worker, doc_crosscheck,
   descriptor_enrichment, static_analysis). Tier-3 tools can never enter
   the projected surface.
4. **Pi dynamic surface without vendor changes.** The spawn dump becomes
   a v2 object: every model-visible, non-tier-3 tool is *registered*
   with the extension (native 7 keep full schemas, the long tail
   registers compressed dormant schemas); `default_active` stays the
   frozen native surface (Phase 1 compatibility). Per turn,
   `bind_turn_prompt` derives an active list from the V3 selector and
   appends a `WEBGIS_ACTIVE_TOOLS` marker; the extension applies it in
   `before_agent_start` via `pi.setActiveTools` (vendor semantics:
   effective next turn). Bare-name calls to dynamically registered tools
   classify as `execute` and flow through the same ToolDispatchService
   pipeline — ToolRegistry remains the single execution truth; the Pi
   surface is only a projection. `PI_DYNAMIC_TOOL_SURFACE=0` reverts to
   frozen behavior.
5. **Model runtime goes live, compatibly.**
   `model_routing_bridge.py` routes `_call_llm` / `_call_llm_stream` /
   `_generate_title` / subagent engines through the router whose primary
   IS the existing `resolve_llm_config` result. Router failures fall
   back to the legacy path; `MODEL_ROUTER_ENABLED=0` disables routing.
   Outcomes are classified into the provider health table (breaker,
   cooldown, capability mismatch). Subagent engines route by their role
   profile. Eight new role profiles (architecture, debugger,
   scientific_review, corpus_worker, doc_crosscheck,
   descriptor_enrichment, code_worker, static_analysis) declare policy
   only — pools resolve through abstract `fallback_group` descriptors,
   never vendor names.
6. **GIS-aware context budgeting V2** (`context_budget.py`): four new
   sections (DATA_PROFILE, ALGORITHM_METADATA, CARTOGRAPHY_METADATA,
   TOOL_RESULTS) with deterministic caps. `GisBudgetAdvisor` produces
   per-component handling *advice* (keep / condense / offload_ref /
   drop_oldest / compress) with an observable report; it never mutates
   prompts — execution authority stays with the existing components.
   The assembler's `budget_report` carries the advice block.
7. **No-progress gets GIS eyes.** `GisProgressTracker` layers
   `unchanged_map:N` (mapspec fingerprint static across N successful
   calls), `unchanged_workflow:N` (SessionPlan progress static) and
   `repeated_planning:N` on top of call-pattern reason codes. The Pi
   dispatch path feeds it after every real dispatch and surfaces
   threshold hits as `no_progress_hints` in the response details.
   Subagent recursion gets a contextvar depth guard (defense in depth;
   the primary defense remains tool-surface exclusion).
8. **Evidence chain + replay V3** (`app/lib/runtime/gis_trace.py`,
   `app/evaluation/replay.py`): the canonical 18-stage chain from user
   intent to user output, bounded and sanitized; emitters at the model
   routing and Pi dispatch seams. Replay gains deterministic, side-
   effect-free comparisons: tool-surface A/B, chain coverage regression,
   route decision diff. Metrics for all five evaluation families live in
   `app/evaluation/runtime_metrics.py`, with retrieval relevance labels
   derived from the AlgorithmRegistry against golden-case capabilities.

## Consequences

- Backward compatible: the router primary, the frozen native surface and
  the legacy budget semantics are unchanged by default; every new
  surface has an explicit off-switch and an honest failure fallback.
  Legacy role profiles (execution/planner/title/spatial) deliberately do
  NOT override operator settings (temperature/max_tokens/timeout stay
  `None` in the profile so `resolve_llm_config` values flow through);
  explicit profile numbers only apply to the new roles.
- The in-band `WEBGIS_ACTIVE_TOOLS` marker is protected: user-supplied
  occurrences are neutralized before Python attaches its own, and the
  extension enforces a hard activation ceiling — the kill-switch and the
  per-turn projection budget cannot be forged from message content.
- Fingerprints legitimately changed once where descriptors gained
  values — the fingerprint contract itself (same input → same output,
  description-insensitive schema fp) is unchanged and still pinned by
  tests.
- Known limitations: the 18-stage chain is only emitted at the seams
  wired in this branch (routing + dispatch); intent/verdict emitters
  land with their respective engine changes. `deterministic` stays
  blank for 9 mixed-path tools. Pi-side health observation is not
  wired (LLM calls happen inside the Node subprocess). Planner-phase
  LLM calls resolve through the router but their outcomes are not
  observed into the health table (the planner orchestrator owns the
  call). Fallback models inherit the primary endpoint (single-gateway
  assumption; documented in model-runtime docs).
