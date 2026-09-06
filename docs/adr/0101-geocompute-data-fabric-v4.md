# ADR-0101: GeoCompute & Data Fabric V4 — Distributed Execution & Data Foundation

**Date:** 2026-09-06
**Status:** Accepted
**Extends:** ADR-0096 (GeoCompute Data Plane V3), ADR-0094 (Enterprise Geospatial Data Fabric V2),
ADR-0052 (Durable Job Runtime), ADR-0092 (Reproducible GIS Runtime)
**Branch:** `feat/geocompute-data-fabric-v4`

## Context

V3 delivered the two-plane boundary (AST-enforced), a lower-level `ExecutionPlan`, durable-job
dispatch, a rule-optimized `plan_query` with bounded N-source (≤4) left-deep federation, Raster
Runtime V5 consolidation, hierarchical budget scaffolding, execution tracing, and artifact/lineage
links. A code-level forensic audit (not just ADR text) confirmed the V3 deferred items are still
deferred and surfaced the exact maturity gaps V4 addresses:

1. The executor schedules by hard wave barriers; `graph.invalidation_set`/`descendants_of` exist
   but are not wired into the run loop (no checkpoint/partial-rerun); `RetryPolicy` backoff fields
   are dead policy; failure classification is a single `retry_safe` bit.
2. Statistics are process-local only (TTL 60 s); the declared `geoparquet_footer` collector has no
   producer; no execution→estimate feedback exists; federation join ordering uses caller hints only.
3. Pushdown is binary per capability; there is no coarse-prefilter/equivalent-transform distinction
   and no single automated capability-honesty gate.
4. Vector interchange is GeoJSON/in-memory only; no streaming seams; spatial index reuse is ad hoc.
5. Raster: multi-band `execute_windowed` is an unwired extension point; color interpretation is
   never preserved; one duplicate grid-alignment implementation survives; remote reads have zero
   retry/cancellation/request-budget semantics.
6. Governance: tenant/project/node scopes are unwired enum values; no concurrency dimension;
   bytes are never charged; `ensure_scope` is not atomic.
7. Cache invalidation has no cross-process broadcast; no stampede coordination.

## Problem

The substrate must carry large vector/raster/temporal workflows reproducibly and efficiently below
the tool granularity, without the Data Plane learning anything about turns, chat, recipes, or UI.

## Decision

### D1 — Two-plane boundary unchanged and strengthened

The Data Plane (`geocompute`, `data_fabric`, `geo_raster`, `geo_analysis`, `geo_processor`) still
understands only execution/data contracts. New checks join `tests/unit/test_geocompute_boundary.py`
coverage: plan validation rejects cross-plane semantics in node parameters (a shallow forbidden-key
guard — `prompt`, `map_spec`, `session_plan`, `ui_state`, …), and trace/replay/lineage projections
remain bounded, payload-free. No new plane-facing module may import Agent/Product Plane state.

### D2 — ExecutionPlan V2: canonical normalization + full node contract

`EXECUTION_PLAN_VERSION` bumps to 2 (fingerprint namespace change; old reuse entries expire by
design). Nodes gain additive, defaulted fields: payload type contract (`produces`/`accepts`,
`PayloadKind`), `resource_class` (memory/cpu/io 1..5), `deterministic` (false ⇒ reuse forbidden),
`upstream_fingerprints` (input node → fingerprint; checkpoint verification), `lineage_inputs`
(links to existing ArtifactRef/DatasetVersion identities — no second lineage store), and
`evidence_schema`; `NodeEvidence` gains `failure_codes` and `checkpoint_verified`.
`app/services/geocompute/normalization.py` canonicalizes fingerprints: sorted keys, tuple→list,
set→sorted list, enum→value, equivalent CRS spellings (`epsg:4326` ≡ `URN …EPSG::4326` ≡
`EPSG:4326`), non-finite floats → deterministic markers; non-JSON-native values degrade to a
deterministic marker in fingerprints but are **rejected at validation** (strict mode), so
instability surfaces at admission, never mid-run. Validation additions: materializing categories
require inputs; side-effect categories (`materialize`/`artifact_register`/`export`) refuse
`max_attempts > 1` without an explicit `parameters.idempotent` declaration; payload-contract edge
compatibility; impossible CRS expectations (`reproject` + `allow_reproject=false`); upstream
fingerprint keys must be declared inputs.

