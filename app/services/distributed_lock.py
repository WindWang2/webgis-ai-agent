"""Distributed per-session lock for multi-worker safety (CONCURRENCY-V2).

The architecture is single-process-per-pod by design (Pi subprocess ownership +
localhost HTTP callback, ADR-0006). But production k8s runs replicas:2 + HPA, so
two pods can process the same session concurrently unless routing is sticky.
This module provides defense-in-depth: a Redis-backed per-session lock so a
MapSpec mutation (or any critical per-session section) is mutually exclusive
across pods when Redis is reachable, with a transparent fallback to an in-process
asyncio.Lock when Redis is unavailable (single-worker / test / outage).

Contract:
- ``async with session_lock(session_id): ...`` — mutually exclusive per session.
- Resilient (review P2-1): a Redis error at acquire time (incl. post-startup
  outages) degrades to an in-process lock for that acquisition — it NEVER raises
  unhandled or blocks the request indefinitely.
- TTL + token-checked Lua release so a slow holder never releases a different
  owner's lock; best-effort renewal while held.
- The in-process fallback registry is bounded with waiter-aware eviction (review
  P2-5): only locks that are unlocked with zero waiters are evictable.
"""
from __future__ import annotations

import asyncio
import logging
import os
import secrets
from typing import Dict, Optional

logger = logging.getLogger(__name__)

#: F12: 旧 loop 上 client 的异步关闭任务是 fire-and-forget —— 丢弃引用会让它
#: 在运行前被 GC，且对任何 background-task drain 不可见。保持强引用直到任务
#: 完成（与 execution_engine 的 _background_tasks 同一模式）。
_client_close_tasks: set = set()


def _track_client_close(task) -> None:
    _client_close_tasks.add(task)
    task.add_done_callback(_client_close_tasks.discard)

_DEFAULT_TTL_MS = 30_000          # 30s base TTL
_RENEW_INTERVAL_S = 8.0           # renew well before TTL expiry
_FALLBACK_CAP = 4096              # bound the in-process fallback registry
# Lua: release only if we still own the token (avoid releasing another's lock).
_RELEASE_SCRIPT = (
    b"if redis.call('get', KEYS[1]) == ARGV[1] then "
    b"return redis.call('del', KEYS[1]) else return 0 end"
)

# #397: token-checked renewal. A plain pexpire would blindly extend whatever
# value the key holds — including the NEW owner's token after ours expired and
# was re-acquired — silently breaking mutual exclusion. Renew only if the key
# still holds OUR token; a 0 return means ownership was lost and the loop stops.
_RENEW_SCRIPT = (
    b"if redis.call('get', KEYS[1]) == ARGV[1] then "
    b"return redis.call('pexpire', KEYS[1], ARGV[2]) else return 0 end"
)


class _InProcessLock:
    """Thin wrapper so the fallback path has the same context-manager API."""

    def __init__(self):
        self._lock = asyncio.Lock()

    @property
    def idle(self) -> bool:
        """True iff unlocked AND no waiters — safe to evict (review P2-5)."""
        return (not self._lock.locked()) and (not self._lock._waiters)

    async def __aenter__(self):
        await self._lock.acquire()

    async def __aexit__(self, exc_type, exc, tb):
        self._lock.release()


class _ResilientSessionLock:
    """One-shot per-session lock: Redis when reachable, in-process on any error.

    Acquisition never raises an unhandled Redis error: on connection failure /
    timeout it logs once and falls back to the supplied in-process lock so the
    caller's critical section is still serialized within the process.
    """

    def __init__(self, client, key: str, fallback: _InProcessLock,
                 ttl_ms: int = _DEFAULT_TTL_MS):
        self._client = client
        self._key = key
        self._fallback = fallback
        self._ttl_ms = ttl_ms
        self._mode: str = "inprocess"   # "redis" once acquired via Redis
        self._token: Optional[str] = None
        self._renewer: Optional[asyncio.Task] = None

    async def __aenter__(self):
        if self._client is not None:
            try:
                self._token = secrets.token_hex(16)
                deadline = asyncio.get_event_loop().time() + 10.0
                while True:
                    ok = await self._client.set(
                        self._key, self._token, nx=True, px=self._ttl_ms
                    )
                    if ok:
                        self._mode = "redis"
                        self._renewer = asyncio.create_task(self._renew_loop())
                        return self
                    if asyncio.get_event_loop().time() > deadline:
                        raise TimeoutError(
                            f"session lock contention: could not acquire {self._key} in 10s"
                        )
                    await asyncio.sleep(0.05)
            except (TimeoutError, asyncio.TimeoutError):
                # Genuine contention (another owner holds it): do NOT silently
                # fall back — that would break cross-pod mutual exclusion. Re-raise
                # so the caller surfaces a real "busy" rather than a lost update.
                raise
            except Exception as e:
                # Redis unavailable / errored → degrade to in-process for this
                # acquisition. Observable, non-fatal (review P2-1).
                logger.warning(
                    "session lock Redis acquire failed for %s, degrading to in-process: %s",
                    self._key, e,
                )
                self._token = None
        # In-process fallback (also the path when no client is configured).
        await self._fallback.__aenter__()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        if self._mode == "redis":
            if self._renewer:
                self._renewer.cancel()
                try:
                    await self._renewer
                except (asyncio.CancelledError, Exception):
                    pass
            if self._token:
                try:
                    await self._client.eval(_RELEASE_SCRIPT, 1, self._key, self._token)
                except Exception as e:  # release failure is observable but non-fatal
                    logger.warning("session lock release failed for %s: %s", self._key, e)
        else:
            await self._fallback.__aexit__(exc_type, exc, tb)

    async def _renew_loop(self):
        """Extend the TTL while held so a slow operation doesn't lose ownership."""
        try:
            while True:
                await asyncio.sleep(_RENEW_INTERVAL_S)
                try:
                    renewed = await self._client.eval(
                        _RENEW_SCRIPT, 1, self._key, self._token, self._ttl_ms
                    )
                except Exception:
                    continue  # best-effort; the initial TTL still bounds stale locks
                if not renewed:
                    # Key expired and someone else owns it (or it vanished) —
                    # stop extending a lock we no longer hold.
                    logger.warning(
                        "session lock renew lost ownership of %s — stopping renewal",
                        self._key,
                    )
                    break
        except asyncio.CancelledError:
            pass


