# GeoCompute & Data Fabric V4 — Working Notes

- Baseline SHA: `544a09c0c80798e262f60ab591cbcc5f0ee95960` (origin/master, 2026-09-05)
- Branch: `feat/geocompute-data-fabric-v4`
- Worktree: `/home/kevin/projects/webgis/webgis-ai-agent-geocompute-v4`
- Goal: production-grade execution/data substrate (V3 → V4); PR only, no auto-merge; local verification is the gate.

## V3 ADR-0096 deferred items (to verify against code)

1. FDW-style full-pushdown N-source federation
2. Durable (DB) persistence of dataset statistics
3. Parallel branch execution (tool/workflow graph level)
4. GeoArrow vector interchange carrier
5. Cross-process stats/cache invalidation broadcast
6. Zarr, WFS 3/CQL2-JSON full dialect

## V3 ADR-0096 key decisions to preserve

- D1 two-plane boundary enforced by AST test `tests/unit/test_geocompute_boundary.py`
- D2 ExecutionPlan/ExecutionNode additive; dispatches through AnalysisTask; NO new job table
- D3 optimizer lives INSIDE `plan_query` signature + QueryPlan shape ("plan equals execution")
- D4 raster fingerprint authority in geo_raster/fingerprint; frozen siblings documented
- D5 execution policy in_process | durable_job; no external scheduler
- D6 hierarchical budgets, tenant/project scaffolded but not wired to identity
- D7 plan fingerprint in RuntimeManifest; ref_lifecycle governs caches; NodeResultStore/StatisticsStore are engine-local TTL/LRU hints

### Tests / env / conventions — audit distillation

**Interpreter**: repo venv = `/home/kevin/projects/webgis/webgis-ai-agent/.venv/bin/python` (3.13.15); worktree has NO .venv (ci-local.sh falls through to PATH). Miniconda python3 (3.13.14) also has full deps + **pyarrow 24** — use both to test Arrow/no-Arrow paths.

**Deps**: pyarrow NOT declared (geoparquet adapter lazily imports → geopandas fallback; bench self-skips); geoarrow NOT declared; **h3 4.5.0 mandated (v4 API `latlng_to_cell`, 严禁 v3)**; redis 5.3.1 hard dep; fakeredis dev (installed 2.37.0 below pin floor — pre-existing); rtree NOT declared (use shapely STRtree); shapely 2.1.2; rasterio 1.5.0; geopandas 1.1.4; numpy 2.4.6 (<2.6 ceiling for numba); pyogrio 0.13; aiosqlite; pytest 9.1.1.

**pytest.ini**: testpaths tests, asyncio_mode auto, timeout 60 thread, coverage ON by default (`--no-cov` for speed), CI gate `--cov-fail-under=75`; markers: heavy / perf (self-skips unless `-m perf`) / cartography / real_services. **Ruff is the only Python lint** (`ruff check --output-format=github app/ tests/`), no mypy/pyright.

**Boundary test** (tests/unit/test_geocompute_boundary.py): DATA_PLANE_ROOTS = geocompute, data_fabric, geo_raster, geo_analysis, geo_processor (auto rglob; **add new data-plane dirs here**); FORBIDDEN_PREFIXES = gis_harness, agent_pi_bridge, chat, task_tracker, tools, skills, execution_engine; AST-only, level==0 imports only; anti-shrink guard ≥20 files.

**Test patterns**: no shared factories (each file has private `_node`/`_fc`/`_spec`); DB = (a) `_StubDB` monkeypatching `app.core.database.SessionLocal`, (b) tmp SQLite `create_engine` + `Base.metadata.create_all` + monkeypatched session factories, (c) REST tests on real global DB. **Migrations never run in tests** (create_all only). Chaos contract: "no sleeps > 1s, no network, no docker, no marks". Fault injection uses `tests/fixtures/data_fabric/fake_server.py` (FakeFabricAdapter SSRFSafeHTTPAdapter). `retry_call(..., sleep=lambda _: None, base_sleep=0)`.

**Baseline hazard (pre-existing)**: `test_geocompute_authz.py` standalone on fresh worktree → 2 failed (no such table: users) — lacks the create_all fixture its sibling has; suite-order-dependent. `docs/data-plane/` DOES NOT exist yet.

