# OBSERVABILITY GAP MAP — webgis-ai-agent-quality-v1

Date: 2026-09-08. Read-only audit; all paths relative to repo root. Evidence cited as `file:line`.

## 1. Executive summary

The repo has an unusually mature **correlation backbone** and an unusually immature **shipping pipeline**:

- **Logging**: stdlib `logging` only (no structlog/OTel). A `RuntimeCorrelationFilter` injects `request_id/session_id/turn_id/run_id` from a ContextVar into every line (`app/core/logging_config.py:15-45`); `X-Request-ID` is bound and echoed by middleware (`app/main.py:312-335`). Mixed f-string/%-format call styles; no event-name vocabulary; `project_id` exists on RuntimeContext but is **not** in the log filter.
- **Tracing**: four distinct in-process trace models (TurnTrace event ring, GisTraceChain 18-stage evidence chain, gis_harness session trace, geocompute flat-span trace) — all bounded, all **memory-only**. Nothing survives process restart except three JSONL/log files. The geocompute module already documents an OpenTelemetry-compatible flat projection (`app/services/geocompute/tracing.py:1-7`) — the natural seed for a vendor-neutral wave.
- **Metrics**: Prometheus surface is nearly empty: instrumentator defaults + one label-free auth counter. Rich per-tool aggregates (p50–p99, errors, cancellations) exist **only** in an in-process aggregator exposed via an admin-only JSON endpoint (`/api/v1/metrics/digest`) — invisible to alerting. A strong CI drift gate (`tests/test_alerts_metrics_consistency.py`) keeps PromQL honest and is worth keeping/extending.
- **Redaction**: genuinely good and layered (trace meta sanitizer, job payload redactor, decision-log truncation, traceback sanitizer). Cardinality discipline in Prometheus labels is deliberate.
- **Frontend**: error boundaries + request-id propagation exist, but production console output is deliberately suppressed with **no remote reporting channel** — client errors are invisible server-side.
- **Biggest gaps for the next wave**: (1) no persistent trace/event store or exporter; (2) business/engine metrics not in Prometheus; (3) four parallel trace abstractions to unify; (4) no client→server error beacon.

---

## 2. Current logging architecture

**Framework**: stdlib `logging` exclusively. `grep structlog` → 0 hits; requirements pin only prometheus libs.

**Setup** — `app/core/logging_config.py`:
- `get_logger(name, level)` factory (72-108): console handler + one shared `RotatingFileHandler` (`logs/app.log`, 50MB × 14 backups, 57-69).
- `RuntimeCorrelationFilter` (15-34): pulls `RuntimeContext` from a ContextVar and sets `record.request_id/session_id/turn_id/run_id`, `"-"` when unbound. Attached both at handler and logger level.
- Format embeds correlation: `%(asctime)s [%(levelname)-8s] [req=%(request_id)s sess=%(session_id)s turn=%(turn_id)s run=%(run_id)s] %(name)-20s: %(message)s` (37-45).
- Pre-built loggers: `app`, `app.db`(WARNING), `app.api`, `app.tasks`, `celery` (114-126). `setup_logging_from_env()` (130-154) only sets root level from `LOG_LEVEL`.

**Context propagation** — `app/lib/runtime/context.py`:
- Frozen `RuntimeContext(request_id, session_id, turn_id, run_id, project_id)` on a ContextVar (30-67); `bind_runtime_context` merges parent→child (98-125); propagates through `asyncio.to_thread` into sync GIS tool threads. ID gens: `req-`, `turn-`, `run-` + uuid hex 12 (130-141).

**HTTP entry** — `RequestCorrelationMiddleware` (`app/main.py:312-335`): reads or generates `X-Request-ID`, binds it, echoes on response; registered outermost after CORS (420); CORS allows/exposes the header (407-415). Frontend transport injects the same header (see §8).

