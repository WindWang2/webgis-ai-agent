# AUDIT — production call-path recon (Direction 01)

## Swarm process-local state (verified)

- `SwarmOrchestrator` holds task descriptors/states in memory; `status_snapshot()` is ephemeral.
- `SwarmConcurrencyGovernor` is a process singleton with `asyncio.Semaphore` + `_active` map; loop death clears active ledger.
- `SwarmBridge` in `agent_pi_bridge.py` constructs orchestrator per call; feature-gated; returns bounded summary only.
- ADR-0187 D2: "v1 集群运行态是进程内有界对象（不建 swarm_instance 表）" — still accurate on master @ c8c7a902.

## Workflow durability (verified)

- `InstanceStore.acquire_run_lease` / `release_run_lease` / node leases — suitable for execution ownership, **not** multi-session goal ownership.
- Recovery distinguishes orphan nodes vs live holders; cancelled/superseded refuse new leases.

## GeoCompute fencing (verified)

- `lease_epoch` incremented on claim; heartbeat/write paths CAS on epoch — Scenario A (stale owner) is already solved here; Mission copies this discipline.

## Harness Kernel checkpoints (verified)

- SessionPlan rotating checkpoint ring + recovery metadata; session-scoped, not cross-session mission.

## Intent / incremental replan (verified)

- `intent_diff` + SessionPlan progress carry; Execution Graph ADR-0184 incremental replan.
- Mission goal_revision should call into this rather than reinventing.

## Frontend / API

- No Mission inspector exists; prefer thin diagnostics API following workflow/geocompute projection style (bounded JSON). Large UX redesign out of scope.

## Tenancy

- org_id NOT NULL discipline on workflow/geocompute tables (ADR-0139). Mission tables must match.
