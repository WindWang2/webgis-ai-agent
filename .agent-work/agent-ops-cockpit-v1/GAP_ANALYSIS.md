# GAP_ANALYSIS — cockpit capability vs what exists at faa453a8

Legend: **EXISTS** (use as-is, with file:line), **PARTIAL** (some data/API exists, gap listed),
**ABSENT** (nothing readable over HTTP; in-process store that would back an additive projection named).

---

## 1. Mission timeline / goal revisions / states / frontier / checkpoints / blocked reasons

**PARTIAL.**

Exists:
- `GET /api/v1/mission-runtime/missions/{mission_id}` → full `MissionRecord` (state, goal_revision, revision, frontier{completed,running,pending,failed,blocked}, recovery.blocked_reason, failure, lease_owner/epoch) — `app/api/routes/mission_runtime.py:70`.
- `GET /api/v1/mission-runtime/missions/{mission_id}/diagnostics` → `MissionDiagnostics` (bounded summary incl. `last_checkpoint`, `recovery_count`, `swarm_status`, `resource_use`) — `mission_runtime.py:82`.
- Durable checkpoint ring (8/mission) in `GISMissionCheckpointRow` — `app/models/mission.py:79`.

Missing (must ADD as additive projections):
- **Mission LIST** (no `GET /missions`): cockpit cannot discover missions for the org. Backing store exists: `MissionStore` + `GISMissionRow` (query by `org_id`, order by `updated_at desc`, bounded limit).
- **Checkpoint history** (`GET /missions/{id}/checkpoints`): rows exist; in-process `MissionStore` has the query path; no route.
- **Goal-revision history / state-transition timeline**: only current `goal_revision` int and checkpoint `goal_revision`s are persisted; a per-transition event log is ABSENT. Options: (a) project timeline from checkpoint ring (has state + goal_revision + created_at + note) — no new writes; or (b) add a bounded append-only mission-event table (bigger change; not required for v1).
- Recommendation: additive GET endpoints on the `mission_runtime` router (list + checkpoints). Timeline v1 = checkpoints + current record; do not add new write-path tables.

## 2. Execution / Swarm DAG (running/failed/skipped nodes, specialists, receipts)

**PARTIAL.**

Exists:
- Workflow Runtime DAG reads (13 endpoints) — `GET /api/v1/workflow-runtime/instances/{id}` returns `nodes[]` with `state ∈ {PENDING,READY,RUNNING,SUCCEEDED,FAILED,BLOCKED,SKIPPED,CANCELLED,STALE}`, `counts`, `decisions`, `explain{why_recomputed, why_reused, blocked}` — `app/api/routes/workflow_runtime.py:239,288,309,387`. Typed frontend client already exists (`frontend/lib/api/workflow-runtime.ts`). Node retry/cancel actions exist (331, 351).
- Swarm durable receipts: `GISMissionSwarmRunRow.tasks` (Dict[task_id → SwarmTaskReceipt{state, operation_class, produced_refs, summary, error_code, attempt, idempotency_key}]) — durable, restart-safe.
- Live swarm `SwarmExecutionStatus{counts, active_task_ids, tasks}` — process-local only (`app/services/agent_swarm/orchestrator.py:652`), and swarm delegation is default-OFF (`app/agent_pi_bridge.py:2995`).

Missing:
- **Swarm run reads over HTTP** (ABSENT): receipts live in `MissionStore.get_swarm_run` / `list_swarm_runs_for_mission(mission_id, *, org_id)` — additive route needed (`GET /missions/{id}/swarm-runs`, optionally `GET /swarm-runs/{swarm_run_id}`). #1335 already added the org filter — code against it.
- Specialist identity: `SwarmTaskReceipt` has `assignment_id` but no specialist name/role field; specialists are resolved at dispatch time in-process. Show `assignment_id`/`task_id` + `operation_class` + status; do not promise specialist names unless the additive projection joins `SpecialistAssignment` at settle time (out of scope v1).
- Mission→workflow linkage: `MissionRefs.workflow_instance_refs` already lists instance ids — the cockpit DAG view can deep-link into the EXISTING workflow-runtime endpoints per ref. Reuse; do not re-serve workflow payloads from mission endpoints.

