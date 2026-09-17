# CURRENT_ARCHITECTURE — load-bearing map for the cockpit implementation

All paths are relative to repo root `C:\Users\wangj.KEVIN\projects\webgis-wt-agent-ops-cockpit-v1` at `faa453a8`.
Endpoint paths are exact; HTTP APIs are mounted under prefix `/api/v1` (see `app/main.py:787-846`).

---

## PART A — BACKEND

### A1. App shell and routing

- FastAPI app: `app/main.py`. Router registration block at `app/main.py:787-846`, e.g.
  `app.include_router(mission_runtime_routes.router, prefix="/api/v1", tags=["Mission Runtime"])` (line 824).
- No Django `urls.py` — routing is FastAPI `APIRouter(prefix=...)` per module in `app/api/routes/`.

### A2. Mission runtime (the core read-side subject)

Files:
- `app/services/mission_runtime/contracts.py` — pure pydantic types, `SCHEMA_VERSION = "mission.v1"`
- `app/services/mission_runtime/store.py` — durable DB ledger + lease fencing
- `app/services/mission_runtime/service.py` — `MissionRuntimeService` facade + `get_mission_runtime()` singleton (line 222)
- `app/services/mission_runtime/recovery.py` — `MissionRecoveryCoordinator.recover(...)`
- `app/services/mission_runtime/resources.py` — `MissionResourceLedger` (quota/reserve/commit/release)
- `app/services/mission_runtime/swarm_bridge.py` — `DurableSwarmBridge` (persist/settle swarm receipts)
- `app/services/mission_runtime/artifacts.py` — `MissionArtifactOwnership`
- Models: `app/models/mission.py` — `GISMissionRow` (line 31), `GISMissionCheckpointRow` (line 79), `GISMissionSwarmRunRow` (line 101)

State machine (`contracts.py:26-103`):
- `MissionState`: `created, planning, running, waiting_dependency, partially_complete, suspended, recovering, complete, failed, cancelled`
- `TERMINAL_STATES = {complete, failed, cancelled}`; `ACTIVE_STATES` = the other 7.
- `TRANSITIONS` whitelist map at `contracts.py:56-90` (e.g. `running → {waiting_dependency, partially_complete, suspended, recovering, complete, failed, cancelled}`; terminal states → `{}`).
- Helpers: `transition_allowed(current, to)` (line 97), `is_terminal(state)` (line 106).

Bounds (`contracts.py:16-23`): `MAX_REFS=64, MAX_SESSIONS=16, MAX_CHECKPOINTS_RING=8, MAX_FRONTIER=64, MAX_GOAL_CHARS=2000, MAX_SUMMARY_CHARS=400, MAX_CHECKPOINT_BYTES=16*1024, MAX_SWARM_TASKS=48`.

`MissionRecord` (exact fields, `contracts.py:277-306`):
```python
schema_version: str = "mission.v1"
mission_id: str; org_id: str; user_id: str = ""; project_id: Optional[str]
owner_scope: str = ""; root_goal: str = ""; goal_revision: int = 1
state: MissionState = "created"; revision: int = 1
created_at: float; updated_at: float
refs: MissionRefs; frontier: MissionFrontier; resource_budget: MissionResourceBudget
failure: MissionFailureState; recovery: MissionRecoveryState
lease_owner: str = ""; lease_epoch: int = 0; lease_expires_at: float
```
- `MissionRefs` buckets: `active_session_ids, session_plan_refs, workflow_instance_refs, swarm_run_refs, artifact_refs, map_product_refs, evidence_refs`
- `MissionFrontier` buckets: `completed, running, pending, failed, blocked` (each bounded to `MAX_FRONTIER=64`)
- `MissionResourceBudget`: `quota, consumed, reserved, retry_cost` (all `Dict[str, float]`)
- `MissionFailureState`: `error_code, detail, recovery_class, at`
- `MissionRecoveryState`: `attempt, last_checkpoint_id, last_checkpoint_at, last_recovery_at, blocked_reason, unresolved_ops`

`MissionCheckpoint` (`contracts.py:309-339`): `checkpoint_id, mission_id, mission_revision, goal_revision, state, frontier, refs, resource_budget, swarm_run_id, session_plan_ref, workflow_instance_refs, note, created_at` + `to_bounded_dict()` (16KB cap).

