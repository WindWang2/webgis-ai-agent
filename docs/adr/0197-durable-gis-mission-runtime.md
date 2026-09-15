# ADR-0197: Durable GIS Mission Runtime & Distributed Harness Control Plane

- Status: Proposed
- Date: 2026-09-15
- Line: `harness/durable-gis-mission-runtime-v1`
- Related: ADR-0187 (Swarm), ADR-0184 (Execution Graph), ADR-0180 (Harness Kernel / SessionPlan),
  ADR-0182 (Resource Governor), ADR-0077 (multi-pod turn ownership), ADR-0052 (durable job runtime),
  GeoCompute cluster lease_epoch fencing (V6)

## Context

Long-running GIS goals must survive chat turns, session changes, process/pod restarts, partial
execution, and interrupted specialist swarms while preserving artifact refs and deterministic
recovery. Today:

- Workflow Runtime V5/V6 provides durable DAG node state + run/node leases.
- GeoCompute cluster provides `lease_epoch` fencing for heavy jobs.
- SessionPlan / Harness Kernel provide session-scoped checkpoints.
- Specialist Swarm (ADR-0187) keeps run state **process-local** — pod restart loses unfinished swarm truth.

There is no durable **ownership envelope** that spans sessions and binds SessionPlan + workflow +
swarm + artifacts + budget under one recoverable Mission identity.

## Decision

### D1 — Mission envelope above existing systems

Introduce `app/services/mission_runtime/` as the Mission lifecycle / ledger / lease / recovery /
swarm-durability bridge. Pi remains Agent Host. Do **not** build a second agent, workflow DAG, or
job framework.

### D2 — Durable ledger (`gis_missions` + checkpoints + swarm runs)

Persist Mission state with revision CAS, goal_revision, frontier, ref inventories (session plans,
workflow instances, swarm runs, artifacts, map products, evidence), resource budget/consumption,
failure/recovery metadata. Checkpoints and swarm task receipts are separate bounded tables.
Zero Big Data: refs only.

### D3 — Distributed lease with epoch fencing

Mission ownership uses GeoCompute-style `lease_epoch` + TTL heartbeat. Stale owners cannot commit
after lease expiry and re-acquire by another worker. Two resume signals → exactly one active owner.

### D4 — Swarm durability bridge

Map swarm task settle onto durable task receipt rows under the Mission. Recovery resumes only
incomplete work; completed specialists are not repeated; destructive/unknown outcomes stay unresolved.

### D5 — Mission resource scope

Extend governor accounting conceptually with a durable `mission` cumulative ledger on the Mission
row (reservation/release/retry cost), without replacing SessionBudgetLedger or creating billing.

## Consequences

- Long-running GIS goals gain crash/distributed recovery without duplicating Workflow/Swarm engines.
- Swarm v1 process-local gap is bridged at Mission layer (ADR-0187 K-level deferral closed here).
- Parallel tracks (skills, evidence graphs, cartography) consume Mission hooks without owning lifecycle.
