# Adversarial Review — Durable GIS Mission Runtime (ADR-0197)

Date: 2026-09-15 (Asia/Shanghai)
Branch: `harness/durable-gis-mission-runtime-v1`
Baseline master: `c8c7a902`

## Axes

| Axis | Finding | Severity | Disposition |
|---|---|---|---|
| Architecture | Mission is envelope over Workflow/Swarm/Governor; no second DAG/agent | — | Pass |
| Durability | `gis_missions` + checkpoints + swarm runs; refs only | — | Pass |
| Distributed races | `lease_epoch` fencing rejects stale writes; contend/expiry covered by tests | — | Pass |
| Idempotency | Swarm task settle + budget charge keyed; duplicate delivery harmless | — | Pass |
| Crash consistency | Destructive RUNNING → UNRESOLVED on recover; never blind-rerun | — | Pass |
| Transaction boundaries | Per-row CAS on revision+epoch; SQLite OperationalError → StoreUnavailable | P2 | Acceptable for v1; Postgres production path identical |
| Security / tenancy | org_id on all tables; get_mission org filter; API 404 on cross-org | — | Pass |
| Resource leaks | Suspend releases lease; terminal clears lease | — | Pass |
| Artifact lifecycle | Ownership is ref lists into existing registry — no second registry | — | Pass |
| Backward compatibility | Kill-switch `GIS_MISSION_RUNTIME`; SwarmBridge mirror opt-in via mission_id | — | Pass |
| Performance | Bounded lists/checkpoint ≤16KB; no GIS payloads in mission rows | — | Pass |

## P0/P1

None confirmed after local matrix (17 hermetic tests).

## Residual gaps (honest)

1. **Pi auto-binding**: Mission is not yet auto-created on every chat turn — callers/API create Missions; SwarmBridge mirrors only when `mission_id` passed.
2. **Workflow instance deep link**: Mission stores workflow_instance_refs but does not yet drive Workflow Driver recovery (reuses existing workflow recovery when refs present).
3. **Governor live reservations**: durable cumulative ledger only; live slots remain process-local (by design, ADR-0182).
4. **Frontend inspector**: diagnostics API only; no UX redesign (out of ownership).

## Test evidence

```text
pytest tests/unit/mission_runtime/ -q  → 17 passed
```

Covers lifecycle, lease fencing, crash/recovery matrix, swarm partial/idempotent settle, resources, goal revision, org isolation.