**Call-site conventions** (inconsistent — three styles coexist):
- %-style with ids spelled out: `app/services/jobs/worker.py:395` `"[jobs] started job_id=%s worker=%s"`; `app/services/jobs/store.py:239` `"[jobs] created job_id=%s kind=%s type=%s session=%s agent_task=%s"`; `app/services/ref_lifecycle.py:48-53` `"ref_lifecycle event=%s session=%s ref=%s reason=%s"`.
- f-strings: `app/main.py:268` `f"[lifespan] swept {swept} stale job(s)..."`; `app/services/session_data.py:618`.
- Bracketed prefixes (`[jobs]`, `[lifespan]`, `[PiBridge]`, `[tool_metrics]`) act as an informal event namespace; there is no closed vocabulary.

**Side-channel JSONL writers** (custom, both non-blocking by design):
- `logs/tool_metrics.jsonl` — `app/services/tool_metrics.py:29` path, bounded queue 8192 + daemon writer, 10MB × 5 rotation (32-34), drop-on-full backpressure (160-161). One row per tool dispatch with 24 fields incl. full correlation and plan/failure fields (260-285).
- `logs/tool_decisions.jsonl` — `app/services/chat/decision_log.py:21`; single-worker-thread background writes, bounded 256 in-flight (88-124).

**Global exception handler** — `app/core/exception.py:114-161`: always logs full `exc_info`; prod returns generic message + sanitized traceback (33-61); dev returns details. Request-id reaches the log line via the filter, not the message.

**Gaps**: no JSON output option; `project_id`/`job_id`/`tool_call_id` not in the standard filter set; no sampling; message conventions drift per module; `logs/` is the only "transport" for two of the richest event streams.

---

## 3. Current trace architecture (and persistence model)

Four coexisting in-process models, none persisted:

| Model | File | Scope/bounds | Consumed by |
|---|---|---|---|
| `TurnTrace` event ring | `app/lib/runtime/trace.py` | 17 closed event kinds (28-53); ≤256 events/turn, LRU 128 turns (156-169) | Nothing external; debug/replay input (docstring 16-17) |
| `GisTraceChain` evidence chain | `app/lib/runtime/gis_trace.py` | 18 canonical stages `USER_INTENT…USER_OUTPUT` (27-47); ≤8 records/stage; LRU 128 chains (125-137); `completeness()` metric (109-111) | Nothing; designed for A/B replay |
| GIS runtime trace | `app/services/gis_harness/trace.py` | Per-session ring 64 events ×256 sessions; **fixed counter key set** `finalizations / runtime_repairs / observation_rejects…` (32-44) | Session-level summary (tests/diagnostics) |
| geocompute trace events | `app/services/geocompute/tracing.py` | Flat OTel-compatible span projection; **field allowlist** (53-57); ring 1024 (20-21); also emits `logger.info(json.dumps(record))` (63) | `recent_events()` exists but has **no route caller** (grep: 0 usages) |

**Emitters per model**:
- TurnTrace: tool-surface selection (`app/services/chat/tool_pipeline.py:29-31,556-574`), turn lifecycle (`app/services/chat/execution_engine.py:558-574`), model fallback (`app/services/chat/model_runtime/routing.py:220-235`, `app/services/chat/model_routing_bridge.py`).
- GisTraceChain: Pi bridge dispatch path records `TOOL_CALLS/ARGUMENTS/TOOL_RESULTS/MAP_MUTATIONS` stages per tool call (`app/agent_pi_bridge.py:898-916`).
- geocompute: `run_started/run_finished` (`app/services/geocompute/executor.py:303,331`), `node_admitted/dispatched/completed/cancelled/attempt_failed/failed/reused/skipped` (executor.py:452-841), `materialized` (`app/services/geocompute/ops.py:581`).

**Structured evidence (not "trace" but the same loop)** — `app/lib/harness/evidence.py`: `ToolCallEvidence` carries full correlation `run/session/turn/tool_call` (307-330); `MapActionEvidence` carries frontend ACK incl. `sse_event_id`, `requested` vs `actual` (124-146); `MapSpecValidityEvidence` tiered ladder `NOT_EVALUATED→MUTATION_ACCEPTED→SEMANTIC_VALID` (87-97); `CartographicReviewEvidence` with bounded checks/repair attempts and explicit `*_omitted` counts (192-252). Held in per-session `PiAgentHarness` (`app/services/cartography_runtime.py:54-118`).

