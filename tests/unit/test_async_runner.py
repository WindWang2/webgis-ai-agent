"""audit ISSUE-056（#1350）回归：共享 sync→async 桥。

- 每线程持久 loop：同一 worker 线程内跨调用复用同一事件循环
- 已运行 loop 内调用 → RuntimeError（不嵌套）
- coroutine 结果原样返回
"""
import asyncio
import threading

import pytest

from app.core.async_runner import run_sync


async def _get_loop():
    return asyncio.get_running_loop()


async def _value(x):
    return x


def test_run_sync_returns_result():
    assert run_sync(_value(42)) == 42


def test_run_sync_reuses_persistent_loop_in_thread():
    loop_a = run_sync(_get_loop())
    loop_b = run_sync(_get_loop())
    assert loop_a is loop_b
    assert loop_a.is_running() is False  # 持久但不在 run 态


def test_run_sync_distinct_loops_per_thread():
    main_loop = run_sync(_get_loop())
    worker_loops = []

    def _worker():
        worker_loops.append(run_sync(_get_loop()))

    t = threading.Thread(target=_worker)
    t.start()
    t.join()
    assert worker_loops[0] is not main_loop


def test_run_sync_rejects_running_loop():
    async def _inside():
        coro = _value(1)
        with pytest.raises(RuntimeError, match="running event loop"):
            run_sync(coro)
        coro.close()

    asyncio.run(_inside())


# ── #1437：run_sync 桥接协程的 per-thread NullPool async 引擎隔离 ──────


def test_thread_async_engine_is_per_thread_and_null_pool():
    """每线程一个独立 engine；NullPool 保证 asyncpg/aiosqlite 连接绝不跨
    loop 复用（全局 QueuePool 会把主 loop 的连接交给线程 loop →
    'Future attached to a different loop'）。"""
    from sqlalchemy.pool import NullPool

    from app.core.async_runner import get_thread_async_engine

    eng_main = get_thread_async_engine()
    assert isinstance(eng_main.pool, NullPool)
    assert get_thread_async_engine() is eng_main  # 同线程复用

    engines: list = []

    def _worker():
        engines.append(get_thread_async_engine())

    t = threading.Thread(target=_worker)
    t.start()
    t.join()
    assert engines[0] is not eng_main  # 跨线程绝不共享


def test_thread_async_session_runs_on_thread_loop():
    """thread_async_session 在线程持久 loop 上可用且绑定本线程缓存引擎。"""
    from sqlalchemy import text

    from app.core.async_runner import (
        get_thread_async_engine,
        run_sync,
        thread_async_session,
    )

    async def _use():
        async with thread_async_session() as db:
            assert db.bind is get_thread_async_engine()
            await db.execute(text("SELECT 1"))
        return "ok"

    assert run_sync(_use()) == "ok"