### D3 — Dependency-aware bounded scheduling (not wave barriers)

The executor keeps deterministic node state transitions and the existing evidence schema, but
schedules from a ready-set (indegree-driven) instead of hard wave barriers: an independently
scheduled branch no longer waits for an unrelated slow wave. Concurrency stays bounded (existing
`max_workers` clamp), admission of each node passes through `ResourceGovernor` weighted by its
resource class (concurrency slots are a new budget dimension), backpressure is structural (a node
occupies at most one queued slot; no unbounded task creation), cancellation/deadline propagate to
queued and running nodes, and worker threads remain cooperatively checkpointed (repo-wide
no-force-kill constraint; reservations are held until work settles). Durable nodes still dispatch
through the existing AnalysisTask runtime — no second job truth.

### D4 — Checkpoint, partial rerun, descendant invalidation

`invalidation_set`/`descendants_of` are wired into the run loop: completed-node results remain
checkpointed in the owner-scoped `NodeResultStore`; a rerun validates each checkpoint against the
node's current `semantic_fingerprint` **and** its declared `upstream_fingerprints`, rejecting
stale entries as evidence (`checkpoint_verified=false`) rather than silently reusing; changed
upstream inputs invalidate descendants; unchanged branches reuse. Failed materializations clean
their temp state (existing atomic-output contract) and never register artifacts.

### D5 — Honest retry semantics

Retry classification stops being one bit: failures are typed as transient-remote / transient-DB /
worker-loss / retry-safe-idempotent / deterministic-unsupported / invalid-data / budget-exceeded /
deadline-exceeded / cancelled / scientific / partial-materialization, with only explicitly
safe classes retried. Backoff is bounded exponential from `RetryPolicy` fields; jitter applies
only when deterministic replay is not required (`jitter=false` for replayed runs); retries are
deadline-aware (no retry that cannot finish inside the deadline) and never apply to invalid
parameters or deterministic scientific failures. Provider-level health reuses
the existing `data_fabric` circuit breaker; remote raster (GDAL) errors are
classified by a local conservative string-marker classifier (rasterio errors
are not `DataFabricError`) — breaker shared, classifier local by necessity.

### D6 — Durable advisory statistics + adaptive collection + bounded feedback

Statistics remain **advisory** (never correctness truth; query results never depend on them).
A new bounded DB persistence (additive Alembic migration, dual-dialect, idempotence-guarded per
repo convention) keys rows by dataset fingerprint + revision identity with explicit
`collected_at`/`confidence`/`revision_strength`; retention is bounded; invalidation is safe
(stale rows are disclosed, not silently trusted). Collection stays bounded and best-effort:
PostGIS metadata/pg_stats (existing) and the GeoParquet footer (an honest producer for the
previously declared `geoparquet_footer` collector) ship in this increment; FlatGeobuf
metadata, raster headers/overviews, and bounded sampling are deferred (the store's
`collector` label makes adding them additive). Estimates carry confidence classes (exact / metadata-derived / sampled /
heuristic / unknown). A bounded, explainable, disableable feedback loop folds observed
rows/bytes/latency back into future estimates for the same (dataset fingerprint, operator class),
scoped by version, TTL-bounded, never learning from failed/partial executions, with drift
disclosure when estimates and reality diverge persistently.

### D7 — Federation V4: capability-aware pushdown + bounded smarter planning

The `plan_query` signature and "plan = execution" invariant are unchanged. Federation gains, under
the existing hard cap: semi-join reduction (send keys, not rows), aggregate/projection-before-
transfer, remote filter/spatial/bbox/temporal prefilter, join-key statistics, and a bounded
left-deep vs bushy comparison at the capped N (no exponential search). Pushdown becomes a truthful
capability model: adapters declare per-predicate-class support as **exact**, **equivalent
transform**, **coarse prefilter** (e.g. remote bbox → local exact geometry predicate), or
**unsupported**; the planner never pushes unsupported semantics and EXPLAIN/evidence discloses
which class ran. Database-native execution covers same-source chains: the first hop of a
chain whose two sides share a source id and support `server_spatial_join` runs server-side
(typed failures surface; unexpected failures fall back locally with a warning); multi-hop
in-database federation stays deferred. Local bounded federation remains the first-class
fallback — no FDW deployment is required.

