# Changelog

## [Unreleased] - 2026-09-11 (V9: API contract & versioning foundation, ADR-0138)

### Added
- Unified error envelope: HTTPException / 422 validation errors now return
  `{code, success, message, data}` (+ `category`/`retryable` taxonomy fields);
  rollback switch `LEGACY_DETAIL_ENVELOPE` + per-request `X-Error-Envelope: detail`
  header. 503/502/504 no longer mis-coded as SERVER_ERROR.
- HTTP idempotency: `Idempotency-Key` middleware (24h replay, SET-NX singleflight,
  fail-open on Redis outage) — JSON POST only, SSE/binary excluded.
- API v2 mount layer: lakehouse / geocompute / workflow-runtime demo reuse of the
  same routers (59 paths); v1 responses carry `Deprecation`/`Sunset` headers +
  `webgis_api_version_requests_total` usage counter. No v1 endpoint removed.
- `scripts/gen_api_docs.py`: endpoint catalog generated from `app.openapi()`
  into docs/api-docs.md (marker-scoped); `tests/test_api_docs_drift.py` fails CI
  on handwritten drift — rate-limit 60/min vs 240/min drift fixed.
- Field-level contract gate (#1217 backend half): per-endpoint response field
  signatures snapshot; removals/renames/type changes fail with typed diff
  (`tests/unit/api_contract/test_field_contract.py`).
- Schemathesis contract fuzz shard (`max_examples=30`, ASGI in-process,
  allowlisted no-dependency paths) + independent CI lane
  `.github/workflows/contract.yml` (production.yml untouched).

### Changed
- response_model coverage: 123 uncovered endpoints typed (220 total; 16
  streaming/binary exclusions ledgered in
  `tests/unit/api_contract/_contract_util.py::EXCLUSIONS`); 65 inline route
  BaseModels migrated verbatim to `app/schemas/<subsystem>_schema.py` (19
  schema modules); naming `<Resource><Action>Request/Response`.
- Pagination: additive completion of page metadata (chat/sessions `has_more`);
  `Page[T]` envelope unchanged; ad-hoc limit deprecation deferred to v2.
- docs/api-docs.md: lakehouse / geocompute / workflow-runtime chapters restored
  via generator; rate limit documented as 240/60s matching code.

### Fixed
- weasyprint optional-dependency guards catch OSError (Windows GTK absence) —
  22 test files now collectable on Windows.
- starlette-layer HTTPException (malformed body 400) bypassing the unified
  envelope handler (registration key must cover the starlette base class).

## [Unreleased] - 2026-09-10 (V7/V8 epic integration round)

Ten prepared epic branches (platform-v4, lakehouse-v8, data-fabric-v8,
geocompute-v8, science-v6, modelops-v3, workflow-v6, cartography-v7,
workbench-v7, harness-v7) integrated into master in dependency order.
Cross-branch collisions resolved: ADR-0130 five-way renumber
(lakehouse keeps 0130→0135; data-fabric→0132, geocompute→0133,
harness-v7→0134, modelops→0136 — final numbering aligned with
origin/master PRs; watermark 137 after Harness V8), three 0035 migration heads
converged by merge revision e7a51c9d2f04, CHANGELOG sections unified.
Per-epic detail sections follow; entries for epics that documented in their
ADR/work records rather than CHANGELOG are summarized below.

### Added (platform-v4: Production Control)
- Production build identity (VERSION alignment, extensions_api sourced from
  CORE_API_VERSION), error taxonomy semantics, end-to-end single-trace
  acceptance, worker lifecycle control (ADR-0131).

### Added (lakehouse-v8: Versioned Geospatial Lakehouse, ADR-0130)
- Cube v3 schema (model/scenario dims, per-variable nodata), retention
  plan/execute with bounded scans, Arrow IPC adapter for dataset exchange,
  workflow/fabric bridges, dataset REST surface + e2e acceptance.

### Added (geocompute-v8: Distributed Spatial Compute Fabric, ADR-0133)
- V8 partitioning (plan/spill/rehydrate accounting, speculative windows),
  placement guard with latent-bug fix, observability completion
  (transfer/cache/lineage/utilization/quarantine), robustness + acceptance
  suites.

### Added (science-v6: Spatial Intelligence Domain Packs)
- Sampling pack (spatial random/systematic/stratified), ecology pack
  (habitat suitability HSI + landscape metrics with ED double-count fix),
  temporal smooth_gapfill, tolerance semantics + purity guards.

### Added (modelops-v3: Production GeoAI Inference Runtime, ADR-0136)
- Inference scheduling, vectorization + georeferenced output, memmap merge
  cleanup with Windows unmap-before-rmtree, VRAM reservation leak fix,
  determinism acceptance, review rounds 1-2 fixes.

### Added (workflow-v6: Durable Cluster Runtime)
- Two-level leases / journal / recovery / retry / dispatch surfaces,
  Inspector API, real GIS adapter execution path (fixing a V5 param-loss
  defect), clone_run terminal-state guard, independent review fixes.

### Added (workbench-v7: Professional GIS Workbench)
- HUD-only rows bypass server mutations, lazy attribute rows, two-phase
  tool-call matching, dock draft CSS cleanup, null-safe store selectors,
  persistence debounce polling in tests.

## [Unreleased] - 2026-09-10 (data-fabric-v8)

### Added (data-fabric-v8: Adaptive Federated Spatial Data Plane, ADR-0132)
- FabricRuntime single production resolution path: registry-first
  (scope/revision/health/secret separation) -> legacy session fallback
  (first use registers into the registry — unified governance view,
  single-construction reuse) -> DB-registered sources attach on demand
  under the row's owner scope. All 11 tool-layer call sites and the five
  DataFabricManager REST/worker paths converge; any governance failure
  fails open to the existing factory build (byte-identical contract).
- Adaptive loop closed (orphan libraries activated): probed capability
  overrides (probed-basis-only), SourceFacts row counts, and decayed
  feedback correction factors are flattened into pure planning hints
  before planning; explicit caller hints are never overridden and hint
  drift is disclosed. `ChainSource.source_type` now flows from the
  registry record, activating static capability injection (pushdown
  disclosure / aggregate-pushdown eligibility) on the tool path.
- EXPLAIN `estimate_basis:` disclosure (rows basis / caps basis / hint
  drift); output is byte-identical to V7 when no enrichment is present.
- Process-level engine fallback breaker: >=3 consecutive V6 crashes open
  the breaker so engine=v6 requests run V5 directly (double-execution
  cost eliminated), half-open single trial after cool-down, success
  resets; additive `engine_breaker` disclosure on fallback results
  (closes the ADR-0120 R2-Mi-4 known limitation).
- Result cache strengthening: per-key single-flight around the miss path
  (concurrent identical requests share the first execution; owner
  failure/waiter timeout proceed independently) and an optional Redis
  second tier behind a fail-open `ResultCacheBackend` seam (write-through,
  fingerprint-baked keys, honest `basis=ttl+fingerprint+distributed`
  disclosure with age=None).
- Connection Registry V8: `ConnectionRecord.redacted_profile` fixes the
  V7 rebuild defect (records previously kept only four endpoint fields,
  so options-shaped sources could never be rebuilt) with
  `ensure_adapter` faithful rebuild+backfill and
  `attach(prebuilt_adapter=)` single-construction legacy bridging.

### Fixed (data-fabric-v8)
- Credential-leak hardening: `redacted_profile` now strips URL userinfo
  via `redact_url` and moves sensitive keys inside nested trees (options,
  the REST `create_data_source` credential path) into the SecretStore,
  with deep-merge restoration on rehydrate — records and diagnostics are
  constructively secret-free.
- Factory-seam fidelity: governed resolution reuses `cls.get_adapter` as
  the single construction point and registers the built instance via
  `attach_prebuilt` (test monkeypatch/`build_adapter` seam preserved);
  fixed `attach(build_adapter=False, prebuilt_adapter=...)` never storing
  the prebuilt instance.
- Engine-breaker half-open trial leak: trial slots are now released via
  `finally` on exits that never reach success/crash accounting (negative
  cache, cache hits, typed plan errors) — previously one such exit during
  HALF_OPEN disabled V6 until process restart.
- URL-userinfo secrets are captured into the SecretStore before
  redaction: basic-auth/DSN URLs rebuild with credentials intact and
  userinfo-only rotation now produces a new revision instead of an
  idempotent stale hit; sensitive keys inside options lists are
  extracted too; shared (content-deduped) secret refs are no longer
  evicted while a sibling record references them; `ensure_adapter`
  refuses expired records; global-scope connections report governed
  metadata (probe/enrichment no longer silently disabled); throttled
  `sweep()` wired into resolution (idle-TTL eviction is live).

## [Unreleased] - 2026-09-11 (cartography-v7: Template Component Runtime)

### Added (cartography-v7: Component Graph + Constraint Set + Style Tokens, Goal 08)
- Component graph (`app/lib/cartography/component_graph.py`): MapSpec 扁平
  组件列表之上的运行时图投影 —— 节点带 registry 语义投影，类型化边
  binds_to/requires/groups/annotates/under（derived：options.layerId、
  subtitle→title；explicit：layout.component_links）；validate 输出
  duplicate_binding/cycle/orphan_binding 结构化问题；确定性拓扑序
  （under=src 先绘制）。MapSpec schema 1.2 additive（identity upgrader，
  round-trip 保真，TS 投影再生成）。
- Registry intelligence（`component_registry.py`）：search() 确定性 token
  评分（受控中英关键词词表 + 过滤器硬约束 + 弃用缺省排除）、recommend()
  可解释上下文推荐（模型兼容/语义角色/类目亲和/在场去重）；descriptor
  新增 deprecated/deprecated_by/preview/search_keywords_zh。
- Layout geometry（`layout_geometry.py`）：margins/bleed 安全区、zone
  矩形近似、浮动组件 user-wins 确定性布放（越界钳制 + 重叠级联推移，
  不可解显式披露）—— QA 与 repair 共用同一几何真值。
- Style tokens（`style_tokens.py`）：线宽/符号/视觉层级比例尺 ×
  screen/publication 输出预设；调色板 profile 迁移建议器（色带子集精确
  反查，用户自定义色不猜测改写）。
- Multi-alternative composition selection（`composition_selection.py`）：
  同一数据 → 排序的候选组合（类目亲和审定表 + plan_composition 复用 +
  确定性加权评分 + 类目轮转多样性合并）；「学校分布」场景由结构化事实
  推导出点图/热力/统计组件组合，无 query 硬编码。
- Graph-level cartographic QA（semantic_checks 新增三条 deterministic
  规则 DUPLICATE_LEGEND_BINDING / COMPONENT_OUTSIDE_CANVAS /
  COMPONENT_LINK_CYCLE）+ AUTO_SAFE `resolve_floating_layout`（只挪
  floating x/y）+ `derive_product_verdict` additive cartographic_review
  参数（deterministic fail 压低 READY 档位）。
- Publication DPI 参数化：`/export/vector-pdf` 可选 target_dpi（72-600
  钳制 + 生效值披露；缺省 300 输出 byte 一致）。


## [Unreleased] - 2026-09-10 (harness-v7)

### Added (harness-v7: Long-Horizon Contextual GIS Agent Runtime, ADR-0134)
- Runtime state machine (`gis_harness/runtime_state_machine.py`): task-level
  cognitive-loop phase (12-state closed vocabulary) + legal transition table
  (docs/test-oracle/commanded fail-closed) + ring transition records; phases
  are deterministic derivations of chapter facts (no second truth source);
  persisted additively at `gis_chapter["runtime_state"]` with gate
  fingerprint idempotency + in-lock drift guards; `suspended` overlay flag
  marks unfinished tasks at turn settle (anchor-resumable); wired to the
  three existing production triggers (tool_result / turn_settled / render
  observation) — zero new event sources; `[GIS Runtime]` projection line.
- Plan runtime (`gis_harness/plan_runtime.py`): plan identity fingerprint
  (goal+rows+contract) → monotonic versioning with bounded history (≤8) and
  rollback points (≤4); replan loop gets BOTH a budget entry
  (`LOOP_BUDGETS.replan=1`) and its first production driver (`request_replan`,
  invoked from the finalizer exit when repair is unreachable) — flag cleared
  when plan facts change (replan consumed); `seed_recompute_from_failures`
  emits a minimal-rerun list (failed row + downstream closure, reuse for
  unaffected satisfied nodes).
- Context layers (`gis_harness/context_layers.py`): nine bounded domain
  projections (turn/session/project/workspace/map/data/workflow/artifact/
  capability), all rebuildable — anchor carries only per-domain digests
  (`context_digest` durable key); deterministic budget (per-domain byte caps
  → total-budget prune order, violations recorded); single-key checkpoint
  with monotonic revision + content fingerprint (turn-boundary automatic).
- Capability descriptors (`gis_harness/capability_descriptors.py`): unified
  read-only projection over capability/algorithm/template/component
  registries with preconditions (geometry/CRS class/min features/required
  fields/scientific), postconditions (outputs/uncertainty), cost/latency
  profile, fallback chains; `select_capabilities` (hard precondition filter
  + CJK-bigram lexical seed + cost bias + bounded reliability penalty from
  the durable recovery ledger); gated additive signal in
  `tool_surface_v3.select` (kill switch `GIS_CAPABILITY_RETRIEVAL_V7=0`).
- Scenario corpus (`app/evaluation/scenario_corpus.py`): generative gold
  structure — domain packs × bounded slot tables × deterministic expansion
  (14 families, 2316 scenarios ≥ 2000 gate), expected lists validated
  against the live registries (`registry_missing=[]`), coverage gate that
  flags registry families with no pack, deterministic stride sampling with
  p@1 evaluation.
- Map critique (`gis_harness/map_critique.py`): deterministic checks —
  blank_map_risk (all rendered layers explicitly report count 0),
  invalid_result_bounds, publication export completeness (title/north_arrow/
  scale_bar with family repair routing), label collision (telemetry ratio vs
  existing CARTO_LABEL_* thresholds, honest absence), planned/observed
  overlay mismatch; aggregated as bounded `MapCompletionFinding`s and merged
  additively into the finalizer's validate pass.
- Finalization hook (V7): independent intent acceptance
  (`gis_harness/intent_acceptance.py` — verdict/desired/observed three-way
  check replacing the `intent_verified=(status==complete)` tautology;
  missing telemetry honestly downgrades `intent_verified`); finalizer exit
  directly consumes `decide_continuation` (V6 follow-up) with replan routing
  through `request_replan`; READY triggers context commit (nine-domain
  checkpoint + `commit_runtime_context` + `verdict_ready` phase advance);
  display confirmation hook (`gis_harness/display_confirmation.py`, default
  auto-confirm, `GIS_FINAL_DISPLAY_CONFIRM=required` opts into explicit
  ack with render-seq freshness).
- Delegation (`gis_harness/delegation.py`): handoff schema
  (`DelegationSpec`, role fail-closed) wrapping `SubagentDispatcher` with a
  bounded parent-side ledger (`gis_chapter["delegations"]`, ring ≤8, closed
  status vocabulary, lineage write-back); deterministic failure recovery
  (one retry within repair budget, then honest fail); optional production
  driver `delegate_cartography_qa` (env `GIS_HARNESS_DELEGATION=1`,
  idempotent per product revision).
- New ADR: `docs/adr/0130-gis-harness-v7-agentic-runtime.md`.

- New ADR: `docs/adr/0134-gis-harness-v7-agentic-runtime.md`.

## [Unreleased] - 2026-09-10 (data-fabric-v8)

### Added (data-fabric-v8: Adaptive Federated Spatial Data Plane, ADR-0130)
- FabricRuntime single production resolution path: registry-first
  (scope/revision/health/secret separation) -> legacy session fallback
  (first use registers into the registry — unified governance view,
  single-construction reuse) -> DB-registered sources attach on demand
  under the row's owner scope. All 11 tool-layer call sites and the five
  DataFabricManager REST/worker paths converge; any governance failure
  fails open to the existing factory build (byte-identical contract).
- Adaptive loop closed (orphan libraries activated): probed capability
  overrides (probed-basis-only), SourceFacts row counts, and decayed
  feedback correction factors are flattened into pure planning hints
  before planning; explicit caller hints are never overridden and hint
  drift is disclosed. `ChainSource.source_type` now flows from the
  registry record, activating static capability injection (pushdown
  disclosure / aggregate-pushdown eligibility) on the tool path.
- EXPLAIN `estimate_basis:` disclosure (rows basis / caps basis / hint
  drift); output is byte-identical to V7 when no enrichment is present.
- Process-level engine fallback breaker: >=3 consecutive V6 crashes open
  the breaker so engine=v6 requests run V5 directly (double-execution
  cost eliminated), half-open single trial after cool-down, success
  resets; additive `engine_breaker` disclosure on fallback results
  (closes the ADR-0120 R2-Mi-4 known limitation).
- Result cache strengthening: per-key single-flight around the miss path
  (concurrent identical requests share the first execution; owner
  failure/waiter timeout proceed independently) and an optional Redis
  second tier behind a fail-open `ResultCacheBackend` seam (write-through,
  fingerprint-baked keys, honest `basis=ttl+fingerprint+distributed`
  disclosure with age=None).
- Connection Registry V8: `ConnectionRecord.redacted_profile` fixes the
  V7 rebuild defect (records previously kept only four endpoint fields,
  so options-shaped sources could never be rebuilt) with
  `ensure_adapter` faithful rebuild+backfill and
  `attach(prebuilt_adapter=)` single-construction legacy bridging.

### Fixed (data-fabric-v8)
- Credential-leak hardening: `redacted_profile` now strips URL userinfo
  via `redact_url` and moves sensitive keys inside nested trees (options,
  the REST `create_data_source` credential path) into the SecretStore,
  with deep-merge restoration on rehydrate — records and diagnostics are
  constructively secret-free.
- Factory-seam fidelity: governed resolution reuses `cls.get_adapter` as
  the single construction point and registers the built instance via
  `attach_prebuilt` (test monkeypatch/`build_adapter` seam preserved);
  fixed `attach(build_adapter=False, prebuilt_adapter=...)` never storing
  the prebuilt instance.
- Engine-breaker half-open trial leak: trial slots are now released via
  `finally` on exits that never reach success/crash accounting (negative
  cache, cache hits, typed plan errors) — previously one such exit during
  HALF_OPEN disabled V6 until process restart.
- URL-userinfo secrets are captured into the SecretStore before
  redaction: basic-auth/DSN URLs rebuild with credentials intact and
  userinfo-only rotation now produces a new revision instead of an
  idempotent stale hit; sensitive keys inside options lists are
  extracted too; shared (content-deduped) secret refs are no longer
  evicted while a sibling record references them; `ensure_adapter`
  refuses expired records; global-scope connections report governed
  metadata (probe/enrichment no longer silently disabled); throttled
  `sweep()` wired into resolution (idle-TTL eviction is live).

## [Unreleased] - 2026-09-09 (science-v5)

### Added (science-v5: Scalable Scientific Computing + Uncertainty + Spatiotemporal GeoAI)
- Cross-validation framework (`app/lib/geo_analysis/cv.py`): method-agnostic
  orchestration with deterministic splitters (index / spatial-block lifted
  from kriging as a shared helper / temporal forward-chaining with strict
  no-future-leakage evidence), z-score calibration, duplicate-coordinate
  disclosure, honest insufficient-sample decline.
- Variogram v5: deterministic multi-start polish on the fit failure path
  (primary path bitwise unchanged) and per-model sanity diagnostics
  (range/sill-at-bound, nugget-dominated) in `select_variogram_model`.
- Batched solvers: LMC full co-kriging and spatiotemporal kriging switch
  from per-target solves to stacked batched systems (bitwise-equal to the
  per-row reference; isolation on LinAlgError OR non-finite); new SGS
  `numpy_batched` backend (group-shared paths, chunk-stacked solves,
  solve count scales with groups not realizations) alongside the bitwise
  reference backend; dynamic target ceiling = ensemble budget / R.
- Sequential-regime SGS fixes (owned P1s): simulated-neighbour
  cross-covariance replaces the inconsistent diagonal approximation
  (non-PSD systems made variances explode beyond 1 chunk), and the
  conditioning tree position indices are mapped through the path before
  reading simulated values.
- `UncertaintyArtifact` contract: mandatory closed-vocabulary estimator,
  model-uncertainty vs data-quality separation, honest std absence for
  R<2 ensembles, bounded summaries + renderer metadata wired into
  sgs / cokriging / st-kriging surface metadata.
- Backend dispatch v5: `plan_execution` pure projection (closed
  native/vectorized/chunked vocabulary — distributed deliberately absent),
  `numpy_batched` variants declared in cell-space windows for
  sgs / cokriging_lmc / st_kriging, driver wiring with execution-plan
  evidence.
- Temporal science: `TemporalCube` SAR/optical adapter (honest gap and
  missing-slice disclosure), phenology engine (bounded gap fill +
  Savitzky-Golay + double-harmonic LS + threshold SOS/EOS/LOS/peak, index
  semantics disclosed), temporal anomaly/change (climatology z-score +
  Welch-approx two-period effect size); new `science_temporal_tools`
  surface with `science_temporal_analysis` parameter contract.
- Hydrology v5: multi-level Pfafstetter coding (inter-basin recursion,
  parent*10+digit, level-1 bitwise equal to the single-level function)
  and D8 flow-topology validation (bounded coloring cycle check, dangling
  receivers, strict accumulation monotonicity with equal-acc plateaus
  counted apart); exposed via `hydrology_v4_analysis` (contract v2).

## [Unreleased] - 2026-09-10

### Added (geocompute-v7: Distributed Adaptive Spatial Compute Fabric)
- Worker capability profiles (CPU/memory/GPU/backends/version fingerprint,
  honest zero-default probing) registered at worker_ready and consumed by
  resource-aware run admission gating; worker-side admission guard with
  bounded celery requeue (<=3) converging GPU-required nodes onto GPU
  workers, then typed PLACEMENT_MISMATCH failure (ADR-0119 D1).
- Distributed run event trace (`geocompute_run_events`): bounded per-run
  node-event budget (512, drop+metric on overflow) with run-level/terminal
  events exempt, monotonic after_id cursor resume reads, owner-scoped
  `GET /runs/{id}/events`, and read-time progress projection (idempotent
  across attempts; no mutable progress columns).
- Data locality: owner-scoped worker object-cache location registry
  (`geocompute_worker_cache`, LRU/TTL/prune-cascaded), advisory placement
  locality scoring, and worker-local payload cache (owner-bound identity,
  bounded entries/bytes) wired to session-ref input handoff.
- Fully-durable node chains: upstream session refs handed to workers via
  task_kwargs (input handoff), enabling multi-hop distributed DAGs.
- Admin surface (require_admin): worker capability view, stuck-run view,
  force-requeue (single-row reclaim semantics), ledger limit management;
  cluster metrics gained queue-wait percentiles, waiting-by-profile and
  event counters (closed vocabularies only).
- Real-broker E2E lane (`REAL_SERVICES=1`): full durable chain through a
  production celery worker + persistent coordinator, worker-crash ->
  stale reconcile -> bounded reschedule, cross-process cancellation,
  duplicate-delivery guard, broker reconnect.
- Async bridge rewritten to a supervised dedicated event-loop thread with
  bounded typed timeouts: removes the V6 process-wide `_SERIAL`
  serialization of session-store IO (relative-benchmark guarded) and the
  dead-loop hang surface (ADR-0119 D5).

### Fixed (geocompute-v7)
- P1 (V6 latent): celery worker registration never worked on real workers
  - kombu `Signal.connect` defaults to `weak=True`, so the closure handlers
  were garbage-collected before `worker_ready` fired; registration and
  heartbeats silently never happened (all V6 tests were eager). Fixed with
  explicit `weak=False` registration; first exercised by the V7
  real-broker E2E.
- P2: SQLite engine now sets busy_timeout=30s for multiprocess control
  plane contention; durable node polling switched from fixed 50ms to
  adaptive backoff (0.05->0.5s, cancellation latency dominated by the
  0.5s run heartbeat).

## [Unreleased] - 2026-09-10

### Added (cartography-v6: Typed MapSpec + Publication Engine, ADR-0120)
- Authoritative MapSpec schema (Pydantic v2 strict): typed spine with
  structured invalid-field disclosure (no silent coercion), unknown-field
  preservation, order-preserving canonical serialization, explicit migration
  registry (1.0 -> 1.1 additive: layout.frames / layout.labels), forward
  version typed rejection; deterministic TS projection generator
  (types.generated.ts) replacing the hand-maintained parallel schema.
- Publication engine: backend twin `include_chrome` renders canonical-scene
  driven marginalia (title block, north arrow, projection-aware scale bar,
  single-source legend box, frame border, graticule, inset locator) and
  `bounds` explicit extent; `POST /api/v1/map/export/vector-pdf` renders
  true vector PDFs via WeasyPrint (selectable text, per-frame pages, deny-all
  url fetcher, process-level render mutex, honest raster-layer omission,
  graceful 503 when the engine is absent).
- Deterministic export label collision (opt-in `layout.labels.collision`):
  portable Python solver + 1:1 TS port locked by differential fixtures;
  top-level labels group; `label_collision_relaxed` / `label_budget_exceeded`
  diagnostics (400/export budget).
- Spec-level atlas frames (`layout.frames`, <=50) honored by backend
  publication PDF (per-frame extents/views, layerOverride deep-merge,
  @page sizes) and adapted for frontend frame export (title/extent/view).
- Diagnostics dead-code gate: EMITTER_REGISTRY contract test binding every
  vocabulary code to a real emission site; multi-frame DiagnosticSink with
  per-frame quota and `diagnostics_truncated` meta disclosure.
- Cross-language parity corpus: render-scene / legend-model / label-solver
  golden fixtures consumed by both pytest and vitest; report chain upgraded
  to the publication path (visual delta disclosed via publication_chrome).

### Changed (cartography-v6, intentional deltas — see 05-legend-convergence-diff-table.md)
- Canvas export legend now honors user labels and palette caps (previously
  ignored labels, drew out-of-palette entries); legend range labels unified
  on formatLegendValue (zh-CN aware); title fallback unified ("图例");
  continuous legend entry count now 3 (was 0) in export disclosure math.

## [Unreleased] - 2026-09-10

### Added (workbench-v6: Server-Side Multi-User Collaboration)
- Server collaboration notification plane: per-session CollabEventBus
  (Redis pub/sub + in-process degraded fan-out), authenticated WebSocket
  channel `/ws/collab/{session_id}` (bearer JWT or anonymous owner_token),
  presence (TTL 30s, cap 32), and advisory edit leases (Lua token-checked,
  TTL 60s). Correctness never depends on the bus: revision-mismatch
  reconciliation refetches the authoritative workbench state.
- `patch_workbench_delta` intent (absolute-value semantics, replay-idempotent)
  with server-side ordered application + result-level validation, and
  `base_workbench_revision` workbench-level CAS on full-doc commits (engine
  stamps `mapspec.workbench._rev`) closing the stale-full-doc clobber window.
- Server-enforced layer lock guard built into the MapSpec engine (single +
  presentation-batch paths, family semantics, `error_code="layer_locked"`),
  closing the agent-tool bypass of user locks.
- Collaborative undo: field-scoped inverse-delta replay replaces whole-table
  snapshot restore; bounded single rebase on 409 superseded.
- Artifact awareness projection `GET .../workbench/artifact-status` + ref
  invalidation `artifact` events; frontend stale badges and lineage inspector.
- Frontend: collab client (reconnect/jitter/heartbeat reconciliation), delta
  persistence channel, CollabBar (presence/degraded/conflict disclosure),
  group reparent drag & keyboard parity, O(n) tree traversal helpers.

## [Unreleased] — Quality V3 / Integration Platform（Epic 10）

### Added
- 并发研发协调面（`app/lib/integration/`）：shared-file ownership 元契约、
  migration/ADR 分配协调（watermark 棘轮）、语义 integration manifest、
  import 图影响选择（完备性护栏）、跨分支合并模拟（checked/unknown/
  not_checked 三段契约）、release readiness 证据（不可绕过政策断言）
- W3C traceparent 关联（纯 ASGI 中间件，http+websocket 全覆盖）
- SRE 组件健康面 `/api/v1/status/detailed`（鉴权）+ `sre_*` 有界指标
  + staleness 告警；real-services lane 与多进程 harness（kill -9 chaos）
- chaos V3：JOBS_WORKER_LOSS / CANCEL_STORM / JOBS_STALE_REVISION_CAS /
  DB_TRANSIENT_SEQUENCE（生产零改动，注册表字节闸同步）

## [Unreleased] - 2026-09-09

## [Unreleased] - 2026-09-10

### Added (methodology-v2: GIS Methodology & Template Intelligence V2, ADR-0120)
- Method knowledge layer `app/lib/gis/methodology/`: 20-category GIS task
  taxonomy (data demands projected from the task ontology, never hand-written),
  method knowledge graph (typed read-only projection over canonical registries,
  421 nodes / 883 edges, fail-closed integrity, fingerprint-keyed cache,
  structural diff), 51 method enrichment descriptors (problem class /
  assumptions / alternatives / invalid_when / assumes_continuous_measure),
  knowledge provenance ledger (no LLM-generated source kind by design).
- Unified method qualification engine: 8-dimension four-state adjudication
  reusing scientific_preconditions + AlgorithmDescriptor.crs_class + crs_safety;
  geographic-CRS buffers pass (built-in reprojection), categorical measures
  reject continuous-assumption methods, explicit no-geometry profiles fail.
- Method candidate ranker: frozen 7-component weighted scoring, category-pool
  ranking with V4 routing consistency bonus, rejected-last partitioning,
  minimal-tier descriptive fallback, abstention semantics; bilingual frozen
  corpus (25 cases) benchmarked recall@1=0.96 / MRR=0.98 / invalid=0.04.
- Template intelligence: TemplateSpecV2 (data bindings / capability
  requirements / export constraints / uncertainty & comparison obligations)
  with 12 category-affinity specs and a deterministic composition planner
  (base slots ∪ taxonomy expectations ∪ viz-bridge bindings; forbidden slots
  skipped; no query hardcoding).
- Algorithm-output ↔ visualization bridge: 16 viz families, 6 legend
  semantics, 21 artifact entries; cross-source divergences become structured
  disclosures (curated reconcile table) while dangling ids stay fatal.
- KnowledgeService facade + 6 tier-1 read-only agent tools
  (gis_task_classify / gis_method_qualify / gis_method_rank /
  gis_method_explain / gis_template_plan / gis_component_query).
- GIS end-to-end case corpus: 22 fixture cases covering Epic §9 scenarios
  (schools / medical accessibility / POI density / admin stats /
  raw-vs-normalized / hotspot / clustering / interpolation fit & misfit /
  land change / DEM / hydrology / service areas / RS classification /
  uncertainty / multi-period / mixed CRS / antimeridian / tiny sample /
  invalid geometry / no geometry / large layers).
- Ontology additive completion: 5 new tasks (proximity/clustering/
  spatiotemporal/overlay/atlas) and a 13th methodology family (proximity)
  with 7 new curated candidate methods.


### Added (harness-v6: Contextual Cartographic Harness V6)

- Canonical Workflow Runtime Projection: `derive_runtime_block` pure
  projection into the single `gis_chapter[workflow_runtime_v6]` key; typed
  node ids become the shared namespace (StageState vocabulary reused, no
  parallel enums); evidence drift marks the typed-edge downstream closure
  stale; wired into 3 triggers with kill switch `GIS_WORKFLOW_RUNTIME_V6`.
- Artifact lineage: bidirectional `artifact_index` (ref → producer /
  consumers / layers / components, capped at 96) plus node `inputs`
  bloodline; `artifact_lineage` / `node_lineage` query APIs (absent → None).
- Partial recompute: field-level change classification
  (algorithm/parameter/data) feeding `compute_affected_subgraph` as the
  sole closure engine; `changes` + `recompute_plan` persisted into the
  runtime block; reuse validation snapshot (`list_artifacts` ≤ 128) with
  safe/unknown/unsafe verdicts (`reuse_unsafe:*` forces recompute).
- Unified findings + single verdict: 12-field `UnifiedFinding` projection
  (34 finalizer + 18 render-diagnostic codes, bounded ≤ 24); runtime stale
  is a hard input to the completion contract (READY* pressed to NEEDS_REPAIR).
- Observation & repair loop: deterministic layout findings
  (floating-component overlap/off-canvas → warning) plus a 7-phase
  component lifecycle; visual evaluator seam (default off via
  `GIS_VISUAL_EVALUATOR`); table-driven repair planner (16 classes ×
  5 safety levels, locks → not_allowed) with fingerprint + epoch ledger
  (≤ 32 entries, ≥ 3 attempts → repair_exhausted disclosure).
- Tool Retrieval V6: production `ToolSemanticIndex` (cosine floor 0.15,
  6.0 magnitude, top-512 cap) behind `TOOL_RETRIEVAL_SEMANTIC` with
  lexical fallback; paraphrase corpus 66 → 306 entries
  (p@1 0.6242 / r@5 0.8282 / r@10 0.8775 / invalid 0.2917).
- Contextual assembly: three-tier projections with byte hard caps
  (node-local 2048B / workflow-global 4096B / map-situation 2048B).
- Resume VNext: live/stale/unknown revalidation (anchor schema v2)
  instead of assume-valid resume.
- Human-agent convergence: unified `guard_locked_partitions`
  (code=layer_locked), `lockedComponentIds`, three-way override
  classification, transient-key stripping at commit boundary.
- Closed-loop corpus: 17 chart types × 12 fault injections = 204 cases;
  ten deterministic §57 E2E scenarios (S1–S10) green.
- Perf/security gates: 7 structural perf contracts (zero wall-clock) +
  5 security gates, all green, no production code touched.
### Added (data-fabric-v7: Federated Data Fabric V7, ADR-0119)
- Connection Registry V7: tenant-scoped (org/owner/project) connection
  governance with content-addressed revisions, secret separation seam
  (opaque refs + bounded in-memory store), health state machine, idle-TTL
  and capacity-bounded lifecycle eviction; P1 fix — DB-backed profile
  rebuild now restores username/password/credentials top-level fields.
- Provider capability probing service: scoped cache keyed by profile
  revision, probe-cost accounting (requests/bytes/latency), passive
  rate-limit header observation, honest fallback to the static matrix
  (caps_basis: default|probed|stale).
- SourceFacts: scoped, provenance-annotated dataset facts (row-count basis,
  extent, CRS, NDV, null fraction, spatial histogram, temporal extent,
  freshness) with plumb-not-scrape collection and advisory durable store
  (migration 0034: data_fabric_source_facts + data_fabric_federated_feedback).
- Server-side CRS transform placement: output_crs scan channel (PostGIS
  ST_Transform verified plumbing), delivered-CRS execution ledger from
  adapter metadata, one-shot refetch + local transform fallback on delivery
  mismatch — fixes the V6 latent double-transform for sources whose native
  SRID differs from the 4326 delivery default.
- Safe aggregate pushdown: five-condition semantic-equivalence proof
  (attribute equality join, group keys cover join field, measured unique
  left key, decomposable aggregates, source aggregation cap); unproven
  joins stay local with honest EXPLAIN disclosure.
- Bushy adaptive replan: one-shot whole-tree re-enumeration with pinned
  observed cardinalities on >=4x deviation, strictly-better guard, disabled
  under order_strategy="given".
- Distributed execution feedback: decayed (30-min half-life) weighted
  median correction factors, outcome=ok-only learning, unfiltered-scan-only
  SourceFacts write-back guard, advisory durable persistence.
- Federated query result cache: scope+fingerprint+request keyed, TTL +
  entry/byte bounded LRU, mandatory hit disclosure (age/basis), negative
  caching limited to unreachable/auth failures, global-scope connections
  excluded.
- CQL2-JSON compiler (structure-encoded, no string concatenation surface)
  with OGC API Features filter-lang negotiation and STAC filter-extension
  conformance probing for /search filter pushdown.
- Federated Arrow batch lane: GeoParquet iter_query_arrow_batches +
  bounded batch scanning with identical row shape/predicate semantics and
  honest typed fallback when pyarrow is unavailable.
- Fabric counters per execution (remote requests, bytes, rows, peak
  streaming bytes, server placements, fallbacks, pushdowns, cache hits,
  replans, probe cost) surfaced in the additive `fabric` result section.
### Added (harness-v6: Semantic Retrieval, Durable Context & Long-Horizon Autonomy)
- Hybrid tool retrieval V6 (`semantic_retrieval.py`): bilingual synonym
  expansion (fused secondary lexical pass), capability aliasing to the 139-cap
  vocabulary, methodology-evidence channel (12 families, priority-weighted),
  negation anti-evidence, optional embedding retriever (lazy, bounded-failure,
  `TOOL_RETRIEVAL_EMBEDDING=0` off-switch); kill switch
  `GIS_TOOL_RETRIEVAL_V6=0` restores V5 bit-identical behavior.
- Retrieval confidence & abstention: calibrated score (level/margin/coverage),
  pinned 0.35 threshold; production seam withholds the dynamic surface on
  abstention with a chain-disclosed reason (no silent blind dispatch).
- Golden corpus 66 -> 358 open-loop query->tool cases (direct / near-duplicate
  / hard-negative / ambiguous / out-of-scope, zh+en+mixed) with per-kind gates,
  oos-abstention / over-abstention / ECE calibration metrics. Same-corpus vs
  V5 lexical baseline: p@1 0.4944->0.5587, r@5 0.6731->0.7523, r@10
  0.7289->0.8059, invalid-selection flat at 0.25.
- Lexical discriminability fix (`tool_retrieval.py`): closed CJK stopword set
  (zero weight) + single-char down-weighting (x0.25) + multi-char-only
  anti-evidence; V3 == V4-off contract preserved. 22 declarative
  `anti_examples` added to confusable sibling tools.
- Durable recovery ledger (`recovery_ledger.py`): per-(session, tool,
  failure-class) attempt budget persisted session-plane with flock +
  write-through; success write-back clears a tool's counts; TTL lazy decay;
  LRU-bounded; resume carries budget across sessions (no budget rebirth).
  `classify_and_remediate` prefers the durable channel when a session id is
  present; process ledger remains the no-session fallback.
- Trace store V6: segmented layout (`trace_v6/seg_N.jsonl(.gz)` + manifest),
  O(segment) appends with gzip roll archive, exact 64-record window with
  per-record FINAL_VERDICT protection preserved, `read_chains_since`
  incremental reads (skipped segments never parsed), manifest O(1) `last_seq`,
  torn-tail healing, `iter_session_chains` bridging API. V4/V5 single-file
  layouts remain read-tolerant with seq continuation.
- Durable context three-tier model (`durable_context.py`): closed
  durable-facts / rebuildable-projections / forbidden vocabularies (conservative
  default), bounded recovery_state (position/loop budgets/history) at turn
  boundaries; resume anchors now carry `recovery_state` + `reasoning_digest`
  (additive JSON keys, no migration).
- Long-horizon continuation (`continuation.py`): single pure decision point —
  budget-exhausted/unrecoverable failures abort with disclosure; render
  failures run repair->reobserve; insufficient data qualification runs
  deepen->requalify then refuses to execute; pending observations force
  re-observation. Wired into runtime repair outcomes.
- Subagent budget classes: light/standard/heavy/research vocabulary with
  intersect semantics (classes can only tighten), token ceilings, usage()
  audit field; existing role numbers untouched.
- Observation state ladder (`observation_states.py`): unknown->pending->
  mounted->loaded->rendered->data_present->semantically_correct with
  `to_workflow_health` (ok/degraded/partial/blocked) consumed via the additive
  `map_product.observation_health` key (always emitted; missing observation =
  blocked).
- Chaos corpus (`chaos_corpus.py`): 16 deterministic scenarios covering all
  eight epic-mandated classes, each pinned to a real pytest node (meta-gate
  verifies existence); real kill -9 flock-release, cross-session isolation,
  and resume-budget continuity invariant tests.

### Added (lakehouse-v7: Spatial Lakehouse V7 — Cloud-Native N-D Geospatial Lakehouse)
- N-D labeled cube (schema v2): zarr v3 `dimension_names` binding, dim
  whitelist {time,band,polarization,vertical,y,x} with y/x anchored last,
  explicit monotonic coordinates, per-variable dtypes, CRS validation
  (rasterio with regex-fallback disclosure), xarray adapter (typed degrade,
  V6 synthesized projection). V6 read/fork entries version-gate v2 stores.
- Deterministic chunk planner + labeled selection (chunk-touch ∝ window,
  50k block cap, 8M cell budget, rechunk metadata planning with time-axis=1).
- Remote-sensing cube builder: optical/SAR/mask role composition, geometry-
  identity alignment (no silent resample), complete time axis enforcement,
  64M-cell assembly budget.
- S3 productionization: streaming multipart put (8MiB parts, abort-clean
  failures, per-part retry), digest-in-Metadata put path, streaming get with
  tail digest verification, ETag sidecar recording, paginated object
  enumeration, stale multipart/staging sweeps.
- Virtual DataObjects: zero-copy child-reference identity, recursive verify
  (missing children ≠ verified), global resolution budget, lazy/inline
  materialization, sharded composition for 10k+ chunk metadata.
- Lakehouse catalog projection (+ migration 0034, additive): surrogate PK,
  multi-owner uniqueness, bbox/time indexes, PG-only tags GIN, bounded
  pagination search, revoke tombstones, manifest-driven reconciliation,
  STAC 1.0.0 projection (typed required-field gates).
- Zero-byte project publishing: find-or-create artifact → immutable content
  revision → catalog row, owner-chain enforcement, idempotent republication,
  project-scoped resolve via catalog authorization.
- Dereference-based GC: union blob protection (fork-shared blobs survive),
  downward reachability propagation, plan token + watermark + execute-time
  reference recheck (stale plans rejected), running-run protected roots,
  72h grace (≥ registry TTL), deterministic deletion evidence.
- DR scrub: deterministic sample/full digest verification, ETag
  record-vs-head comparison, virtual deep states.

### Added (workflow-v5: Semantic Workflow Runtime V5)
- Executable typed-DAG runtime consuming Workflow V4 packages: durable
  package registry (semver publish, re-emit fingerprint verification),
  runtime instances with per-node CAS state machine (9-state table incl.
  STALE reuse-resolution and lease-gated orphan recovery), and bounded
  wave scheduling over the existing GeoCompute execution plane
  ([op, MATERIALIZE] plans; unwired operators honestly NODE_NOT_EXECUTABLE).
- Incremental recompute closed loop: semantic changes drive V4
  compute_affected_subgraph (fixed for real bounded edge form), mark
  exact stale subtrees via node-level CAS, resolve via reuse-first
  verdicts (content-level fingerprints with content_revision; shape-level
  recorded but never reused), and re-execute only dirty nodes with
  effective parameter values in reuse fingerprints.
- Runtime typed-port verification against live artifact descriptors
  (artifact type / geometry family / CRS class via crs_safety / unit /
  required / cardinality), PASS and blocked evidence both persisted.
- REST /api/v1/workflow-runtime (packages/instances lifecycle, dry-run
  recompute plan, explanations), fail-open chat-session hooks
  (plan attach, tool-result record outside the session lock, MapSpec
  style changes), and a minimal workflow runtime inspector panel.
- Migration 0034 (packages/instances/nodes/reuse tables, additive,
  re-entrant DDL, single-head asserted).

### Added (science-v4: Spatial Science & GeoAI Platform V4)
- Geostatistics V4: simple kriging, external-drift kriging (KED), normal-score
  transform, nested-variogram fitting (honest ConvergenceFailure when not
  beneficial), sequential Gaussian simulation (caller-seeded, bitwise
  reproducible, P10/P50/P90 ensemble), full co-kriging under an LMC
  (per-structure PSD by construction), and spatiotemporal kriging
  (separable / product-sum with second-scale mixture, PSD by construction).
- Hydrology & terrain V4: depression breaching, HAND, Shreve magnitude,
  single-level Pfafstetter coding, hypsometric analysis (Strahler 1952
  integral), clear-sky solar radiation (FAO-56 anchor), and a chunked
  priority-flood backend variant (banded, low heap footprint) registered
  against the full-path reference with a pinned parity bound.
- Scientific contract ratchet: heavy algorithms must declare
  resource_envelope / cancellation_profile / NumericalTolerance (frozen
  44-entry baseline, shrink-only); interpolation + terrain domains fully
  declared. Cancellation checkpoints sunk into terrain hot loops
  (fill heap / D-infinity topology / viewshed sectors).
- Typed scientific errors: pyproj.CRSError folded into InvalidCRS at the
  UTM boundary (KNOWN-GAP #1 xfail promoted), NumericalInstability and
  ConvergenceFailure with real producers; geometry repair disclosure
  (never-silent make_valid) with strict typed rejection in zonal
  statistics (KNOWN-GAP #2 xfail promoted).

### Fixed (science-v4)
- raw pyproj.CRSError no longer escapes as TOOL_ERROR (loses correction_hint);
- zonal statistics no longer silently accepts self-intersecting polygons;
- terrain tool layer wraps RasterioIOError into typed RasterReaderError.

### Added
- GIS Extension Platform V2 (ADR-0105): worker-isolated execution
  (`execution.mode=worker` — extension code runs in a subprocess the host
  never imports, with a sanitized environment, RLIMIT memory/CPU budgets,
  output caps, and crash → rollback → quarantine) plus a default-deny
  capability broker for worker host access (network allowlist + the
  authoritative SSRF gate, confined artifact roots, provisioning-gated
  secrets with an audited ring).
- Supply chain for extension packs: HMAC content signing (`package` /
  `verify` — tampered or invalid signatures quarantine a pack even if
  allowlisted, and verified signatures can elevate trust under
  `EXTENSIONS_TRUST_SIGNED`), a deterministic SBOM with secret-shape
  scanning (`sbom`), and a certification suite (`certify`).
- `model_provider` extension type for GIS domain inference models: each
  provider projects to a typed invoke tool on the real dispatch path
  (in-process streaming via event iterators with cooperative
  cancellation; worker mode = single-frame aggregate, streaming
  typed-refused). Example pack: `extensions/examples/extdemo-ml-pack`.
- Dependency version constraints (`">=1.2,<2.0"` syntax) checked at
  validate time, deterministic topological activation order,
  upgrade-conflict refusal (`dependency_conflict`), and explicit
  `allow_downgrade=True` rollback semantics.
- A host projection-change hook: every post-startup activate / deactivate /
  rollback (including worker-crash auto-deactivation) now recompiles the
  runtime manifest and refreshes tool args, removing the V1 known
  limitation that left the manifest stale after deactivation.
- Professional Cartographic Rendering V5 (ADR-0118 cartographic-rendering-v5): authoritative render
  diagnostics vocabulary (`app/lib/cartography/render_diagnostics.py`, 18
  codes) exported via component catalog schemaVersion 5 and locked as a
  frontend subset by registry-parity tests; diagnostics now travel with the
  exported artifact (`render_diagnostics` form field on `POST /api/v1/export`)
  and persist as `{filename}.diagnostics.json` sidecars, readable via
  `GET /api/v1/export/diagnostics/{filename}` with download-equivalent
  fail-closed ownership.
- True vector SVG export: the orphaned MapSpec→SVG compiler and SVG
  marginalia generators are wired into the exporter (`vector-svg-export.ts`)
  with honest degradation (`basemap_omitted_vector_svg`, raster fallback via
  `vector_svg_fallback_raster`); atlas/multi-frame export runtime
  (`frames` + `frameLayout`, pdf pages / png grid) with deterministic frame
  restore, page caps and per-frame skip disclosures; swipe comparison exports
  compose the second view explicitly instead of silently dropping it.
- Label text fitting contract: `fit_label_text` / `wrap_label_text` in the
  label engine (60 code points, ellipsis), wired into the Python twin SVG
  renderer and the vector SVG export path; a shared 220-char fixture locks
  cross-twin truncation parity.
- Semantic render-scene oracle (`describeRenderScene`) with golden corpus and
  live-composition semantics checks — live ↔ export parity assertions on
  meaning (presence/visibility/disclosure), not pixels.
- Spatial Data Lakehouse & Cube V6 (ADR-0118 spatial-data-lakehouse-cube-v6): durable DataObject identity —
  manifests are content-addressed (id = canonical sha256, deterministic,
  owner-scoped, with input reuse fingerprints and an environment
  fingerprint), published through the existing BlobStore CAS with free
  byte-level dedup.
- S3-compatible BlobStore backend (same interface, optional boto3 with
  typed degrade): staging->copy atomic publish, verify-before-trust
  put-if-absent, digest-verified reads; endpoint gated by the existing
  SSRF guard before any runtime dependency imports. Selected via
  WEBGIS_OBJECT_STORE_BACKEND (default filesystem — zero behavior change).
- `ref:fabric-parquet/<id>` is now a first-class ledger citizen: the
  previously write-only dangling ref gained a resolver, session-ledger
  registration, probe/GC visibility, durable content identity, and a
  row-group-pruned window scan (`POST /api/v1/lakehouse/vector/scan`)
  with honest structural evidence (row_groups_read/total, truncation).
- Zarr V6 cube runtime: group cubes (one (time,y,x) array per band) with
  consolidated metadata, chunk-granular window reads, and immutable
  revisions via hardlink copy-on-write forks (source store stays
  byte-identical). Session production path: `POST /api/v1/lakehouse/cubes`
  + `/cubes/window`; refs (`ref:cube/<id>`) are ledger-visible with GC
  protection. Grid mismatches are typed refusals — never silent resamples.
- Lakehouse REST surface (`/api/v1/lakehouse/*`): owner-guarded object
  manifest reads, vector window scan, cube build/window, and DR verify;
  foreign-owner access returns 404 without leaking existence.
- Workspace durability for lakehouse refs: snapshots can materialize
  fabric-parquet (binary lane) and cube (manifest lane) pointers; restore
  re-materializes from the BlobStore with per-blob digest verification
  (session-death reopen). DR helpers: working-copy verify/repair, CAS
  chunk backup, read-only missing/orphan scans.
- `WEBGIS_REF_CONTENT_HASH` now defaults ON — session ref descriptors carry
  a canonical payload sha256 (<=1MiB) by default; set the env to
  0/false/no/off for the legacy always-None behavior.

### Fixed
- `raster_store.save_png` now publishes atomically (tmp + os.replace) —
  the last non-atomic durable write in the repo; crashes can no longer
  leave half-written PNGs.
- Lakehouse tests sandbox the BlobStore root: the process-wide
  content-store cache is reset per test, so tests monkeypatching DATA_DIR
  no longer leak blobs into the repo's ./data.

- GeoCompute Cluster Runtime V6: runs gain a durable control plane
  (`geocompute_runs`/`geocompute_workers`/`geocompute_resource_usage`) with
  lease/epoch fencing, coordinator leadership arbitration, stale-lease
  reclamation, distributed cancellation (any process can request; queued runs
  converge directly, running runs via 0.5s heartbeat watchdog), priority
  preemption at node boundaries, tenant weighted-fair dispatch with
  starvation-free round-robin, profile channel matching, and a cluster
  resource ledger (advisory by default, `WEBGIS_CLUSTER_LEDGER_ENFORCING=1`
  for enforced admission). New REST: `POST /geocompute/plans/runs` (202 async
  submit with 413/429 bounds), `GET /geocompute/runs` (owner-scoped merge of
  cluster rows and terminal snapshots), upgraded `GET /runs/{id}` /
  `POST /plans/runs/{id}/cancel` (cross-process), `GET /geocompute/cluster/
  metrics` (admin, bounded cardinality). Coordinator is opt-in via
  `WEBGIS_CLUSTER_COORDINATOR=1`; sync `/plans/execute` behavior is unchanged.
  Durable node dispatch now carries the plan budget into the worker task body
  (worker-side row caps no longer rely on the hard node cap alone).

### Fixed
- `SetLayoutIntent` with explicit `legend={"visible": false}` no longer gets
  silently flipped back by the pre-commit AUTO_SAFE repair loop
  (user-wins suppression channel + honest `carto.legend.completeness`
  finding); legend/margins intents now merge field-wise instead of dropping
  pre-existing keys.
- Twin SVG compiler respects layer visibility and actually enforces
  `thresholds.maxFeatures` / `thresholds.timeoutMs` (previously declared but
  never consumed), emitting `features_truncated` / `export_timeout_partial`.
- PDF exports no longer draw the title twice, no longer garble CJK text
  (rasterized with `pdf_text_rasterized_cjk` disclosure), and the success
  message no longer claims a fully vector artifact; report-chain SVG
  compilation is bounded by `asyncio.wait_for`.
- Export chrome truth now composes pending presentation/removals exactly like
  live; legend titles use `legend.title`; dead degradation codes gained real
  emitters; nodata legend entries render on live and export sides; a
  prior-blocking-cache eviction TypeError in `apply_presentation_batch`
  (batch transactions rolled back once the cache filled) is fixed.
- GeoCompute default session factories (`run_evidence`, `reuse_index`,
  `durable`) handed back a `sessionmaker`/function object instead of a
  `Session`, which is not a context manager on SQLAlchemy 2.0 — production
  default-path run evidence snapshots, cross-process reuse records, and
  durable-node await polling silently failed (fail-open). They now return a
  session instance (same discipline as the jobs subsystem).

### Added (Workbench V5 & Collaboration — ADR-0105)
- Workbench organization state (nested group tree / membership / layer locks /
  workbench mode) is now durable: new `patch_workbench_state` MapSpec mutation
  intent persists a `WorkbenchDocV5` into the session's `mapspec["workbench"]`
  branch over the existing lock + CAS + provenance chain (256KB real-UTF-8
  gate, deterministic structural validation). Selection/isolate remain
  session-transient per ADR-0104 gis-harness-autonomous-runtime-v4; no second truth store.
- Agent layer-lock enforcement: `set_layer_visibility` and `remove_layer`
  transactions partition targets by the user lock set - fully locked targets
  fail with a typed `layer_locked` ack error; partially locked targets apply
  to unlocked ones and disclose `locked_layer_ids`. User unlock is the only
  override.
- Undo/redo (bounded 50, session-scoped) via a capture-before-execute command
  model that replays inverse mutations through the same CAS channels; removal
  is journaled as irreversible. Ops journal (opsLog) now receives live writes
  with who/what/reversible metadata; panel header undo/redo buttons + global
  Ctrl/Command+Z / Shift+Z / Ctrl+Y.
- Same-session multi-tab foundation: committed workbench docs broadcast over
  BroadcastChannel (`wb5:{sessionId}`) with revision-gated adoption;
  deterministic conflict resolution stays server-side CAS last-writer-wins.
- Refresh resume: authenticated sessions auto-restore via a localStorage
  session anchor (pointer only, 7-day TTL, no tokens persisted); pagehide
  best-effort flush of pending doc edits.
- True side-by-side comparison: the primary canvas shrinks to the left half
  while the secondary pane owns the right half with synced cameras; secondary
  parity for terrain (is3D), legend filters, selection filters, and the
  secondary layer family legend (same LegendStack). Escape exits comparison.
- 10k-layer panel virtualization (windowed rendering above 200 rows, stable
  row keys, collapsed-subtree pruning) and deterministic viewport grid
  thinning for large inline GeoJSON (8x8 cells, area-first, 5000-feature
  budget) with stale-viewport apply cancellation (generation token + idle).
- Layer provenance badge (backend `provenance.result_ref`/`tool_call_id`)
  linking to the results workbench - reads existing lineage facts only.

### Changed (Workbench V5)
- Nested user groups replace the flat V4 group list (`parentId` on
  `LayerGroupEntity`, depth <= 4, cycle-safe projection with visited guard);
  rename/remove/assign/lock/drop mutations are undoable doc commands;
  group removal promotes children.

### Added (Harness V5 — ADR-0118 gis-harness-autonomous-runtime-v5)
- Durable trace V5: session trace-chain JSONL is now multi-worker safe
  (cross-process flock + per-session monotonic `seq` + settle idempotency);
  FINAL_VERDICT records are never dropped by the rolling window; chains survive
  in-registry LRU eviction via a bounded pinned area (multi-process zero-loss
  contract test: 8 processes × 6 records).
- Unified failure taxonomy + typed remediation: 11-class `HarnessFailureClass`
  (CRS/renderer/stale-ref/timeout/partial/... ) adapters over the existing
  planning/geocompute enums; the dispatch error seam now attaches a
  `harness_failure` verdict with bounded retry budget (max 3 per class,
  exhaustion → `abort_with_disclosure`); pyproj CRS failures no longer escape
  as generic TOOL_ERROR (KNOWN-GAP #1 fixed, xfail promoted).
- Longitude-convention hardening: new pure `app/lib/gis/longitude.py`
  (pm180/e360 detection that never guesses, antimeridian geometry splitting,
  0–360 normalization) + profile facts feeding the planner.
- Progressive DatasetProfile: explicit cheap→deep `deepen_profile` (cheap
  provenance kept, deep-failure falls back to cheap); longitude facts flow into
  the resolver contract additively (emitted only with real evidence).
- Rendered-state observation: per-layer telemetry (`source_status`,
  `render_complete`, `feature_count`) and chart render telemetry
  (`charts[].rendered/data_points`) — all optional, old clients keep the V4
  gate; finalization now detects requested-vs-actual mismatches including
  "chart mounted but rendered without data".
- Subagent accounting: child runs bind a dedicated TurnEvidence so provider
  token usage rolls up into `SubagentBudget.llm_usage` and the parent turn
  evidence (once, idempotent); results carry `budget_usage` + lineage
  (parent_turn_id/depth/role).
- Open-loop query→tool retrieval evaluation: 66 hand-gold cases (direct/
  near-duplicate/hard-negative/ambiguous, zh+en) with precision@1 over the
  ranked (non-core) segment; measured pins p@1 0.65 / invalid-selection 0.33
  recorded as the honest V5 baseline.
- Project-level workflow resume: `workflow_resume_anchors` table (migration
  0032) + `POST /chat/sessions/{id}/workflow-resume-anchor` and
  `POST /chat/workflow-resume/{anchor_id}` — resume creates a fresh session
  with the plan/goal/instance blocks restored, ref payloads rehydrated
  best-effort, `missing_refs` disclosed, anonymous resume refused.
- Live-failure corpus (8 categories) with deterministic typed expectations and
  a budget-ladder termination proof, plus an end-to-end mid-failure recovery
  scenario (CRS fault → typed diagnose → remediation retry → verified
  finalization with render telemetry → durable trace).

## [Unreleased] - 2026-09-07

### Added
- Durable workspace snapshots: project-side snapshot save/list/get/restore/
  clone/delete under `/api/v1/projects/{project_id}/workspace/...` plus
  `describe_workspace` and snapshot tools. Snapshots survive session purge/TTL;
  save accepts `materialize=` to write payload bytes into the durable content
  store; restore re-materializes with digest verification and explicitly
  discloses degraded refs (dead refs are never restored as valid).
- Durable artifact revisions: every materialization now records an immutable
  revision keyed by payload digest (`artifact_revisions`), with artifact pinning,
  zero-copy clone, and reference-counted content GC (grace period
  `PROMOTION_STORE_GC_GRACE_HOURS`, default 168h).
- Upload/ingest pipeline: re-uploading the same file in a session is now
  idempotent (returns the existing record via `content_sha256`); CSV decoding
  falls back utf-8 → gb18030 with honest errors; CRS assumptions are disclosed
  instead of silently defaulting; rasters get nodata/overview profiles at
  upload; new `ingest_dataset` tool exposes the pipeline to agents.
- Data quality repair: quality issues now produce reviewable repair proposals
  (plan-only by default — nothing is auto-applied); executing a repair creates
  a new artifact with bounded digest-only evidence and never mutates the
  source; dataset `quality_status` gains `repairable`/`blocked` states.
- GeoCompute run control: `POST /api/v1/geocompute/plans/runs/{run_id}/cancel`
  and a `cancel_execution_run` tool; run results survive process restarts via
  bounded evidence snapshots; nodes route to six capability queues
  (light_cpu/heavy_cpu/high_memory/raster/network/external_io — single-worker
  deployments consume all queues and behave exactly as before), with
  worker-loss retries re-affine to the same queue.
- Vector performance lane: GeoParquet sources can execute filter/projection/
  aggregate natively in Arrow when `pyarrow` is installed (results disclose
  which lane ran); without pyarrow everything falls back to the universal
  dict path. Aggregate semantics (count/stddev/distinct-count) unified across
  both lanes.
- Raster runtime: chunk-level execution with opt-in resumable chunk cache
  (`WEBGIS_CHUNK_CACHE_BYTES`), COG conversion at ingest or on demand
  (`convert_raster_to_cog` tool), a typed "Zarr unavailable" foundation (no new
  dependency), terrain full-read byte guards, and streamed STAC DEM windows
  (bit-identical results).
- Federated queries: filters are now pushed down per-clause — partially
  pushable filters no longer pull full datasets (with a pinned bit-compatible
  fallback when equivalence cannot be proven); N-source chain federation is
  exposed as a `query_federated_chain` tool; per-source minimal projections
  are derived by default; semi-join reduction now also applies to two-source
  joins.
- Per-project storage quotas (`WEBGIS_PROJECT_ARTIFACT_MAX_BYTES/_MAX_COUNT/
  _MAX_REVISION_BYTES`, default unlimited) with an honest `quota_exceeded`
  status (the record survives metadata-only; no bytes written); retention
  policy (`WEBGIS_RETENTION_MAX_AGE_DAYS`, default keep-forever) sharing one
  protection predicate (pinned / workspace / lineage-rooted) with GC;
  operator endpoints `GET .../data-usage` and `POST .../data-gc/plan|execute`
  (confirm-gated).
- Provenance hardening: run lineage carries a reproducibility verdict and a
  runtime environment fingerprint (Python/GEOS/PROJ/GDAL/shapely versions);
  lineage parameters and trace args are redacted at write time (secrets →
  `[REDACTED]`, oversized values → digests).

### Fixed
- Resource governor self-denial-of-service: rows/bytes/nodes were lifetime
  counters, so ~25 estimate-heavy runs could permanently exhaust global
  budgets until restart. Usage is now a concurrent in-flight gauge — fully
  returned when a run ends (regression-tested), with an opt-in cross-process
  Redis counter (`WEBGIS_CROSS_PROCESS_GOVERNOR`, advisory and fail-open) and
  heavy nodes consuming weighted concurrency slots.
- Cache isolation and stampede safety: tool cache keys now include the
  session owner domain (cross-user sharing eliminated for session-bearing
  tools), describe-cache keys include tenant/owner scope, hot rebuilds are
  single-flighted, and the raster tile cache gained a byte bound
  (`RASTER_TILE_CACHE_MAX_BYTES`, 256 MiB) plus TTL'd stats
  (`RASTER_STATS_CACHE_TTL_S`) wired into the existing invalidation authority.
- A raster materialization path could report success before bytes were
  durable; ingest/materialize rollback ordering is now write-first.
- Lineage write-time redaction closes a secrets-hygiene gap where inline
  GeoJSON and credentials could land verbatim in database rows.

### Changed
- Four additive migrations (0026–0029): `artifact_revisions`,
  `uploads.content_sha256`, `artifact_lineages.repair_evidence` + quality
  CHECK vocabulary, and two GeoCompute cache/evidence tables. All are pure
  additions; with no new env vars set, deployment behavior is unchanged.

## [Unreleased] - 2026-09-08

### Added
- GIS Extension Platform V1（ADR-0104 gis-extension-platform-v1）：第三方/内部扩展的统一宿主
  `app/extensions_platform/` —— `GisExtensionManifest`（fail-closed、
  schema 版本化、命名空间强制、保留词表）、typed 诊断码、扩展 API/核心
  版本窗口兼容判定、权限模型（声明≠授权、typed 拒绝、仅可收窄）、
  信任分级（trusted-code boundary，不宣称沙箱）、有界发现 + 内容指纹、
  完整生命周期（discover→validate→activate→health→deactivate→unload
  →reload，激活原子、卸载零僵尸、依赖环检测、disable/enable）。
- 五个扩展 SDK：tool / algorithm / provider / cartography / workflow
  pack —— 声明式 spec、词表与宿主规则校验、权限包裹、与核心 register
  完全同构的投影；扩展条目强制命名空间前缀，物理上不可遮蔽核心 ID。
- Developer CLI：`python -m app.extensions_platform`
  list/inspect/validate/doctor/scaffold/catalog（human + --json）。
- 示例扩展包 `extensions/examples/extdemo-pack/`：2 工具 + 1 算法 +
  1 离线 tile-catalog provider + 1 planned 制图组件 + 1 recipe，
  兼作集成测试 fixture（零网络）。
- 一致性语料库：2014 个确定性 case（manifest 矩阵 / 权限矩阵 /
  策略×manifest 评估 / 生命周期不变量），`tests/unit/extensions_platform/`。
- 完整文档 `docs/extension-platform/`（架构 / manifest 参考 / 各 SDK
  authoring 指南 / 权限与信任 / 兼容性 / 打包 / 测试 / CLI / OGC-STAC /
  已知限制）。
- 设置项（默认全关，不配置即零行为变化）：`EXTENSIONS_ENABLED`、
  `EXTENSIONS_DIRS`、`EXTENSIONS_ALLOW/BLOCK/BUILTIN_IDS`、
  `EXTENSION_PERMISSION_GRANTS`、`EXTENSION_FEATURE_FLAGS`、
  `EXTENSION_SETTINGS_JSON`、`STAC_API_URL`。

### Fixed
- WMS/WMTS `describe()` 不再伪造 `EPSG:3857` 与全球 bbox：CRS/bbox 取自
  capabilities（WMS 1.1.1/1.3.0 双形、URN 归一、父 Layer 继承），
  不可判定时诚实置空并附 metadata 说明。
- GDAL `/vsicurl` 远程读取接入 SSRF 门（复用 data_fabric
  `validate_url`；本机/私网/云元数据地址拒绝）；STAC asset href 同门前置。
- Professional Cartography Workbench V4（feat/professional-cartography-workbench-v4）:
  Explore / Analyze / Compose 三模式工作台（模式只改面板组合；agent `set_mode`
  确定性合约 + 一键返回）；专业图层工作台（用户分组树/折叠/锁定/隔离/批量
  显隐与不透明度/复制粘贴样式/单层重载/搜索，列表自下而上如实标注叠放方向）；
  图层与地图的 golden-model parity 测试与 500 层压力冒烟。
- 浮动图表双向联动补全：9 类图表可点选发布地图过滤（分级/连续专题层色彩
  受保护，拒绝会抹平分级的改色意图）；可选「视野联动」（地图视野 → 图表
  过滤，显式 opt-in，结构无环）。
- 对比工作台（swipe 卷帘）：键盘可达分割线、主副视图相机同步（防回环）；
  side-by-side 在「主图不动」约束下无法构成有效对比，诚实下线（词表保留）。
- typed 样式意图合约（`apply_style_intent`）：颜色/透明度/线宽/点径等封闭
  词表，相对意图按当前样式求值；palette/分级改写在后端通道就绪前如实失败。
- 导出显式降级：图表面板/表格面板数据拉取失败不再静默缺席 —— 导出完成的
  系统消息会列出未进入成品件的组件。
- `dasymetric_map`（分区密度图）planned → native：控制要素重分配算法
  （总量守恒、负值钳制披露、退化碎片计数、确定性），工具 `dasymetric_reallocation`。

### Changed
- 模式化导航栏：rail 顶部模式切换，tab 组合随模式过滤（每模式记忆自己的
  工作面板）；dock 标签页支持键盘切换。
- 组件运行时：浮动面板拖拽吸附锚槽、点击置顶、键盘缩放/折叠/隐藏
  （Ctrl+方向键 / Enter / Delete）；`patch_component` 合约补齐
  style/options/position 字段。

### Fixed
- 图层状态：spec 镜像修剪吞新行的窗口（B1）；用户改显隐/不透明度后永久
  假「待同步」徽章（B2/B3：观测新增 presentation_converged，服务端回灌
  保留认证）；跨组拖拽不生效（B4）；批量操作/隔离跳过锁定层。
- live/export 组件词表反向 parity（披露族/表格面板此前导出画、live 不挂）
  —— 包含测试锁定单一词表来源。
- 设计 token：修复无背景/无配色的 WebGL 错误兜底与卡片（坏 utility）、
  `--accent` 双重声明暗雷、删除漂移的第二主题色表；图表轴灰对比度达标。
- 无障碍：分组重命名键盘可达、模式切换焦点保持、skip link + 地图地标、
  dock 键盘导航、批量删除确认聚焦、锁定/专题保护语义在失败提示中可感知；
  eslint 接入 jsx-a11y（`--max-warnings 0` 门保持通过）。

## [Unreleased] - 2026-08-28

### Fixed
- Pi-host pre-landing review pass: `ensure_session_plan_slot` uses
  double-checked locking (lockless fast path — per-callback slot checks no
  longer cost a Redis lock cycle); a session-lock `TimeoutError` during a
  SessionPlan apply retries once so a supersede is not silently lost to a
  long cartographic evaluation holding the lock; the native-dump fail-fast
  test owns its rpc-entry precondition (CI without built `vendor/pi`
  asserts the dump contract, not a vendor `FileNotFoundError`).
- Spec/doc truth: `pi-as-agent-host.md` no longer cites the nonexistent
  `app/services/cartography_runtime.py`; `api-docs.md` documents
  `agent_runtime` on `/api/v1/health` (fail-closed) and the three
  stream-only SessionPlan SSE events.
- New regression tests: status `session_id` passthrough exemption,
  execute-without-`toolName` reject, dead-Pi `/health` reports
  `chatengine` (#1032).

## [Unreleased] - 2026-08-20

### Fixed
- Unfiltered local full-suite runs no longer execute `@pytest.mark.perf`
  benchmarks mid-suite: perf items now self-skip with the isolated-run
  command unless `-m` selects perf (baselines assume isolated execution;
  mid-suite timing was nondeterministically red — 0/4/7 failures across
  three same-day runs, worst on clean master) (#664).

### Added
- `RAG_EMBEDDING_OFFLINE` setting (default false): when enabled, the lazy
  SentenceTransformer load runs with `local_files_only=True` — an uncached
  model fails in seconds (RAG degrades via its documented fallback) instead
  of an unbounded HuggingFace download hanging the `to_thread` worker and
  stalling graceful shutdown (#662). Production surfaces (Dockerfile.prod,
  both prod composes' api + celery-worker) enable it; cache-provisioning
  options documented in docs/DEPLOYMENT.md.

### Changed
- Env loading moved out of `app/main.py` import time into the launchers
  (`python main.py` / `manage.py` call `load_dotenv()` themselves; bare
  `uvicorn app.main:app` now needs `--env-file .env`). Importing app code no
  longer mutates `os.environ` — this was the leak that armed the real-services
  smoke lane mid-suite on machines with a reachable Redis (#661/#663).
- The test suite pre-pins every `.env.example` key to a Settings-default-
  equivalent baseline, so locally exported real keys (API tokens, Redis,
  database URLs) can no longer change suite behavior (#663).


### Added
- User map chrome (visibility, opacity, remove, reorder, explicit frame) now
  commits through the same MapSpec mutation engine the Agent uses, so hide and
  fade survive refresh and the next Agent turn.
- Live map paints committed MapSpec plus an in-flight pending overlay. Gesture
  camera stays Observed Map and does not rewrite Desired view.
- Fail-closed evaluation vocabulary: MapSpecValidity stops at SEMANTIC_VALID,
  live Observed Map is the runtime oracle, Cartography Verdict is
  `pass` | `fail` | `not_evaluated`, and CartographicQuality is the production
  gate (ADR-0060–0064).

### Changed
- Session `map-state` POST no longer replaces desired layers. Chrome edits that
  used to write HUD-only now go through `apply_mutation` with `expected_revision`.
- A stale chrome edit is `superseded` (HTTP 409) and the HUD re-projects from
  the committed MapSpec instead of last-write-wins.

## [Unreleased] - 2026-08-17

### Issue-resolution wave: 52 open issues (#514–#565) fixed root-cause-first (PRs #566–#576)

Every open issue at the time was re-verified against master, fixed at the root
(no symptom suppression), covered by regression tests, independently reviewed,
and merged in 11 scoped PRs. Highlights by theme:

- **Auth & contract (P1).** MVT tiles for >5000-feature layers 404'd in every
  session because browser-native MapLibre fetches can't attach headers — fixed
  via `transformRequest` credential injection with live token reads and
  origin-exact first-party matching (#514). Export/report downloads and
  chat-embedded links/images always 401'd on bare `<a>`/`<img>` — all routed
  through a new authenticated blob transport (#515). `LEGACY_TOOL_NAME_MAP`
  shadowed the live `remove_layer`/`zoom_to_layer` tools (#516). ~30
  `to_llm_response()` tool sites bypassed geojson-ref mounting, so analysis
  results never reached the map (#517). `explorer_progress` had no reachable
  producer: logged-in sessions now use the owner-verified independent stream,
  anonymous sessions get progress bridged into their session-isolated chat
  stream with a bounded, explicit-terminal lifecycle (#518).
- **Session & agent runtime.** Session-store serialization moved off the event
  loop with request DTO caps (#521); anonymous sessions bucket per
  `owner_token`, eliminating cross-user cap evictions and their FK-swallow
  cascade (#522); the ownership guard no longer selectinloads all messages on
  every 3s poll (#525); explorer chain-run state is durable across restarts
  (status/abort/stream all work post-restart) (#526); `{"error": ...}` tool
  results are classified as failures across dispatch, plan mode, and metrics —
  plans no longer advance past failures and retries no longer lie "已成功执行"
  (#529).
- **GIS numeric truth.** `raster_difference`/`temporal_raster` honor declared
  nodata in streamed and in-memory stats (#523); `buffer_smart` divides by the
  metres-per-unit factor instead of multiplying — foot-CRS buffers were ~10.8×
  too small (#524); EVI/NDVI stop counting nodata zero-pixels as valid and the
  output header matches the bytes (#537); DANGLING_ENDPOINT tolerance is live
  again via buffered STRtree queries (#538); no-AOI temporal analysis covers
  the scene instead of a unit square, and zero-data trends report "unknown"
  instead of a fabricated "stable" (#541); Amap transit and Baidu
  distance-matrix read the real response contracts (#542).
- **Performance.** Quality-audit topology checks are budgeted with explicit
  truncation reporting (400 rings: 4116ms → 286ms) (#539); network engine:
  indexed barriers, top-K-first closest facility (D×K route builds), O(1)
  2-opt delta (320 stops: 51.1s → 572ms), conditional service-area graph copy
  (#540). Equivalence to the naive paths is proven by randomized tests;
  persistent perf gates have floors at the pre-fix numbers.
- **Frontend contract & UI.** Export idle-wait is bounded with pixelRatio
  restore (#527); project writes are auth-gated in the UI (#528); heatmap
  raster results carry an addressable image through authoring → validator →
  mount (#533); `set_map_view` accepts bearing/pitch-only (#534); the ghost
  `query_features` command is implemented and a backend⊆frontend catalogue
  invariant test guards the whole command family (#535); OpenTopoMap's
  unexpanded `{s}` placeholder is fixed (#536). Explorer tasks are capped,
  dismissible, and cleared on session switch (#548); workflow polling resumes
  on visibilitychange (#549); the story page shows honest errors and renders
  shared content (#552); sessions can be deleted with a two-step confirm and
  new-session wipes are guarded (#553); chat requests carry the active
  `project_id` (#558). Settings basemap cards bind to the real TILE_PROVIDERS
  catalogue (#550); fake settings controls were wired or removed one by one
  (#551); the apply_template contract's five breakpoints are fixed at the
  emitter (#557).
- **Security, RAG & data lifecycle.** Monitoring/asset tools scope UploadRecord
  queries to the calling session (#543); FAISS indexes invalidate on the same
  mtime signal as metadata (#544); `add_document` compensates vectors when the
  DB write fails — no more permanent orphans (#545); upload failure branches
  clean their directories (#546); duplicate indexes dropped, model↔migration
  drift closed — including the root cause of the long-red DB Migration Gate:
  `alembic_version.version_num` was VARCHAR(32) but the chain's revision ids
  exceed it (#547).
- **Deploy & CI.** `DATA_DIR` is set across the whole matrix with shared
  api/celery storage (#519); rollback/preview pin `WEBGIS_IMAGE` (#520);
  Prometheus configs are transported to the prod host (#530); the
  real-services lane exercises the production Celery app (#531); the
  Playwright runtime validator runs nightly with `REQUIRE_BROWSER=1` (#532);
  kustomize image coordinates match CI pushes (#559); secure-stack Redis stops
  evicting broker keys (#560); k8s secret keys match the documented contract
  (#561); Grafana dashboards actually load via provisioning (#562); `rich`
  (and `networkx`) are declared dependencies (#563); PR perf smoke and real
  coverage ratchets (backend 75, frontend thresholds) (#564); async routes no
  longer run sync ORM on the event loop (#565); Pi bridge lock/drain/dispatch
  defects fixed (#554); tool-event lines are escape-before-wrap fenced against
  injection (#555); tool vocabulary derives from the live registry (#556).

## [Unreleased] - 2026-08-12

### Unified durable job runtime & cancellation lifecycle (ADR-0052)

Replaces three disconnected task-state stores (in-memory `TaskTracker`, the
zero-caller `analysis_tasks` table, and the process-local
`TaskQueueService._task_owners` dict) with one durable job lifecycle spanning
Agent task → tool step → heavy GIS job → Celery worker → artifact.

- **Cancellation now actually stops compute.** `TaskTracker.cancel()` used to flip
  a bool that was polled only *between* tool calls, so an in-flight NDVI or buffer
  analysis ran to completion (30–60s) before stopping — CPU was never released.
  A `CancellationToken` is now lit on cancel and propagates three ways: the stream
  engine `await`s it inside the parallel tool wave and **preemptively** cancels
  in-flight asyncio tasks; a ContextVar carries it into synchronous GIS code
  (`asyncio.to_thread` copies context, so ~40 tool signatures stay unchanged) where
  hot loops exit at `jobs.checkpoint()`; and a per-job watchdog thread pushes the
  durable cancel fact to cross-process workers. Benchmark: cancelling after 50 of
  10 000 chunks now executes 51 chunks (99.49% of the work skipped).
- **Task ownership survives API restart.** `_task_owners` is demoted to a fast-path
  cache; the durable row is the source of truth. After a restart a legitimate owner
  can still query *and* cancel their Celery job, while other users still get 404.
  Ownership accepts three proofs (authenticated `creator_id`, anonymous
  `owner_token` mirroring SEC-08, or a verified `session_id`); with none of them the
  predicate is constant-false rather than an unfiltered scan.
- **Explicit state machine with atomic transitions.** Every status write is a
  conditional `UPDATE ... WHERE status IN (<legal predecessors>)`, so `cancelled`
  and `completed` can never be overwritten — a worker's late success after a cancel
  converges to `cancelled` and its result is discarded. Double cancel is idempotent;
  cancelled jobs are never retried.
- **Worker-crash recovery.** Workers heartbeat independently of progress reporting;
  a 60s sweeper converges heartbeat-timed-out jobs to `stale` (retryable) — or to
  `cancelled` when the worker died mid-cancellation — so a job can no longer stay
  `running` forever.
- **Bounded progress writes.** Unified `{phase, progress, message, current_step,
  total_steps}` contract that explicitly allows `progress = null` for indeterminate
  work (no fake 99% that then hangs). Throttling at ≥1% / ≥500ms turns 100 000
  progress reports into ~100 DB writes.
- **Atomic artifact commit.** NDVI output and the `raster_math` windowed writers now
  write a temp file and `os.replace` on success; cancel/failure discards the partial
  file, so a cancelled task can no longer leave half a GeoTIFF that looks valid.
  Already-finalized artifacts are never deleted by later cleanup.
- **Agent ↔ job linkage.** Durable jobs created inside a tool inherit
  session/owner/run/turn/tool_call/step from a ContextVar and are reported back on
  `step_result` as `background_job_ids`, so a background GIS job is shown under the
  step that started it instead of as an unrelated entry.
- **Unified task API (expand-contract).** New owner-scoped `GET /tasks/jobs`,
  `GET|DELETE /tasks/jobs/{job_id}`, `POST /tasks/jobs/{job_id}/retry` returning a
  single `JobView` shape for both agent tasks and durable jobs. All five pre-existing
  `/tasks/*` endpoints keep their contract; `/tasks/status/{celery_task_id}` gains
  durable `job_id`/`durable_status`/`progress` and now degrades gracefully when the
  Celery result backend is unavailable instead of returning 500.
- **Retry is real or refused.** Retry re-enqueues the task using a persisted
  `dispatch_spec`; when no faithful spec exists (missing, oversized, or carrying a
  sensitive argument) it is refused with a reason rather than flipping the row to
  `queued` with nothing to run it.
- **Frontend Task Center.** New sidebar tab showing name/type/status/progress/message/
  elapsed with cancel and retry. Cancel shows "取消中…" until the backend confirms a
  terminal state. Polling is strictly bounded: none without active jobs, paused when
  the tab is hidden, aborted on unmount/session switch, capped after consecutive
  errors, with generation+session guards so a stale response can never write into a
  new session's UI. No new websocket transport.
- **Payload safety.** Task rows and API responses carry redacted, size-capped
  summaries — credentials are replaced, large GeoJSON/raster payloads become
  `{__omitted__, count}`, and errors are single-line (never a traceback). Task-center
  responses measure ~478 B/job.

### Fixed (pre-existing bugs surfaced by this work)

- NDVI analysis assets were never registered: the insert used `format="tif"`, which
  `ck_upload_format` rejects (`geotiff` is the allowed value), and the resulting
  `IntegrityError` was swallowed as a warning — so the tool's "结果已入库" claim was
  never true on a constraint-enforcing database.
- Migration `e46935cd5dd1` only dropped `analysis_tasks.creator_id NOT NULL` on the
  PostgreSQL branch, leaving SQLite dev databases diverged from the model.
- `analysis_tasks.status` carried both `index=True` and an explicit `idx_task_status`,
  producing two identical indexes under `create_all`.

### Migration

`0013_unified_durable_job_runtime` — additive: 17 nullable columns, 5 indexes,
widened `ck_task_status` (adds `cancelling`, `stale`), and relaxed
`org_id`/`creator_id` nullability. Upgrade and downgrade are both implemented and
existing rows survive the round trip (`cancelling`/`stale` normalize to `failed` on
downgrade rather than being deleted). Dialect-split: SQLite rebuilds the table via
`batch_alter_table`, PostgreSQL uses idempotent `IF NOT EXISTS` DDL.

## [Unreleased] - 2026-08-07

### Performance — Full-stack optimization (spatial compute / agent dispatch / rendering / storage)

- **Spatial compute offloaded off the event loop**: synchronous CPU-bound tools
  (ST-DBSCAN, KDE, Moran's I, hotspot, clustering, Voronoi, IDW, ...) now run via
  `asyncio.to_thread` at the registry dispatch boundary (`app/tools/registry.py`)
  instead of blocking the asyncio event loop — a 30s cluster analysis no longer
  freezes every concurrent SSE stream / WebSocket / request. `cache_hit_var`
  (ContextVar) propagation preserved through the thread boundary.
- **Vectorized Chinese-CRS transforms**: WGS84/GCJ-02/BD-09 conversions now run as
  NumPy array ops (`coord_transform.py`) — 100k points: ~180ms → ~36ms (−80%),
  with 1e-9 numerical parity vs the scalar reference (parity tests added).
- **`to_utm_gdf` memoization**: identity-keyed, thread-safe LRU cache
  (64 entries) around parse + UTM reprojection; repeat analysis on the same
  layer drops from ~98ms → ~0.1ms. Cache hits return defensive copies. Cache
  entries pin a strong reference to the source object — prevents CPython
  `id()` address reuse from silently serving another object's cached result
  (a flaky full-suite failure in hotspot classification).
- **Plan-mode wave-based parallel execution** (`plan_mode.py`): independent DAG
  steps now dispatch concurrently per wave via `asyncio.wait(FIRST_COMPLETED)`
  with fail-fast cancellation of remaining wave tasks. 6 independent 0.3s steps:
  ~1.8s serial → ~0.33s (5.5×). `executed` remains deterministic (topo order).
- **Streaming chat tool dispatch parallelized** (`execution_engine.py`):
  `chat_stream` previously awaited each tool call sequentially. Now a 3-phase
  pipeline — (1) emit per-tool `step_start`/`tool_call` in declaration order
  while launching all tools concurrently, (2) stream results as each completes
  (`asyncio.wait`), (3) append LLM-context messages in original `tool_call_id`
  order. 4 independent 0.3s tools: ~1.2s serial → ~0.31s (3.9×, −74%).
  Per-tool SSE ordering (start before result) and cancellation semantics preserved.
- **Concurrency-safe tool dedup**: check-and-add on `executed_tools` is now
  atomic (`tool_dispatch_service.py`) — two parallel identical tool calls can no
  longer both pass the loop guard before either adds.
- **SSE batching**: `SSEBatcher` (time/count-coalesced flush, terminal event
  bypass) added to `app/utils/sse.py`, wired into the `chat_stream` token hot
  path (32 events / 80ms window). 500-token LLM stream: ~500 HTTP writes →
  ~20 (−96%). Structural events (`step_start`/`step_result`/`done`) stay
  one-per-write so frontend event semantics are unchanged; fixed Pydantic v1
  serialization bug (v1 branch called the v2-only `model_dump()`).
- **L1 memory + L2 Redis two-layer cache** (`session_data_redis.py`):
  `get_map_state` / `get_session_metadata` read-through an in-process LRU
  (2s TTL, 512 sessions) and are write-invalidated by every state mutation —
  collapses repeated Redis round-trips within a chat turn.
- **MapSpec save path**: revision files now pruned to the newest
  `MAPSPEC_REV_RETENTION` (20) — unbounded per-session disk growth eliminated;
  identical re-saves skip all three writes (no-op guard).
- **Frontend bundle**: `export_map` command now lazy-loads the heavy
  `MapExporterEngine` (~1300 lines + canvas/layout deps) via dynamic `import()`
  inside the render callback — removed from first-load bundle of every map screen.
- **Viewport bbox filtering utilities**: `filterFeaturesByBounds` /
  `geometryBBox` / `bboxIntersects` in `frontend/lib/utils/geo.ts` — pure
  pre-`setData` culling for large inline GeoJSON sources (worker-safe, tested).
- **Viewport filtering wired into the render pipeline**: `renderer.ts`
  `addGeoJsonSource` accepts an optional `viewport` (trimming large inline
  FeatureCollections before setData, retaining the raw data for re-filtering);
  `refreshGeoJsonSourcesByViewport` re-filters every registered inline source
  on the map's debounced move (map-panel 100ms viewport write), with a
  per-source + exact-viewport result cache so a stable viewport never re-runs
  setData (F31 fast path intact) and small sources pass through unchanged;
  `MapSpecRuntime.applySource` passes the current viewport at apply time.
  100k-feature layers parse only the visible subset per pan/zoom.
- **MapSpecRuntime appliedSpec timing fix** (`mapspec-runtime/runtime.ts`):
  `reconcileAsync` was updating `appliedSpec` at ENQUEUE time while ops run
  next frame — a rapid second spec could coalesce `source:apply:S` in the
  RenderDebouncer and drop the first patch's layer add while `appliedSpec`
  already claimed the map reflected the second spec (silent update loss, and
  the diff basis permanently diverged from the map). Now requests are
  serialized (promise chain) and `appliedSpec` is updated only when the
  patch's last op (unique-id z-order marker) actually executes; dispose
  releases in-flight applies. TDD: 3 tests fail on old code, 4 added.
- **Bugfix**: `kde_contours` leaked the final loop `i` into every feature's
  `level` property — now carries per-polygon level index.

### Performance & Correctness — Raster resource guard

- **Raster output resource guard** (`raster_math.py`): `resample_raster()`
  validates the *output* grid before warping — `target_resolution <= 0` is
  rejected, and a computed output grid exceeding `MAX_OUTPUT_PIXELS` (250 M),
  `MAX_OUTPUT_DIMENSION` (100k/side), or a 10,000× upscale ratio raises a
  `ValueError` with estimated pixels/size and *suggested coarser
  target_resolution values* (agent-actionable correction hint). A
  unit-confusion request (3°×3° EPSG:4326 → EPSG:3857 @ 1 m) previously
  produced a 334,035×334,035 px / ~111.6 G-pixel / ~415.7 GiB warp — minutes
  of CPU + 4.3 GB output; it now fails in **~10 ms** (ADR-0042).
- **Fixed pathological raster test**: `test_raster_resample_with_crs_change`
  had been performing that 111.6 G-pixel warp on every run (multi-minute
  accidental benchmark; the 300→600s timeout bump masked it). It now uses
  `target_resolution=10000` (→ 34×34 px) and asserts the transform math
  (`new_shape`, `target_crs`); heavy/timeout markers removed.
- **Regression tests** (`tests/unit/test_raster_resource_guard.py`): rejection
  of pixel-explosion warps (CRS-change and in-CRS), zero/negative resolution,
  no partial output file on rejection, suggestion math, and a sane-warp
  success path. Raster test suite: minutes → ~2 s.
- **Cleanup**: removed 45 stale pathological warps (45 × 4.3 GB ≈ 176 GB) from
  `data/` (gitignored test residue).

### Performance — Async-tool offload (closes remaining event-loop blockers)

- **`webgis_runtime_validate`**: the headless Chromium/Playwright subprocess
  (up to `RUNTIME_TIMEOUT_S` = 90s) now runs via `asyncio.to_thread` instead
  of a sync `subprocess.run` inside the async tool — a slow browser launch no
  longer freezes every concurrent SSE stream / WebSocket / request
  (`runtime_validator.py`).
- **`webgis_source_profile` / `webgis_layer_upsert`**: per-feature GeoJSON
  profiling now runs in a thread (`mapspec_store.source_profile`) — large
  inline FeatureCollections no longer block the loop during profiling.
- **`compute_ndvi` / `fetch_sentinel` / `fetch_dem` / `compute_terrain`**:
  band-algebra and Horn-window terrain derivatives (slope/aspect/hillshade)
  now run in a thread (`spectral_engine.py`) — multi-million-pixel numpy math
  off the loop.
- **Behavioral regression tests** (`tests/unit/test_execution_offload.py`):
  4 loop-responsiveness tests fake the slow work with a sync sleep and assert
  the event loop stays responsive while the tool runs — verified to fail on
  the pre-fix code and pass post-fix.
- **ADR-0043**: documents the actual tool execution policy (sync → `to_thread`
  at the registry seam; async tools must be await-only; Celery reserved for
  NDVI/change-detection/heatmap), superseding the stale ADR-0003.

### Performance & Correctness — Tool metrics pipeline (ADR-0044)

- **Queued metrics writer** (`tool_metrics.py`): `record_tool_call` no longer
  does sync `open/write/close` per tool call on the event loop — rows go to a
  bounded queue (8192; full → drop row, never block) drained by a daemon
  writer in batches (512 rows / 0.1 s idle flush, single `open` per batch,
  `atexit` flush). Caller-thread cost ~24 µs → ~23 µs; the I/O syscalls are
  gone from the loop entirely (robust under disk contention/rotation).
- **Real log rotation**: the 10 MB × 5-backup rotation promised in the
  docstring is now implemented (size check + `.1→.5` shift before each
  append) — the file no longer grows unbounded.
- **True percentiles**: a bounded log2 histogram (33 bins/tool) now feeds real
  `p50/p95/p99` estimates into `aggregator_snapshot()` and the digest; the old
  `top_p99` digest field (actually max latency) is honestly relabeled and
  reports `max` separately.
- **Tests**: async-write contract via row polling; new coverage for rotation
  bounds, percentile estimation, and queue-full backpressure
  (`tests/test_tool_metrics.py`).

### Performance — Batched reference/alias resolution (goal §8)

- **One Redis round-trip per dispatch instead of one per string argument**:
  `ToolRegistry._resolve_references` previously awaited `resolve_alias`
  (HGET) for every string arg — a 9-string tool call paid 9 serialized RTTs.
  New `resolve_aliases()` (protocol + memory + Redis stores) resolves the
  whole arg tree with a single `HMGET`; `ref:`/alias/plain-string semantics,
  skip keys, and the missing-ref self-healing error are unchanged.
- **Benchmark** (fakeredis, 9 strings × 2000 dispatches): 1100 ms → 206 ms
  (**5.3×**; 9 → 1 RTT per dispatch; savings scale with real network RTT).
- **Regression tests**: `tests/unit/test_ref_resolution_batching.py`
  (single-call spy, alias/ref/plain semantics, nested args, no-session fast
  path) — red on pre-fix code.

### Performance — Tool cache singleflight (goal §7, ADR-0045)

- **Concurrent identical tool calls compute once**: `cached_tool` now takes a
  Redis `SET NX` lock (random token + TTL, default 120 s, opt-out via
  `singleflight=False`) on cache miss. The lock winner computes and publishes
  the value before releasing (Lua compare-and-delete); followers poll the
  value with backoff and take over if the lock disappears without a value
  (failed winner). Stale locks expire via TTL; Redis failures degrade to
  direct compute — **no distributed deadlock, no permanent lock**.
- **Benchmark**: 5 concurrent identical 0.5 s calls → 5 computes → **1
  compute**, all callers get the same result. Regression tests:
  `tests/unit/test_tool_cache_singleflight.py` (async + sync suppression,
  failed-winner takeover, stale-lock fallback, Redis-down fallback, opt-out,
  warm-cache fast path).
- **Test fix**: 4 caching tests (`test_buffer_caching`, `test_h3_binning_caching`,
  `test_kde_contours_caching`, `test_heatmap_caching`) read the metrics log
  synchronously — now poll for the queued writer's rows (ADR-0044 async-write
  contract).

### Performance — Regression harness (goal §10, ADR-0046)

- **Deterministic perf gate** (`tests/benchmarks/test_perf_harness.py`,
  `-m perf`, ~1.5 s, no network/LLM): fixed workloads for this session's hot
  paths — raster guard rejection (~4.8 ms median, was 2–5 min warp),
  10-string batched ref resolution (~0.37 ms, was 10 serial RTTs), metrics
  enqueue caller cost (~17 µs), dispatch overhead (~0.31 ms).
- **Three-level gate**: median ≤ baseline × 1.75 passes (warning beyond),
  > baseline × 4.0 fails as a **hard regression**; baselines committed to
  `tests/benchmarks/baselines.json`, refreshed with
  `PERF_UPDATE_BASELINES=1` after a measured improvement. Median-of-7 +
  absolute floors keep CI noise from flaking.

### Performance — Windowed raster processing (goal §5, Phase D)

- **`reclassify` is now windowed**: fixed 512×512 window grid (immune to
  single-block sources) so memory is O(window) instead of O(full raster) with
  several full-size temporaries. 4096×4096 float32 source: peak RSS
  403,544 KB → 160,320 KB (**−60%**; raster-data portion ~215 MB →
  window-sized), pixel-identical output + stats vs the full-array reference.
- **Characterization tests** (`tests/unit/test_raster_reclassify_windowed.py`):
  pixel-exact equality vs inlined reference, first-match-wins, nodata/NaN
  isolation, stats quirks, single-block sources, lzw/tiled/dtype preserved.
- **Fix (metrics writer)**: the writer thread is now crash-proof — a failing
  batch (`_flush_batch` try/except + finally) can't kill it or wedge the
  pending-rows counter (surfaced by the disk-failure test under full-suite
  load); `_reset_for_tests` waits for writer quiescence so an in-flight batch
  can't leak into the next test's log file. Combined metrics+caching suites:
  19 passed in 4 s (was 8 failed / 198 s).
- **`raster_calculator` is now windowed** (aligned/constant paths): two
  4096×4096 aligned float32 sources — peak RSS 518,580 KB → 220,048 KB
  (**−58%**; ~6-8 full-size temporaries → window-sized), pixel-identical
  output + stats. The unaligned B-reproject path stays full-array (docs
  advise resampling first). Characterization tests in
  `tests/unit/test_raster_calculator_windowed.py`.
- **Sync-tool thread concurrency bound**: registry `to_thread` offload now
  runs under an `asyncio.Semaphore` (`max(4, min(16, cpu+4))`) — parallel
  tool waves can't oversubscribe the GIL-bound pool (goal §3 concurrency
  bound). Regression test: 4 concurrent sync tools peak at ≤ limit.
- **Fix (OSM limit contract)**: `query_osm_poi/roads/buildings` advertised
  `limit` (1–500) but the Overpass query never applied it — city-wide
  queries returned 10k–100k features (up to ~26 MB GeoJSON, the Data Plane
  pain point's root cause). `_query_overpass` now appends
  `out body geom <limit>;` and tools defensively slice; boundary queries
  unchanged. Regression tests: 1000-element mock → 50 returned, query
  contains the limit clause.

### Performance — Data Plane: MVT vector tiles for large POI display (goal Phase G, ADR-0047)

- **Backend**: stdlib-only MVT 2.1 encoder (`app/services/mvt.py`) + tile
  endpoint `GET /api/v1/layers/data/{ref}/tiles/{z}/{x}/{y}.mvt` (same auth
  as `/layers/data`, gzip, `private max-age=300`). 100k POI city viewport:
  GeoJSON 24,788 KiB raw / 2,580 KiB gzip → **4 MVT tiles = 22 KiB gzip**
  (~1,100× vs raw, 117× vs gzip, ~280 ms encode).
- **Frontend**: `VectorMapSpecSource` + adapter threshold (5000 features) +
  runtime/renderer vector-source support; ref layers mint `_tileUrl` and
  large FeatureCollections render from MVT tiles instead of a whole-file
  `setData`. GeoJSON path unchanged for small results / LLM context.
- **Tests**: independent protobuf-decoder round-trips (geometry, properties,
  projection, tile filtering, empty tiles), endpoint auth/404/400/nested
  shapes, frontend adapter threshold x3 + runtime vector apply/source-layer/
  geojson->vector upgrade. tsc clean, vitest 466 passed, eslint 0.

### Performance - Artifact Cache (goal §6, ADR-0048) + §5 windowing decisions

- **Content-addressed artifact cache** (`app/lib/artifact_cache.py`):
  `resample_raster` (the most expensive file-producing op) now caches its
  output under `data/artifacts/<key>.tif` keyed by
  `sha256(source identity, source mtime+size, operation, params, software
  version namespace)`. A repeat call with identical inputs returns in ~ms
  (file stat + meta read) instead of minutes - no recompute. Atomic publish
  (temp file + `os.replace`), LRU eviction (default 5 GiB cap), automatic
  invalidation on source mtime/size change, manual `ARTIFACT_VERSION_NS`
  bump for algorithm/rasterio version changes. Sits below the existing
  singleflight (ADR-0045): a miss here still singleflights the compute.
- **§5 windowing decisions** (ADR-0048 Part 1): the four remaining raster
  paths (`resample`, `zonal_stats`, NDVI/spectral, `change_detection`) were
  audited - **all are already block-streamed by GDAL/rasterstats** (or run
  on already-materialized band arrays), so no Python-side windowing is
  needed. Documented rather than refactored.
- **Tests** (`tests/unit/test_artifact_cache.py`): key determinism,
  source-change invalidation, hit-skips-recompute, atomic publish, stale
  miss, LRU eviction, resample_raster integration (identical output on
  cache hit, different params -> recompute).

### Performance - Harness expansion (goal §10)

- **3 new workloads** added to `tests/benchmarks/test_perf_harness.py`
  (now 7 total): `reclassify_windowed` (1024² multi-block raster, ~106 ms),
  `h3_binning_10k` (10k synthetic points, ~21 ms), `artifact_cache_hit`
  (~0.06 ms). Covers the spec's Vector + Raster-compute + Agent-runtime
  axes (Frontend workloads remain a follow-up - they need a browser harness).
  Baselines refreshed; 7/7 stable across 3 runs.

## [0.1.3] - 2026-08-03

### Performance & Remediation

- **JS Bundle Optimization**: Reduced First Load JS from 1.15 MB to 301 KB (-73.8%) via Next.js `optimizePackageImports` (`lucide-react`, `recharts`, `framer-motion`) and dynamic component code-splitting (`ssr: false` for secondary drawers & MapPanel).
- **Backend Lock Unbinding**: Unbound long-running async I/O (WeasyPrint PDF rendering, SVG compilation) from synchronous DB transactions in `report_service.py` to prevent connection pool exhaustion under load.
- **Deep Modules & Architecture Consolidation**: Consolidated `SpatialAnalysisEngine`, `EmbodiedHudEngine`, and `SpatialReportEngine` for atomic state management and clean separation of concerns.

## [Unreleased] - 2026-07

### Security

- **Comprehensive security audit & hardening** (PRs #129–#135): 100+ findings
  addressed across backend, frontend, and infrastructure.
  - **SEC-01**: Pi agent bridge now requires HMAC shared secret
    (`X-Pi-Bridge-Secret` header); tier ≥3 tools rejected at bridge boundary.
  - **SEC-02**: Dynamic skill creation (`create_new_skill`) gated behind
    `ALLOW_DYNAMIC_SKILLS` env var — prevents arbitrary code execution via
    agent-authored skills.
  - **SEC-03**: WebSocket IDOR closed — WS now requires valid access token +
    session ownership check (was anonymous, dead code in frontend).
  - **SEC-05/06**: `require_admin` uses versioned user lookup; `org_id`
    included in JWT claims for cross-tenant scoping.
  - **SEC-07**: SSRF validator resolves hostnames via `socket.getaddrinfo`
    and rejects private/loopback/link-local IPs (closes static malicious DNS).
  - **SEC-08**: Anonymous session ownership via `owner_token` column —
    `get_or_create_conversation` mints `secrets.token_urlsafe(32)` for
    anonymous sessions; `get_session` requires token match.
  - **SEC-11**: `/ready` endpoint returns 503 when dependencies unhealthy
    (was 200 with `ready: false` — k8s readinessProbe treated as ready).
  - **DEPS-01**: Migrated JWT library from `python-jose` (unfixed
    CVE-2024-33664/33663) to `PyJWT` with mandatory `algorithms` allowlist.
- **Frontend hardening** (PR #149):
  - `map-action-renderer` validates per-command params schema before dispatch
    (rejects malformed AI output).
  - `history-drawer` implements full dialog ARIA pattern (focus management,
    Escape-to-close, `role=dialog`/`aria-modal`).
  - `chat-panel` replaces `as any[]` chart cast with `adaptChartData()`
    runtime validation.
  - `mini-md` anchor `href` explicitly applies `safeUrlTransform`
    (defense-in-depth).

### Infrastructure

- **CI pipeline unblocked**: Was broken for 100+ consecutive runs due to
  workflow syntax errors. Now fully green with real test gating (no `|| true`).
- **K8s hardening**: Removed `namePrefix`/`nameSuffix` (broke HPA selectors);
  added PDB + HPA; `readOnlyRootFilesystem` SecurityContext.
- **Docker**: GDAL/GEOS/PROJ runtime libraries in multi-stage build;
  Dependabot configured for pip/npm/docker/github-actions with minor/patch
  batching (CICD-05).
- **Production deployment**: Image push to registry; rollback pulls from
  registry instead of rebuilding (CICD-03); preview env uses correct
  `.env.Priv` (CICD-04).

### Tests

- **1223 backend tests** (was ~105 files / fragmented). 15 new cross-tenant
  isolation tests (TEST-03). 6 WebSocket auth tests. 19 source-text
  inspection tests converted to behavioral tests (TEST-04).
- **272 frontend tests** (was ~240). Added map-action params validation
  tests, dialog a11y coverage.

### Dependencies

- `PyJWT>=2.8.0,<3.0.0` (replaces python-jose)
- `scikit-learn>=1.4.0`, `numpy<2.0.0` (API compat pin)
- `starlette>=0.40.0`, `fastapi>=0.115.0` (prometheus instrumentator compat)
- `alembic` added to requirements (was transitively available only)
- `prometheus-fastapi-instrumentator>=7.0.0` (FastAPI 0.115 `_IncludedRouter` fix)

## [0.1.2] - 2026-05-31

### Added

- **Security & Sanitization**: Added `app/utils/security.py` for masking database passwords, key-value secrets, and OpenAI keys in tool execution logs and SSE payloads.
- **WebSocket optional auth**: WebSocket connections support optional JWT token validation; anonymous connections allowed for compatibility until frontend implements login flow.
- **Robust test suite**: Added unit tests for WebSocket auth validation, error sanitization, viewport naming task tracking, and context builder component integration.
- **`display_layer` AI tool**: lets the agent explicitly show a hidden data
  layer on the map with a meaningful name. All GeoJSON tool results are now
  loaded as hidden layers by default (layer ID = `ref_id`); the agent must
  call `display_layer(ref_id, name)` to surface the final result layer.
  Intermediate layers (boundary queries, raw POI searches, buffer helpers)
  remain hidden, keeping the map clean.
- **`LAYER_VISIBILITY_UPDATE` command extended**: now accepts optional `name`
  (renames the layer in the panel) and `color` (overrides the fill/stroke
  color) params alongside the existing `visible` and `opacity`.

### Fixed

- **Modular context builder refactor**: Split `context_builder.py` into decoupled sub-modules: `geometry.py`, `layer_schema.py`, `session_overview.py`, `history_compression.py`, and `formatters.py`.
- **Bounding Box walker DRY consolidation**: Consolidated coordinate walkers into `app/utils/geojson.py::geojson_bbox` and refactored `map_view.py` to use it.
- **Flaky Viewport Naming Tests Fix**: Replaced fragile `asyncio.sleep` calls with deterministic background task tracking (`_active_tasks`) and a `wait_all_tasks()` wait utility.
- **Vertex circles on polygon/line vector layers** removed. Overpass API was
  returning untagged topology nodes (polygon boundary vertices with no
  attributes) as Point features; these are now skipped at parse time
  (`_overpass_to_geojson` requires `el.get("tags")` for node elements).
  Frontend cleanup: stale `*-point` MapLibre sublayers are explicitly hidden
  when a layer has no point features, and the circle sublayer carries an
  explicit `['==', '$type', 'Point']` filter.
- **Think content now collapsed** in the UI. The `is_reasoning` flag was
  being stripped from the `token` SSE event before reaching the frontend;
  it is now forwarded so reasoning tokens route to `CollapsibleThink` instead
  of the main message body.

### Changed

- Default UI theme is now **light** (was dark).
- Agent `max_rounds` raised from 30 to 60, reducing "达到最大轮数" aborts
  on complex multi-step analyses.

### Performance

- Tool-layer result cache (`@cached_tool`) opt-in via decorator, Redis-keyed,
  with graceful fallback when Redis is unreachable.
- Automatic per-dispatch timing in `ToolRegistry.dispatch` — every tool call
  writes one JSONL row to `logs/tool_metrics.jsonl` and contributes to an
  in-process aggregator that emits a `TOOL_METRICS_DIGEST` line every 100
  calls and at FastAPI shutdown.
- `trim_features` helper for payload reduction (caps FeatureCollection at
  5000 features, rounds coordinates to 6 decimals).
- `buffer_analysis`, `heatmap_data`, `h3_binning`, `kde_contours` opted in.

## [Unreleased] — GIS Extension Platform V3 (feat/extensions-v3-secure-ecosystem, ADR-0120)

### Added
- 扩展包非对称签名（Ed25519，`cryptography`）+ 文件型 trust store（rotation/retired/revocation）；HMAC v1 载荷逐字节保留
- 服务端 marketplace registry（content-addressed blob、文件锁串行 publish、claim-once 防依赖混淆、确定性分页搜索、deprecate/revoke/yank）+ 只读 HTTP API
- 分发安装器：统一 preflight（digest/验签/吊销/pin/降级/依赖冲突）、tar 白名单安全解包、原子换装（中断可恢复）、versions/ 归档与回滚
- worker 隔离后端 `bubblewrap`（netns + 最小 bind 面 + tmpfs；per-spawn 失败 typed 拒绝不静默回退）
- 流式协议 V3（信用流控/协作取消/逐帧 idle timeout/事件上界/宿主侧帧预算强制）
- worker 化投影：algorithms（描述符）、data providers（7 方法 RPC 代理 + mixin 动态继承 + 串行排队 + DataFabricError 映射）、cartography/recipes（声明式 payload）
- Data Fabric bridge：`stream_catalog_item_features` 能力感知流式分发（additive）
- Lifecycle：drain、版本 pin、吊销惰性传播 + `.refresh` 通知信号
- Certification V3 检查（signature_trust/package_layout/budget/protocol/provider conformance）+ 恶意语料 20 场景
- 示例扩展 extdemo-v3-pack + 完成证明端到端测试（签名→发布→安装→隔离→broker→流式→升级→回滚→吊销不可回退）
