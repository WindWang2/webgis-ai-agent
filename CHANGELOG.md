# Changelog

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