**Commands**: single file `.venv/bin/python -m pytest tests/unit/X.py --no-cov -q`; CI lane `pytest --cov=app --cov-fail-under=75 --timeout=60 -m "not perf and not cartography and not real_services" -q`; bench `python tests/benchmarks/bench_geocompute_v3.py [--quick|--heavy]` (structural invariants gate, timing INFO-only).

## V4 design decisions (from audit)

1. **Wave 1 (in progress)**: plan V2 contract — normalization.py (canonicalize/normalize_crs_ref/canonicalize_strict), node fields (produces/accepts PayloadKind, resource_class, deterministic, upstream_fingerprints, lineage_inputs, evidence_schema, failure_codes+checkpoint_verified), RetryPolicy backoff fields, graph validation additions (materialization sequence, unsafe retry on side-effect categories, deterministic⇒no-reuse, cross-plane param keys, upstream fp ⊆ inputs, payload-contract edges, impossible CRS, strict canonical params). EXECUTION_PLAN_VERSION 1→2.
2. Wave 2 executor fixes: wire invalidation_set/descendants_of into run loop (checkpoint/partial-rerun); use RetryPolicy backoff fields (exponential+jitter, deadline-aware refusal); failure classification taxonomy; charge bytes; fix `durable.py:100 job.error_message` bug (AnalysisTask has error_trace); fix ensure_scope atomicity; ready-set scheduler with weighted governor admission (keep determinism); cap + backpressure.
3. Stats (Wave 3): implement honest geoparquet_footer producer; durable DB store via migration 0025 (advisory-only, keyed by dataset fingerprint, TTL/retention bounded); planner feedback loop (bounded, TTL, explainable, disableable, skips failed/partial runs).
4. Federation (Wave 4): keep plan_query signature; semi-join reduction; bounded bushy-vs-left-deep comparison; pushdown capability granularity (exact/equivalent-transform/coarse-prefilter/unsupported) declared per adapter + EXPLAIN evidence; same-PostGIS native multi-source variant.
5. Vector (Wave 5): GeoArrow carrier behind pyarrow availability probe (NOT a new hard dep; honest unavailable mode); streaming scan/filter/aggregate seams; h3 (v4 API)/STRtree partition runtime with fingerprint-scoped bounded cache.
6. Raster (Wave 6): wire multi-band execute_windowed; colorinterp preservation; per-band finalize() stats; eliminate spatial_tasks._grids_pixel_aligned duplicate (converge on raster_grid); tile service through RasterReader; remote read retry + cancellation checkpoints + request budget.
7. Governor (Wave 7): wire TENANT/PROJECT from existing identity (actor_ids/project_id); concurrency budget dimension; ensure_scope atomic; charge bytes.
8. Invalidation (Wave 8): optional Redis pub/sub broadcast (redis is a hard dep; runtime-optional via USE_REDIS), id-only bounded messages, correctness never depends on receipt; per-key single-flight stampede prevention.
9. Jobs (Wave 7): capability hints in dispatch_spec + honest no-worker/fallback behavior; NO new queue/truth.

### Jobs / provenance / cache / security — audit distillation

**Durable jobs**: `AnalysisTask` table (db_model.py:91-186), 8-state machine in `jobs/lifecycle.py:84-119` (cancelling→{cancelled,failed} never completed; cancelled/completed terminal; failed/stale→queued only via start_retry); atomic conditional UPDATE via `sources_for(target)` (store.py:383-440). Idempotency: `idempotency_key` unique column; `_is_reusable` (store.py:113-126) non-terminal or completed reusable, terminal-released keys cleared for re-run; submit atomic claim pending→queued before apply_async (submit.py:127-168). Cancel fact = `cancel_requested_at` column; `_CancelWatchdog` (worker.py:212-294) polls 0.5s + heartbeats 30s; `durable_job` ctx (worker.py:302-447) duplicate-delivery guards via conditional `mark_running`; `ensure_not_cancelled` forces DB probe pre-side-effect. Stale sweep 300s (cancelling→cancelled, running→stale); orphan sweep 3600s→failed; `start_retry` (store.py:939-986) preserves error_trace. Redaction: params ≤8KiB/result ≤16KiB/error ≤500c, dispatch_spec sensitive-keyed → `__truncated__`. **No deadline column on job rows** — deadline is caller-side (geocompute await poll) + Celery soft/hard 3300/3600. **Single default Celery queue; NO capability routing** (task_acks_late, reject_on_worker_lost=False).

