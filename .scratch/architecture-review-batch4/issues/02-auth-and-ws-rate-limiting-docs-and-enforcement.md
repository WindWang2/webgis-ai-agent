# 02 — Document and enforce rate limiting strategy across Auth and WebSocket paths

**What to build:**
Provide comprehensive documentation in `docs/rate-limiting.md` for API rate limiting policies. Ensure auth routes (`/api/v1/auth/login`, `/api/v1/auth/register`) and WebSocket connection/viewport change handlers apply active rate limits to prevent brute-force attacks and outbound provider DoS.

**Blocked by:** 01 — Delete vaporware RAGAdapter and consolidate OSM HTTP fetching.

**Status:** closed

- [x] Create `docs/rate-limiting.md` documenting rate limit thresholds, sliding window/token-bucket policies, and error responses.
- [x] Verify rate limiter decorators on auth endpoints (`/login`, `/register`) in `app/api/routes/auth.py`.
- [x] Apply rate limiting / token bucket protection on `schedule_populate` calls triggered by WebSocket events in `app/services/ws_service.py`.
- [x] Add unit/integration tests verifying rate limit responses (`HTTP 429 Too Many Requests`).
