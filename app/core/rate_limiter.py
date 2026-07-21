"""Rate limiter with Redis backend and in-memory fallback."""
import logging
import time
from collections import defaultdict, deque
from typing import Protocol

from app.core.config import settings

logger = logging.getLogger(__name__)


class RateLimiter(Protocol):
    async def is_allowed(self, key: str, max_requests: int, window_seconds: int) -> bool:
        ...


class RedisRateLimiter:
    """Sliding-window rate limiter backed by Redis sorted sets."""

    def __init__(self, redis_client):
        self._redis = redis_client

    async def is_allowed(self, key: str, max_requests: int, window_seconds: int) -> bool:
        now = time.time()
        window_start = now - window_seconds
        pipe = self._redis.pipeline()
        pipe.zremrangebyscore(key, 0, window_start)
        pipe.zadd(key, {str(now): now})
        pipe.zcard(key)
        pipe.expire(key, window_seconds + 1)
        _, _, count, _ = await pipe.execute()
        return count <= max_requests


class MemoryRateLimiter:
    """Sliding-window rate limiter backed by an in-memory deque with TTL eviction."""

    _EVICT_INTERVAL = 300  # purge empty keys every 5 minutes
    _MAX_KEYS = 10000       # hard cap to prevent memory growth

    def __init__(self):
        self._requests: dict[str, deque[float]] = defaultdict(deque)
        self._last_evict: float = 0.0

    async def is_allowed(self, key: str, max_requests: int, window_seconds: int) -> bool:
        self._maybe_evict()
        now = time.time()
        timestamps = self._requests[key]
        while timestamps and now - timestamps[0] >= window_seconds:
            timestamps.popleft()
        if len(timestamps) >= max_requests:
            return False
        timestamps.append(now)
        return True

    def _maybe_evict(self) -> None:
        """Periodically remove empty keys and enforce a hard key cap."""
        now = time.time()
        if now - self._last_evict < self._EVICT_INTERVAL and len(self._requests) < self._MAX_KEYS:
            return
        self._last_evict = now
        empty = [k for k, v in self._requests.items() if not v]
        for k in empty:
            del self._requests[k]
        # If still over cap, drop oldest keys entirely
        if len(self._requests) > self._MAX_KEYS:
            excess = len(self._requests) - self._MAX_KEYS
            oldest = sorted(self._requests.keys(), key=lambda k: self._requests[k][0] if self._requests[k] else 0)
            for k in oldest[:excess]:
                del self._requests[k]


_rate_limiter: RateLimiter | None = None


async def get_rate_limiter() -> RateLimiter:
    """Return a shared rate limiter instance, preferring Redis when available."""
    global _rate_limiter
    if _rate_limiter is not None:
        return _rate_limiter

    try:
        import redis.asyncio as aioredis

        client = aioredis.from_url(
            settings.REDIS_URL,
            socket_connect_timeout=2,
            decode_responses=True,
        )
        await client.ping()
        _rate_limiter = RedisRateLimiter(client)
        logger.info("[RateLimiter] Using Redis backend")
    except Exception as exc:
        logger.warning(f"[RateLimiter] Redis unavailable ({exc}), falling back to in-memory")
        _rate_limiter = MemoryRateLimiter()

    return _rate_limiter
