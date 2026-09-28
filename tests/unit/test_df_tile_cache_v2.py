"""DfTileCache W12 契约：字节记账真源 + 单条上限 + 观测计数 + 构建收敛。

回归锚点：此前 ``put(key, (gz, fp))`` 存 tuple 而 ``len(value)`` 数元素个数
（恒 2）—— 256MB 字节预算从未生效（无界驻留）；本文件钉死修复语义。
"""
import asyncio


from app.services.data_fabric.tile_cache import (
    DfTileCache,
    TileBuildCoalescer,
    TileCacheEntry,
)


# ── 字节记账 ─────────────────────────────────────────────────────────────────


def test_put_accounts_gzip_bytes_not_tuple_arity():
    cache = DfTileCache(max_entries=8, max_bytes=64 * 1024 * 1024)
    cache.put(("it", "org:o|owner:u", "fp", 3, 1, 0), TileCacheEntry(gz=b"x" * 1024, fingerprint="fp"))
    s = cache.stats()
    assert s["bytes"] == 1024, "字节记账必须是 gzip 载荷长度，而非 tuple 元素个数"
    assert s["puts"] == 1


def test_put_accepts_legacy_tuple_and_still_accounts_bytes():
    cache = DfTileCache(max_entries=8, max_bytes=64 * 1024 * 1024)
    cache.put(("it", "s", "fp", 0, 0, 0), (b"z" * 512, "fp"))
    assert cache.stats()["bytes"] == 512
    assert cache.get(("it", "s", "fp", 0, 0, 0)) == (b"z" * 512, "fp"), "裸 tuple 读取语义保持"


def test_oversize_entry_rejected_not_cached():
    cache = DfTileCache(max_entries=8, max_bytes=64 * 1024 * 1024, max_entry_bytes=1024)
    cache.put(("big", "s", "fp", 0, 0, 0), TileCacheEntry(gz=b"q" * 4096, fingerprint="fp"))
    assert cache.get(("big", "s", "fp", 0, 0, 0)) is None
    s = cache.stats()
    assert s["oversize_rejects"] == 1 and s["entries"] == 0 and s["bytes"] == 0


def test_bytes_budget_evicts_lru_and_frees_on_invalidate():
    cache = DfTileCache(max_entries=64, max_bytes=2048, max_entry_bytes=1024)
    for i in range(4):
        cache.put(("it", "s", "fp", i, 0, 0), TileCacheEntry(gz=bytes([i]) * 700, fingerprint="fp"))
    s = cache.stats()
    assert s["bytes"] <= 2048, "字节预算必须真实生效"
    assert s["evictions"] >= 1
    cache.invalidate_item("it")
    assert cache.stats()["bytes"] == 0 and cache.stats()["entries"] == 0


def test_hit_miss_counters():
    cache = DfTileCache()
    key = ("it", "s", "fp", 1, 0, 0)
    cache.get(key)  # miss
    cache.put(key, TileCacheEntry(gz=b"a", fingerprint="fp"))
    cache.get(key)  # hit
    cache.get(key)  # hit
    s = cache.stats()
    assert (s["hits"], s["misses"]) == (2, 1)


# ── TileBuildCoalescer（single-flight）───────────────────────────────────────


def test_coalescer_merges_concurrent_same_key_builds():
    calls = {"n": 0}

    async def scenario():
        coalescer: TileBuildCoalescer[str] = TileBuildCoalescer()

        async def builder():
            calls["n"] += 1
            await asyncio.sleep(0.02)
            return "tile"

        results = await asyncio.gather(*(coalescer.run("k", builder) for _ in range(50)))
        assert results == ["tile"] * 50
        assert calls["n"] == 1, "同键并发必须收敛为一次构建"
        assert coalescer.inflight_keys() == 0, "完成后不得泄漏 in-flight 槽"

    asyncio.run(scenario())


def test_coalescer_different_keys_build_independently():
    async def scenario():
        coalescer: TileBuildCoalescer[int] = TileBuildCoalescer()
        results = await asyncio.gather(
            *(coalescer.run(i, lambda i=i: _int_builder(i)) for i in range(20))
        )
        assert sorted(results) == list(range(20))

    asyncio.run(scenario())


async def _int_builder(i: int):
    await asyncio.sleep(0)
    return i


def test_coalescer_failure_propagates_to_all_waiters_and_slot_freed():
    async def scenario():
        coalescer: TileBuildCoalescer[str] = TileBuildCoalescer()

        async def boom():
            await asyncio.sleep(0.01)
            raise RuntimeError("tile build failed")

        results = await asyncio.gather(
            *(coalescer.run("k", boom) for _ in range(10)), return_exceptions=True
        )
        assert all(isinstance(r, RuntimeError) for r in results), "失败必须逐份传播"
        assert coalescer.inflight_keys() == 0

        # 失败不缓存：后续请求重新构建且可成功。
        async def ok():
            await asyncio.sleep(0)
            return "fine"

        assert await coalescer.run("k", ok) == "fine"

    asyncio.run(scenario())


def test_coalescer_wait_timeout_degrades_to_own_build():
    async def scenario():
        coalescer: TileBuildCoalescer[str] = TileBuildCoalescer(wait_timeout_s=0.05)
        slow_started = asyncio.Event()

        async def slow():
            slow_started.set()
            await asyncio.sleep(0.4)
            return "slow"

        leader = asyncio.create_task(coalescer.run("k", slow))
        await slow_started.wait()

        async def own():
            await asyncio.sleep(0)
            return "own"

        assert await coalescer.run("k", own) == "own", "领导者超时后到者必须自建，不悬挂"
        assert await leader == "slow"
        assert coalescer.inflight_keys() == 0

    asyncio.run(scenario())


def test_coalescer_max_inflight_degrades_without_queueing():
    async def scenario():
        coalescer: TileBuildCoalescer[int] = TileBuildCoalescer(max_inflight=1)
        release = asyncio.Event()

        async def blocker():
            await release.wait()
            return 1

        leader = asyncio.create_task(coalescer.run("k1", blocker))
        await asyncio.sleep(0.01)
        assert coalescer.inflight_keys() == 1

        async def own():
            return 2

        # max_inflight=1 已满 → 该请求直接自建（有界降级），不等 k1。
        assert await coalescer.run("k2", own) == 2
        release.set()
        assert await leader == 1

    asyncio.run(scenario())
