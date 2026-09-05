# 0102. Model/Provider Runtime Foundation

**Date:** 2026-09-06
**Status:** Accepted
**Branch:** `feat/agent-tool-model-runtime-v2`

## Context

Model knowledge today is three env-var strings (`LLM_BASE_URL/LLM_MODEL/
LLM_API_KEY`) plus two role overrides (`LLM_PLANNER_MODEL/LLM_TITLE_MODEL`),
resolved at the single point `model_config.resolve_llm_config` (audit4 #997).
The httpx-based OpenAI-compatible client retries connect-phase only and,
by design, never mid-stream (duplicate tokens / double tool execution).
There is no LLM provider health (ADR-0027's circuit breaker covers geodata
providers only), no capability descriptors, no fallback semantics, and no
role profiles beyond max-token splits.

The Pi host brings its own Node-side model runtime; the application writes
`models.json` at spawn. Both entry paths must share the same model
*knowledge*, not the same transport.

## Decisions

1. **`model_config.resolve_llm_config` remains the single base-triple
   resolution point.** The model runtime layers *on top*: descriptors add
   capability/limit dimensions; routing composes them. No second config
   truth.
2. **Descriptors are config-driven, never invented** (`descriptors.py`):
   context window, tool-calling, streaming, reasoning-content, JSON mode,
   vision, cache support, cost/latency classes, local flag, fallback group —
   from conservative defaults, `MODEL_DESCRIPTORS_FILE`, or
   `MODEL_DESCRIPTOR_OVERRIDES` (operator JSON), with strict unknown-field
   rejection. Unknown capabilities stay `None`/`unknown`; nothing vendor
   marketing is hardcoded. Health state lives in the health tracker, not in
   descriptors.
3. **Role profiles are policy, not vendor** (`roles.py`): execution /
   planner / title / spatial keep their `model_config` semantics and gain
   timeout, temperature, reasoning, tool-access class, context budget,
   fallback chain, attempt bounds. New roles: subagent_worker,
   subagent_reviewer, structured_extraction. `MODEL_ROLE_PROFILES` env JSON
   overrides without code changes.
4. **LLM provider health is keyed by (provider, model)** (`health.py`),
   reusing the geodata tracker's breaker semantics (consecutive-failure
   open + exponential cooldown + recovery) but a separate instance —
   geodata and LLM failures are different domains and never conflated.
   Bounded metrics only: availability, consecutive failures, recent rate
   limit, latency bucket, last success, cooldown, capability mismatch. No
   prompts/responses stored. Non-self-healing failures
   (context_too_large / invalid_tool_schema / capability_mismatch) do not
   advance the backoff counter — waiting cannot fix them; they set a
   capability-mismatch flag instead.
5. **Routing is deterministic policy evaluation** (`routing.py`): primary =
   operator-configured model (or explicit preference); fallback chain =
   profile fallbacks + same fallback group, filtered by capability
   requirements (never silently downgrade tool-capable requirement to a
   non-tool model — skipped candidates emit `capability_skip:<model>` reason
   codes) and by health (cooldown skips, rate-limit/mismatch deprioritizes).
   Every deviation emits reason codes; no opaque model-chooses-model.
6. **Failure taxonomy** (`provider.FailureKind`): transport / timeout /
   rate_limit / provider_unavailable / unsupported / context_too_large /
   invalid_tool_schema / model_refusal / malformed_output / unknown — the
   shared vocabulary for fallback decisions, health records, and trace.
7. **Provider error bodies are untrusted input** (§39): `sanitize_provider_error`
   bounds them, strips control characters and pseudo-XML tags before any
   model reinjection.
8. **No transport rewrite.** The client's connect-phase-only retry
   discipline is preserved; resilience is orchestration-layer work. The
   router's `observe()` is the single seam where call outcomes reach the
   health table.

## Consequences

- Wave 5's context budget manager consumes role-profile
  `context_budget_tokens` and descriptor `context_window`.
- Wave 8's provider contract tests drive `FailureKind` classification
  against fake servers (split tool-call deltas, malformed args, rate
  limits, disconnects).
- Pi path keeps writing `models.json`; descriptor capability data can be
  projected into that file in a later wave without protocol change.
