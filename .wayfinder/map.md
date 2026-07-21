# Wayfinding Map: Project Hardening to Production Readiness

## Destination

All P0-P2 issues in webgis-ai-agent identified and fixed. The project reaches production hardening: no security vulnerabilities, no infrastructure misconfigurations, no code quality issues that would cause runtime failures, and documentation that matches reality.

Additionally, the spatial analysis package has been systematically reviewed and optimized through a grilling session, addressing performance anti-patterns (O(n²) algorithms, per-cell CRS transforms, code duplication) and consistency issues across 7 core files.

## Notes

Domain: webgis-ai-agent (FastAPI + Celery + Next.js + PostGIS)
Skills to consult: brainstorming, systematic-debugging, test-driven-development, verification-before-completion
Standing preferences: fail-fast on missing env vars, async-first patterns, timezone-aware datetimes, structured logging over print(), explicit over implicit security defaults

## Decisions so far

- [Replace sync requests with async httpx](ticket-001-sync-requests-async.md) — Migrated `third_party_api_adapter.py` from `requests.get()` to `httpx.Client` context manager with `raise_for_status()` for proper async error handling
- [Replace asyncio.run() with persistent event loop in Celery](ticket-002-asyncio-run-celery.md) — Introduced `_get_celery_loop()` / `_run_async()` helpers that reuse a single event loop per worker process, replacing 7 `asyncio.run()` calls
- [Add startup validation for required env vars](ticket-003-env-var-validation.md) — Added `_validate_required_env_vars` model validator that raises `RuntimeError` if `LLM_API_KEY` is placeholder/missing in production; warns in development
- [Harden docker-compose security defaults](ticket-004-docker-compose-security.md) — Redis healthcheck uses `REDISCLI_AUTH` env var instead of `-a password`; `WEBGIS_DEV_MOUNT` defaults to empty (explicit opt-in); celery-worker gains healthcheck
- [Pin K8s image tags + add dev celery healthcheck](ticket-005-k8s-image-tags.md) — Pinned K8s image to `v0.1.2` with `imagePullPolicy: Always`; added celery-worker healthcheck to dev compose
- [Replace naive datetime.now() with timezone-aware UTC](ticket-006-datetime-timezone.md) — 8 files updated; production paths use `datetime.now(timezone.utc)`; display helper handles both naive and aware inputs
- [Fix SSRF validation to cover all URLs including defaults](ticket-007-ssrf-validation.md) — Removed `_DEFAULTS` bypass; all URLs (including defaults) validated against SSRF patterns
- [Clarify WebSocket auth model and add rate limiting](ticket-008-websocket-auth.md) — Added per-session rate limit (5 connections per 60s) to prevent anonymous connection abuse
- [Fix login rate limiter to prevent IP-based user lockout](ticket-009-login-rate-limiter.md) — Changed rate limit key from `auth_login:{ip}:{identifier}` to `auth_login:{ip}` to prevent NAT attacker from locking out users
- [Use random dummy hash for password verification timing safety](ticket-010-password-timing.md) — Module-level `_DUMMY_STORED` generated at import time using full scrypt parameters (N=2^14); eliminates timing side-channel
- [Replace bare except Exception clauses with specific exceptions](ticket-011-bare-except.md) — 8 files fixed; bare `except Exception:` → `except Exception as e:` (11 instances)
- [Replace print() with structured logger calls](ticket-012-print-to-logger.md) — OSS adapter: `print()` → `logger.warning()`; added logging import
- [Guard console statements by NODE_ENV in frontend](ticket-013-console-guards.md) — Created `frontend/lib/utils/logger.ts` with `devOnly`/`safeError` utilities; 14 files updated to suppress console in production
- [Fix CORS defaults and add explicit configuration](ticket-014-cors-config.md) — Default changed from `["*"]` to `["http://localhost:3000"]`; explicit `allow_methods` and `allow_headers` lists in `main.py`
- [Add composite DB indexes for common query patterns](ticket-016-composite-indexes.md) — Added `idx_layer_org_status`, `idx_layer_org_category_status`, `idx_task_org_status`, `idx_task_org_type_status` with Alembic migration
- [Update documentation for current requirements](ticket-017-documentation.md) — README updated with explicit env var requirements list
- [Grilling: spatial analysis package optimizations](grilling-spatial-analysis.md) — 6 issues fixed across 7 files: `_build_weights` → sparse COO, `calculate_central_feature` batched cKDTree + n>5000 guard, `kde_surface` batch CRS, `calculate_isochrones` MultiGraph + cKDTree, IDW full bounding box + deduplicated `h3_to_geojson`, H3 resolution meter-based thresholds, `zonal_stats` unified to `.to_llm_response()`, `_call_llm` docstring clarified

## Not yet specified

- P3 low-priority items (type annotation consistency, TODOs, import style, error boundaries) — out of scope for this hardening pass
- Test coverage improvement beyond existing 1,104 backend + 267 frontend tests — separate effort
- Performance profiling beyond identified anti-patterns — separate effort
- Real LLM integration for `spatial_reasoning._call_llm` — planned, mock in place

## Out of scope

- Feature additions or new functionality
- Frontend UI redesign
- Database schema changes beyond adding indexes
