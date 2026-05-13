# TODOS — Base Framework Review

Generated from /plan-eng-review on 2026-05-09.

## Architecture (D2–D7)
- [ ] D2 — Enable Redis by default for session storage, with graceful fallback to in-memory
- [ ] D3 — Require JWT_SECRET_KEY in production, fail startup if missing
- [ ] D4 — Replace in-memory rate limiter with Redis-backed implementation
- [ ] D5 — Move tool imports to lazy init function called from lifespan startup
- [x] D6 — Move schema init (init_db) to standalone CLI command (Completed: `manage.py init-db`)
- [x] D7 — Add Redis and Celery checks to /ready health endpoint (Completed: `app/api/routes/health.py:80` — /ready returns database/llm/redis/celery status)

## Code Quality (D8–D12)
- [ ] D8 — Migrate all API routes to Depends(get_db), services to db_session() context manager
- [ ] D9 — Audit and narrow exception types in tool functions
- [ ] D10 — Use single app.log file with shared handler
- [ ] D11 — Standardize error response format to match global exception handler
- [x] D12 — Add validate_data_path() helper for file-based tools (Completed: v3.2 — `app/utils/path.py`; threat model documented inline, see docstring re: symlinks)

## Tests (D13)
- [ ] D13 — Add comprehensive framework tests:
  - test_auth.py (JWT create/verify, get_current_user, optional auth)
  - test_exception.py (sanitize_traceback, format_error_response, global handler)
  - test_network.py (SSL context, shared client, connection pool)
  - test_logging_config.py (handler setup, env-based config)
  - test_utils.py (parse_bbox, db_session, asset_href)
  - test_main.py (lifespan, middleware, router registration)

## Performance (D14–D16)
- [ ] D14 — Add SQLAlchemy async support (asyncpg/aiosqlite)
- [ ] D15 — Use deque(maxlen) in rate limiting middleware
- [ ] D16 — Skip traceback formatting in production exception handler

## Frontend Bridge Tests (D17)
- [x] D17 — Write missing tests for Agent-Map Bridge (identified in eng review 2026-05-13):
  - `lib/utils/geo.test.ts` (NEW) — `bboxToFlyTo`: all 4 zoom thresholds, center calc, invalid bbox guard
  - `lib/api/chat.test.ts` (EXTEND) — `streamChat` SSE: parse loop, JSON failure yields raw string, AbortSignal abort
  - `lib/hooks/useMapBridge.test.ts` (NEW) — send/abort/onEvent routing/all guards/aiStatus transitions (10 cases)
  - `components/map/map-action-handler.test.tsx` (NEW) — `fly_to` bearing+pitch forwarded to `map.flyTo()`
  - `test/test-utils.tsx` (UPDATE) — add `mapLoaded: false` + `setMapLoaded: vi.fn()`, remove `analysisResult`/`setAnalysisResult`
  - Test plan: `~/.gstack/projects/WindWang2-webgis-ai-agent/kevin-master-eng-review-test-plan-20260513-1448.md`

## Frontend Bridge DX (D18)
- [x] D18 — Apply DX review decisions for Agent-Map Bridge (identified in devex review 2026-05-13):
  - `types/agent-events.ts` (UPDATE) — add `export type { SSEEvent, SSEEventType } from '@/lib/api/chat'` [DX3]
  - `page.tsx` (UPDATE) — remove `isLoading` useState; derive `const isLoading = bridge.aiStatus === 'thinking' || bridge.aiStatus === 'acting'` [DX4]
  - `lib/hooks/useMapBridge.ts` (NEW) — add JSDoc + dev-mode console.error guard for onEvent [DX5]
  - `page.tsx` (UPDATE) — remove all explicit `abort()` call-sites; AbortController is internal to hook [DX1]
  - DX plan: `~/.gstack/projects/WindWang2-webgis-ai-agent/ceo-plans/2026-05-13-agent-map-bridge.md` (## GSTACK REVIEW REPORT → DX section)

## NOT in scope
- Individual tool implementations (remote_sensing.py, terrain_analysis.py, etc.)
- Frontend components and tests
- Deployment/infrastructure changes beyond health checks
- LLM prompt engineering or model switching
- New feature development (new tools, new API endpoints)
