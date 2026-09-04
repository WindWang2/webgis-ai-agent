# ADR-0096: GeoCompute & Data Fabric V3 — Unified Execution Plane

**Date:** 2026-09-04
**Status:** Accepted
**Extends:** ADR-0094 (Enterprise Geospatial Data Fabric V2), ADR-0092 (Reproducible GIS Runtime),
ADR-0089 (Raster & RS Runtime V3), ADR-0052 (Durable Job Runtime)
**Branch:** `feat/geocompute-data-plane-v3`

## Context

Data Fabric V2 delivered truthful capability contracts, a deterministic single-plan planner, EXPLAIN,
and bounded two-source federation. Raster Runtime V4 unified the raster source/reader/execution facade.
The project workflow runtime executes tool-level DAGs with reproducible fingerprints. Three gaps remain
between this state and a production geospatial execution plane:

1. There is no **data-plane execution graph**: data operations (queries, joins, raster windows,
   materializations) execute ad-hoc inside tools; there is no cross-node cancellation, deadline,
   checkpoint, partial re-run, or descendant invalidation below the tool granularity.
2. The planner is **rule-based with hard-coded selectivity constants**, produces exactly one plan, and
   federation is structurally limited to two sources.
3. Raster content identity is fragmented across four fingerprint schemes, and several production raster
   paths (temporal engine, tile service, STAC online math) bypass the V4 runtime contracts.

## Problem

Agent/Product Plane consumers (tools, workflow steps, REST, frontend) currently reach into
plane-specific internals (adapters, managers, analysis functions) to compose data operations. Each new
composition duplicates resource guards, cancellation plumbing, and evidence assembly, and none of it is
reproducible below the tool boundary.

## Decision

### D1 — Two-plane boundary (enforced, not conventional)

```text
Agent / Product Plane   (tools, Pi, chat, MapSpec, SessionPlan, frontend)
        ↓ stable contracts only
GeoCompute / Data Plane (geocompute executor, data_fabric query runtime, geo_raster, geo_analysis)
        ↓
Storage / External Data Sources
```

The Data Plane understands only: `DatasetDescriptor`, `DatasetVersion`, `QuerySpec(V2)`,
`ExecutionPlan`/`ExecutionNode` (new), `ArtifactRef`, `QueryEvidence`, `RuntimeManifest`, resource
budgets, execution status, and lineage. It must not import `gis_harness`, `agent_pi_bridge`, chat/turn
semantics, or frontend state. This is enforced by an AST-based import-boundary contract test
(`tests/unit/test_geocompute_boundary.py`), not by convention. Existing lib→services boundary violations
outside the V3 scope are pre-existing debt and are not silently expanded.

### D2 — Unified Geo Execution DAG = lower-level ExecutionPlan + coordinator executor

The execution graph is a **new, additive contract owned by the Data Plane** that (a) is produced by
compiling data operations, (b) executes through existing primitives, and (c) can be consumed by tools
and (later) the workflow runtime. Specifically:

- `app/services/geocompute/plan.py` defines `ExecutionNode` and `ExecutionPlan` (serializable,
  fingerprinted, additive; node categories reuse existing vocabulary: `SOURCE_SCAN`, `QUERY`, `FILTER`,
  `SPATIAL_JOIN`, `AGGREGATE`, `VECTOR_OPERATION`, `RASTER_WINDOW_OPERATION`, `MATERIALIZE`,
  `ARTIFACT_REGISTER`, …).
- `app/services/geocompute/executor.py` is a **coordinator over existing truth**: heavy/dispatched nodes
  execute *through* the existing durable-job runtime (ADR-0052) or bounded in-process worker threads;
  cancellation reuses `CancellationToken` + the durable-job cancel fact; node outputs checkpoint into the
  existing session/project ref + artifact stores; completed-node reuse keys on the deterministic node
  fingerprint; descendant invalidation reuses provenance fingerprint semantics.
- **No second workflow engine, no new job table** (amends ADR-0052 in writing: the execution plan may
  *dispatch through* `AnalysisTask` rows; it adds none). WorkflowEngine remains the project-domain,
  tool-level runtime; it may compile to ExecutionPlans in a later increment.