**Provenance** (at `app/services/provenance/` NOT app/lib): fingerprint.py pure canonical_dumps + `compute_dataset_fingerprint(source_type, source_ref, crs, schema_profile)`; `compute_content_fingerprint` excludes random ref_id; manifest.py `build_run_manifest` + `_stable_projection` (excludes artifact ids/random refs; keeps graph fp, inputs, sorted steps, tool_versions) → `compute_run_fingerprint`; `ToolExecutionContext` contextvar {user_id, org_id, project_id, run_id, session_id} set by workflow_engine.py:360. DB lineage: `ArtifactLineage` (project.py:246-310) + `LineageService.record_lineage` (lineage_service.py:47-186): cycle-safe (BFS depth 64), cross-project parents filtered (INV-LIN3), root edge carries source_dataset_id/fingerprint (INV-LIN4), commit=False flush-only (INV-TX2); `get_lineage_graph` level-batched BFS max_depth 5, project-scoped (IDOR fix).

**ref_lifecycle.py (88L)**: THE invalidation authority — `invalidate_ref_caches` (L54-77) drops mvt.spatial_index_cache + tile_lru_cache (+ref_payload_cache only on Redis backend); reasons OVERWRITE|DELETE|EVICT|EXPIRE|ROLLBACK|REPLACE; bounded events ref_stored/overwritten/invalidated/deleted/evicted. Per-(session,ref) revision int (memory `_ref_revisions`, Redis hash `session:{sid}:ref_revisions`); ref_payload_cache has monotonic epoch anti-resurrection (ref_payload_cache.py:40-45). Invalidation wired at store/overwrite/delete/evict (session_data.py:124,178,219).

**Artifacts**: identity IS the ref string (`ref:geojson-…`, `ref:raster/<id>`); `ArtifactRecord` in session-store alias `artifacts` (≤128, supersede+replaces, locked upsert artifact_registry.py:356-448); ownership enforced upstream (session auth), not in registry. Project-persisted `Artifact` (project_id FK, content_fingerprint 64, storage_ref) + promotion path (project_artifact_promotion.py). Two lineage systems (session ArtifactGraph vs DB ArtifactLineage) linked only at promotion — BY DESIGN.

**RuntimeManifest**: MANIFEST_VERSION=3; fingerprint sha256 canonical payload (order-sensitive tool_candidates; excludes compiled_at); `is_stale_plan(stored)` (runtime_manifest.py:102-116): stale iff well-formed 64-hex AND != current (corrupt/short → NOT stale). Consumers: session_plan.py:172, gis_harness/tools.py:143.

**Migrations**: next = `0025_...`; conventions: `_column_exists/_index_exists` sa.inspect guards both directions, SQLite `op.batch_alter_table`, PG `ADD COLUMN IF NOT EXISTS`, NOT NULL needs server_default, additive-only, `BigInteger.with_variant(Integer,"sqlite")`, no index=True when named __table_args__ index exists, naive-UTC in DB comparisons.

**Redis**: HARD dependency (requirements.txt:78 redis>=5,<9) but runtime-optional via `settings.USE_REDIS` (config.py:171); False ⇒ eager Celery + memory broker + MemorySessionStore. Direct imports only in session_data_redis.py + tool_cache.py.

**Auth truth**: `get_current_user` → anonymous sentinel `{user_id:"anonymous"}`; `actor_ids` collapses to None (auth.py:90); `verify_session_owner` (auth.py:499) Conversation user_id or owner_token hmac (legacy NULL token DENIED); jobs 3-way ownership (creator/owner_token/proven session_ids) → 404 not 403; data-fabric `_tenant_filter` (org_id OR owner_id OR ownerless), `_require_tenant_owned` 404; `ToolExecutionContext` carries user/org/project/run/session into tools. SSRF: `validate_url` (security.py:94-191, scheme allowlist, metadata IPs, RFC1918/127/169.254/::1/fc00/fe80, getaddrinfo all-records), `SSRFSafeHTTPAdapter` per-hop revalidation, `ensure_same_origin_url` cursors; local paths `resolve_safe_local_path` (realpath, blocked dirs, allowed_roots, 1GiB cap); defusedxml global patch.