### D8 — Large vector foundation

A GeoArrow-family interchange carrier is added behind an availability probe: if `pyarrow` is
importable, features convert to chunked Arrow/GeoArrow-encoded tables and back (and to GeoParquet)
with schema/nulls/CRS metadata preserved; if unavailable, the carrier raises a typed
`VECTOR_CARRIER_UNAVAILABLE` and callers keep the feature-payload path — pyarrow/geoarrow do
**not** become required dependencies. In this increment the carrier is a self-contained
conversion/interop module; flowing Arrow batches *between* the streaming seams is deferred — the
seams themselves (bounded batches under governor pressure) cover scan, attribute filter, bbox
filter, projection, lightweight transform, aggregate, partition, and streaming materialization on
feature dicts with the same semantics as the local operators.
An execution-time partition/index facility (STRtree reuse, grid/tile and H3 (v4 API) partitions,
bbox partition) is fingerprint-scoped, bounded, thread-safe, invalidated through authoritative
revision identity, and never a correctness mechanism.

### D9 — Raster Runtime V6

Multi-band maturity is finished, not redesigned: multi-band windowed execution is wired as a real
capability, writes preserve per-band nodata/dtype and color interpretation, per-band statistics are
finalized (band-1 repetition removed), tiled/compressed/overview/atomic-write contracts carry over
unchanged. Temporal workflows converge on the single `raster_grid` alignment authority (the
remaining duplicate in `spatial_tasks` is removed); alignment plans declare resampling method,
missing-acquisition and nodata policy, target grid, and time-order determinism — no silent
resampling. Remote raster access gains request budgets, bounded retry (transient-only), range-read
semantics per COG, connection reuse, provider health via the existing circuit breaker, cancellation
checkpoints between windows/blocks — never a full remote download where a window suffices
(test-enforced). Bounded prefetch is deferred (reads are window-granular).

### D10 — ResourceGovernor V4

The scope chain tenant → project → session → execution → node is wired only where authenticated
identity truth exists (`actor_ids` / project-scoped refs); no second authorization identity is
invented. `BudgetLimits` gain an explicit concurrency dimension; admission remains atomic
reserve-with-rollback along the chain (and scope creation becomes race-free); bytes are charged
where measurable (materialized/transferred estimates and actuals); exhaustion keeps producing typed
errors with lower-cost suggestions.

### D11 — Cache & invalidation V4

`ref_lifecycle` remains the invalidation authority. An optional cross-process broadcast (Redis
pub/sub — Redis is a declared dependency, runtime-optional via `USE_REDIS`) carries **ids and
reasons only**, never payloads; in-process mode works unchanged; correctness never depends on
receiving a broadcast (fingerprint/revision validation stays authoritative; missed broadcasts
recover safely): ref invalidation publishes, the app lifespan starts the listener, and the listen
path applies events through `ref_lifecycle` with the authority's own types. Per-key single-flight
coordination prevents stampede rebuilds in both modes (wired into describe cache rebuilds);
builder crash/timeout/invalidation-during-build keep the existing race-window guarantees.

### D12 — Lineage, reproducibility, observability

Execution nodes link to existing `ArtifactLineage`/provenance identities via `lineage_inputs`;
lineage projections are bounded and project-scoped, and never leak payloads to LLM context. A
reproducibility bundle (runtime manifest fingerprint + plan/execution-plan version + per-node
fingerprints and bounded parameter digests + declared dataset fingerprints + CRS + backend variant
+ materialized refs + drift verdict) classifies runs as reproducible / conditionally reproducible /
stale / source-unavailable / non-deterministic; dataset *revision* identities and seed metadata are
deferred (declared fingerprints are disclosed as such). Tracing extends the existing bounded ring with the full node lifecycle
(admitted/optimized/queued/admitted-started/cache-hit/ checkpoint/retry/fallback/durable-dispatch/
progress/completed/failed/cancelled/deadline/materialized/artifact-registered/finished) with
correlation ids, reason codes, and bounded counters. Replay tooling is **test-only**: recorded
bounded traces assert state-machine invariants (no impossible transitions, no post-cancellation
artifact registration, retry/resource-release correctness) — it is not a second engine.

