"""Singleflight (cache stampede) tests — goal §7.

Two concurrent callers with identical args previously both computed on cache
miss. Now the miss path takes a Redis SET NX lock (token + TTL): the winner
computes and publishes, followers poll until the value appears. Lock expiry /
Redis failure degrade to direct compute (bounded duplicate) — no deadlock.
"""
import asyncio
import threading
import time

import pytest
from unittest.mock import MagicMock, patch
from redis.exceptions import ConnectionError

from app.lib.tool_cache import (
    cached_tool,
    make_cache_key,
    _reset_redis_client_for_tests,
)


@pytest.fixture()
def _mock_redis():
    storage = {}
    locks = {}

    def fake_set(name, value, nx=False, px=None):
        if nx:
            if name in locks:
                return False
            locks[name] = value
            return True
        storage[name] = value
        return True

    with patch("app.lib.tool_cache._get_redis_client") as mock_client:
        mock_redis = MagicMock()
        mock_redis.set.side_effect = fake_set
        mock_redis.setex.side_effect = lambda k, ttl, v: storage.__setitem__(k, v)
        mock_redis.get.side_effect = lambda k: storage.get(k)
        mock_redis.exists.side_effect = lambda k: 1 if k in locks else 0
        mock_redis.eval.side_effect = lambda script, num, key, token: (
            1 if locks.get(key) == token and locks.pop(key, None) is not None else 0
        )
        mock_client.return_value = mock_redis
        _reset_redis_client_for_tests()
        yield mock_redis, storage, locks
        _reset_redis_client_for_tests()


@pytest.mark.asyncio
async def test_async_concurrent_miss_computes_once(_mock_redis):
    """Two concurrent identical calls → one compute, both get the result."""
    calls = 0

    @cached_tool(ttl=3600)
    async def slow_tool(x: int):
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.2)
        return {"r": x * 2}

    t0 = time.monotonic()
    r1, r2 = await asyncio.gather(slow_tool(x=3), slow_tool(x=3))
    elapsed = time.monotonic() - t0

    assert r1 == {"r": 6}
    assert r2 == {"r": 6}
    assert calls == 1  # singleflight suppressed the duplicate
    assert elapsed < 2.0  # follower waited for the winner, didn't hang


@pytest.mark.asyncio
async def test_sync_concurrent_miss_computes_once(_mock_redis):
    """Sync tools (run in the threadpool) get the same protection."""
    calls = 0

    @cached_tool(ttl=3600)
    def slow_tool(x: int):
        nonlocal calls
        calls += 1
        time.sleep(0.2)
        return {"r": x * 2}

    results = []

    def run():
        results.append(slow_tool(x=3))

    threads = [threading.Thread(target=run) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(results) == 2
    assert all(r == {"r": 6} for r in results)
    assert calls == 1


@pytest.mark.asyncio
async def test_stale_lock_degrades_to_compute(_mock_redis):
    """A lock held by a crashed caller must not hang new callers (no deadlock)."""
    _, storage, locks = _mock_redis
    key = make_cache_key("slow_tool", {"x": 3})
    locks[f"{key}:lock"] = "stale-token-from-crashed-caller"
    calls = 0

    @cached_tool(ttl=3600, lock_ttl=1)  # 1s lock window → deadline falls back fast
    async def slow_tool(x: int):
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.05)
        return {"r": x * 2}

    t0 = time.monotonic()
    result = await slow_tool(x=3)
    elapsed = time.monotonic() - t0

    assert result == {"r": 6}
    assert calls == 1
    assert elapsed < 5.0  # waited briefly for the stale lock, then computed
    # the computed result is cached for the next caller
    assert storage[key] is not None


@pytest.mark.asyncio
async def test_failed_winner_releases_lock_and_follower_computes(_mock_redis):
    """A winner whose compute raises must not strand followers (lock released)."""
    calls = 0

    @cached_tool(ttl=3600)
    async def flaky_tool(x: int):
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.05)
        if calls == 1:
            raise RuntimeError("boom")
        return {"r": x}

    results = await asyncio.gather(
        flaky_tool(x=3), flaky_tool(x=3), return_exceptions=True
    )
    assert isinstance(results[0], RuntimeError)  # winner failed
    assert results[1] == {"r": 3}  # follower took over immediately
    assert calls == 2


def test_redis_failure_degrades_to_compute(_mock_redis):
    """Redis lock failure must not break the tool call."""
    mock_redis, _, _ = _mock_redis
    mock_redis.set.side_effect = ConnectionError("redis down")
    calls = 0

    @cached_tool(ttl=3600)
    def slow_tool(x: int):
        nonlocal calls
        calls += 1
        return {"r": x}

    result = slow_tool(x=9)
    assert result == {"r": 9}
    assert calls == 1


@pytest.mark.asyncio
async def test_singleflight_opt_out_computes_twice(_mock_redis):
    """singleflight=False preserves the old behavior."""
    calls = 0

    @cached_tool(ttl=3600, singleflight=False)
    async def slow_tool(x: int):
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.1)
        return {"r": x * 2}

    r1, r2 = await asyncio.gather(slow_tool(x=3), slow_tool(x=3))
    assert r1 == {"r": 6}
    assert r2 == {"r": 6}
    assert calls == 2


@pytest.mark.asyncio
async def test_cache_hit_path_unaffected(_mock_redis):
    """Warm cache returns immediately; no lock round-trips on the hit path."""
    calls = 0

    @cached_tool(ttl=3600)
    async def fast_tool(x: int):
        nonlocal calls
        calls += 1
        return {"r": x}

    assert await fast_tool(x=1) == {"r": 1}
    assert await fast_tool(x=1) == {"r": 1}  # hit
    assert calls == 1


# ---- #677 review corner: node-dense but byte-small args ----

def test_cache_key_none_when_ref_walk_budget_exhausted():
    """节点密集而字节小的 args（>20k 节点但 <256KB）不触发 oversized 短路，
    却会耗尽 _contains_ref 的节点预算 — 埋在预算之后的 ref: 叶子必须仍然
    导致跳过缓存（证明不了"无 ref"就不缓存），不得产出错误缓存键。"""
    from app.tools.registry import _ESTIMATE_MAX_NODES

    # ~21k 个 {"a": 1} ≈ 168KB（< 256KB 门限），节点数 ≈ 43k > 预算 20k。
    args = {"layers": [{"a": 1} for _ in range(_ESTIMATE_MAX_NODES + 1000)] + ["ref:session1/layer9"]}
    assert make_cache_key("node_dense_tool", args) is None


def test_cache_key_still_built_for_small_ref_free_args():
    """对照组：小而无 ref 的 args 照常产出缓存键（预算门不误伤正常路径）。"""
    key = make_cache_key("small_tool", {"geojson": {"type": "Point"}, "n": 1})
    assert key is not None and key.startswith("tool_cache:v1:")


def test_cache_key_none_for_shallow_ref_args():
    """常规浅层 ref 语义保持：任一叶子是 ref: 开头即跳过缓存。"""
    assert make_cache_key("shallow_ref_tool", {"source": "ref:abc", "n": 1}) is None