**BUG (fix in V4)**: `geocompute/durable.py:100` reads `job.error_message` — AnalysisTask has `error_trace`; attribute always None (honest error text lost).

**Line refs**: lifecycle.py:33-119,177-184; store.py:88-126,187-272,383-440,486-551,589-744,806-986; submit.py:31-39,127-168; worker.py:106-134,212-294,302-447; ref_lifecycle.py:25-87; artifact_registry.py:356-448,606-776; lineage_service.py:47-218; runtime_manifest.py:102-116,406-424; manifest.py:41-161; security.py:94-191,316-342,399-489.

### GeoCompute execution plane — audit distillation

**plan.py (224L)**: `EXECUTION_PLAN_VERSION=1` (L22). `NodeCategory` 18 members; `ExecutionPolicyKind` in_process|durable_job; `NodeReusePolicy` ALLOW|DISALLOW; `RetryPolicy{max_attempts 1..4=1, retry_transient_only=True}` (**retry_transient_only NEVER read — dead**); `ResourceEstimate{rows,bytes,memory_mb,cpu_seconds,confidence∈high|medium|assumption}`; `ResourceBudget{max_rows 200k≤10M, max_bytes ≤2GiB, deadline_s 300≤3600, max_nodes 64≤256}`; `CrsExpectation{output_crs, allow_reproject}`. `ExecutionNode` 16 fields (node_id/category/operation/inputs/dataset_fingerprints/parameters/crs/estimate/policy/reuse/retry/deadline_s/cancellable/locality_hint/description) — **missing vs V4 spec: typed input/output schemas, durability req, deterministic flag, upstream fingerprint set, lineage links, evidence schema, memory/CPU/IO class**. `semantic_fingerprint` (L120-136) sha256[:16] of {v,category,operation,inputs(sorted),dataset_fingerprints(sorted),parameters,crs} — parameters hashed via `json.dumps(default=str)` **without sort_keys → ordering-sensitive = unstable fingerprint risk (Wave 1 fix)**. `ExecutionPlan{plan_id,nodes,budget,description}` + `graph_fingerprint()` (sorted node fps + edges, order-independent) + `node_by_fingerprint`. `NodeEvidence{status∈pending|ready|running|completed|reused|failed|cancelled|skipped, attempts, duration_s, rows_emitted, bytes_emitted(never written), output_ref, output_summary, error_code/message/retry_safe/fingerprint/policy}`. `ExecutionRun{run_id,plan_id,plan_fingerprint,status,evidence,wall_time_s,error_code,message}`.

**graph.py (128L)**: `validate_plan` (L17-37: empty, dup node_id O(n²), unknown refs, cycles DFS, max_nodes); `topo_wave_order` (L57-81 Kahn, lexicographic in-wave); `descendants_of` (L84-98) + `invalidation_set` (L101-113) **exist but NO executor call site**; `node_reuse_key = owner_scope:plan_fp:node_fp` (L116-128) — cross-plan reuse deferred "M6".

**executor.py (601L)**: `owner_scope_for(caller, session_id)` (L50-75, u:/s:/anonymous sha1). `NodeResultStore` (L91-136): LRU 256 entries/128MB, **no TTL**, key=owner:plan_fp:node_fp, `_measure` samples features/rows only; reuse-hit requires `__size__` marker. `GeoExecutionEngine.__init__(*, result_store, max_workers=2 (≤8), run_cache_size=128, retain_outputs=False)` (L143-163). `execute_plan` (L167-273): validate → `_admission_check` (L304-336 sums estimates, assumption→unknown flag, over→BudgetExceeded+suggestions) → governor EXECUTION scope + reserve(rows=Σest, nodes=1) → run_id=gexec-uuid12 → `_run_waves` → terminal aggregation (cancelled > failed > completed), governor.teardown in finally. `_run_waves` (L338-402): per wave cancel-check→mark cancelled; deadline-check→mark exceeded; ancestor-failed/cancelled/skipped → skip (only runtime invalidation); ThreadPoolExecutor(max_workers) per wave — **waves are hard barriers; parallel branches exist but coarse; no weighted admission/backpressure**. `_execute_one` (L404-568): durable branch L424-480 (session required; dispatch_node + await_node_job poll 50ms; NO retry on durable; output must be self-contained — durable tasks get empty payloads); in_process: reuse check L484-494 → retry loop L496-558 (pre-attempt cancel check; ops.execute_node under use_token; success→store.put+charge; GeoComputeError retry iff NodeExecutionError.retry_safe; backoff `0.05*attempt` linear no jitter; generic Exception→retry_safe=False break). `_governor_charge` (L570-578) charges rows+nodes only, **never bytes**. Module singleton `engine` (L600). evidence/outputs mutated from pool threads on disjoint keys (GIL-atomic assumption).