### D13 — Security propagation

User/session/project identity entering an ExecutionPlan is propagated (operator context, durable
dispatch params, cache/owner scoping) using existing auth truth: project-scoped refs stay
project-scoped, artifact reads enforce existing ownership, durable nodes receive authorization
context, cache keys never leak cross-tenant content, traces carry no secrets, local-file adapters
keep root/symlink guards, remote URLs keep SSRF guards. Materialization keeps atomic replace and
gains adversarial test coverage (traversal, symlink escape, temp races, filename injection,
untrusted extensions).

## State Ownership (unchanged truths)

| Truth | Owner | V4 change |
|---|---|---|
| Durable job row / state machine | `AnalysisTask` + `jobs/lifecycle` | capability hints + honest no-worker behavior only |
| Query truth | QuerySpecV2 + `plan_query` | stats/feedback/pushdown live inside |
| Cache invalidation | `ref_lifecycle` | optional broadcast *notifies*; never owns truth |
| Artifact identity | ArtifactRegistry (session) / Artifact+ArtifactLineage (project) | nodes link, never duplicate |
| Catalog truth | `spatial_catalog_items` | durable stats are an advisory sibling |
| Workflow identity | WorkflowEngine + provenance | plan/runtime fingerprints remain additive siblings |

## Failure Semantics

Typed, classified failures as in D5; cancellation/deadline propagate queued→running→materializing;
a cancelled node never registers a valid artifact after the fact; retry storms cannot duplicate
artifacts (idempotency keys + side-effect retry guards); partial materializations clean up.

## Compatibility

- All existing REST routes, tool signatures, plan JSON (new fields optional), legacy QuerySpec,
  MapSpec, artifact refs, workflow manifests, and Pi integration keep working.
- `QueryPlan`/evidence changes are additive; capability-honesty is additionally locked by an
  automated flag-vs-behavior contract test family.
- Migrations are additive (Alembic, dual SQLite/Postgres dialect, idempotence-guarded, no runtime DDL).
- pyarrow/geoarrow remain optional: availability-probed with honest degradation.

## Performance red lines

No unbounded plan enumeration (federation caps unchanged); no O(N²) joins where an index exists;
no whole-raster reads outside budget-approved paths; no giant payloads into LLM context; no
blocking I/O on the event loop; scheduler concurrency bounded, never sharing the ToolRegistry
semaphore; no unbounded task creation; broadcast messages bounded and payload-free.

## Rejected Alternatives

- **Second scheduler / Temporal / new job table** — rejected (ADR-0052); V4 schedules in-process
  nodes and dispatches durable ones through the existing runtime.
- **Black-box cost-based optimizer search** — bounded, explainable rewrites with rejected-reason
  evidence only.
- **Making statistics authoritative** — they stay advisory; correctness never depends on them.
- **Requiring PostgreSQL FDW deployment** — native in-database variant is capability-based, not
  deployment-based; local federation stays first-class.
- **Hard pyarrow/geoarrow dependency now** — carrier is optional with honest degradation.
- **Replacing the wave executor with an async event loop** — thread-based cooperative execution
  is the repo constraint; scheduling logic stays synchronous and deterministic.
- **OpenTelemetry SDK** — structured events keep OTel-compatible field semantics; adoption waits
  for a collector (carried from ADR-0096).

## Deferred

- Zarr adapter; WFS 3 / full CQL2-JSON dialect (carried from V3).
- Cross-process budget accounting (broadcast invalidation covers caches, not budgets).
- Byte-for-byte reproducibility of remote changing sources (conditional reproducibility only).
- Multi-process governor aggregation (single-process admission stays the truth per process).
- Durable-job capability-based routing across heterogeneous workers (hints declared; enforcement
  waits for a real multi-pool deployment).
