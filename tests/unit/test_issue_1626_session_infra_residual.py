"""
Regression tests for issue #1626 (session/infra distributed-correctness residuals).

Acceptance from issue:
- (a) production-shape ``RedisSessionStore(redis_url=...)`` (redis=None) whose
  first Redis touch is ``get_shared`` must NOT raise AttributeError — it
  lazily connects like every sibling and degrades to None (cache-miss).
- (b) at-capacity session with interleaved ``get(oldest_ref)`` and
  ``store(new)``: the read ref's data key must always survive (the LRU
  snapshot must not evict a ref bumped after the snapshot).
- (c) ``MemorySessionStore.get_map_state`` snapshot discipline: a reader
  interleaved with ``update_layer_in_state`` sees each layer either fully
  old or fully new (copy-on-write pinned).
- (e) ``RefPayloadCache.invalidate_session`` must move freshly incremented
  epochs to the MRU end so a foreign invalidation tide cannot trim them
  off the front of the 4×max_entries epoch table.
"""

import asyncio
import copy as real_copy
import json

import pytest

import fakeredis.aioredis

import app.services.session_data as session_data_module
from app.services.ref_payload_cache import RefPayloadCache
from app.services.session_data import MemorySessionStore
from app.services.session_data_redis import RedisSessionStore


# ─── (a) get_shared: lazy connect in production shape ───────────────────────


@pytest.mark.asyncio
async def test_get_shared_production_shape_degrades_to_none():
    """#1626(a): get_shared was the only public method without
    ``_ensure_connected()`` — in production shape (redis=None) a first-touch
    ``get_shared`` hit ``self._r is None`` and raised AttributeError, which
    is NOT part of the method's RedisError degradation contract. After the
    fix it lazily connects like every sibling and returns None (cache-miss)
    when Redis is unreachable."""
    store = RedisSessionStore(
        redis_url="redis://127.0.0.1:1", socket_timeout=0.5,
    )
    assert store._r is None  # production shape: no injected client
    # 必须走降级契约返回 None，而不是 AttributeError。
    assert await store.get_shared("sess-1626a", "ref:x") is None


@pytest.mark.asyncio
async def test_get_shared_injected_backend_still_resolves():
    """Happy path stays intact after adding the connect gate (fakeredis)."""
    store = RedisSessionStore(
        redis_url="redis://unused",
        redis=fakeredis.aioredis.FakeRedis(decode_responses=False),
    )
    sid = "sess-1626a-hit"
    ref = await store.store(sid, {"v": 1})
    assert await store.get_shared(sid, ref) == {"v": 1}
    assert await store.get_shared(sid, "ref:nonexistent") is None


# ─── (b) store() LRU eviction vs get() recency bump race ────────────────────


class _FakeClock:
    """Deterministic strictly-increasing clock.

    A recency bump (``zadd now``) must land strictly ABOVE the snapshot
    score for the eviction filter to exclude it. The real clock can return
    the same tick for back-to-back calls, which would make the filter
    compare equal scores and evict a just-read ref.
    """

    def __init__(self) -> None:
        self._t = 1_000_000.0

    def time(self) -> float:
        self._t += 0.001
        return self._t

    def monotonic(self) -> float:
        return self._t


class _SnapshotInterleavingClient:
    """Fakeredis wrapper reproducing the #1626(b) race interleave point.

    After ``store()``'s (unwatched) ``zrange`` eviction snapshot returns
    but before its evict pipeline executes, run one full ``get()`` of the
    oldest ref — the concurrent reader that bumps recency. With the fix,
    the eviction re-reads live scores under WATCH and skips the bumped ref.
    """

    def __init__(self, inner, store, session_id: str):
        self._inner = inner
        self._store = store
        self._sid = session_id
        self._pending = None  # (ref_id, expected_payload)

    def arm(self, ref_id: str, expected) -> None:
        self._pending = (ref_id, expected)

    async def zrange(self, *args, **kwargs):
        result = await self._inner.zrange(*args, **kwargs)
        if self._pending is not None:
            ref_id, expected = self._pending
            self._pending = None
            # 并发读者：完整跑一次 get（alias hget + GET + recency bump）。
            got = await self._store.get(self._sid, ref_id)
            assert got == expected, "reader must observe the payload this round"
        return result

    def __getattr__(self, name):
        return getattr(self._inner, name)


