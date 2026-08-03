# Changelog

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