`SwarmRunDurable` (`contracts.py:365-375`): `swarm_run_id, mission_id, goal_slice, state("running|succeeded|failed|cancelled|partial"), tasks: Dict[str, SwarmTaskReceipt], created_at, updated_at`.
`SwarmTaskReceipt` (`contracts.py:342-362`): `task_id, assignment_id, state, operation_class, produced_refs(≤12), summary(≤400), error_code, attempt, idempotency_key, settled_at`.
`SwarmTaskDurableState` (`contracts.py:149-159`): `PENDING, READY, RUNNING, SUCCEEDED, FAILED, SKIPPED, CANCELLED, UNRESOLVED` (UNRESOLVED = destructive unknown, never blind-rerun).
`OperationClass` (`contracts.py:113-121`): `pure, idempotent, repeatable, destructive_at_most_once, compensatable`.

`MissionDiagnostics` (`contracts.py:378-392`): `mission_id, goal, state, revision, goal_revision, current_frontier, blocked_reason, artifact_count, swarm_status, resource_use, last_checkpoint, lease_owner, lease_epoch, recovery_count`.

DB rows (`app/models/mission.py`): `GISMissionRow` columns `mission_id(PK), org_id, user_id, project_id, owner_scope, root_goal(Text), goal_revision, state, revision, schema_version, refs(JSON), frontier(JSON), resource_budget(JSON), failure_state(JSON), recovery_state(JSON), lease_owner, lease_epoch, lease_expires_at, heartbeat_at, created_at, updated_at, terminal_at`. `GISMissionCheckpointRow`: autoincrement id, `checkpoint_id, mission_id, org_id, mission_revision, goal_revision, state, snapshot(JSON), created_at` (ring of `MAX_CHECKPOINTS_RING=8` per mission enforced in store). `GISMissionSwarmRunRow`: `swarm_run_id(PK), mission_id, org_id, goal_slice, state, tasks(JSON), revision, created_at, updated_at, terminal_at`.

Store guards (`store.py`): `DEFAULT_LEASE_TTL_S = 30.0` (line 27); exceptions `StoreUnavailable` (61), `FencingError` (65), `TransitionRejected` (69). Every mutating write CAS-checks `lease_epoch`. ID format: `msn-<hex16>` (line 50), checkpoints `mcp-<mission_id>-r<rev>-<hex6>` (line 54), swarm runs `swr-<hex14>` (line 58).

### A3. Mission REST API (EXISTS — exact contract)

Router: `app/api/routes/mission_runtime.py`, `prefix="/mission-runtime"` (line 23) → full path prefix `/api/v1/mission-runtime`.
Auth: all routes except `/health` take `user: Dict = Depends(get_current_user)` (JWT Bearer, `app/core/auth.py:304`). Org resolution: `tenancy.effective_org_in_thread(user)` (`app/core/tenancy.py:206`; anonymous/`AUTH_DISABLED` map to the `default` org bucket). **No `ownerToken` (X-Session-Token) support on these endpoints — they are JWT-auth surfaces, not anonymous-session surfaces.**

| Method | Path | Request | Response / errors |
|---|---|---|---|
| GET | `/api/v1/mission-runtime/health` | — | `{"enabled": bool, "schema": "mission.v1"}` (line 46) |
| POST | `/api/v1/mission-runtime/missions` | `CreateMissionRequest{root_goal:str≤2000, project_id?, session_id:str="", quota:Dict[str,float]}` (line 35) | `MissionRecord.model_dump(mode="json")`; 503 `mission_runtime_disabled` |
| GET | `/api/v1/mission-runtime/missions/{mission_id}` | — | `MissionRecord` dict; 404 `mission_not_found` (line 70) |
| GET | `/api/v1/mission-runtime/missions/{mission_id}/diagnostics` | — | `MissionDiagnostics` dict; 404 (line 82) |
| POST | `/api/v1/mission-runtime/missions/{mission_id}/start` | `WorkerRequest{worker_id: str 1..128}` (line 42) | `MissionRecord`; 409 `FencingError/TransitionRejected`; 404; 503 |
| POST | `/api/v1/mission-runtime/missions/{mission_id}/suspend` | `WorkerRequest` | same (line 103) |
| POST | `/api/v1/mission-runtime/missions/{mission_id}/resume` | `WorkerRequest` | recovery dict (`{"ok": true, ...}` from `MissionRecoveryCoordinator.recover`); 409 on fencing (line 112) |
| POST | `/api/v1/mission-runtime/missions/{mission_id}/cancel` | `WorkerRequest` | `MissionRecord` (line 127) |

