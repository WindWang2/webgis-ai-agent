"""Distributed per-session lock for multi-worker safety (CONCURRENCY-V2).

The architecture is single-process-per-pod by design (Pi subprocess ownership +
localhost HTTP callback, ADR-0006). But production k8s runs replicas:2 + HPA, so
two pods can process the same session concurrently unless routing is sticky.
This module provides defense-in-depth: a Redis-backed per-session lock so a
MapSpec mutation (or any critical per-session section) is mutually exclusive
across pods when Redis is reachable, with a transparent fallback to an in-process
asyncio.Lock when Redis is unavailable (single-worker / test).

Contract:
- ``async with session_lock(session_id): ...`` — mutually exclusive per session.
- Auto-renews the TTL while held (best-effort) and releases with token check so a
  slow holder never releases a different owner's lock.
- Any Redis error degrades to an in-process lock (never blocks the request).
"""
from __future__ import annotations

import asyncio
import logging
import os
import secrets
from typing import Dict, Optional

logger = logging.getLogger(__name__)

_DEFAULT_TTL_MS = 30_000          # 30s base TTL
_RENEW_INTERVAL_S = 8.0           # renew well before TTL expiry
# Lua: release only if we still own the token (avoid releasing another's lock).
_RELEASE_SCRIPT = (
    b"if redis.call('get', KEYS[1]) == ARGV[1] then "
    b"return redis.call('del', KEYS[1]) else return 0 end"
)


class _InProcessLock:
    """Thin wrapper so the fallback path has the same context-manager API."""

    def __init__(self):
        self._lock = asyncio.Lock()

    async def __aenter__(self):
        await self._lock.acquire()

    async def __aexit__(self, exc_type, exc, tb):
        self._lock.release()


class _RedisSessionLock:
    """A single Redis-backed lock acquisition (one-shot context manager)."""

    def __init__(self, client, key: str, ttl_ms: int = _DEFAULT_TTL_MS):
        self._client = client
        self._key = key
        self._ttl_ms = ttl_ms
        self._token: Optional[str] = None
        self._renewer: Optional[asyncio.Task] = None

    async def __aenter__(self):
        self._token = secrets.token_hex(16)
        # Spin briefly to acquire — bounded so a contended lock fails fast rather
        # than blocking indefinitely (the caller surfaces the failure).
        deadline = asyncio.get_event_loop().time() + 10.0
        while True:
            ok = await self._client.set(self._key, self._token, nx=True, px=self._ttl_ms)
            if ok:
                self._renewer = asyncio.create_task(self._renew_loop())
                return self
            if asyncio.get_event_loop().time() > deadline:
                raise TimeoutError(f"could not acquire session lock {self._key} after 10s")
            await asyncio.sleep(0.05)

    async def __aexit__(self, exc_type, exc, tb):
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

    async def _renew_loop(self):
        """Extend the TTL while held so a slow operation doesn't lose ownership."""
        try:
            while True:
                await asyncio.sleep(_RENEW_INTERVAL_S)
                try:
                    await self._client.pexpire(self._key, self._ttl_ms)
                except Exception:
                    pass  # best-effort; the initial TTL still bounds stale locks
        except asyncio.CancelledError:
            pass


class SessionLockRegistry:
    """Per-session lock registry: Redis-backed when available, else in-process."""

    def __init__(self):
        self._client = None
        self._client_checked = False
        self._fallback_locks: Dict[str, _InProcessLock] = {}

    def _get_client(self):
        if self._client_checked:
            return self._client
        self._client_checked = True
        redis_url = os.getenv("REDIS_URL")
        use_redis = os.getenv("USE_REDIS", "true").lower() in ("true", "1", "yes")
        if not redis_url or not use_redis:
            return None
        try:
            import redis.asyncio as aioredis  # local import: don't pay cost if unused
            self._client = aioredis.from_url(redis_url, decode_responses=False)
            logger.info("SessionLockRegistry: Redis-backed distributed locks enabled")
        except Exception as e:  # noqa: BLE001
            logger.warning("SessionLockRegistry: Redis unavailable, using in-process locks: %s", e)
            self._client = None
        return self._client

    def lock(self, session_id: str, ttl_ms: int = _DEFAULT_TTL_MS):
        """Return an async context manager mutually exclusive for ``session_id``."""
        client = self._get_client()
        if client is not None:
            return _RedisSessionLock(client, f"webgis:sessionlock:{session_id}", ttl_ms)
        # Fallback: in-process asyncio.Lock per session (single-worker / test).
        return self._fallback_locks.setdefault(session_id, _InProcessLock())


# Module-level singleton. Production uses Redis; tests (USE_REDIS=false) get
# in-process locks. Either way the per-session mutual exclusion holds within a
# process; Redis extends it across pods.
session_lock_registry = SessionLockRegistry()