class SessionLockRegistry:
    """Per-session lock registry: Redis-backed when available, else in-process.

    Re-verifies Redis reachability periodically (does not cache the "unavailable"
    decision forever — review P2-1) and bounds the in-process fallback table with
    waiter-aware eviction (review P2-5).
    """

    def __init__(self):
        self._client = None
        # The cached client's connection pool is bound to the event loop that
        # was running when it was created (F12); tracked here so a loop change
        # (event-loop restart, pytest per-test loops) triggers a rebuild.
        self._bound_loop: Optional[asyncio.AbstractEventLoop] = None
        self._last_check_s: float = 0.0
        self._fallback_locks: Dict[str, _InProcessLock] = {}
        # asyncio primitives bind to the loop they were first used on. When
        # the running loop changes (pytest per-test loops; loop restarts), a
        # cached in-process lock from the OLD loop deadlocks cross-test
        # acquirers — reproduced by test_delete_session → resume chaos. The
        # client rebuild above (F12) has the same rationale.
        self._fallback_bound_loop: Optional[asyncio.AbstractEventLoop] = None

    @staticmethod
    async def _close_client(client) -> None:
        """Best-effort close of a client bound to a dead loop; failures expected."""
        try:
            await client.aclose()
        except Exception:
            pass

    def _get_client(self):
        import time as _time
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        # F12: if the running loop changed, the cached client's pool is bound to
        # the old (likely closed) loop — every op raises "Event loop is closed"
        # and locking silently degrades to in-process. Rebuild on the current
        # loop (same pattern as RedisRateLimiter._ensure_client /
        # RedisSessionStore._ensure_connected).
        if self._client is not None and self._bound_loop is not loop:
            old, self._client = self._client, None
            self._bound_loop = None
            self._last_check_s = 0.0  # don't let the 60s re-verify gate delay the rebuild
            if loop is not None:
                # F12: 保持强引用直到关闭任务完成 —— create_task 的返回值被丢弃
                # 时任务可能在运行前就被 GC，且对 background-task drain 不可见。
                _track_client_close(loop.create_task(self._close_client(old)))
        now = _time.monotonic()
        # Re-verify at most every 60s so a transient Redis outage is recovered.
        if self._client is not None or (now - self._last_check_s) < 60.0:
            return self._client
        self._last_check_s = now
        redis_url = os.getenv("REDIS_URL")
        use_redis = os.getenv("USE_REDIS", "true").lower() in ("true", "1", "yes")
        if not redis_url or not use_redis:
            return None
        try:
            import redis.asyncio as aioredis  # local import: don't pay cost if unused
            self._client = aioredis.from_url(redis_url, decode_responses=False)
            self._bound_loop = loop
            logger.info("SessionLockRegistry: Redis-backed distributed locks enabled")
        except Exception as e:  # noqa: BLE001
            logger.warning("SessionLockRegistry: Redis unavailable, using in-process locks: %s", e)
            self._client = None
        return self._client

    def _fallback_lock(self, session_id: str) -> _InProcessLock:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if self._fallback_bound_loop is not loop:
            # Loop changed: drop every old-loop lock (they can only deadlock
            # on this loop). Single-loop processes never hit this branch
            # after the first call.
            self._fallback_locks.clear()
            self._fallback_bound_loop = loop
        lock = self._fallback_locks.get(session_id)
        if lock is None:
            self._evict_idle_fallbacks()
            lock = _InProcessLock()
            self._fallback_locks[session_id] = lock
        return lock

    def _evict_idle_fallbacks(self) -> None:
        """Bound the fallback table: drop only unlocked, zero-waiter locks."""
        if len(self._fallback_locks) < _FALLBACK_CAP:
            return
        for sid in [s for s, lk in self._fallback_locks.items() if lk.idle]:
            self._fallback_locks.pop(sid, None)
            if len(self._fallback_locks) < _FALLBACK_CAP * 3 // 4:
                break

    def lock(self, session_id: str, ttl_ms: int = _DEFAULT_TTL_MS):
        """Return an async context manager mutually exclusive for ``session_id``."""
        client = self._get_client()
        fallback = self._fallback_lock(session_id)
        return _ResilientSessionLock(
            client, f"webgis:sessionlock:{session_id}", fallback, ttl_ms
        )


# Module-level singleton. Production uses Redis; tests (USE_REDIS=false) get
# in-process locks. Either way the per-session mutual exclusion holds within a
# process; Redis extends it across pods.
session_lock_registry = SessionLockRegistry()