Revision/idempotency semantics: lifecycle ops acquire a lease first (`_require_lease`, `service.py:57-65`) — a second operator while a worker holds the lease gets **409** with `LEASE_ACQUIRE_FAILED`; stale epoch → `FencingError` → 409. There is **no If-Match/revision header** — the guard is the server-side lease epoch, surfaced to clients only as 409 + `lease_owner`/`lease_epoch` fields on the record. Terminal states reject further transitions (`TransitionRejected` → 409).

**ABSENT read surfaces** (needed by cockpit, see GAP_ANALYSIS): mission list (`GET /missions` with org scope), checkpoint ring listing, swarm-run receipts listing, mission event/timeline feed, mission SSE.

### A4. SSE / WebSocket infrastructure

- **Chat SSE**: `POST /api/v1/chat/stream` (`app/api/routes/chat.py:1020`). Envelope from `app/utils/sse.py::sse_event` (line 104): `event: <type>\nid: <n>\ndata: <json>\n\n`. Per-turn monotonic ids via `sse_event_id_scope` (line 46); resume via `Last-Event-ID` header or `last_event_id` query param → replay from `TurnEventBuffer` (`app/services/chat/event_resume.py`; `RESUME_MAX_EVENTS=256` line 82, `RESUME_MAX_BUFFERS_PER_SESSION=4` line 89, `RESUME_MAX_SESSIONS=32` line 84; process-local, cross-restart resume explicitly out of scope). Terminal events `{done, task_complete, task_error, task_cancelled}` always flush immediately (`sse.py:141`). `SSEBatcher` coalesces (32 events / 0.08s). Keepalive = SSE comment lines (`: ...`).
- Event vocabulary (frontend mirror `frontend/lib/api/chat.ts:49-104`): `token, content, tool_call, tool_result, step_start, step_result, step_error, step_cancelled, task_start, task_complete, task_error, task_cancelled, session, plan_ready, plan_step_done, plan_finalized, session_plan_updated, session_plan_progress, session_plan_superseded, session_plan_step, map_finalization, ui_action, explorer_progress, keep_alive, resume_gap, heartbeat, error, done`.
- `step_result` payload (`frontend/lib/api/chat.ts:113+` `StepResultPayload`): `tool, name, arguments, geojson_ref, ref_descriptor, result{success, type, task_id, ...}, turn_id` (additive turn_id injected at `app/agent_pi_bridge.py:125-131`).
- Reasoning tokens: `token` events carry `is_reasoning: bool` (`app/services/chat/pi_event_mapper.py:129`); frontend routes them to a collapsed think widget. **Any new replay/projection endpoint must NOT forward `is_reasoning` token content.**
- **WebSocket**: `WS /api/v1/ws/{session_id}` (`app/api/routes/ws.py:30`) — JWT required, token via `Sec-WebSocket-Protocol: bearer,<jwt>` (IP rate-limited). `WS /api/v1/ws/...` collab variant `app/api/routes/ws_collab.py:211`. Both are session-scoped, not mission-scoped.

### A5. SkillPolicy (in-process; NO HTTP API)

- `app/services/gis_harness/skills/policy.py`:
  - `SkillPolicyMode = Literal["none","guide","execute_guided","shadow","blocked","fallback"]` (line 38)
  - `SkillTrustTier = Literal["core","candidate","experimental","quarantined","deprecated"]` (line 48)
  - Kill-switch env `GIS_SKILL_POLICY` (line 60), `policy_enabled()` (line 161)
  - `SkillPolicyDecision` fields (line 66-83): `mode, selected_skill, skill_version, trust_tier, confidence, confidence_band, eligibility_ok, situation_signature, reasons[], fallback_reason, shadow_candidate, shadow_candidate_version, rejected_codes[], matched_signals[], clarification_code` — with `to_bounded_dict()` (line 85) and `pi_context_card()` (line 104, `critical_obligations` = `reasons[:4]`).
- Binding seam: `app/services/gis_harness/hotpath_convergence/skill_bind.py::bind_skill_guidance_at_plan_seam` (once per plan).
- Per-session process-local holder: `app/services/gis_harness/hotpath_convergence/session_ctx.py` — `HotpathTurnContext{session_id, skill_bundle, skill_guidance, mission_id, claim_store, last_pi_card}` (line 17-24), `_MAX_SESSIONS=256` FIFO eviction (line 14), accessors `get_turn_context`, `set_skill_bundle`, `set_mission_id`, `get_or_create_claim_store` (line 36-68). **A read API must go through these accessors and tolerate "missing/evicted" as an honest empty state.**
- Bounded Pi disclosure card (CoT-safe precedent): `app/services/gis_harness/hotpath_convergence/pi_card.py::build_hotpath_pi_context` — pops banned keys `("chain_of_thought","cot","raw_llm","messages","thinking")` (line 71-73).