## 3. SkillPolicy view (selected skill / confidence / mode / obligations / shadow)

**ABSENT over HTTP; data EXISTS in-process.**

Backing stores:
- `HotpathTurnContext.skill_bundle` / `skill_guidance` — `app/services/gis_harness/hotpath_convergence/session_ctx.py:17-52` (per-session, process-local, 256-session FIFO).
- `SkillPolicyDecision.to_bounded_dict()` gives exactly the cockpit shape (mode, selected_skill, skill_version, trust_tier, confidence, confidence_band, eligibility_ok, situation_signature, reasons, fallback_reason, shadow_candidate, shadow_candidate_version, rejected_codes, matched_signals, clarification_code) — `app/services/gis_harness/skills/policy.py:85-102`.
- Obligations: `pi_context_card().selected_procedure.critical_obligations` (= `reasons[:4]`, `policy.py:119`); richer per-procedure obligations live in `skills/contract.py::SkillContract` and `skills/replay.py::ProcedureReplayReport` (step/obligation satisfaction).
- Caveat: `SkillPolicyDecision.reasons` is a coarse reason-code list, not a full obligation manifest; `ProcedureReplayReport` requires a plan/evidence projection input and is computed on demand.

Must ADD: `GET /api/v1/.../sessions/{session_id}/skill-decision` (additive, session-scoped, optionally authenticated like `/chat/skills` which uses `get_current_user_optional`, `chat.py:2088`). Honest empty state when flag `GIS_SKILL_POLICY=0` or context evicted.

## 4. Evidence / Claim view (claim status, why-claim, support refs, stale/contradiction)

**ABSENT over HTTP; data EXISTS in-process.**

Backing stores:
- `ClaimStore` per session — `session_ctx.get_or_create_claim_store(session_id)` (`session_ctx.py:61`); full accessor list in `evidence_claim/store.py:22-132` (`all_claims`, `all_evidence`, `all_edges`, `stats`).
- Why-claim: `verify.py::verify_claim` → `VerificationResult{status, steps[VerificationStep{stage,ok,detail,evidence_ids}], positive_proof, reasons}` (bounded `to_bounded_dict`, `contracts.py:313`); `grounding.py::grounding_projection`; `contradiction.py` → `ContradictionResult`; `freshness.py` (stale/superseded/expired).
- Note: `ClaimStore` is in-memory and #1335 re-keys it by `tenant_id|session_id` — read only through `session_ctx` accessors; expect process restart to empty it (honest empty state required).

Must ADD: `GET /api/v1/.../sessions/{session_id}/claims` (+ optional `/claims/{claim_id}/verification`) using the existing bounded `to_bounded_dict()` projections; cap page sizes (`MAX_CLAIMS=2048` worst case — enforce `limit` param, default ≤100).

## 5. Resource Governor view (budget / reservation / use / backpressure / provider health)

**PARTIAL.**

Exists:
- Durable mission budget: `MissionResourceBudget{quota, consumed, reserved, retry_cost}` already serialized in `GET /missions/{id}` (no new backend work) + `blocked_reason` / `BUDGET_EXHAUSTED:<dim>` semantics (`resources.py:57`).
- Live governor snapshot: `HarnessResourceGovernor.snapshot(session_id, turn_id, goal_id)` → `{mode, channels{name:{in_flight,waiting}}, sessions_active, live_reservations, budgets, retries, storage, slo_breach_total, cancelled_sessions}` — `app/services/governor/governor.py:381-394`; process singleton `get_governor()` (474). NO HTTP route.
- Provider health: `ProviderHealthTracker.snapshot() -> dict[str, dict]` (`app/services/provider_health.py:141`), `FabricHealthBridge.states()` (227). NO HTTP route.
- Infra health WITH HTTP: `GET /api/v1/geocompute/cluster/metrics` (geocompute.py:407), `/cluster/workers` (661), `/cluster/runs/stuck` (695), `POST /cluster/ledger/limits` (757); `GET /api/v1/health` family (`app/api/routes/health.py`).