@pytest.mark.asyncio
async def test_store_eviction_skips_ref_bumped_by_concurrent_get(monkeypatch):
    """#1626(b): at-capacity session, interleave ``get(oldest)`` with
    ``store(new)`` for 100 rounds — the concurrently-read ref's data key
    must always exist afterwards.

    The eviction set comes from a non-WATCHed zrange snapshot; a get()
    recency bump landing between the snapshot and the evict pipeline used
    to delete a just-read ref's data key (reader got data this time, a
    missing ref next time). The fix re-reads live scores under WATCH and
    only evicts members whose score was NOT raised.
    """
    monkeypatch.setattr("app.services.session_data_redis.time", _FakeClock())

    raw = fakeredis.aioredis.FakeRedis(decode_responses=False)
    store = RedisSessionStore(
        redis_url="redis://unused", redis=raw, capacity=2,
    )
    sid = "sess-1626b"
    wrapper = _SnapshotInterleavingClient(raw, store, sid)
    store._r = wrapper
    order_key = store._refs_order_key(sid)

    await store.store(sid, {"v": 1})
    await store.store(sid, {"v": 2})

    for round_no in range(100):
        # 当前最旧 ref（经 raw 直读，避开 wrapper 的注入点）。
        oldest = await raw.zrange(order_key, 0, 0)
        read_ref = oldest[0].decode()
        raw_payload = await raw.get(store._data_key(sid, read_ref))
        expected = json.loads(raw_payload)
        # 在下一次 store 的快照点注入并发读者。
        wrapper.arm(read_ref, expected)
        await store.store(sid, {"v": round_no + 3})
        # 被读 ref 的数据键必须仍在（读取成功且未被误删）。
        assert await store.get(sid, read_ref) == expected, (
            f"round {round_no}: concurrently-read ref {read_ref} was evicted"
        )

    # 读即豁免不等于永不淘汰：容量仍然收敛（不无界增长）。
    live = await raw.zcard(order_key)
    assert live <= 4, f"capacity must converge, got {live} live refs"


# ─── (c) get_map_state snapshot discipline (copy-on-write) ──────────────────


@pytest.mark.asyncio
async def test_get_map_state_layer_snapshot_all_or_nothing(monkeypatch):
    """#1626(c): get_map_state's "loop-thread shallow snapshot + worker-thread
    deepcopy" relies on the copy-on-write discipline for layer NESTED
    objects (only replaced, never mutated in place). The deepcopy is held
    in flight while ``update_layer_in_state`` lands: the reader must see
    each layer either fully old or fully new — an in-place nested mutation
    would surface here as a torn (mixed) layer.
    """
    store = MemorySessionStore(capacity=10)
    sid = "sess-1626c"
    old_layer = {
        "id": "L1",
        "source": {"kind": "wms", "url": "https://example.com/wms"},
        "paint": {"color": "red"},
        # 大体量嵌套对象：拉长 deepcopy 窗口，使写方落笔与读取真并发。
        "features": [{"i": i, "geom": {"type": "Point", "coordinates": [i, i]}}
                     for i in range(2000)],
    }
    await store.set_map_state(sid, "layers", [old_layer])
    new_layer = {**old_layer, "paint": {"color": "blue"}}

    snapshot_taken = asyncio.Event()
    real_to_thread = asyncio.to_thread

    class _AsyncioShim:
        """Signal once the shallow snapshot is taken (deepcopy in flight)."""

        def __init__(self, real):
            self._real = real

        def __getattr__(self, name):
            return getattr(self._real, name)

        async def to_thread(self, fn, *args, **kwargs):
            if fn is real_copy.deepcopy:
                snapshot_taken.set()
            return await real_to_thread(fn, *args, **kwargs)

    monkeypatch.setattr(session_data_module, "asyncio", _AsyncioShim(asyncio))

    reader = asyncio.create_task(store.get_map_state(sid))
    await snapshot_taken.wait()  # 浅快照已取，deepcopy 正在工作线程飞行

    # 写方在 deepcopy 飞行期间落笔（copy-on-write：整体替换 layer dict，
    # 嵌套对象只进不出）。
    assert await store.update_layer_in_state(
        sid, "L1", {"paint": {"color": "blue"}}
    )

    seen = await reader

    layer = seen["layers"][0]
    assert layer in (old_layer, new_layer), (
        f"reader saw a torn layer (neither fully old nor fully new): {layer}"
    )
    assert layer["source"] == old_layer["source"], "nested object was mutated in place"
    assert layer["features"] == old_layer["features"]
    # 活存储已是新值（写方生效，读者快照与活存储互不撕裂）。
    live = await store.get_map_state(sid)
    assert live["layers"][0]["paint"] == {"color": "blue"}


# ─── (e) invalidate_session epoch must land on the MRU end ──────────────────


def test_invalidate_session_epoch_survives_epoch_table_pressure():
    """#1626(e): invalidate_session increments epochs without move_to_end,
    so a foreign invalidation tide trimmed the freshly incremented epochs
    off the FRONT of the 4×max_entries table — an in-flight builder could
    then misread epoch 0 as "not invalidated". Construct exactly one front
    trim (table at cap+1): without move_to_end the victim is precisely the
    session's epoch; with it, the older foreign entry absorbs the trim.
    """
    cache = RefPayloadCache(ttl=60, max_entries=8)
    cap = 4 * 8

    cache.invalidate("sess-e", "ref:a")     # 目标 epoch（队首）
    cache.invalidate("other", "ref:x0")    # 更旧外国条目（吸收裁剪）
    assert cache.current_epoch("sess-e", "ref:a") == 1

    cache.invalidate_session("sess-e")     # 递增 + move_to_end
    assert cache.current_epoch("sess-e", "ref:a") == 2

    have = len(cache._epochs)
    for i in range(cap + 1 - have):
        cache.invalidate("other", f"ref:churn-{i}")

    assert len(cache._epochs) == cap, "epoch table must converge at 4×max_entries"
    assert cache.current_epoch("sess-e", "ref:a") == 2, (
        "freshly incremented epoch must not be trimmed off the front "
        "(invalidate_session must move_to_end)"
    )