### A6. Evidence / Claim graph (in-process; NO HTTP API)

- Contracts: `app/services/gis_harness/evidence_claim/contracts.py`, `SCHEMA_VERSION="evidence_claim.v1"`.
  - Bounds: `MAX_EVIDENCE_NODES=4096, MAX_CLAIMS=2048, MAX_EDGES=10000, MAX_SUPPORTING_REFS=16, MAX_TEXT=200, MAX_REF=128, MAX_TRAVERSAL_DEPTH=12, MAX_TRAVERSAL_NODES=256`.
  - `EvidenceKind` (18 values: dataset, dataset_version, spatial_subset, transformation, analysis, statistic, spatial_relation, artifact, map_layer, map_view, chart, simulation, observation, user_assertion, external_source, method, uncertainty, product_facet).
  - `EvidenceFreshness`: `fresh, stale, superseded, expired, unknown, missing`.
  - `ClaimType` (20 values incl. `narrative`), `ClaimStatus`: `supported, partially_supported, unsupported, contradicted, stale, unknown`.
  - `RelationType`: `derived_from, computed_by, aggregated_from, filtered_from, supports, contradicts, visualized_as, summarized_by, supersedes, invalidates, depends_on`.
  - `EvidenceNode` (line 183): `evidence_id, kind, ref, producer, version, revision, scope, method, uncertainty_ref, freshness, tenant_id, session_id, metadata` + `to_bounded_dict()`.
  - `Claim` (line 219): `claim_id, claim_type, subject, predicate, value, value_text, unit, comparator, reference_scope, temporal_scope, spatial_scope, method, supporting_evidence_refs, contradicting_evidence_refs, confidence, uncertainty_ref, status, tenant_id, session_id, narrative, product_facet_id, map_layer_id` + `to_bounded_dict()`.
  - `Scope` (line 141): `spatial_name, spatial_level, aoi_ref, temporal_start, temporal_end, temporal_label, filter_digest, group_by`.
  - `VerificationResult` (line 306): `claim_id, status, steps[VerificationStep{stage, ok, detail, evidence_ids}], positive_proof, reasons` (positive-proof invariant: missing evidence never becomes PASS).
  - `ContradictionResult` (line 323): `contradiction_id, claim_ids, kind("hard|scoped_divergence"), detail, inspected`.
  - `CartoBinding` (line 340): `layer_id, style_class, classification_breaks, metric_field, metric_value, statistic_evidence_id, claim_id, dataset_version_ref, mapspec_revision, stale`.
- Store: `app/services/gis_harness/evidence_claim/store.py::ClaimStore` — `upsert_evidence/get_evidence/evidence_by_ref/all_evidence/upsert_claim/get_claim/all_claims/claims_for_artifact/mark_claim_status/add_edge/edges_from/edges_to/all_edges/to_dict/from_dict/stats` (lines 22-132). **In-memory, per-session**, held in `session_ctx` (see A5).
- Derived views (all pure functions over ClaimStore): `graph.py::RelationGraph.traverse/ancestors/descendants` (bounded `TraversalResult.to_bounded_dict`), `grounding.py::grounding_projection`, `verify.py::verify_claim`, `contradiction.py`, `freshness.py`, `narrative.py`, `query.py`, `census.py`.
- #1335 (open PR) changes the ClaimStore keying to `tenant_id|session_id` — read endpoints must go through `session_ctx.get_or_create_claim_store(session_id)` / grounding defaults `persist_status=False`.

### A7. Resource Governor + provider health (in-process; NO HTTP API for governor)

- `app/services/governor/contract.py`: `Dimension` enum — 12 dims: `memory_bytes, gpu_memory_bytes, network_bytes, storage_bytes, feature_count, pixel_count, context_tokens, output_tokens, estimated_llm_cost, wall_time_s, external_service_calls, render_work_units` (line 41-55). `Certainty`: `known, estimated, unknown, unavailable` (line 32). `DimValue{certainty, min, expected, max, confidence, source, reason}` (line 76). `Subsystem` (159), `ResourceClass` (180), `ExecutionPriority` (193), `AdmissionDecision` (201), `RetryClass` (211), `CancelReason` (223), `DegradationSemantics` (233), `ResourceEstimate` (246), `ResourceDemand` (300), `ResourceBudget` (323), `ResourceReservation` (348), `ResourceUsage` (368), `ResourceDecision` (386).
- `app/services/governor/governor.py::HarnessResourceGovernor` — process singleton `get_governor()` (line 474).
  `snapshot(session_id="", turn_id="", goal_id="")` (line 381) returns exactly:
  ```python
  {"mode": <GovernorMode>, "channels": {name: {"in_flight": int, "waiting": int}},
   "sessions_active": int, "live_reservations": int,
   "budgets": <ledger.snapshot(...)>, "retries": <...>, "storage": <...>,
   "slo_breach_total": int, "cancelled_sessions": int}
  ```