**Task classes: who emits what** — geocompute executor nodes: rich trace events. Durable jobs (`app/services/jobs/worker.py`): plain `[jobs]` logs + DB rows only, **no trace events**. Legacy Celery tasks (status API `app/api/routes/task.py:162-171`): `celery_task_id` correlation in DB, no traces. Chat/Pi turns: both TurnTrace + GisTraceChain.

**Persistence**: DB has only legacy `AnalysisTask.error_trace` Text column (`app/models/db_model.py:122`) and `execution_trace` JSON in the project-workflow migration (`migrations/versions/0010_project_workspace_workflow.py:91`). No trace/span tables otherwise. Durable observability = `logs/app.log`, `logs/tool_metrics.jsonl`, `logs/tool_decisions.jsonl`. **A restart erases every trace model.**

---

## 4. Current metrics

- **Prometheus**: `prometheus-fastapi-instrumentator` defaults on `/metrics` (unauthenticated, docs-hidden; network-isolation checklist in `app/main.py:289-309`): `http_requests_total{handler,method,status}`, `http_request_duration_highr_seconds_bucket`, `process_*`.
- **Custom counter**: `auth_jwt_validation_errors_total` — deliberately label-free to prevent cardinality attacks (`app/core/auth_metrics.py:18-24`), incremented on JWT failure.
- **In-process aggregator** (`app/services/tool_metrics.py:367-387`): per-tool `count/total_ms/max_ms/hit_count/error_count/cancelled_count/total_result_bytes` + log2-bucket histogram estimating p50/p90/p95/p99 (310-331). Exposed **only** via admin JSON `/api/v1/metrics/digest` (`app/api/routes/metrics.py:17-34`, along with `SpatialAnalyzer` cache stats and aggregated harness telemetry from `cartography_runtime.py:823-875`). Digest log line every 100 calls and at shutdown (38, 390-417; `app/main.py:152-157`).
- **Metrics contract test** — `tests/test_alerts_metrics_consistency.py`: CI gate that (a) live-drives an instrumented app and parses its exposition (121-168), (b) inventories wired exporters (postgres/redis/node, 45-66), (c) rejects phantom metrics (FORBIDDEN list 92-102), (d) enforces required alert coverage incl. `Celery_Task_Backlog`, `Auth_JWT_Errors` (106-118), (e) verifies rule files are actually mounted (309-318). This is the existing "metrics contract" and it works — but only for what Prometheus already has.

**Gap**: everything domain-specific (tool errors, cancellations, no-progress, harness completion rates, job queue depth/stale counts, MapSpec compile failures, geocompute node failures) lives in memory/JSONL and can **never fire an alert**. `Celery_Task_Backlog` is satisfied only via the redis-exporter `redis_key_size{key="celery"}` hack (test file 55-56).

---

## 5. Diagnostics surface

| Endpoint | What it gives | Evidence |
|---|---|---|
| `GET /api/v1/health` | liveness + agent runtime badge (`pi`/`chatengine`) + `pi_workers_alive` | `app/api/routes/health.py:92-121` |
| `GET /api/v1/health/live` | bare liveness for k8s | health.py:124-131 |
| `GET /api/v1/ready` | DB+LLM+Redis+Celery probes; 503 on failure; **minimal body by design** (SEC-11 anti-recon) — details only in logs | health.py:134-163 |
| `GET /api/v1/metrics/digest` (admin) | tool aggregator + spatial cache + harness telemetry (mean across session harnesses) | `app/api/routes/metrics.py:17-34`, `cartography_runtime.py:823-875` |
| `GET /api/v1/sessions/{id}/analysis-graph` | read-only projection of SessionPlan+MapSpec+evidence (goal, DAG, facets) | `app/api/routes/analysis_graph.py:16-23` |
| `GET /api/v1/geocompute/runs/{id}`, `/runs/{id}/summary` | execution-plane run state | `app/api/routes/geocompute.py:202,216` |
| (internal) `recent_events()` | geocompute ring reader — **no route wired** | `app/services/geocompute/tracing.py:66-70` |
| (service) data-source health | status vocabulary + circuit breaker consult | `app/services/data_fabric/health.py:17-35` |
| (service) LLM provider circuit breaker | per-provider `can_call/record_*`, `snapshot()` | `app/services/provider_health.py:84-141` |