Must ADD (additive): `GET /api/v1/governor/snapshot` (or `/cockpit/resources`) wrapping `get_governor().snapshot(session_id=...)` and `ProviderHealthTracker().snapshot()`; treat process-local scope honestly (this pod only) in the UI copy.

## 6. Replay / Debug view (bounded events, no CoT/secrets)

**PARTIAL.**

Exists:
- Bounded event-trace exemplar WITH cursor semantics: `GET /api/v1/geocompute/runs/{run_id}/events?after_id=&limit=` (≤200/page; 404 after retention; 503 typed) — `app/api/routes/geocompute.py:610-659`; frontend cursor consumer `use-cluster-run-events.ts`.
- Mission checkpoints (bounded snapshots, 16KB-capped `to_bounded_dict`) — durable but no route (see §1).
- Chat SSE resume buffer: 256 events/turn, process-local, transient — NOT suitable as a replay source (by design: cross-restart out of scope, `event_resume.py:40-60`).
- Session event log: `SessionStore.get_event_log(session_id)` (`app/services/session_data.py:748`) — no HTTP route; contents are tool/map events, not a mission audit trail.
- `ProcedureReplayReport` (`skills/replay.py`) — deterministic procedure replay projection (steps covered/missing/skipped_declared + obligations satisfied/missing). No route.

Must ADD: a bounded, read-only mission/turn event projection (e.g. `GET /api/v1/mission-runtime/missions/{id}/events?after_id=&limit=`) backed by either (a) checkpoint ring + swarm receipts + failure/recovery state (zero new writes), or (b) a new bounded mission-event ring (durable, capped). If (b), enforce the strip list from CURRENT_ARCHITECTURE A11: no `is_reasoning` token content, no tool `arguments`, banned keys `chain_of_thought/cot/raw_llm/messages/thinking` (pi_card precedent, `pi_card.py:71-73`), truncate free text to `MAX_SUMMARY_CHARS=400`.

## 7. Operator actions (suspend / resume / cancel / retry)

**EXISTS for mission lifecycle and jobs/workflow/geocompute; retry semantics differ per domain.**

- Mission: `POST /api/v1/mission-runtime/missions/{id}/suspend|resume|cancel|start` (exact contract in CURRENT_ARCHITECTURE A3). Guards: lease epoch fencing → **409** `LEASE_ACQUIRE_FAILED` / `TransitionRejected`; kill-switch → **503** `mission_runtime_disabled`; unknown mission → 404. `resume` = recovery coordinator (`{"ok": true, ...}`). **No mission-level retry** — retry = resume (recovery re-drives unfinished work; destructive ops become UNRESOLVED and are never blind-rerun, `contracts.py:149-159`). Node-level retry EXISTS in workflow runtime (`POST /instances/{id}/nodes/{node_id}/retry`) and jobs (`POST /tasks/jobs/{job_id}/retry`).
- Geocompute: `POST /api/v1/geocompute/runs/{run_id}/cancel` (501) and `POST /cluster/runs/{run_id}/reset` (713).
- The cockpit should call these EXISTING endpoints only; `worker_id` (1..128 chars) is required for mission actions — use a stable operator identity string (e.g. `ops-console:<user_id>` truncated).
- UX guard implications: action buttons must (a) confirm, (b) disable in terminal states (`complete/failed/cancelled`), (c) render 409 as "lease held by <lease_owner> (epoch N)" with retry affordance, (d) render 503 as "mission runtime disabled".

## 8. SSE live updates (mission-scoped)

**ABSENT for missions; strong generic infra EXISTS.**

- Chat SSE (`POST /api/v1/chat/stream`) is turn/session-scoped — not a mission feed. Resume buffer is transient (256 events, process-local).
- WS (`/api/v1/ws/{session_id}`) is session-scoped with JWT — same scope problem.
- No mission event stream of any kind. The durable state changes at human-observable frequency (state transitions, checkpoints, task settles) — see DECISIONS.md (d): bounded cursor polling is the v1 answer; an additive `GET .../events?after_id=` projection gives the same replay/cursor semantics as geocompute run events without new push infra.