**budgets.py (234L)**: `ScopeKind` GLOBAL/TENANT/PROJECT/SESSION/EXECUTION/NODE — **production creates only SESSION (api.py:120) and EXECUTION; TENANT/PROJECT/NODE unwired**. `BudgetLimits{max_rows,max_bytes,max_nodes: Optional=None=unlimited}` — **no concurrency dimension**. `ensure_scope` (L70-81) find-then-create NOT atomic (duplicate children possible); `reserve` (L122-171) root→leaf per-scope lock check-then-increment with compensation rollback on denial (atomic per call, fixed lock order); `charge` (L173-186) unconditional (param named `bytes` vs `bytes_` asymmetry); `usage()` leaf-only, unused; `_chain` tolerant, `_find` strict; children lists read WITHOUT lock (benign race); no cross-process accounting (deferred ADR-0094:273).

**tracing.py (65L)**: logger webgis.geocompute.trace; ring deque 1024 + Lock; `emit(event, *, run_id, node_id, plan_fingerprint, status, duration_s, rows, error_code, **fields)` allowlist {nodes,reason,attempts,job_id,policy,budget_scope,plan_id,scope,wired,category,dataset_id}; 8 event names emitted (run_started/finished, node_skipped/completed/reused/cancelled/attempt_failed/failed/marked).

**errors.py (99L)**: GeoComputeError(GEOCOMPUTE_ERROR)/UnsupportedOperationError(OPERATION_UNSUPPORTED)/BudgetExceededError(RESOURCE_BUDGET_EXCEEDED,+suggestions)/DeadlineExceededError/AuthorizationError(AUTHORIZATION_DENIED→404)/NodeExecutionError(NODE_FAILED, retry_safe=False default — only retryable class, **no transient classification taxonomy: transient-remote/DB/worker-loss/etc missing (Wave 2/7 target)**); `wrap_unexpected`. OperationCancelled from app.lib.cancellation.

**drift.py (147L)**: DriftVerdict{current|stale_runtime|degraded_plan|unknown, reusable}; `build_plan_record`; `check_plan_drift` (runtime fp wins); `assert_reusable`. Uses runtime_manifest.get_runtime_manifest().fingerprint.

**durable.py/tasks.py**: `dispatch_node(node, session_id, plan_fingerprint, deadline_s)` → submit_durable_job(task_type="geocompute_node", celery run_geocompute_node); `await_node_job` polls DurableJobStore 50ms, cancel→request_cancel_sync once, deadline→cancel+DeadlineExceeded, resolves result_ref via session_data_manager. **Execution-key idempotency in jobs/submit.py L36-41**: `build_execution_key=task_type:session:sha256(params sort_keys)[:32]`, stored in AnalysisTask.idempotency_key unique; create_sync returns existing row on collision; terminal rows clear idempotency_key (re-run allowed); atomic claim pending→queued before apply_async. Task body: durable_job ctx, ensure_not_cancelled twice, **empty payloads (self-contained only)**, `_store_payload` → session_data prefix=geocompute-node-{fp}, `_bounded_summary` {rows, ref_id, scalar metadata}.