No self-check/debug-dump HTTP endpoint for TurnTrace/GisTraceChain; no log-level change API.

---

## 6. Redaction & cardinality status

**Redaction (layered, reusable)**:
- Trace meta sanitizer `bound_meta` (`app/lib/runtime/trace.py:55-93`): sensitive-key hints → `[REDACTED]`, strings ≤512 chars, ≤16 meta entries, container summaries `<list len=N>`, big-int clamping. Reused by GisTraceChain (`gis_trace.py:24,86`).
- Durable-job payload redactor (`app/services/jobs/redaction.py`): `SENSITIVE_KEY_PARTS` incl. `owner_token/signed_url/bearer` (38-56), `BULK_KEYS` geometry summaries (59-73), caps 8KB params / 16KB results / 512-char strings, error = `ExcType: first line` only (22-28).
- Decision log: args truncated to 2000 chars + key redaction (`decision_log.py:47-76`).
- Traceback sanitizer for client-facing errors (`app/core/exception.py:33-61`).
- geocompute trace uses a **field allowlist**, not a denylist (`tracing.py:49-57`).
- pi bridge truncates recorded args to `_RECORD_ARGS_BOUND` (`app/agent_pi_bridge.py:907`).

**Cardinality**: Prometheus labels intentionally minimal — auth counter label-free; instrumentator labels are bounded route templates; the consistency test enforces "no label that doesn't exist" (test 269-274). In-memory maps are all bounded LRU/fixed-key (trace.py:167-169, gis_trace.py:135-137, gis_harness/trace.py:32-44). **Risk to carry forward**: `session_id/turn_id` are fine as log fields/JSONL columns, but must never be lifted into metric labels; the digest endpoint already aggregates rather than keys.

**Payload size accounting**: `estimate_json_bytes` (`app/lib/json_size.py:26-60`, node-budgeted extrapolation) is used at dispatch for `arg_bytes/result_bytes` with `_approx` flags flowing into tool_metrics rows (`tool_metrics.py:208-211,276-277`) — byte budgets are observable and their approximations are flagged.

---

## 7. Per-subsystem event inventory & gaps

Correlation shorthand: **C-id** = fields carried (req/sess/turn/run/tool_call/plan/step/mapspec).

### 7.1 Harness (workflow compile, replan, tool retrieval, dispatch, no-progress, completion)

| Event | Today | Missing | C-id carried |
|---|---|---|---|
| Tool retrieval / surface | `EVENT_TOOL_SURFACE_SELECTED` into TurnTrace with tools/bytes/fingerprint/retrieval_added (tool_pipeline.py:561-570); debug log of projection summary | None for surface; retrieval **miss** (chosen tool not in surface) only inferable from decision_log | turn, (sess via filter) |
| Tool selection decision | JSONL `tool_decisions.jsonl` per round: subset_size, tool_chosen, result_quality, plan_step_matched (decision_log.py:24-52) | Not in metrics; no sampling; sync path can still write inline (`background=False` default) | session, round, plan/step |
| Dispatch | TurnTrace `dispatch_started/completed`; tool_metrics JSONL row (24 fields, tool_metrics.py:260-285); GisTraceChain stages via pi bridge (agent_pi_bridge.py:903-913) | Node/mark | turn, run, tool_call, session, plan_id/revision/step_id, failure_class, recovery_action, mapspec_revision/fingerprint |
| Replan / fallback | TurnTrace `EVENT_FALLBACK` (routing.py:227-235); harness evidence `fallback_decisions` list (evidence.py:268) | No metric; replan reason taxonomy only in memory | turn, session |
| No-progress | Tracker with reason codes + `reason_summary()` (chat/no_progress.py:146-155); GIS-aware停滞 codes (`unchanged_map:N`, `repeated_planning:N`, no_progress.py:160+); surfaced to model as `no_progress_hints`; TurnTrace `no_progress_detected` | No aggregate metric; reason codes never leave the process | session (tracker), turn (event) |
| Completion | TurnTrace `turn_settled`; harness telemetry `rates/counts` via `/metrics/digest` aggregated across sessions (cartography_runtime.py:823-875) | Completion/failure rates not in Prometheus; "completeness()" of evidence chain computed but unexported (gis_trace.py:109-111) | turn, session |
| Workflow compile (GIS planner) | **Almost silent**: `workflow_compiler.py`/`planner_runtime.py` have zero logger calls; only duplicate-registration warnings (recipes.py:746, product_templates.py:268); gis_harness/trace.py counters finalizations/repairs | Compile latency/result events; family/template selection outcomes | session (ring only) |

