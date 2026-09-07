# Model Provider / LLM Adapter System (investigation)

Scope: `app/adapters/` is NOT the LLM system (it is geodata `BaseDataAdapter`). The model-provider
runtime lives in `app/services/chat/` per ADR-0102 (`docs/adr/0102-model-provider-runtime-foundation.md`).

## 1. File inventory

| Path | Purpose |
|---|---|
| `app/adapters/base.py` | Geodata `BaseDataAdapter` ABC (discover/quick_assess/fetch/parse) — unrelated to LLM |
| `app/adapters/gov/gov_data_adapter.py` | Concrete geodata adapter |
| `app/services/chat/llm_client.py` | Sole transport: OpenAI-compatible httpx client (`call_llm`, `call_llm_stream`, `LLMConfig`, pooled `LLMHttpClientRegistry`, connect-phase-only retry) |
| `app/services/chat/model_config.py` | Single resolution point `resolve_llm_config(role)`; base triple (base_url/model/api_key) + role overrides |
| `app/services/chat/model_runtime/__init__.py` | Public API of ADR-0102 Wave 4 package |
| `app/services/chat/model_runtime/descriptors.py` | `ModelDescriptor` (frozen dataclass) + `ModelDescriptorRegistry` (capability/limit metadata, config-driven) |
| `app/services/chat/model_runtime/roles.py` | `ModelRoleProfile` role-policy table + `MODEL_ROLE_PROFILES` env overrides |
| `app/services/chat/model_runtime/health.py` | `LLMProviderHealth` — per (provider, model) circuit breaker, bounded metrics, no content stored |
| `app/services/chat/model_runtime/provider.py` | `FailureKind` taxonomy, `classify_exception`/`classify_status_failure`, `ProviderResponseView`, `sanitize_provider_error` |
| `app/services/chat/model_runtime/routing.py` | `ModelRouter` — deterministic route → `RouteDecision`, `resolve_config`, `observe()` |
| `app/services/chat/model_routing_bridge.py` | Live-engine bridge: `resolve_routed_config`, `observe_outcome`, `MODEL_ROUTER_ENABLED` kill-switch, legacy fallback |
| `app/services/chat/pi_rpc_client.py` | Pi (Node) host: writes `models.json` + injects `OPENAI_API_KEY` env at subprocess spawn |
| `app/services/chat/execution_engine.py` | ChatEngine consumption sites (`_llm_config`, `_planner_llm_config`, `_call_llm`, `_call_llm_stream`) |
| `app/api/routes/config.py` | Admin config routes `GET/POST /llm`, `POST /llm/test` |
| `app/core/config.py` | `LLM_*` Settings (L87-102), SSRF validation (L327-332) |
| `app/api/routes/health.py` | Readiness `HEAD {base_url}/models` probe (L33-48) |
| Tests: `tests/unit/test_model_runtime_v2.py`, `tests/unit/test_model_routing_bridge.py` | Coverage |

## 2. Adapter interface

There is **no LLM adapter Protocol/ABC**. The seam is a plain module-level async function pair:

- `llm_client.py:378` — `async def call_llm(cfg: LLMConfig, messages: list[dict], tools: Optional[list] = None) -> dict`
- `llm_client.py:444` — `async def call_llm_stream(cfg: LLMConfig, messages: list[dict], tools: Optional[list] = None) -> AsyncGenerator[tuple[str, dict], None]` (yields `('token', …)` / `('done', {message, finish_reason, usage})`)
- `llm_client.py:103-115` — `@dataclass LLMConfig(base_url, model, api_key, use_prompt_caching=False, max_tokens=16384, temperature=None, timeout_s=120.0)`
- `llm_client.py:118` — `class ProviderStreamTruncated(RuntimeError)` (honest truncated-stream failure, raised at `llm_client.py:605-608`)
- `llm_client.py:411` — `async def test_llm_connection(cfg, timeout) -> dict` (connectivity probe for admin UI)

