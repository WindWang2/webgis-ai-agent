# Changelog

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