### 7.2 Data (ingest, profile, query, cache, promotion)

| Event | Today | Missing | C-id carried |
|---|---|---|---|
| Ref lifecycle (cache) | Log line per create/overwrite/invalidate/delete/evict with reason (ref_lifecycle.py:40-53) | No counters/hit-ratio; message lacks run/turn (filter supplies only if bound) | session, ref, reason (+filter ids) |
| Payload cache / single-flight | `cache_hit` flag in tool_metrics rows; `arg_bytes_approx` | Cache hit-ratio metric per tool; eviction metrics | tool_call, run, turn, session |
| Ingest | Single warning on failure path (`data_ingest/pipeline.py:264`); data_fabric adapter probes with status vocabulary + circuit breaker (`data_fabric/health.py:17-35`, `circuit_breaker.py`) | Ingest volume/duration/error metrics; per-source failure counters in Prometheus | source/session (varies) |
| Profile | `data_profile/profiler.py` — effectively silent (0 logger hits) | Profiling duration/outcome events entirely | — |
| Query / lineage | `lineage_service.record_lineage` persists lineage, warns on failure (lineage_service.py:35,92,114) | Query latency histograms; lineage write-failure metric | dataset/artifact ids |
| Promotion / eviction | `sweep_aged_artifacts`, `sweep_expired_session_files` wired to periodic sweep with warnings (`app/main.py:216-231`); session eviction debug logs (session_data.py:125) | Swept-count metric (only warning when >0, main.py:267-269) | session, ref |

### 7.3 Compute (queue, dispatch, retry, timeout, cancellation, resource rejection)

| Event | Today | Missing | C-id carried |
|---|---|---|---|
| Queue / job lifecycle | `[jobs] created/transition/claim/skip-duplicate/late-success` logs (store.py:239,423,518,522); stale+orphan sweeps every 60s with count warning (`app/main.py:238-273`; store.py:865,931) | Queue depth & oldest-job age as **gauges**; backlog alert only via redis key hack | job_id, kind, type, session, agent_task |
| Dispatch / heartbeat | worker logs started/progress-fail/heartbeat-fail with `exc_info` (worker.py:268-375) | Per-node dispatch latency metric (exists only in geocompute trace ring) | job_id, worker |
| Retry / failure | Job rows carry status incl. `stale` (retryable); failure log with exception class (worker.py:440) | Retry-count metric; failure_class aggregation (failure_class exists per tool call, not per job) | job_id |
| Cancellation | Dedicated `cancelled_count` in aggregator, explicitly not an error (tool_metrics.py:350-357); geocompute `node_cancelled` events (executor.py:651,818); cancel observed logs (worker.py:268-272) | Cancellation-rate metric | tool, job |
| Resource rejection / budget | geocompute `node_admitted` vs `node_skipped` with reason (executor.py:484-496); budgets module | Rejection counters by reason; budget-exhaustion alert | run_id, node_id, reason |
| Timeout | `ApiTimeoutError`-style handling frontend-side; backend job timeout via heartbeat cutoff | Timeout-vs-failure split not metric-ized | job_id |
| Execution trace | geocompute emit set (§3) — OTel-shaped, ring 1024, allowlist fields | `recent_events()` unrouted; ring lost on restart; no OTLP exporter | run_id, node_id, plan_fingerprint, error_code |