Hardcoded OpenAI shape: `Authorization: Bearer` header (`llm_client.py:122-126`), payload builder
`_build_payload` (`llm_client.py:129-145`, `/chat/completions`, `tool_choice: "auto"`, `stream_options.include_usage`),
SSE parsing with DeepSeek-R1/MiniMax reasoning-content + `<think>` stripping (`llm_client.py:533-574`)
and MiniMax XML tool-call parsing (`llm_client.py:80-99`). Anthropic/Ollama/vLLM named only as a
*future* adapter slot in `model_config.py:14` docstring; `anthropic` appears nowhere else in `app/`.

## 3. Registration / config flow + secrets

- Settings: `LLM_BASE_URL`/`LLM_API_KEY`/`LLM_MODEL` + `LLM_PLANNER_MODEL`/`LLM_TITLE_MODEL` + timeout/max_tokens/temperature/context_window (`app/core/config.py:87-102`). Placeholder-key audit guard at `config.py:257-263`; SSRF check on `LLM_BASE_URL` at `config.py:327-332` (private nets allowed).
- Single resolution point: `model_config.py:74` `resolve_llm_config(role) -> LLMConfig`; precedence runtime-override > settings (`model_config.py:64-71`); role model overrides at `model_config.py:78-81`.
- Runtime override (no persistence): `model_config.py:44` `set_runtime_override(base_url, model, api_key)`, written by admin route `POST /api/v1/config/llm` → `engine.update_config` (`app/api/routes/config.py:80-96` → `execution_engine.py:600-616`).
- API key handling: kept server-side; sent per-request only (`llm_client.py:122-126`); pooled httpx clients deliberately carry **no** auth header to avoid bleed (`llm_client.py:165-175`); key never written to Pi config file — Pi gets `$OPENAI_API_KEY` indirection with env injection guarded against placeholder (`pi_rpc_client.py:227-234`).
- Descriptor registration: `descriptors.py:97-143` — default descriptor from settings triple + `MODEL_DESCRIPTORS_FILE` (JSON array) + `MODEL_DESCRIPTOR_OVERRIDES` (inline JSON), strict unknown-field rejection (`descriptors.py:79-84`), `upsert_override()` (`descriptors.py:171-175`) for admin writes, `invalidate()` (`descriptors.py:145`).
- Role registration: `roles.py:61-140` `DEFAULT_ROLE_PROFILES` (execution/planner/title/spatial + subagent_worker/reviewer/structured_extraction + ADR-0103 strong/cheap groups: architecture/debugger/scientific_review/corpus_worker/doc_crosscheck/descriptor_enrichment/code_worker/static_analysis); env override `MODEL_ROLE_PROFILES` (`roles.py:146-163`), `set_role_profile_override` (`roles.py:174`).
- Pi registration: `models.json` written at spawn with one provider `"webgis"`, `api: "openai-completions"` (`pi_rpc_client.py:235-260`).

## 4. Runtime selection / fallback / routing

