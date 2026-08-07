"""Tests for the L1 (in-process) cache layered in front of Redis L2.

Phase 7 perf: get_map_state / get_session_metadata are read multiple times
per chat turn (context_builder + ws_service + tool dispatch). The L1 cache
collapses those repeats into one Redis round-trip within L1_TTL_SECONDS, and
is invalidated on every write so it never serves stale data written by the
same process.
"""
import time

import pytest

import fakeredis.aioredis

from app.services.session_data_redis import (
    RedisSessionStore,
    L1_TTL_SECONDS,
    L1_MAX_SESSIONS,
)


def _store():
    return RedisSessionStore(
        redis_url="redis://unused",
        redis=fakeredis.aioredis.FakeRedis(decode_responses=False),
    )


@pytest.mark.asyncio
async def test_map_state_l1_hit_avoids_second_redis_read():
    store = _store()
    await store.set_map_state("s1", "layers", [{"id": "L1"}])
    store.clear_l1_cache()  # set_map_state invalidated; start clean

    first = await store.get_map_state("s1")
    assert first["layers"] == [{"id": "L1"}]
    # L1 now populated
    assert ("s1", "map_state") in store._l1

    # Swap the underlying Redis value OUT FROM UNDER the L1 — if L1 serves the
    # cached copy, the second read returns the OLD value (proving it didn't hit Redis).
    await store._r.hset(store._state_key("s1"), "layers", '["HACKED"]')
    # NB: bypass set_map_state so its invalidation doesn't fire.

    second = await store.get_map_state("s1")
    assert second["layers"] == [{"id": "L1"}], "L1 should have served cached value"


@pytest.mark.asyncio
async def test_map_state_l1_invalidated_on_write():
    store = _store()
    await store.set_map_state("s1", "layers", [{"id": "old"}])
    await store.get_map_state("s1")  # populate L1
    assert ("s1", "map_state") in store._l1

    await store.set_map_state("s1", "layers", [{"id": "new"}])
    assert ("s1", "map_state") not in store._l1  # write-through invalidation

    fresh = await store.get_map_state("s1")
    assert fresh["layers"] == [{"id": "new"}]


@pytest.mark.asyncio
async def test_update_layer_invalidates_l1():
    store = _store()
    await store.set_map_state("s1", "layers", [{"id": "L1", "opacity": 1.0}])
    await store.get_map_state("s1")  # populate L1

    await store.update_layer_in_state("s1", "L1", {"opacity": 0.5})
    assert ("s1", "map_state") not in store._l1

    out = await store.get_map_state("s1")
    assert out["layers"][0]["opacity"] == 0.5


@pytest.mark.asyncio
async def test_remove_layer_invalidates_l1():
    store = _store()
    await store.set_map_state("s1", "layers", [{"id": "L1"}, {"id": "L2"}])
    await store.get_map_state("s1")  # populate L1

    await store.remove_layer_from_state("s1", "L1")
    assert ("s1", "map_state") not in store._l1

    out = await store.get_map_state("s1")
    assert [layer["id"] for layer in out["layers"]] == ["L2"]


@pytest.mark.asyncio
async def test_metadata_l1_hit_then_invalidate():
    store = _store()
    await store.store("s1", {"x": 1})
    await store.get_session_metadata("s1")  # populate L1
    assert ("s1", "metadata") in store._l1

    # any write to the session invalidates metadata L1 too
    await store.set_map_state("s1", "is_3d", True)
    assert ("s1", "metadata") not in store._l1


@pytest.mark.asyncio
async def test_l1_expires_after_ttl(monkeypatch):
    store = _store()
    await store.set_map_state("s1", "v", 1)
    await store.get_map_state("s1")  # populate L1

    # Advance monotonic clock past TTL by patching time.monotonic.
    real_monotonic = time.monotonic
    clock = [real_monotonic()]

    def fake_monotonic():
        return clock[0]

    monkeypatch.setattr("app.services.session_data_redis.time.monotonic", fake_monotonic)
    # populate uses patched clock
    store.clear_l1_cache()
    await store.get_map_state("s1")
    assert store._l1_get("s1", "map_state") is not None

    # advance past TTL
    clock[0] += L1_TTL_SECONDS + 0.01
    assert store._l1_get("s1", "map_state") is None


@pytest.mark.asyncio
async def test_l1_evicts_beyond_capacity():
    store = _store()
    # L1_MAX_SESSIONS is per (session, kind); overflow should evict oldest.
    for i in range(L1_MAX_SESSIONS + 5):
        await store.set_map_state(f"s{i}", "v", i)
        await store.get_map_state(f"s{i}")  # populate L1 for this session
    assert len(store._l1) <= L1_MAX_SESSIONS
    # the oldest entries (s0..s4) should have been evicted
    assert ("s0", "map_state") not in store._l1
    # the newest should still be present
    assert ("s%d" % (L1_MAX_SESSIONS + 4), "map_state") in store._l1


@pytest.mark.asyncio
async def test_clear_l1_cache():
    store = _store()
    await store.set_map_state("s1", "v", 1)
    await store.get_map_state("s1")
    assert len(store._l1) > 0
    store.clear_l1_cache()
    assert len(store._l1) == 0
    assert len(store._l1_order) == 0