### 7.4 Cartography (MapSpec compile, render, observe, export)

| Event | Today | Missing | C-id carried |
|---|---|---|---|
| MapSpec mutation/compile | Error/warning logs on mutation failure and Node CLI compile failure (lifecycle_engine.py:1616,1632,1876; coordinator.py:77,98); validity ladder evidence (evidence.py:87-97); `mapspec_revision/fingerprint` on every tool_metrics row (tool_metrics.py:219-220,283-284) | Compile duration/success metrics; fingerprint-mismatch counter | session, revision, fingerprint, checkpoint_id |
| Render / observe | `MapActionEvidence` ACK from frontend with `sse_event_id`, requested-vs-actual viewport, terminal statuses (evidence.py:100-146); observation rejects counted in gis_harness trace (`COUNTER_OBSERVATION_REJECTS`, gis_harness/trace.py:40) | Observation latency; render failure metrics | run, session, turn, tool_call, sse_event_id, action_id |
| Cartographic review / repair | `CartographicReviewEvidence` with bounded checks/repair attempts/termination_reason/counters (evidence.py:192-252); runtime repair warnings + ledger persist failures (gis_harness/runtime_repair.py:409-473); repair counters (gis_harness/trace.py:34-37) | Repair-loop length distribution; verdict metric | session, fingerprint, source_tool_call_id |
| Export / artifacts | Artifact sweeps + aged-artifact reclamation logs (main.py:224-231); owner sidecars | Export size/duration/failure events; artifact_registry emits nothing | session, artifact id (implicit) |

---

## 8. Frontend observability

- **Error boundaries**: app-level `ErrorBoundary` (`frontend/components/providers/client-providers.tsx:16-67`) + scoped `MapErrorBoundary` with local remount retry and network-cause naming (`frontend/components/map/map-error-boundary.tsx:23-68`); a `PanelErrorBoundary` exists for layout panels.
- **Network failure surfacing**: `transport.ts` generates `X-Request-ID` per request (crypto.randomUUID with fallback, transport.ts:160-162), sends it (172-176), prefers server echo, and attaches it to `ApiError`/`ApiTimeoutError` (93-128, 237-261) — so any surfaced error is joinable to server logs. SSE parser honors `Last-Event-ID` resume (`frontend/lib/api/sse-stream-parser.ts:26-30`). Task center wraps failures in labeled errors (`frontend/lib/api/jobs.ts:108-153`).
- **Console policy**: `frontend/lib/utils/logger.ts` suppresses all `console.*` in production (`devOnly`), and `safeError` prints a generic message — **there is no remote error reporting at all** (no Sentry/OTel/beacon; grep confirms zero client telemetry deps). Production client crashes and API failures are visible only insofar as the user reports them.
- **Gaps**: no `window.onerror`/`unhandledrejection` capture, no Web Vitals, no client-error ingest endpoint to pair with `X-Request-ID`.

---

## 9. Vendor-neutral abstraction recommendations

