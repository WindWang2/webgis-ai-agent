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
