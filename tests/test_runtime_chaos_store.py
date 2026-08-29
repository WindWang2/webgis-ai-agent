"""Runtime chaos hardening tests for the session store layer (WP-STORE).

F13: RedisSessionStore.clear_session must invalidate the L1 cache — every
     other writer does; without it a recreated session reads the DELETED
     session's map_state/refs/event_log within L1_TTL_SECONDS.
F22: Redis fault isolation — set_alias / resolve_alias / resolve_aliases /
     list_refs / get_started_at / get_event_log must degrade like their
     siblings (reads → cache-miss/empty, writes → best-effort) instead of
     propagating RedisError into every tool dispatch.
F12: SessionLockRegistry must rebuild its cached Redis client when the
     running event loop changes, instead of silently degrading to
     in-process locking on "Event loop is closed".
F27: MemorySessionStore.append_map_action_event first-terminal-wins must be
     guarded by a lock, not by the accidental absence of awaits.
"""
import asyncio
import logging

import fakeredis.aioredis
import pytest
import redis.asyncio as aioredis

from app.services.distributed_lock import SessionLockRegistry
from app.services.session_data import MemorySessionStore
from app.services.session_data_redis import RedisSessionStore


def _redis_store():
    return RedisSessionStore(
        redis_url="redis://unused",
        redis=fakeredis.aioredis.FakeRedis(decode_responses=False),
    )


# ── F13: clear_session must invalidate L1 ────────────────────────────────


@pytest.mark.asyncio
async def test_f13_clear_session_invalidates_l1():
    """Write state, populate L1, clear, immediately re-read → must be empty.

    Without the invalidation, get_map_state / get_session_metadata serve the
    DELETED session's data from L1 for up to L1_TTL_SECONDS.
    """
    store = _redis_store()
    await store.set_map_state("s1", "viewport", {"zoom": 5})
    await store.store("s1", {"foo": "bar"})
    await store.append_event("s1", "tool_executed", {"tool": "buffer_analysis"})
    # Populate both L1 buckets within L1_TTL_SECONDS.
    await store.get_map_state("s1")
    await store.get_session_metadata("s1")
    assert ("s1", "map_state") in store._l1
    assert ("s1", "metadata_raw") in store._l1

    await store.clear_session("s1")

    # All L1 entries for the session must be dropped by clear_session.
    assert not [k for k in store._l1 if k[0] == "s1"]
    # Immediate re-read (well within L1_TTL_SECONDS) must reflect the deletion.
    assert await store.get_map_state("s1") == {}
    meta = await store.get_session_metadata("s1")
    assert meta["map_state"] == {}
    assert meta["list_refs"] == {}
    assert meta["event_log"] == []
    assert meta["started_at"] is None


# ── F22: Redis fault isolation ────────────────────────────────────────────


class _FaultyPipeline:
    """Pipeline stand-in whose execute() always raises RedisError."""

    def __getattr__(self, name):
        return lambda *a, **kw: self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return None

    async def execute(self):
        raise aioredis.RedisError("pipeline execute timed out")


class _FaultyRedis:
    """Every read raises RedisError — simulates a mid-turn Redis outage."""

    def pipeline(self, *a, **kw):
        return _FaultyPipeline()

    async def hget(self, *a, **kw):
        raise aioredis.RedisError("hget timed out")

    async def hmget(self, *a, **kw):
        raise aioredis.RedisError("hmget timed out")

    async def zrange(self, *a, **kw):
        raise aioredis.RedisError("zrange timed out")

    async def hgetall(self, *a, **kw):
        raise aioredis.RedisError("hgetall timed out")

    async def lrange(self, *a, **kw):
        raise aioredis.RedisError("lrange timed out")

    async def smembers(self, *a, **kw):
        raise aioredis.RedisError("smembers timed out")


def _faulty_store():
    return RedisSessionStore(redis_url="redis://unused", redis=_FaultyRedis())


@pytest.mark.asyncio
async def test_f22_resolve_alias_degrades_to_identity():
    """Read path → cache-miss semantics: return the input unchanged."""
    store = _faulty_store()
    assert await store.resolve_alias("s1", "my-alias") == "my-alias"


@pytest.mark.asyncio
async def test_f22_resolve_aliases_degrades_to_identity_map():
    """Read path on EVERY tool dispatch — must not propagate RedisError."""
    store = _faulty_store()
    out = await store.resolve_aliases("s1", ["a", "b"])
    assert out == {"a": "a", "b": "b"}


@pytest.mark.asyncio
async def test_f22_list_refs_degrades_to_empty():
    store = _faulty_store()
    assert await store.list_refs("s1") == {}


@pytest.mark.asyncio
async def test_f22_get_started_at_degrades_to_none():
    store = _faulty_store()
    assert await store.get_started_at("s1") is None


