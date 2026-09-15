# BASELINE — Durable GIS Mission Runtime (Direction 01)

Execution-time master: `origin/master` @ `c8c7a902` (GOAL seeded branch tip `9c52e2d4`).
Open PRs at start: none (after #1318/#1319 merged). Alembic head: `0090_close_model_index_drift`.

## Current source of truth (by concern)

| Concern | Authoritative subsystem | Location |
|---|---|---|
| Agent host loop | Pi Agent Host | `app/agent_pi_bridge.py` |
| Session plan envelope | SessionPlan + Harness Kernel | `app/services/session_plan.py`, `app/services/harness_kernel/` |
| GIS capability / product planning | GIS Harness | `app/services/gis_harness/` |
| Executable DAG ledger | Workflow Runtime V5/V6 | `app/services/workflow_runtime/` (`store`/`driver`/`machine`) |
| Specialist swarm | Agent Swarm (ADR-0187+) | `app/services/agent_swarm/` (process-local run state) |
| Heavy compute lifecycle | GeoCompute Cluster V6/V7 | `app/services/geocompute/cluster/` (`lease_epoch` fencing) |
| Artifacts | Artifact Registry / Graph | `app/services/artifact_registry.py`, project artifacts |
| Resource admission | Harness Resource Governor | `app/services/governor/` (`SessionBudgetLedger`) |
| World model | GIS Situation | `app/services/gis_situation/` |
| Map products | Map Product runtime | `app/services/map_product_service.py`, harness product_* |
| Replay / evidence | Harness replay + workflow events | `app/lib/harness/replay/`, workflow_events |
| Session mutual exclusion | Distributed session locks | `app/services/distributed_lock.py` |
| Turn ownership (multi-pod) | PiTurnRegistry (Redis TTL) | ADR-0077 |

## Durability boundary (today)

- **Durable**: Workflow instances/nodes (DB + run/node leases), GeoCompute runs (DB + epoch fencing), SessionPlan envelopes (session store + revision CAS), artifacts (registry/refs), harness resume anchors.
- **Process-local / lost on restart**: SwarmOrchestrator task graph & in-flight assignment ledger (`SwarmConcurrencyGovernor._active`), live governor reservations (in-process), Pi subprocess turn, swarm bridge outcomes unless mirrored into SessionPlan/workflow.

## Locking boundary

- Session writes: `session_lock_registry` (Redis or memory fallback).
- Workflow: instance `run_lease_*` + per-node `lease_expires_at` + `state_revision` CAS.
- GeoCompute: `lease_epoch` fencing on every write; coordinator leadership CAS.
- Swarm: process-local asyncio semaphore only — **no distributed mission/swarm lease**.

## Recovery boundary

- Workflow: orphan RUNNING sweep on lease expiry (`find_orphan_running_nodes` / recovery module).
- Harness Kernel: interrupted turn discovery on hydrate; checkpoint ring on SessionPlan.
- GeoCompute: reclaim expired leases → requeue with new epoch.
- Swarm: **no durable recovery** — unfinished swarm dies with the process (ADR-0187 D2 explicit deferral).

## Artifact ownership

- Refs (`ref:…`) are the only agent-context carriers (Zero Big Data).
- Registry owns artifact records; SessionPlan/workflow nodes hold bound/output refs.
- No mission-level ownership envelope spanning sessions today.

## Retry / destructive semantics

- Workflow + Swarm share `pure | derived_external | destructive`; destructive = at-most-once (no auto-retry).
- GeoCompute: attempt counters + epoch fencing; uncertain outcomes must not be treated as success.

## Distributed coordination primitives available to reuse

1. Workflow run/node leases (TTL + owner token, revision bump).
2. GeoCompute `lease_epoch` fencing (stronger stale-writer rejection).
3. Redis session locks / Pi active-turn keys.
4. Collab leases (`app/services/collab/leases.py`).

**Chosen pattern for Mission**: GeoCompute-style `lease_epoch` + TTL heartbeat on the Mission row (not naïve SETNX).

## Resource-budget ownership

- Governor session/goal/turn/global chain (`SCOPE_CHAIN`).
- No `mission` scope yet — long-running goals that span sessions under-account.

## Gap this direction fills

Mission = durable ownership/lifecycle envelope **above** SessionPlan + Workflow + Swarm, with distributed fencing, checkpoint/recovery, swarm durability bridge, and mission-scoped budget/artifact refs — without a second agent/workflow framework.