- `model_routing_bridge.py:58` `resolve_routed_config(role, messages, require_tools, require_json, est_context_tokens) -> (LLMConfig, RouteDecision|None)`. Kill-switch `MODEL_ROUTER_ENABLED` (`model_routing_bridge.py:29-33`); any router exception → legacy `resolve_llm_config` (`model_routing_bridge.py:87-89`). decision=None ⇒ no health observation.
- `routing.py:84` `ModelRouter.route(req) -> RouteDecision`: primary = `req.prefer_model` or `resolve_llm_config(role).model`; candidates = primary + profile.fallbacks + descriptors in `preferred_group` (`routing.py:99-108`).
- Capability guard: `routing.py:110-122` — `capability_skip:<model>:tools|json|context` (context window 90% check); never silently downgrades a tool-requiring role to a non-tool model.
- Health guard: `routing.py:127-152` — cooldown skip (`health_skip:<model>:cooldown`), rate-limit/mismatch → degraded chain ordered after healthy (`routing.py:137-145`); primary kept even in cooldown with `primary_cooldown:<s>` reason (`routing.py:148-152`); empty chain → primary + `no_capable_fallback` (`routing.py:154-162`).
- `routing.py:178` `resolve_config(req)` merges decision + profile into `LLMConfig` (timeout/temperature/max_output).
- Outcome loop: `routing.py:208` `observe(decision, latency_s, failure)` → health table + `EVENT_FALLBACK` trace; bridge wrapper `observe_outcome` (`model_routing_bridge.py:130-157`) classifies via `classify_exception`/`classify_status_failure`.
- Health: `health.py:66` `LLMProviderHealth` keyed `(provider_id, model_id)`; threshold 3, cooldown 30→300s exponential (`health.py:19-23`); non-self-healing kinds (context_too_large/invalid_tool_schema/capability_mismatch) set mismatch flag without backoff (`health.py:117-121`); bounded to 256 keys (`health.py:175-181`).
- Engine consumption: `execution_engine.py:1036-1045` (`_llm_config`, `_planner_llm_config`), `execution_engine.py:1180-1210` (`_call_llm`/`_call_llm_stream` wrap calls with `LatencyTimer` + `observe_outcome`); per-subagent `model_role` injection (`execution_engine.py:341-344`); title generation (`execution_engine.py:986-1003`). `app/tools/spatial_reasoning.py:230-231` still calls `resolve_llm_config(ModelRole.SPATIAL)` directly (no routing/observation).

## 5. Extension seams + gaps for a model-provider extension platform

Seams (clean insertion points):
- Config resolution is already centralized: `resolve_llm_config` (`model_config.py:74`) is the declared single point (ADR-0102 Decision 1) — a provider extension can layer without a second config truth.
- `ModelDescriptor.endpoint_profile` (`descriptors.py:31`) exists as a field (`"openai-completions"` only) — intended discriminator for future transports.
- `FailureKind` + `ProviderResponseView` + `sanitize_provider_error` (`provider.py:20,117,164`) form a transport-neutral vocabulary ready for new adapters.
- Router/health/descriptors are singletons with `invalidate()`/`upsert_override()`/`reset()` — admin-plane hot mutation seams.
- Pi path projection: ADR-0102 Consequences note descriptors "can be projected into models.json in a later wave" (`pi_rpc_client.py:235`).

Gaps (blockers for multi-provider extension):
1. **No adapter interface**: transport is two free functions hardcoded to OpenAI SSE shape; a new provider (Anthropic/Ollama) requires either an ABC/Protocol extraction of `call_llm`/`call_llm_stream` or dispatch on `endpoint_profile`.
2. **Single provider identity**: `provider_id="webgis"` hardcoded (`routing.py:78`, `descriptors.py:111`); LLMConfig carries base_url+model but no provider_id, so routing/fallback across distinct providers (different base_url/api_key per candidate) is impossible — fallback candidates are model_ids within one base_url only (`routing.py:186-204` reuses primary cfg.base_url/api_key).
3. **Single credential**: one `LLM_API_KEY` global; no per-provider secret store; runtime override replaces the triple wholesale (`model_config.py:44-56`).
4. **No persistence**: runtime overrides/descriptor upserts are process-lifetime only; no DB model-provider registry (extension platform would need one).
5. **No versioning**: descriptors have `source` provenance (`descriptors.py:49`) and `contract_payload()` fingerprints (`descriptors.py:60-76`, `roles.py:40-54`) but no schema/contract version field.
6. **Uneven adoption**: spatial_reasoning and Pi bypass the router (`app/tools/spatial_reasoning.py:230`, `pi_rpc_client.py:219-260` reads settings directly); Pi `models.json` supports multiple providers but backend writes exactly one.
7. Retry/health live at different layers: connect-phase retry in `llm_client.py:356-365`, orchestration-level fallback decided but **not executed** — router returns a chain, yet `execution_engine._call_llm` never iterates `fallback_chain` on failure (only observes). A provider extension must implement chain-walk.