@pytest.mark.asyncio
async def test_f22_get_event_log_degrades_to_empty():
    store = _faulty_store()
    assert await store.get_event_log("s1") == []


@pytest.mark.asyncio
async def test_f22_set_alias_best_effort_no_raise(caplog):
    """Write path → best-effort with a warning, never raises."""
    store = _faulty_store()
    with caplog.at_level(logging.WARNING, logger="app.services.session_data_redis"):
        await store.set_alias("s1", "ref:x", "alias-x")  # must not raise
    assert any("set_alias" in r.message for r in caplog.records)


# ── F12: SessionLockRegistry event-loop rebinding ─────────────────────────


def test_f12_lock_registry_rebuilds_client_on_loop_change(monkeypatch):
    """Client cached on loop A must be rebuilt when driven from loop B.

    Uses two sequential asyncio.run() calls: each run is a genuinely fresh
    event loop and the previous one is closed — exactly the event-loop
    restart scenario where the cached client's connection pool raises
    'Event loop is closed' on every op and locking silently degrades to
    in-process. The registry must detect the loop change and rebuild.
    """
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("USE_REDIS", "true")
    created = []

    class _FakeClient:
        async def aclose(self):
            return None

    def _from_url(*a, **kw):
        client = _FakeClient()
        created.append(client)
        return client

    monkeypatch.setattr("redis.asyncio.from_url", _from_url)
    registry = SessionLockRegistry()

    holder = {}

    async def _grab(key):
        holder[key] = registry._get_client()
        await asyncio.sleep(0)  # let the best-effort close task run

    asyncio.run(_grab("first"))
    asyncio.run(_grab("second"))  # fresh loop; the first loop is closed

    assert holder["second"] is not holder["first"], (
        "stale client bound to the closed loop was reused"
    )
    assert len(created) == 2
    assert holder["second"] is created[-1]


@pytest.mark.asyncio
async def test_f12_lock_registry_reuses_client_on_same_loop(monkeypatch):
    """No loop change → the cached client is reused (no churn)."""
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("USE_REDIS", "true")
    created = []

    class _FakeClient:
        async def aclose(self):
            return None

    def _from_url(*a, **kw):
        client = _FakeClient()
        created.append(client)
        return client

    monkeypatch.setattr("redis.asyncio.from_url", _from_url)
    registry = SessionLockRegistry()

    first = registry._get_client()
    assert registry._get_client() is first
    assert len(created) == 1


# ── F27: memory-backend ACK atomicity ─────────────────────────────────────


@pytest.mark.asyncio
async def test_f27_concurrent_duplicate_appends_first_terminal_wins():
    """Characterization: N concurrent duplicate appends → exactly one accepted.

    Currently passes because the critical section has no awaits; it guards the
    first-terminal-wins invariant against regressions.
    """
    store = MemorySessionStore()
    n = 8
    start = asyncio.Event()

    async def _append(i):
        await start.wait()
        return await store.append_map_action_event(
            "s1", {"action_id": "ma-dup", "status": "succeeded", "i": i}
        )

    tasks = [asyncio.create_task(_append(i)) for i in range(n)]
    start.set()
    results = await asyncio.gather(*tasks)
    assert sum(results) == 1
    events = await store.get_map_action_events("s1")
    assert [e["action_id"] for e in events] == ["ma-dup"]


@pytest.mark.asyncio
async def test_f27_first_wins_survives_injected_suspension_point():
    """A future await inside the dedupe critical section must not break
    first-terminal-wins — the store's ``_map_action_lock`` must serialize it.

    Injects the suspension by wrapping ``_map_action_lock`` so its held
    section yields the event loop; concurrent duplicate appends must still
    resolve to exactly one winner.
    """
    store = MemorySessionStore()

    class _SuspendingLock:
        def __init__(self, inner):
            self._inner = inner

        async def __aenter__(self):
            await self._inner.acquire()
            # Simulate a future await INSIDE the critical section (e.g. an
            # added metrics hook). The lock must stay held while other
            # appends queue up.
            await asyncio.sleep(0)

        async def __aexit__(self, *exc):
            self._inner.release()

    store._map_action_lock = _SuspendingLock(store._map_action_lock)

    n = 4
    start = asyncio.Event()

    async def _append(i):
        await start.wait()
        return await store.append_map_action_event(
            "s1", {"action_id": "ma-dup", "status": "succeeded", "i": i}
        )

    tasks = [asyncio.create_task(_append(i)) for i in range(n)]
    start.set()
    results = await asyncio.gather(*tasks)
    assert sum(results) == 1
    events = await store.get_map_action_events("s1")
    assert [e["action_id"] for e in events] == ["ma-dup"]
