"""Unit tests for MemorySessionStore concurrency (CORE-05)."""
import asyncio
import time
import pytest
from app.services.session_data import MemorySessionStore


@pytest.mark.asyncio
async def test_concurrent_stores_no_dict_mutation_error():
    """Verify that concurrent store operations do not raise OrderedDict mutation errors
    and keep byte accounting in sync with actual items.
    """
    store = MemorySessionStore(capacity=5)
    session_id = "test-concurrent-session"

    async def worker(worker_id: int):
        for i in range(10):
            payload = {"worker": worker_id, "i": i, "data": "x" * 100}
            ref_id = await store.store(session_id, payload)
            item = await store.get(session_id, ref_id)
            if item is not None:
                assert item["worker"] == worker_id
            await asyncio.sleep(0.001)

    await asyncio.gather(*[worker(w) for w in range(10)])

    session_cache = store._store.get(session_id)
    assert session_cache is not None
    assert len(session_cache) <= store.capacity

    sizes = store._ref_sizes.get(session_id, {})
    total_bytes = store._session_bytes.get(session_id, 0)
    assert set(session_cache.keys()) == set(sizes.keys())
    assert total_bytes == sum(sizes.values())


@pytest.mark.asyncio
async def test_concurrent_store_overwrite_delete_eviction_integrity():
    """Verify that interleaved stores, overwrites, deletes, and evictions maintain
    internal data structure and byte accounting consistency under high concurrency.
    """
    store = MemorySessionStore(capacity=4)
    session_id = "test-mixed-ops"

    refs = []
    for i in range(4):
        ref = await store.store(session_id, {"init": i})
        refs.append(ref)

    async def writer_task():
        for i in range(15):
            await store.store(session_id, {"val": i, "payload": "A" * 50})
            await asyncio.sleep(0.001)

    async def overwriter_task():
        for i in range(15):
            cache = store._store.get(session_id, {})
            if cache:
                ref = next(iter(cache))
                await store.overwrite(session_id, ref, {"updated": i, "payload": "B" * 60})
            await asyncio.sleep(0.001)

    async def reader_task():
        for _ in range(20):
            cache = store._store.get(session_id, {})
            if cache:
                ref = next(iter(cache))
                await store.get(session_id, ref)
            await asyncio.sleep(0.001)

    async def deleter_task():
        for _ in range(10):
            cache = store._store.get(session_id, {})
            if len(cache) > 2:
                ref = list(cache.keys())[-1]
                await store.delete_ref(session_id, ref)
            await asyncio.sleep(0.002)

    await asyncio.gather(
        writer_task(),
        overwriter_task(),
        reader_task(),
        deleter_task(),
    )

    session_cache = store._store.get(session_id, {})
    sizes = store._ref_sizes.get(session_id, {})
    total_bytes = store._session_bytes.get(session_id, 0)

    assert len(session_cache) <= store.capacity
    assert set(session_cache.keys()) == set(sizes.keys())
    assert total_bytes == sum(sizes.values())


@pytest.mark.asyncio
async def test_eviction_with_slow_spill_serializes_cleanly(monkeypatch):
    """Verify that when ref_spill_store.spill takes time in a worker thread,
    concurrent calls to store/overwrite wait on self._lock without clobbering eviction.
    """
    store = MemorySessionStore(capacity=3)
    session_id = "test-slow-spill"

    from app.services import session_data

    orig_spill = session_data.ref_spill_store.spill

    def slow_spill(sid, ref, data):
        time.sleep(0.02)
        return orig_spill(sid, ref, data)

    monkeypatch.setattr(session_data.ref_spill_store, "spill", slow_spill)

    async def store_op(idx: int):
        return await store.store(session_id, {"idx": idx, "data": "Z" * 100})

    results = await asyncio.gather(*[store_op(i) for i in range(8)])
    assert len(results) == 8

    session_cache = store._store.get(session_id, {})
    sizes = store._ref_sizes.get(session_id, {})
    total_bytes = store._session_bytes.get(session_id, 0)

    assert len(session_cache) == store.capacity
    assert set(session_cache.keys()) == set(sizes.keys())
    assert total_bytes == sum(sizes.values())