**ops.py (647L)**: `HARD_NODE_ROW_CAP=500_000`; `_MAX_PARAMS_BYTES=8MiB`. `OperatorContext{run_id,node_id,session_id,caller,budget,deadline_ts,cancel_token,metadata}` + remaining_seconds/enforce_deadline/checkpoint. Injectables: `query_catalog_fn`, `catalog_authorize_fn` (tenant ownership mirror). 11 wired categories: SOURCE_SCAN/QUERY/FILTER/AGGREGATE/SPATIAL_JOIN/ATTRIBUTE_JOIN/VECTOR_OPERATION(buffer|clip|dissolve|overlay)/RASTER_WINDOW_OPERATION(raster_calculator|resample→raster_path payload, ineligible durable)/INTERPOLATION(idw|kriging)/MATERIALIZE/ARTIFACT_REGISTER. 7 unwired: SOURCE_DISCOVERY/PROJECT/REPROJECT/RASTER_OPERATION/NETWORK_OPERATION/DECISION_OPERATION/EXPORT. `execute_node` (L619-643): registry miss→Unsupported; params >8MiB→Budget; checkpoint; wrap_unexpected.

**api.py (126L)**: `_WIRED_CATEGORIES`; `build_plan_from_json` strict (unknown category→error w/ wired list; **rejects durable_job for raster_window_operation+artifact_register**); `GOVERNOR = ResourceGovernor(max_rows=5M, max_bytes=2GiB)` no max_nodes; `run_plan_sync` mounts session scope sha1[:12] under global:root via ensure_scope, **never torn down (accumulates)**.

**_async_bridge.py**: per-thread persistent event loop; `run_coro_sync` holds **process-wide `_SERIAL` lock** (all session-store coroutines serialized; REST-vs-bridge contention = pre-existing defect).

**runtime/context.py+evidence.py**: RuntimeContext frozen dataclass (request/session/turn/run/project) + bind_runtime_context + ContextVar; TurnEvidence (bounded counters, lock-guarded) + TURN_EVIDENCE registry 64; emit_turn_summary single sink.

**Dead/vestigial**: bytes_emitted, retry_transient_only, invalidation_set/descendants_of (no caller), GLOBAL_GOVERNOR, TENANT/PROJECT/NODE scopes, NodeEvidence "ready", governor.usage().

**Limits**: plan ≤256 nodes, deadline ≤3600s, rows ≤10M, bytes ≤2GiB, node cap 500k rows, params ≤8MiB, workers ≤8 (default 2), store 256/128MB no TTL, run cache 128, ring 1024, poll 50ms, retry ≤4 attempts.

**Line refs**: plan.py:101-136,139-168,171-185; graph.py:17-37,57-81,101-128; executor.py:50-75,91-136,143-163,167-273,275-300,304-336,338-402,404-568,570-597; budgets.py:70-81,83-98,100-120,122-171,173-186,188-194,198-231; tracing.py:25-58; errors.py:66-86,89-99; drift.py:68-143; durable.py:42-66,69-142; tasks.py:23-100; ops.py:40-75,86-94,128-154,229-592,596-643; api.py:31-85,90-126; _async_bridge.py:20-38.

### Data Fabric query plane — audit distillation

**Contract surface**: `plan_query(spec, descriptor, caps=None, *, source_id, dataset_fingerprint, query_fp, stats)` at `query/planner.py:111-426`; every adapter calls it and attaches `metadata["query_plan"]` + `build_evidence` → "plan=execution" invariant. `QuerySpecV2` (`query/models.py:117-131`, extra=forbid): select/filter(PredicateAST)/spatial/temporal/aggregate/group_by/distinct/order_by/page(Offset|Cursor)/output/sample/execution(ExecutionBudget deadline_s 30, max_rows 50k, max_bytes 256MiB, max_vertices 50M, max_pages 200). `canonical_dict()` (models.py:142-179) already normalizes ordering; `query_fingerprint`=sha256[:16]. `QueryPlan` additive V3 fields: cost/alternatives/assumptions/statistics_confidence. `QueryEvidence` fields incl. rows_fetched/http_requests/db_queries/cache_hit/retry_count.

**Statistics (Wave 3 target)**: `query/statistics.py` — `ColumnStatistics{name,null_fraction,ndv,min,max,confidence∈measured|estimated|assumption}`; `DatasetStatistics{dataset_fingerprint,source_type,row_count,extent,geometry_type,has_spatial_index,resolution,overview_levels,columns≤128,revision_strength,collected_at,collector∈descriptor|postgis_pgstats|geoparquet_footer}`; `StatisticsStore` process-local singleton, key=dataset_fingerprint, TTL 60s, max 1024, Lock+LRU (L146-175); `statistics_for_request` (L124-143) cache→descriptor harvest→swallow→None. pg_stats probe `collect_postgis_statistics` (L200-228) LIMIT 64. **FACT: `collector="geoparquet_footer"` declared, NO producer.** No DB persistence.

