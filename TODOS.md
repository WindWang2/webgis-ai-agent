# TODOS — Base Framework Review

Generated from /plan-eng-review on 2026-05-09.

## Architecture (D2–D7)
- [ ] D2 — Enable Redis by default for session storage, with graceful fallback to in-memory
- [ ] D3 — Require JWT_SECRET_KEY in production, fail startup if missing
- [ ] D4 — Replace in-memory rate limiter with Redis-backed implementation
- [ ] D5 — Move tool imports to lazy init function called from lifespan startup
- [ ] D6 — Move schema init (init_db) to standalone CLI command
- [ ] D7 — Add Redis and Celery checks to /ready health endpoint

## Code Quality (D8–D12)
- [ ] D8 — Migrate all API routes to Depends(get_db), services to db_session() context manager
- [ ] D9 — Audit and narrow exception types in tool functions
- [ ] D10 — Use single app.log file with shared handler
- [ ] D11 — Standardize error response format to match global exception handler
- [ ] D12 — Add validate_data_path() helper for file-based tools

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

## NOT in scope
- Individual tool implementations (remote_sensing.py, terrain_analysis.py, etc.)
- Frontend components and tests
- Deployment/infrastructure changes beyond health checks
- LLM prompt engineering or model switching
- New feature development (new tools, new API endpoints)
