# DECISIONS — Durable GIS Mission Runtime v1

## D1 — Mission is an envelope, not an engine

Mission owns lifecycle, lease, checkpoints, refs to SessionPlan / workflow instances / swarm runs / artifacts / budgets.
Execution remains Workflow Runtime + Swarm + GeoCompute + Pi. No second DAG IR, no second agent host.

## D2 — Lifecycle vocabulary

Canonical MissionState:
`created | planning | running | waiting_dependency | partially_complete | suspended | recovering | complete | failed | cancelled`

Terminal: `complete | failed | cancelled`. Transitions are whitelist-checked (pure function).

## D3 — Durable ledger tables

- `gis_missions` — row of record (state, revision, goal_revision, lease_epoch, lease owner/expiry, budget JSON, ref lists, frontier, failure/recovery).
- `gis_mission_checkpoints` — bounded ring of checkpoint snapshots (refs only, ≤16KB each).
- `gis_mission_swarm_runs` — durable swarm run + per-task receipt ledger (bounded), bridging ADR-0187 process-local gap.

Payloads never store GeoJSON/rasters — refs only.

## D4 — Lease / fencing

Mission ownership uses `lease_epoch` (int) + `lease_owner` + `lease_expires_at`.
Acquire increments epoch; every mutating write requires matching epoch (fencing).
Expired lease → another worker may acquire (new epoch); stale worker writes fail closed.

## D5 — Swarm durability bridge

On swarm start: persist run row under mission_id.
On task settle: CAS task receipt (idempotent by assignment_id).
On recovery: reconstruct incomplete frontier; skip SUCCEEDED; destructive unknown → `unresolved` (never blind rerun).

## D6 — Operation classes

`pure | idempotent | repeatable | destructive_at_most_once | compensatable`
Mapped from existing swarm/workflow `side_effect` where present.

## D7 — Resources

Extend conceptual SCOPE with `mission` above session: MissionBudgetLedger persists cumulative consumption on the mission row; live reservations remain governor process concern with durable consumed totals for recovery.

## D8 — API surface

`MissionRuntimeService` facade + optional thin diagnostics route. Kill-switch env `GIS_MISSION_RUNTIME=0` disables bridge side-effects.

## D9 — Parallel ownership

Do not implement SkillPolicy, Evidence/Claim Graph, cartography algorithms, StoryMap redesign, or a new planner.
