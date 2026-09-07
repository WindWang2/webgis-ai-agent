# GeoCompute & Data Fabric V4 — Data Plane Guide

ADR-0101 (`docs/adr/0101-geocompute-data-fabric-v4.md`) is the governing
design document. This guide maps the V4 subsystems to code and states the
operational contracts each one upholds.

> **V5 note:** the data control plane build on top of V4 (durable artifact
> store, workspace V4, ingest seam, capability queues, governor gauges,
> quota/retention/GC) is documented in `v5-data-control-plane.md`
> (ADR-0104). This guide remains accurate for the V4 execution/query plane
> it describes.

## Execution plane (`app/services/geocompute/`)

| Module | Responsibility |
|---|---|
| `plan.py` | `ExecutionPlan` V2 contract (EXECUTION_PLAN_VERSION=2): payload-type contract (`produces`/`accepts`), `resource_class`, `deterministic`, `upstream_fingerprints`, `lineage_inputs`, `evidence_schema`; bounded exponential `RetryPolicy`. |
| `normalization.py` | Canonical pre-fingerprint normalization: sorted keys, tuple→list, set→sorted list, CRS spelling equivalence (`epsg:4326` ≡ URN), non-finite float markers, deterministic degradation for non-JSON values (strict mode rejects at validation). |
| `graph.py` | Validation (materialization sequence, side-effect retry guard, deterministic⇒no-reuse, cross-plane parameter guard, payload-contract edges, impossible CRS), topo/ready helpers, `checkpoint_reuse_key` (cross-plan, upstream-verified) vs `node_reuse_key` (plan-scoped for external sources). |
| `executor.py` | Ready-set scheduler (no wave barriers), governor weighted admission (concurrency slots), checkpoint verification against upstream output fingerprints, classified retries with deadline-aware refusal, bytes+rows charging. |
| `errors.py` | `FailureClass` taxonomy; retry whitelist (`RETRYABLE_FAILURE_CLASSES`, extended by `PARTIAL_MATERIALIZATION` when `retry_transient_only=false`). |
| `durable.py` | AnalysisTask dispatch (no second job truth), capability hints, WORKER_LOSS classification for stale jobs. |
| `drift.py` / `replay.py` | Plan staleness verdicts; trace replay invariant validator (test tooling). |
| `reproducibility.py` | Execution bundle (reproducible / conditionally_reproducible / stale / source_unavailable / non_deterministic) + bounded lineage projection. |

## Query plane (`app/services/data_fabric/`)

| Module | Responsibility |
|---|---|
| `statistics.py` | `DatasetStatistics` (+`row_group_count`/`total_bytes`), process TTL store, `DurableStatisticsStore` (advisory DB persistence, fail-open, explicit expiry), `collect_geoparquet_statistics` (honest footer producer). |
| `feedback.py` | Bounded planner feedback: per (dataset fp, operator), TTL 1h, ≤8 winsorized samples, ≥3 to act, factor clamp [0.1,10], learns only from successful runs, one-flag disable. |
| `federation.py` | Bounded `cost_stats` order enumeration (≤24 candidates, join cardinality via ndv), id-addressed joins, semi-join right-side reduction, server-side first hop, opt-in derived projection. |
| `pushdown.py` | Pushdown class model (exact / equivalent / coarse / unsupported), per-plan disclosure in `QueryPlan.pushdown_classes`, planner↔capability parity (`violation` labels). |
| `streaming.py` | Chunk-oriented scan/filter/bbox/project/transform/aggregate/partition seams with batch-boundary collaboration points and governor-aware batch sizing. |
| `vector_carrier.py` | GeoArrow (WKB) carrier behind optional pyarrow probe; chunked batches; GeoParquet interop; never exposed to tool/LLM context. |
| `spatial_index_runtime.py` | Fingerprint-addressed STRtree reuse cache, grid + H3 partitioning (bounded). |

## Raster runtime (`app/lib/geo_raster/`)

- `windowed.py`: multi-band windowed execution (`bands=…`) under the 512 MiB
  window budget; remote sources route through the read policy.
- `remote.py`: `RemoteReadPolicy`/`RemoteReadSession` — transient-only bounded
  retry, per-session request/byte budgets, host-keyed provider health via the
  existing circuit breaker, cancellation-aware backoff. Local sources keep the
  zero-overhead path.
- `../geo_analysis/raster_grid.py`: single grid-alignment authority
  (`pixel_grids_aligned`); `raster_windowed.py`: per-band writer statistics,
  color interpretation preservation, atomic materialization with overviews.

## Caches & invalidation

- `ref_lifecycle.py` remains the only invalidation truth.
- `singleflight.py`: thread-context per-key single-flight (builder crash
  propagates; timeout degrades to rebuild; bounded inflight).
- `cache_broadcast.py`: optional Redis pub/sub **notify** layer — bounded
  payload-free messages; no-Redis mode unchanged; correctness never depends
  on receipt.

## Governance

- `budgets.py`: atomic `ensure_scope`, `reserve`/`release` with concurrency
  dimension, chain-walk admission with compensation rollback.
- `api.py`: scope chain `tenant:{org} → project:{pid} → session:{sid} →
  execution` mounted from existing identity truth only.

## Key red lines (unchanged or strengthened)

1. No second job table, workflow engine, or lineage store.
2. Data Plane never imports Agent/Product Plane state (AST-enforced).
3. Statistics/feedback/caches are performance hints — never correctness truth.
4. No unbounded enumeration: federation ≤4 sources, ≤24 order candidates,
   retry ≤4 attempts, trace ring 1024, broadcast messages bounded.
5. No whole-raster reads outside budgeted paths; window-sufficient remote
   reads never download full rasters.
6. Raw Arrow payloads never cross into tool/LLM context.