**Selectivity (Wave 3)**: `query/selectivity.py` — V2 constants EQ=0.05/RANGE=0.25/IN=0.3/NE=0.95/NULL=0.05; `estimate_predicate_selectivity` (L45-159) with basis∈statistics|default|assumption; temporal falls to constant labeled assumption (F6). `estimate_group_cardinality` (L162-182) product-of-ndv cap 1M, fallback 5000. **No join fan-out estimation; federation ordering uses caller `estimated_rows` hints only.**

**Optimizer (Wave 4 target)**: `query/optimizer.py` — NO enumeration/search; `cost_of_chosen` (L85-105) post-factors planner decision; `generate_alternatives` (L123-238) ≤8 descriptive, pure, never changes plan; `PlanCost` score weights bytes×1.0/rows_scanned×0.2/remote_req×50/join_cand×0.05/local_cpu×0.5/mem×100/latency×25. `budget_failure_suggestions` (L241-251) defined but NOT called by planner. Rewrites present: predicate/projection/agg/sort pushdown per caps, pagination negotiation, antimeridian split, vector-tile→materialize fallback. NOT present: pushdown-through-join, join reorder in planner, semi-join, CSE, raster window coalescing, cost-based search.

**Federation (Wave 4)**: `query/federation.py` — `MAX_FEDERATED_SOURCES=4` (L602); `plan_federated_chain` (L678-767) pure, left-deep `_chain_order_indices` (L662-675) by estimated_rows asc; joins ALL LOCAL (`attribute_join_local` L259-299 hash; `spatial_join_local` L193-256 STRtree, linear-fallback guard at 10×100k); server-side join only 2-source same-PostGIS (`postgis_adapter.py:1233-1283`); NO semi-join; NO on-the-fly CRS transform in chains (typed fail L698-704); `execute_federated_chain` (L825-948) per-hop budget fail-fast, key lifting F1, geometry carry `__left_geometry__`. `FEDERATION_BUDGET` 60s/200k rows/128MiB/20M vertices/50 pages; JOIN_PAGE_SIZE 2000.

**Capabilities (Wave 4)**: `AdapterCapabilitiesV2` (`query/models.py:193-219`) 17 bool flags + spatial_predicates + max_page_size; defaults `query/capabilities.py:15-217` (postgis richest; pmtiles/wms none); probe overrides merge via `get_capabilities`. **FACT: honesty enforced by hand-written per-adapter tests only; no single automated flag-vs-behavior contract test.** CQL2-text compiler exists (`compilers.py:291-335`) used only by ogc_api behind conformance probe; no dedicated CQL capability flag.

**Adapters**: postgis full pushdown (keyset cursor, TABLESAMPLE, agg+GROUP BY, MVT 20k/tile, `SET LOCAL statement_timeout`, fetchall no streaming); geoparquet row-group pruning + `iter_batches(1024)` streaming, footer count, NO filter pushdown (declared honestly); flatgeobuf pyogrio bbox+columns, pure count via read_info; stac/ogc_api/arcgis/wfs as audited (ogc CQL2 gated by conformance; arcgis count-only agg; wfs 1.0 local slice).

**Caches/fingerprints**: `SafeTTLCache` describe cache 30s/4096, tenant scope REQUIRED in key (P0); postgis meta cache 60s/2048; descriptor fingerprint = sha256 canonical JSON (full 64); query fingerprint = sha256(dataset_fp+canonical spec)[:16]. Query-result cache + ETag = documented follow-up, NOT implemented.

**Reliability**: `circuit_breaker.py` per-source-key closed/open/half_open (threshold 5, cool 30s, single trial + `release_trial`), registry bounded 4096 LRU, wired in `manager._execute_remote_query` (manager.py:44-75); `reliability.py` `RetryPolicy(3, 0.2→5s full jitter, injectable sleep/rng)`, `is_transient` single classifier, POST retried only when `idempotent=True`; `health.py` statuses + TTL cache consulted BEFORE breaker (avoid trial-slot leak).