Node contracts carry: id, operation, inputs/outputs, dataset fingerprints, parameters, CRS expectations,
resource estimate, execution policy (`in_process | durable_job`), deterministic fingerprint, cache/reuse
policy, retry policy (transient-safe only), deadline, cancellation flag, locality hints, evidence, and
lineage links. Failure modes are typed: budget exceeded, deadline exceeded, cancelled, unsupported,
node failed (with retry-safety classification). Degraded/fallback execution is explicit evidence, never
silent.

### D3 — Cost-based query optimization inside the existing planner contract

V3 optimizer capability is added **inside `plan_query`'s existing signature and `QueryPlan` shape**
(adapters already call `plan_query` and attach the plan to results; the "plan equals execution"
invariant is preserved by construction):

- **Statistics**: bounded, truthful `DatasetStatistics` (row count, extent, geometry type, column
  null-fraction/NDV where the source exposes them, index presence, resolution/overview metadata,
  `revision_strength`, per-field `confidence`). Unknown stays unknown; assumptions are labeled.
  Harvested from descriptor metadata (PostGIS meta profile incl. a best-effort `pg_stats` probe,
  GeoParquet footer) and held in a bounded process-level TTL store keyed by descriptor fingerprint;
  collection is best-effort, never blocks queries, and DB persistence is deferred (see Deferred).
- **Selectivity**: replaces the four hard-coded constants with a stats-aware model whose no-stats
  defaults are exactly the V2 constants (behavior-preserving baseline), with bounded, explainable
  estimates for equality/IN/range/temporal/bbox/spatial predicates, group-by cardinality, and join keys.
- **Plan alternatives**: bounded enumeration (hard cap, no combinatorial growth) over pushdown vs local,
  join order/build-probe side, aggregate-before-join, projection/filter-before-transfer,
  materialize vs stream, vector-tile vs feature path, raster overview vs full resolution. `QueryPlan`
  gains additive fields (`alternatives`, `cost` breakdown, `rejected` reasons) for EXPLAIN; existing
  consumers keep working.
- **Cost model**: explainable relative cost (rows/bytes scanned, bytes transferred, rows emitted, memory
  class, join candidates, remote requests, latency class). No fake precision; estimates carry confidence.
- **N-source federation**: an additive `FederatedChainRequest` (hard cap 4 sources by default) with
  cost-based left-deep join ordering and fail-fast budget checks; the existing two-source
  `left/right` API remains fully functional.

### D4 — Raster Runtime V5 = consolidation, not a new runtime

- One **content fingerprint authority** for raster products (geo_raster/fingerprint);
  `RasterReader._fingerprint` delegates to it (value-compatible). The artifact-plane persisted
  fingerprint (`raster_spec.raster_content_fingerprint`) and the V3 writer digest remain frozen,
  documented siblings in the same contract family — no persisted key format changed.
- `rasterio_env` hardening becomes a **runtime property** used by all raster paths (tile service,
  temporal engine, STAC online math), not a reader-lifetime detail.
- The temporal engine converges on the V3/V4 grid-alignment authority (removing the third alignment
  implementation); STAC online numerics are documented as a bounded, decimated preview path (not a
  production compute path) unless later converged.
- Windowed reads/writers gain multi-band support; resource guards are reworked for multi-band without
  loosening ADR-0042 caps. Large-raster paths gain tests that fail on unexpected whole-band reads.

### D5 — Distributed execution policies (ADR-0052 amendment)

