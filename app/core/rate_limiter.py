"""Rate limiter with Redis backend and in-memory fallback."""
import asyncio
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
    """Sliding-window rate limiter backed by Redis sorted sets.

    审计 TEST-13：Redis 客户端在第一次 async 操作时把连接池绑定到当时的 event
    loop。本实例是模块级单例（见 ``get_rate_limiter``），而 pytest-asyncio /
    starlette TestClient 每个测试用新 loop。若客户端绑定的 loop 已关闭，下一次
    调用会报 "Event loop is closed"。因此每次调用前用 ``_ensure_client`` 检测
    loop 变化，必要时重建客户端。
    """

    def __init__(self, redis_client):
        self._redis = redis_client
        try:
            self._bound_loop = asyncio.get_running_loop()
        except RuntimeError:
            self._bound_loop = None

    async def _ensure_client(self):
        """Return a Redis client bound to the currently running loop.

        Recreate the client when the running loop differs from the one the cached
        client's connection pool is bound to (e.g. previous test's loop closed).
        """
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if self._bound_loop is loop and self._redis is not None:
            return self._redis
        # loop 变了（或首次）—— 重建客户端。旧客户端可能绑在已关闭的 loop 上，
        # aclose 会失败，忽略。
        if self._redis is not None:
            try:
                await self._redis.aclose()
            except Exception:
                pass
        import redis.asyncio as aioredis
        self._redis = aioredis.from_url(
            settings.REDIS_URL,
            socket_connect_timeout=2,
            # C-F10: a read timeout is mandatory on the hot-path client. Without
            # it, a half-open Redis connection (up but not responding) makes
            # pipe.execute() hang indefinitely; RateLimitMiddleware awaits this
            # on EVERY request (incl. SSE starts), so one stalled Redis op halts
            # the whole API. 1s keeps the worst case bounded and lets
            # is_allowed fail open fast.
            socket_timeout=1,
            decode_responses=True,
        )
        self._bound_loop = loop
        return self._redis

    async def is_allowed(self, key: str, max_requests: int, window_seconds: int) -> bool:
        client = await self._ensure_client()
        now = time.time()
        window_start = now - window_seconds
        pipe = client.pipeline()
        pipe.zremrangebyscore(key, 0, window_start)
        pipe.zadd(key, {str(now): now})
        pipe.zcard(key)
        pipe.expire(key, window_seconds + 1)
        try:
            _, _, count, _ = await pipe.execute()
        except Exception as exc:
            # C-F10: rate limiting is best-effort. A Redis blip must NEVER block
            # or fail every request (the middleware runs this per-request on the
            # hot path). Fail open: allow the request, log the degrade.
            logger.warning("[RateLimiter] Redis op failed (%s); failing open", exc)
            return True
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
# P2: 首次探测 Redis 失败会回退到内存版。原实现把该回退永久缓存 —— Redis 恢复后
# 整个进程生命周期内跨 pod 限流仍然失效（安全上 fail-open 无害，但语义漂移）。
# 现在记录回退时刻，下次调用时若超过 _FALLBACK_REPROBE_S 则重新探测一次；成功则
# 切换回 Redis 后端，失败则刷新回退时刻（避免每次调用都打 Redis）。
_FALLBACK_REPROBE_S = 60.0
_rate_limiter_fallback_at: float | None = None


async def get_rate_limiter() -> RateLimiter:
    """Return a shared rate limiter instance, preferring Redis when available."""
    global _rate_limiter, _rate_limiter_fallback_at
    if _rate_limiter is not None and not isinstance(_rate_limiter, MemoryRateLimiter):
        return _rate_limiter
    if (
        _rate_limiter is not None
        and _rate_limiter_fallback_at is not None
        and (time.monotonic() - _rate_limiter_fallback_at) < _FALLBACK_REPROBE_S
    ):
        return _rate_limiter

    try:
        import redis.asyncio as aioredis

        client = aioredis.from_url(
            settings.REDIS_URL,
            socket_connect_timeout=2,
            socket_timeout=1,  # C-F10: bound read latency on the hot-path client
            decode_responses=True,
        )
        await client.ping()
        _rate_limiter = RedisRateLimiter(client)
        _rate_limiter_fallback_at = None
        logger.info("[RateLimiter] Using Redis backend")
    except Exception as exc:
        logger.warning(f"[RateLimiter] Redis unavailable ({exc}), falling back to in-memory")
        _rate_limiter = MemoryRateLimiter()
        _rate_limiter_fallback_at = time.monotonic()

    return _rate_limiter