**Hard limits**: page limit 10k/100; max_features ≤200k; sample ≤5000; DWithin ≤2M m; IN ≤1000; legacy where ≤512; postgis MVT 20k feat/tile; per-adapter MAX_QUERY_LIMIT (postgis/ogc/arcgis/wfs 10k, geoparquet/flatgeobuf 5k, stac 1k).

**Line refs**: planner.py:111-426,429-446; models.py:117-131,142-179,233-315,193-219; statistics.py:26-66,124-175,200-228; selectivity.py:45-182; optimizer.py:25-105,123-251; federation.py:88-108,111-151,193-299,414-569,602-767,825-948; compilers.py:105-168,291-327,354-456; postgis_adapter.py:459-581,675-943,964-1011,1070-1138,1152-1283; manager.py:44-75,607-689.

### Raster Runtime (V5) — audit distillation

**Layout**: `app/lib/geo_raster/` (source.py, reader.py, windowed.py, cog.py, env.py, fingerprint.py) over V3 core `app/lib/geo_analysis/raster_grid.py` + `raster_windowed.py`. Whole-read defense: 512MiB reader budget (`reader.py:30`, single-band window reads intentionally exempt), `_ReadSpy` tests, `RasterResourceGuard` (250M px/100k side/1GiB/10k upscale), `window_side_from_budget` clamp [64,2048].

**APIs**: `RasterSource.auto/from_path/from_ref/from_project_artifact` (kind: session_ref|project_artifact|remote|local_file); `RasterReader.open/read_window(window,band,bands)/read_band/read_mask/read_overview/read_full(budget_ok=)`; `execute_windowed(reader, profile, fn, *, band, window_size, on_progress, dst_dtype)` single-band only; `overview_statistics(reader,band,max_pixels=1M)`; `WindowedRasterWriter` (atomic tmp+`os.replace` via `app/lib/artifacts.py:16-54`, streaming sha256, overviews ÷2 ladder ≤4 factors); `build_output_profile` GTiff tiled 256 LZW; `cog.write_cog/validate_cog/range_read_probe`.

**Fingerprints**: V5 canonical `content_fingerprint[:16]` (corner blocks 256×64 — NOT adversarial); reader legacy `[:32]` (prefix-compatible contract, test-locked); artifact-plane `raster_spec.raster_content_fingerprint` (≤1024 side decimated, frozen, persisted in `Artifact.content_fingerprint` + analysis_reuse); V3 writer streaming digest. **Tile caches key on path strings, not fingerprints.**

**Gaps → V4 Wave 6 targets**:
1. Multi-band `execute_windowed` = documented extension point, NOT wired (windowed.py:75-81); `finalize()` repeats band-1 stats for all bands (raster_windowed.py:277-279); `windowed_band_index` writes count=1.
2. Zero colorinterp/photometric handling anywhere.
3. Tile service `render_raster_tile` opens `rasterio.open` directly (not RasterReader), service-local `_RASTER_TILE_CACHE` (LRU 2048, path-keyed) has NO invalidation hook; route-level `TileLRUCache` has gen-based `invalidate_ref/invalidate_session` + `put_if_current` (mvt.py:1533-1650).
4. Temporal: `raster_difference` rejects misalignment via `decide_alignment` weak `same_georeferencing`; `_streamed_difference_stats` block loop (1024) has NO cancellation checkpoint; `TemporalRasterResult.metadata` never populated. Residual duplicate alignment: `app/services/spatial_tasks.py:371 _grids_pixel_aligned`.
5. Remote: GDAL /vsi* only, `GDAL_HTTP_TIMEOUT=5, MAX_RETRY=0`; NO app-level retry, no connection/chunk tuning, no request budget, NO mid-read cancellation; `windowed_band_index`/`raster_math` rely on caller-held rasterio_env (undocumented at sites).
6. STAC online math = single path (bbox crop + ds_factor decimation + max_items=1), bounded-preview semantics.

**Line refs**: env.py:28-46; reader.py:87-108(open/lifetime env),222-273(read_window),313-327(_budgeted_read); windowed.py:56-135(execute_windowed),138-173(overview_statistics); raster_windowed.py:163-293(writer),348-452(windowed_band_index); raster_grid.py:86-142(profile),293-337(decide_alignment),356-410(aligned_reader),428-451(budget); raster_tile_service.py:104-180(caches),183-411(render); fingerprint.py:43-82.