Execution policy selection (`in_process | durable_job`) with queue routing, resource classes, worker
capability hints, deadline propagation into dispatched jobs, and idempotent dispatch via the existing
execution-key mechanism. Local in-process execution remains the default and first-class mode. No new
task framework, no new job table, no external scheduler (re-affirming ADR-0052's rejected alternatives).

### D6 — Resource governance

Hierarchical budgets (session → execution → node scopes; tenant/project mounting is scaffolded in
`ResourceGovernor` but not yet wired to identity providers) enforced at admission (atomic
reserve-with-rollback along the scope chain), during execution (bounded transfer/materialization),
and in evidence. Budget exhaustion produces typed errors with actionable lower-cost alternatives.
No billing system. Node deadlines are cooperative: worker threads are unkillable (repo-wide
constraint), so a non-checkpointing operation can exceed its deadline until its next checkpoint —
hot loops in geo_analysis operators carry cooperative checkpoints.

### D7 — Reproducibility, caches, observability

- Runtime manifests gain the execution-plan fingerprint; reopening persisted plans under incompatible
  runtime semantics yields explicit `stale`/degraded disclosure (extending `is_stale_plan`), never
  silent recomputation claims.
- ref_lifecycle-governed caches (payload/spatial-index/tile) invalidate via the existing contract
  and authoritative fingerprints/`content_revision`; cache lifetime is never a correctness mechanism.
  Race windows (invalidate-during-build, overwrite-during-encode, hit-after-revision, failed
  materialization, rollback) are locked by tests. Engine-local stores (`StatisticsStore`,
  `NodeResultStore`) are bounded TTL/LRU performance hints outside the ref_lifecycle contract.
- Observability adds structured execution-trace events (correlation ids, node state transitions,
  counters) using the existing `RuntimeContext` + bounded-writer patterns; no new telemetry dependency;
  no secrets or raw payloads in events.

## State Ownership

| Truth | Owner | V3 change |
|---|---|---|
| Desired map | MapSpec | none |
| Session plan | SessionPlan | none |
| Tool execution entry | ToolRegistry + ToolDispatchService | none |
| Durable job row / state machine | `AnalysisTask` + `jobs/lifecycle` | nodes dispatch through it |
| Workflow identity/fingerprints | WorkflowEngine + provenance | plan fingerprint is additive sibling |
| Query truth | QuerySpecV2 + `plan_query` | optimizer lives inside |
| Catalog truth | `spatial_catalog_items` | stats store is an in-process sibling (TTL, fingerprint-keyed) |
| Cache invalidation | `ref_lifecycle` | reused, not extended |
| Artifact identity | ArtifactRegistry / Artifact / MapProductVersion | plan lineage links are additive |

## Failure Semantics

Typed, classified failures (`BUDGET_EXCEEDED`, `DEADLINE_EXCEEDED`, `CANCELLED`, `UNSUPPORTED`,
`NODE_FAILED{retry_safe}`); retry only for transient-safe failures; cancellation/deadline propagate
down the graph; worker death converges to the existing stale-sweep semantics; partially written
materializations are atomic (temp + replace) and never partially registered.

## Compatibility

- All existing REST routes, tool signatures, legacy QuerySpec normalization, MapSpec, artifact refs,
  workflow manifests, local execution, Pi integration, and frontend clients keep working.
- `QueryPlan`/`FederatedQueryRequest`/capability changes are additive; capability-honesty contract tests
  continue to gate every adapter declaration.
- Migrations are additive (Alembic 0024+, dual dialect SQLite/PG, no runtime DDL).

## Performance red lines

No O(N²) joins where an index exists; no unbounded pair generation; no whole-raster reads outside
budget-approved paths (test-enforced); no giant inline GeoJSON/feature collections into LLM context;
no blocking I/O on the event loop; executor concurrency bounded and never sharing the tool-registry
semaphore; planner enumeration hard-capped.

## Rejected Alternatives

- **Extend WorkflowEngine to execute data nodes directly** — couples the project tool runtime to data-op
  scheduling and would drag workflow identity rules into the data plane; chosen instead: additive plan
  contract consumed *by* tool-level runtimes later.
- **New job table / Temporal / external scheduler** — rejected by ADR-0052; amendment keeps its truth.
- **Standalone optimizer service returning plans that adapters then re-derive** — violates "plan equals
  execution"; optimizer stays inside `plan_query`.
- **OpenTelemetry SDK dependency now** — structured events with OTel-compatible field semantics; adopt
  the SDK only when a collector actually exists.
- **Zarr adapter** — zero in-repo demand and no dependency justification today; revisit on demand.

## Deferred

- FDW-style full-pushdown N-source federation (V3 ships bounded local-join N-source).
- Durable (DB) persistence of dataset statistics (in-process TTL store ships first; stats are a
  performance hint — loss on restart is honest and harmless).
- Parallel branch execution inside WorkflowEngine tool graphs.
- GeoArrow vector interchange carrier (vector large-data envelope documented; in-memory contract stays).
- Cross-process stats/cache invalidation broadcast (in-process TTL + sync diff remains sufficient).
- Zarr, WFS 3/CQL2-JSON full dialect.