1. **One emit API, four adapters** — make `app/lib/runtime/trace.py` the single façade. Today `tool_pipeline`/`execution_engine`/`routing` emit TurnTrace, `agent_pi_bridge` emits GisTraceChain, and geocompute has its own module. Consolidate on the geocompute shape (`event`, flat OTel-compatible fields, allowlist, `tracing.py:37-63`) since it is already documented as "OTel SDK-mappable without changing emitters". Add a `sink` protocol: `in-memory ring` (dev, current behavior) + `logging` (JSON line) + optional OTLP exporter. Location: `app/lib/runtime/` (leaf, import-safe), reusing `bound_meta` for payload hygiene.
2. **Structured logging without a rewrite**: keep stdlib + `get_logger`; add a JSON formatter variant (env-selected) and extend `RuntimeCorrelationFilter` (logging_config.py:15-31) with `project_id`, `job_id`, `tool_call_id` — all already available via `RuntimeContext`/`JobOrigin` (`tool_metrics.py:241-259` shows the resolution pattern). Standardize on %-style + bracketed event names; forbid f-strings in new code.
3. **Persist the minimum**: a small `turn_summary` write (turn_id, session_id, outcome, counts, dropped_count, failure_class) at `turn_settled` — one row per turn turns the in-memory rings into a queryable SLO base without storing payloads. Reuse the `tool_metrics` writer pattern (bounded queue + daemon) rather than sync writes.
4. **Promote 6–10 metrics to prometheus_client** with bounded labels only (`tool`, `failure_class`, `status`, `kind` — never session/user): tool errors/cancellations by class, job queue depth + oldest-pending-age gauges, stale/orphan sweep counts, MapSpec compile failures, no-progress rate, geocompute node failure/rejection counts. The aggregator already computes all of these (tool_metrics.py:367-387); a `Instrumentator`-registered bridge from `aggregator_snapshot()` is enough to start.
5. **Keep and extend the drift gate**: `tests/test_alerts_metrics_consistency.py` is the right mechanism for the whole vendor-neutral wave — extend its inventory check to new custom counters so alerts can never reference nonexistent series.
6. **Reuse as-is**: `bound_meta` (trace.py:63-93), `jobs/redaction.py` (apply it to any new log/trace sink), `json_size.py` byte accounting, `tool_metrics` non-blocking writer, `provider_health.snapshot()` and `data_fabric/health.py` status vocabulary as canonical status enums.
7. **Frontend**: add a minimal client-error beacon (`POST /api/v1/client-errors`, rate-limited, echoing `X-Request-ID`) and route `safeError` + error-boundary `componentDidCatch` through it; keep prod console suppression.

---

## 10. Key file references

- Logging setup/correlation: `app/core/logging_config.py` (filter 15-34, formats 37-45, factory 72-108); `app/lib/runtime/context.py` (RuntimeContext 30-67, bind 98-125, id gens 130-141); `app/main.py:312-335` (request middleware), `289-309` (Prometheus), `238-273` (stale sweep), `195-235` (cleanup loops)
- Trace models: `app/lib/runtime/trace.py`; `app/lib/runtime/gis_trace.py`; `app/services/gis_harness/trace.py`; `app/services/geocompute/tracing.py`; emitters `app/services/chat/tool_pipeline.py:556-574`, `app/services/chat/execution_engine.py:558-574`, `app/services/chat/model_runtime/routing.py:220-235`, `app/agent_pi_bridge.py:898-916`, `app/services/geocompute/executor.py:303-841`
- Evidence: `app/lib/harness/evidence.py`; `app/services/cartography_runtime.py:823-875`
- Metrics: `app/core/auth_metrics.py`; `app/services/tool_metrics.py`; `app/api/routes/metrics.py`; contract test `tests/test_alerts_metrics_consistency.py`; deploy inventory `deploy/alerts-rules.json`, `deploy/prometheus.yml`
- Redaction/size: `app/services/jobs/redaction.py`; `app/services/chat/decision_log.py:47-76`; `app/core/exception.py:33-61`; `app/lib/json_size.py`
- Diagnostics: `app/api/routes/health.py`; `app/api/routes/analysis_graph.py`; `app/api/routes/geocompute.py:202-230`; `app/services/data_fabric/health.py`; `app/services/provider_health.py`
- Jobs/compute logging: `app/services/jobs/store.py:239-931`; `app/services/jobs/worker.py:268-440`; `app/services/jobs/redaction.py`
- Frontend: `frontend/lib/api/transport.ts`; `frontend/lib/utils/logger.ts`; `frontend/components/map/map-error-boundary.tsx`; `frontend/components/providers/client-providers.tsx`; `frontend/lib/api/sse-stream-parser.ts`; `frontend/lib/api/jobs.ts`
- Persistence exceptions: `app/models/db_model.py:122` (`error_trace`); `migrations/versions/0010_project_workspace_workflow.py:91` (`execution_trace`)
