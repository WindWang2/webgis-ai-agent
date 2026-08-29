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
    assert ("s1", "metadata_raw") in store._l1

    # any write to the session invalidates metadata L1 too
    await store.set_map_state("s1", "is_3d", True)
    assert ("s1", "metadata_raw") not in store._l1


@pytest.mark.asyncio
async def test_store_invalidates_metadata_l1():
    """RUN-06: store() must invalidate the metadata L1 bundle.

    get_session_metadata caches list_refs + event_log under the "metadata_raw" key (#1080 原始字段缓存)
    (2s TTL). If store() doesn't invalidate it, a freshly-stored ref is invisible
    to the next round for up to 2s — the cached bundle still has the old list_refs.
    """
    store = _store()
    await store.get_session_metadata("s1")  # populate L1 (empty list_refs)
    assert ("s1", "metadata_raw") in store._l1

    ref = await store.store("s1", {"x": 1})
    # store() must drop the stale metadata bundle so the next read refetches
    assert ("s1", "metadata_raw") not in store._l1

    meta = await store.get_session_metadata("s1")
    # New ref visible immediately, WITHOUT waiting out the 2s TTL.
    assert ref in meta["list_refs"]


@pytest.mark.asyncio
async def test_append_event_invalidates_metadata_l1():
    """RUN-06: append_event() must invalidate the metadata L1 bundle.

    The metadata bundle includes event_log; a cached copy hides newly-appended
    events from the next round for up to 2s.
    """
    store = _store()
    await store.get_session_metadata("s1")  # populate L1 (empty event_log)
    assert ("s1", "metadata_raw") in store._l1

    await store.append_event("s1", "tool_executed", {"tool": "buffer_analysis"})
    assert ("s1", "metadata_raw") not in store._l1

    meta = await store.get_session_metadata("s1")
    # New event visible immediately, WITHOUT waiting out the 2s TTL.
    assert len(meta["event_log"]) == 1
    assert meta["event_log"][0]["data"]["tool"] == "buffer_analysis"


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


@pytest.mark.asyncio
async def test_metadata_l1_hit_returns_isolated_copy_809():
    """#809: metadata L1 命中返回独立对象树（#749 纪律对齐 get_map_state）——
    嵌套 map_state 的就地改动不得污染缓存与其并发读者。"""
    store = _store()
    await store.set_map_state("s809", "layers", [{"id": "L1"}])
    await store.append_event("s809", "chat", {"m": 1})
    store.clear_l1_cache()

    first = await store.get_session_metadata("s809")
    assert ("s809", "metadata_raw") in store._l1

    # 就地污染尝试（嵌套层）
    first["map_state"]["layers"].append({"id": "POISON"})
    second = await store.get_session_metadata("s809")
    assert [layer["id"] for layer in second["map_state"]["layers"]] == ["L1"]