- Durable mission-scope budget mirror (already serialized in `GET /missions/{id}`): `MissionResourceBudget{quota, consumed, reserved, retry_cost}` (A2) + `MissionResourceLedger` ops in `resources.py` (`BUDGET_EXHAUSTED:<dim>` rejection).
- Provider health: `app/services/provider_health.py::ProviderHealthTracker.snapshot() -> dict[str, dict]` (line 141) and `FabricHealthBridge.states()` (line 227). Circuit-gate semantics (`can_call`, line 84).
- Infra health that DOES have HTTP: `GET /api/v1/geocompute/cluster/metrics`, `/cluster/workers`, `/cluster/runs/stuck`, `POST /cluster/ledger/limits` (`app/api/routes/geocompute.py:407,661,695,757`).

### A8. Swarm / Execution DAG read surfaces

- Live swarm: `app/services/agent_swarm/orchestrator.py::SwarmOrchestrator` — `status_snapshot() -> SwarmExecutionStatus` (line 652). **Process-local** (ADR-0197 Context: "pod restart loses unfinished swarm truth"). Launched from `app/agent_pi_bridge.py:3063-3067` behind a default-off flag; returns bounded summary only (run_id/terminal state/counts/ref tickets).
- `SwarmExecutionStatus` (`app/services/agent_swarm/delegation_contracts.py:280-295`): `run_id, session_id, root_goal, state, counts(Dict[NodeState,int]), active_task_ids, tasks(Dict[task_id, bounded dict]), manifest, manifest_ref, started_at, finished_at`.
- Durable mirror: `GISMissionSwarmRunRow` via `DurableSwarmBridge` (A2). In-process read: `MissionStore.get_swarm_run(swarm_run_id)`, `list_swarm_runs_for_mission(mission_id, *, org_id=None)` (org filter added by #1335). **No HTTP endpoint.**
- Workflow Runtime (closest existing DAG read API — `app/api/routes/workflow_runtime.py`, prefix `/api/v1/workflow-runtime`):
  - `GET /instances` (279), `GET /instances/{instance_id}` (239) → `InstanceDetailResponse` (frontend mirror `WorkflowInstance` below), `GET /instances/{instance_id}/events` (288), `GET /instances/{instance_id}/nodes/{node_id}` (309), `GET /instances/{instance_id}/debug` (387), `GET /instances/{instance_id}/recompute-plan` (259).
  - Actions: `POST /instances` (158), `/instances/{id}/run` (188), `/instances/{id}/cancel` (206), `/instances/{id}/changes` (216), `/instances/{id}/nodes/{node_id}/retry` (331), `/instances/{id}/nodes/cancel` (351), `/instances/{id}/clone` (368).
  - Node state vocab (closed, `contracts.py`): `PENDING READY RUNNING SUCCEEDED FAILED BLOCKED SKIPPED CANCELLED STALE`; `InstanceStatus`: `running succeeded failed cancelled superseded`.
  - Frontend typed client already exists: `frontend/lib/api/workflow-runtime.ts` — `WorkflowNode{node_id, state, attempts, error_code, bound_ref, output_ref, reused, reuse_evidence, binding_violations}`, `WorkflowExplain{why_recomputed[], why_reused[], blocked[{node, codes[]}]}`, `WorkflowInstance{instance_id, package_id, package_version, package_fingerprint, status, revision, cancel_requested, nodes[], counts, decisions[], pending_changes[], error_code, error_detail, methodology_family?, compiler_version?, explain?}`.
- GeoCompute run events (bounded trace exemplar): `GET /api/v1/geocompute/runs/{run_id}/events?after_id=&limit=` (`app/api/routes/geocompute.py:610`; limit ≤200, cursor `after_id` = last event id, 404 `RUN_NOT_FOUND` after retention cleanup, 503 `CLUSTER_UNAVAILABLE`). Runs list/detail: `GET /runs` (356), `GET /runs/{run_id}` (429), `GET /runs/{run_id}/summary` (596). Cancel: `POST /runs/{run_id}/cancel` (501).
- Durable jobs: `GET /api/v1/tasks/jobs` (85), `GET /tasks/jobs/{job_id}` (147), `DELETE /tasks/jobs/{job_id}` (cancel, 168), `POST /tasks/jobs/{job_id}/retry` (217) — `app/api/routes/jobs.py`.

### A9. Session / SessionPlan / event-log read APIs (session-scoped)

- `GET /api/v1/chat/sessions/{session_id}/plan` (`chat.py:1560`) → `SessionPlanProjection` (204/empty when absent). Frontend type `frontend/lib/types/session-plan.ts` (exact mirror incl. `progress[]`, `steps?`).
- `GET /api/v1/chat/sessions` (1366), `GET /sessions/{session_id}` (1400), `GET /sessions/{session_id}/map-state` (1466), `POST /sessions/{session_id}/map-state` (1611), `GET /sessions/{session_id}/plan` (1560), `DELETE /sessions/{session_id}` (2102). Session events: `session_data.py::get_event_log(session_id)` (line 748) / `append_event` (737) — SessionStore surface (Redis or in-memory), no dedicated HTTP route.
- Ownership: `_guard_body_session` + `get_owner_token` (`app/core/auth.py:530`, `X-Session-Token` header) for anonymous sessions; token digest stored in map_state (chat.py stream setup).

### A10. Replay / diagnostics

- `app/services/gis_harness/skills/replay.py`: `ProcedureReplayReport{skill_id, skill_version, steps[StepReplay{step_id, state∈(covered|missing|skipped_declared|unknown), detail, matched_evidence}], obligations[ObligationReplay{obligation_id, state∈(satisfied|missing|unknown), detail}], complete}` — pure projection, zero I/O, **no HTTP route**.
- Mission checkpoints: durable rows (ring 8) — readable in-process via `MissionStore`, **no HTTP route**.
- GeoCompute evidence snapshot of record: `GET /api/v1/geocompute/runs/{run_id}` (429) after trace retention.
- ADR-0183 `docs/adr/0183-harness-replay-benchmark-explainability.md` describes the intent; there is no unified replay API.

### A11. Secrets / CoT exposure notes (fields to strip in any new projection)

- `pi_card.py` banned-key list is the in-repo precedent: strip `chain_of_thought, cot, raw_llm, messages, thinking`.
- `token` SSE events with `is_reasoning: true` carry raw model reasoning — exclude from any replay feed.
- `step_result.arguments` echoes tool arguments (may embed user queries) — bound/omit in operator-facing views.
- Mission REST already states the policy: "Does not expose GIS payloads — refs and bounded diagnostics only" (`mission_runtime.py:4`).
- Error `detail` strings and `MissionFailureState.detail` are free text — truncate (contracts already bound: `MAX_SUMMARY_CHARS=400`, diagnostics `goal[:200]`).

---

## PART B — FRONTEND

### B1. App router

- `frontend/app/layout.tsx` (82 lines) + `frontend/app/page.tsx` (542 lines) — the single workbench page. `frontend/app/story/` is a separate route. **#1336 adds `frontend/app/geoai/page.tsx`** (7-line shell importing `GeoAiPanel`).
- Panels are dynamically imported (`next/dynamic`, `ssr: false`) in `app/page.tsx:38-50`.
- Layout composition in `page.tsx`: `TopBar`, `NavRail`, sidebar (left panel = `ContextPanel`), `MapPanel`, `EmbodiedHud`, drawers (`HistoryDrawer`, `SettingsPanel`, `TemplateGalleryV2`, `QueryConsole`, `SearchDrawer`), `CommandPaletteRoot`, `OnboardingRoot`.

### B2. Navigation seam (where a cockpit entry attaches)

- `frontend/lib/store/hud-types.ts:100`: `export type LeftTab = 'chat' | 'project' | 'layers' | 'components' | 'analysis' | 'exports' | 'export_layout' | 'data_sources' | 'tasks' | 'results' | 'lakehouse' | 'market' | 'modelops' | 'ops';`
- `frontend/components/layout/nav-rail.tsx`: `TAB_GROUPS` registry — append-only rows per ADR-0142 comment at line 77-78: `[{ key: 'ops', icon: Activity }]` is the newest row. Tabs declared as `{ key: LeftTab, icon: LucideIcon }`.
- Tab render switch: `frontend/components/layout/context-panel.tsx` — `{activeTab === 'ops' && <OpsConsole sessionId={sessionId} ownerToken={ownerToken}/>}` at lines 519-523 (with `PanelErrorBoundary` + i18n boundary label). `OpsConsole` imported at line 53.
- Store plumbing: `activeLeftTab`/`setActiveLeftTab` on `useHudStore` (`hud-types.ts:244-245`; `HudStore.toggleLeftDrawer(tab?)` at `useHudStore.ts:244`).

### B3. State store pattern (Zustand 5)

- `frontend/lib/store/useHudStore.ts` — main store; `persist` middleware with `partialize` (persisted keys include `activeLeftTab`, line 150), session-switch resets, and static coordinators (`HudStore.toggleLeftDrawer`).
- Slices: `frontend/lib/store/slices/{workbenchSlice,uiSlice,toolSlice,resultsSlice,dockSlice,settingsSlice,taskSlice,copilotSlice,layersSlice}.ts` (each with colocated tests).
- Separate stores: `useChatStore.ts`; `useToastStore` (`components/ui/toast`); undo history (`lib/hooks/use-undo-history.ts`); `layer-data.ts::setLayerDataSession` (per-session layer data swap on session switch).
- **Ops console precedent**: internal tab memory deliberately NOT in global store ("运维面不参与模式协调", `ops-console.tsx:5`). Recommended for cockpit view-local state.

### B4. API client pattern

- `frontend/lib/api/transport.ts` — unified `apiFetch<T>(path, opts)`:
  - `ApiError{status, body(FastAPI detail), requestId, message}`; `ApiTimeoutError`.
  - Auto `X-Request-ID`; Bearer attach + one-shot 401 refresh (via `lib/auth/tokenStore`); `ownerToken` → `X-Session-Token` (SEC-08); `timeoutMs` (default 30s); **never auto-retries POST/PATCH/CONNECT**; `parseJson:false` for 204; `signal` for abort.
  - `openStream(path, opts)` (line 403) — Response for SSE with the same auth/refresh semantics.
  - `parseSSEStream` in `frontend/lib/api/sse-stream-parser.ts` (typed `SSEEvent{event, data, id?}`).
- Typed per-domain clients live as sibling modules with exact-contract types, e.g. `lib/api/chat.ts` (`SSEEventType` union, `StepResultPayload`, `getSessionPlan`, `deleteSession`), `lib/api/workflow-runtime.ts` (parses backend `detail = "CODE: msg"` into `WorkflowRuntimeApiError{code,message,status}`), `lib/api/geocompute.ts` (`getRunEvents(runId, {afterId, ...})` → `{run_id, events, after_id, count}`, `RunEventsUnavailableError`), `lib/api/jobs.ts`, `lib/api/modelops.ts`.
- Missing: no `lib/api/mission-runtime.ts` — to be created.

### B5. SSE / WS hooks and live-data patterns

- `frontend/lib/hooks/use-sse-stream.ts` (the big chat stream hook):
  - Reconnect: `RECONNECT_OPTS = {maxAttempts: 2, baseDelayMs: 500}` (line 55).
  - **INV-2 cross-session guard** (lines 543-553): any event whose `data.session_id` ≠ `sessionIdRef.current` is dropped; unknown sid adopts the event sid only when local sid empty. Second guard for late payloads at lines 961-963. **This is the stale-session/out-of-order discipline to copy.**
  - MapSpec revision handling: `incomingRevision` (line 557) → `setMapSpecRevision` / `commitMapSpecDocument` (CAS in `lib/mapspec/session-cursor.ts`).
  - Bounded history: `MAX_CHAT_MESSAGES = 200` (line 82).
- `frontend/lib/hooks/use-session-plan.ts` — **the model pattern for a hydrate-then-delta panel**: on `sessionId` change clear to `EMPTY_SESSION_PLAN_STATE` before fetching (stale-session guard), `getSessionPlan()` hydrate (failure → hide, never block chat), `applySessionPlanEvent(eventName, data)` pure reducer for SSE deltas.
- Polling: `frontend/lib/hooks/use-cluster-poll.ts::useBoundedPoll` — discipline: `enabled=false → 0 requests`; document.hidden → pause, visible → immediate refetch; ≥3 consecutive errors → stop with error; generation guard on `resetKey`; abort in-flight on unmount/reset; `MIN_POLL_INTERVAL_MS=3000`.
- Cursor event channel: `frontend/lib/hooks/use-cluster-run-events.ts` — `after_id` cursor accumulation, `MAX_BUFFERED_EVENTS=500` ring, terminal-event stop, 404 → honest `'notfound'` channel state.
- Collab: `frontend/lib/collab/{client,protocol,store,adopt}.ts` (multiuser presence; not needed for cockpit v1).

### B6. Session handling

- `frontend/lib/hooks/use-workspace-session.ts` → `{sessionId, setSessionId, sessionIdRef, sessionTokenRef, activeSessionToken, rememberSessionToken, getSessionTokenFor, sessions, selectSession, startNewSession, refreshSessions, autoRestoreFromAnchor}` — consumed at `app/page.tsx:127-140` and passed down (`OpsConsole sessionId ownerToken`).
- Session identity/store swap: `lib/store/session-identity.ts`, `lib/store/layer-data.ts::setLayerDataSession`, `lib/session/map-state-restore.ts`.
- Cockpit rule: every panel receives `(sessionId, ownerToken)` props like `OpsConsole` and must reset state on `sessionId` change (use-session-plan pattern).

### B7. i18n

- next-intl; message files per namespace: `frontend/messages/{en-US,zh-CN}/{chat,commands,common,copilot,drawers,errors,layout,map,settings,sidebar,story,tweaks}.json`.
- Consumption: `useT(namespace)` from `frontend/lib/i18n/useT.ts` (e.g. `const t = useT('layout')`), or global `t()` from `lib/i18n/t.ts`. Boundary labels use keys like `panel.boundary.ops`.
- Namespace registration: `frontend/lib/i18n/messages.ts` (**#1336 touches this file — collision point, single-line additions merge trivially**).
- Key extraction: `pnpm i18n:extract` (`frontend/scripts/i18n/extract.mjs`). i18n contract tests live in `frontend/test/i18n/` (#1336 ran `vitest run components/geoai test/i18n`).

### B8. Virtualization

- **No third-party virtualizer** (no react-window/virtuoso/react-virtual in `frontend/package.json`).
- `frontend/lib/hooks/use-virtual-rows.ts::useVirtualRows(itemCount, rowHeight, overscan=8)` — fixed-row-height windowing, ResizeObserver viewport measure, `scrollRef` callback ref, ~40 LOC. Use for any 1000+-row cockpit lists.
- Existing stress tests proving the pattern: `frontend/test/workbench-virtual-10k.test.tsx`, `frontend/test/workbench-stress-500.test.tsx`.

### B9. Test conventions

- `frontend/vitest.config.ts`: environment jsdom, `globals: true`, `testTimeout: 15000`, setup `./test/setup.ts`, include `**/*.{test,spec}.{ts,tsx}`, exclude `tests/e2e/**` (Playwright), coverage v8 with thresholds `lines 75 / functions 70 / statements 75 / branches 60`. Alias `@` → frontend root.
- Tests are **colocated** with sources (`component.test.tsx` next to `component.tsx`); cross-cutting suites in `frontend/test/` (incl. a11y: `workbench-editing-a11y.test.tsx`; perf: `workbench-virtual-10k.test.tsx`, `incremental-think.perf.test.ts`).
- Mock style: `vi.fn()`/`vi.spyOn`, `frontend/test/test-utils.tsx`, `frontend/test/__mocks__/`, in-memory localStorage helper (`frontend/test/in-memory-local-storage.ts`). SSE adversarial/invariant suites: `lib/hooks/use-sse-stream.invariant.test.ts`, `use-sse-stream.a3-tdd.test.ts`, `use-sse-adversarial.test.ts`.
- a11y utilities: `eslint-plugin-jsx-a11y` (lint gate `pnpm lint --max-warnings 0`), hooks `use-prefers-reduced-motion.ts`, `use-inert.ts`, `use-dialog-focus.ts`.
- Typecheck is a separate gate: `pnpm typecheck` = `tsc --noEmit && tsc -p tsconfig.test.json --noEmit`.
- Backend tests: `pytest.ini` (testpaths `tests`, `asyncio_mode=auto`, timeout 60s/thread, `addopts = --ignore=tests/smoke-test-buffer.py --ignore=tests/smoke_deep_enhancement.py --cov=app --cov-report=term-missing`; markers `heavy`, `perf`, `cartography`, `real_services`). PRs in this repo run targeted suites with `-o addopts=` to drop coverage (see #1335 test plan). Parallel gate uses `pytest-xdist` (`requirements-dev.txt:15-16`): `pytest tests/unit -q -n 2` — **max `-n 2` per ADR-0153 gatebook convention**.
- Mission runtime backend tests already exist: `tests/unit/mission_runtime/{conftest.py, test_lifecycle.py, test_lease_fencing.py, test_crash_recovery.py, test_swarm_resources_goal.py}` (hermetic via injected session factory — `MissionStore(factory=...)`).
