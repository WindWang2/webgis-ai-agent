# Rate Limiting Architecture & Policy Specification

**Status:** Active
**Module:** `app/core/rate_limiter.py`

This document specifies the rate-limiting design, thresholds, sliding window algorithms, Redis storage patterns, and HTTP / WebSocket response behaviors for the WebGIS AI Agent platform.

---

## 1. Overview & Architecture

Rate limiting is enforced at critical system entry points to prevent:
1. **Auth Brute-Force Attacks**: Protecting authentication routes (`/api/v1/auth/login`, `/api/v1/auth/register`, `/api/v1/auth/refresh`) against credential stuffing.
2. **WebSocket & Outbound Provider DoS**: Throttling unauthenticated or bursty WebSocket connections and background geocoding calls (e.g. Nominatim `schedule_populate`).
3. **API Middleware Throttling**: Protecting global API routes from automated scraping or amplification attacks.

```
                  ┌───────────────────────────────┐
                  │     Incoming Request / WS     │
                  └──────────────┬────────────────┘
                                 │
                     check is_allowed(key, max, window)
                                 │
                                 ▼
                    ┌───────────────────────────┐
                    │    app.core.rate_limiter   │
                    └────────────┬──────────────┘
                                 │
                ┌────────────────┴────────────────┐
                ▼                                 ▼
      [Redis Available]                [Redis Unavailable]
    RedisRateLimiter (zadd/zcard)    MemoryRateLimiter (deque TTL)
```

---

## 2. Sliding Window Algorithm & Storage

Rate limiting uses a **Sliding Window** algorithm rather than fixed counters to prevent boundary burst exploits.

### Redis Backend (`RedisRateLimiter`)
- **Data Structure**: Redis Sorted Set (`zset`).
- **Key Pattern**: `<namespace>:<client_ip_or_session_id>` (e.g. `auth_login:192.168.1.10`, `ws_connect:10.0.0.1`, `ws_viewport_populate:sess_abc123`).
- **Pipeline Operations**:
  1. `zremrangebyscore(key, 0, now - window_seconds)` — Purge expired timestamps.
  2. `zadd(key, {str(now): now})` — Record current request timestamp.
  3. `zcard(key)` — Count active requests in current sliding window.
  4. `expire(key, window_seconds + 1)` — Set key TTL.
- **Rule**: If `count > max_requests`, request is denied (`is_allowed = False`).

### In-Memory Fallback (`MemoryRateLimiter`)
- **Data Structure**: `defaultdict(deque[float])`.
- **TTL Eviction**:
  - Drops timestamps older than `window_seconds`.
  - Automatic background purge every 5 minutes (`_EVICT_INTERVAL`) to prevent memory leaks.
  - Hard key cap (`_MAX_KEYS = 10000`) with LRU-style eviction under memory pressure.

---

## 3. Configured Rate Limit Thresholds

| Endpoint / Action | Namespace Key Pattern | Max Requests | Sliding Window | Scope |
| :--- | :--- | :---: | :---: | :--- |
| `POST /api/v1/auth/register` | `auth_register:{ip}` | 5 | 3600s (1 hour) | Per Client IP |
| `POST /api/v1/auth/login` | `auth_login:{ip}` | 10 | 60s (1 min) | Per Client IP |
| `POST /api/v1/auth/refresh` | `auth_refresh:{ip}` | 10 | 60s (1 min) | Per Client IP |
| `WS /ws/{session_id}` Handshake | `ws_connect:{ip}` | 5 | 60s (1 min) | Per Client IP |
| WS `viewport_change` Populate | `ws_viewport_populate:{session_id}` | 1 | 5s | Per Session ID |
| Global API Middleware | `api_global:{ip}` | Configurable | 60s | Per Client IP |

---

## 4. Response & Rejection Behavior

### HTTP Endpoints
When rate limit is exceeded, HTTP routes return `HTTP 429 Too Many Requests` with structured JSON body:
```json
{
  "code": 42900,
  "message": "请求频繁，请稍后再试",
  "data": null,
  "request_id": "req-123456789"
}
```

### WebSocket Endpoint
When WebSocket connection rate limit is exceeded, connection handshake is rejected before accept:
- **WS Close Code**: `4029`
- **WS Close Reason**: `"Rate limit exceeded"`

---

## 5. Testing & Verification

Rate limiter contracts are verified via automated tests:
- `tests/unit/test_rate_limiting.py`: Unit tests for `MemoryRateLimiter` sliding window, sliding eviction, and key capping.
- `tests/test_critical_auth_hardening.py`: HTTP 429 response enforcement on `/api/v1/auth/login` and `/api/v1/auth/register`.
- `tests/test_network.py`: `get_rate_limiter()` backend selection and event loop binding safety.
