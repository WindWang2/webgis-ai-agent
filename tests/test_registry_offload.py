"""Tests that sync CPU-bound tools are offloaded off the asyncio event loop.

Regression guard for the Phase 3 perf optimization: a sync (def) tool that
sleeps must NOT block the event loop — other coroutines must keep running
concurrently while the tool executes in the thread pool.
"""
import asyncio
import threading
import time


from app.tools.registry import ToolRegistry


def test_sync_tool_does_not_block_event_loop():
    """A sync tool sleeping 0.2s must allow a concurrent ticker to keep ticking.

    Before the offload: the sync call ran inline, so the ticker would freeze
    for the full 0.2s (0 ticks during the tool). After: the tool runs in a
    thread, the event loop stays free, and the ticker advances.
    """
    reg = ToolRegistry()

    def slow_sync_tool(seconds: float = 0.2):
        time.sleep(seconds)  # sync, blocking
        return {"ok": True}

    reg.register("slow_sync", "sleeps synchronously", slow_sync_tool)

    async def main():
        tick_count = 0

        async def ticker():
            nonlocal tick_count
            while True:
                await asyncio.sleep(0.02)
                tick_count += 1

        ticker_task = asyncio.create_task(ticker())
        try:
            await reg.dispatch("slow_sync", {"seconds": 0.2})
            # let the ticker settle a couple more ticks after the tool returns
            await asyncio.sleep(0.05)
        finally:
            ticker_task.cancel()
            try:
                await ticker_task
            except asyncio.CancelledError:
                pass
        return tick_count

    ticks = asyncio.run(main())
    # If the event loop had been blocked, ticks would be ~0 during the 0.2s sleep.
    # With offload, the ticker runs concurrently. Assert a comfortable floor.
    assert ticks >= 5, f"event loop was blocked during sync tool (ticks={ticks})"


def test_sync_tool_returns_result_correctly():
    """Result payload must round-trip through to_thread unchanged."""
    reg = ToolRegistry()

    def echo_tool(x: int, y: int):
        return {"sum": x + y, "product": x * y}

    reg.register("echo", "adds and multiplies", echo_tool)

    async def main():
        return await reg.dispatch("echo", {"x": 3, "y": 4})

    out = asyncio.run(main())
    assert out == {"sum": 7, "product": 12}


def test_sync_tool_exception_surfaces_as_error_response():
    """A raised ValueError must still map to the VALIDATION_ERROR response."""
    reg = ToolRegistry()

    def boom(x: int):
        raise ValueError("bad input")

    reg.register("boom", "always raises", boom)

    async def main():
        return await reg.dispatch("boom", {"x": 1})

    out = asyncio.run(main())
    assert out["success"] is False
    assert out["code"] == "VALIDATION_ERROR"


def test_async_tool_still_awaited_directly():
    """Async tools bypass to_thread (they're already non-blocking)."""
    reg = ToolRegistry()

    async def async_tool(x: int):
        await asyncio.sleep(0)
        return {"async_sum": x + 1}

    reg.register("async_tool", "async adder", async_tool)

    async def main():
        return await reg.dispatch("async_tool", {"x": 41})

    out = asyncio.run(main())
    assert out == {"async_sum": 42}


def test_sync_tool_concurrency_is_bounded():
    """Parallel sync tools must not exceed the thread limit (GIL protection)."""
    import asyncio as _asyncio
    import threading

    import app.tools.registry as reg_mod
    from app.tools.registry import ToolRegistry

    limit = 2
    orig_semaphore = reg_mod._tool_thread_semaphore
    orig_limit = reg_mod._TOOL_THREAD_LIMIT
    reg_mod._TOOL_THREAD_LIMIT = limit
    reg_mod._tool_thread_semaphore = _asyncio.Semaphore(limit)
    try:
        reg = ToolRegistry()
        state = {"running": 0, "peak": 0}
        state_lock = threading.Lock()

        def slow_sync_tool():
            with state_lock:
                state["running"] += 1
                state["peak"] = max(state["peak"], state["running"])
            time.sleep(0.15)
            with state_lock:
                state["running"] -= 1
            return {"ok": True}

        reg.register("slow_sync", "slow", slow_sync_tool)

        async def main():
            await _asyncio.gather(
                *[reg.dispatch("slow_sync", {}, session_id=None) for _ in range(4)]
            )
            return state["peak"]

        peak = _asyncio.run(main())
        assert peak <= limit, f"peak concurrent sync tools {peak} > limit {limit}"
    finally:
        reg_mod._tool_thread_semaphore = orig_semaphore
        reg_mod._TOOL_THREAD_LIMIT = orig_limit


# ---------------------------------------------------------------------------
# GH-406: per-tool wall-time budget + semaphore accounting across cancellation.
# ---------------------------------------------------------------------------


def test_hung_sync_tool_returns_timeout_result_within_budget():
    """A hung sync tool must return a timeout error result within the budget.

    Before: dispatch had no timeout at all — a dead tool occupied the turn
    forever. After: each tool runs under a wall-time budget
    (_TOOL_TIMEOUT_S, env TOOL_TIMEOUT_S, or per-tool metadata 'timeout').
    """
    import app.tools.registry as reg_mod

    reg = ToolRegistry()
    release = threading.Event()

    def forever_tool():
        release.wait(timeout=30)  # hangs until the test lets it go
        return {"never": True}

    reg.register("forever", "hangs", forever_tool)

    orig_timeout = reg_mod._TOOL_TIMEOUT_S
    reg_mod._TOOL_TIMEOUT_S = 0.3
    try:
        async def main():
            start = time.monotonic()
            out = await reg.dispatch("forever", {})
            elapsed = time.monotonic() - start
            # 放行后台泄漏线程：asyncio.run 收尾时 shutdown_default_executor
            # 会 join 所有 worker，不放行测试会挂 30s。
            release.set()
            return out, elapsed

        out, elapsed = asyncio.run(main())
    finally:
        reg_mod._TOOL_TIMEOUT_S = orig_timeout
        release.set()

    # #703：删墙钟断言（满载假红来源）。code == TOOL_TIMEOUT 已完整证明
    # 超时机制生效——若机制退化，forever_tool 会在 30s 后正常返回
    # success=True，被下面的行为断言抓住；elapsed 只影响诊断速度。
    assert out["success"] is False
    assert out["code"] == "TOOL_TIMEOUT"
    assert "超时" in out["message"]


def test_timeout_metadata_overrides_global_budget():
    """Per-tool metadata 'timeout' must take precedence over the global budget."""
    import app.tools.registry as reg_mod

    reg = ToolRegistry()
    release = threading.Event()

    def forever_tool():
        release.wait(timeout=30)
        return {"never": True}

    # 全局预算故意给得很长；工具级 timeout 很短 → 必须按工具级超时返回
    reg.register("forever", "hangs", forever_tool, timeout=0.3)

    orig_timeout = reg_mod._TOOL_TIMEOUT_S
    reg_mod._TOOL_TIMEOUT_S = 60.0
    try:
        async def main():
            start = time.monotonic()
            out = await reg.dispatch("forever", {})
            elapsed = time.monotonic() - start
            release.set()
            return out, elapsed

        out, elapsed = asyncio.run(main())
    finally:
        reg_mod._TOOL_TIMEOUT_S = orig_timeout
        release.set()

    # #703：删墙钟断言（同 test_timeout_kills_hung_dispatch——满载假红来源，
    # code == TOOL_TIMEOUT 行为断言已完整覆盖机制）。
    assert out["success"] is False
    assert out["code"] == "TOOL_TIMEOUT"


def test_cancellation_keeps_semaphore_until_thread_actually_finishes():
    """Cancellation must not release the semaphore before the thread ends.

    Before: cancelling `await to_thread(...)` ran the `async with` __aexit__
    immediately, returning the slot while the worker thread kept running —
    the semaphore stopped reflecting real thread occupancy, so a wave of
    hung tools would pile up threads beyond _TOOL_THREAD_LIMIT. After: the
    slot is returned by a done callback when the thread truly finishes;
    the abandoned call is counted in _tool_thread_leaked_count.
    """
    import asyncio as _asyncio

    import app.tools.registry as reg_mod

    limit = 1
    orig_semaphore = reg_mod._tool_thread_semaphore
    orig_leaked = reg_mod._tool_thread_leaked_count
    reg_mod._tool_thread_semaphore = _asyncio.Semaphore(limit)
    started = threading.Event()
    release = threading.Event()
    try:
        reg = ToolRegistry()

        def blocker():
            started.set()
            release.wait(timeout=30)
            return {"done": True}

        reg.register("blocker", "blocks", blocker)

        async def main():
            task = _asyncio.create_task(reg.dispatch("blocker", {}))
            # 等 worker 线程真正开始运行后再取消
            for _ in range(500):
                if started.is_set():
                    break
                await _asyncio.sleep(0.01)
            assert started.is_set(), "worker 线程未启动"
            task.cancel()
            try:
                await task
            except _asyncio.CancelledError:
                pass
            # 取消已送达，但线程仍在跑 → 信号量必须仍然被占用
            assert reg_mod._tool_thread_semaphore.locked(), (
                "取消后信号量被提前释放——槽位不再反映真实线程占用"
            )
            # 放行线程；线程结束后槽位由 done 回调归还
            release.set()
            for _ in range(500):
                if not reg_mod._tool_thread_semaphore.locked():
                    break
                await _asyncio.sleep(0.01)
            assert not reg_mod._tool_thread_semaphore.locked(), (
                "线程结束后信号量槽位未归还"
            )
            assert reg_mod._tool_thread_leaked_count == orig_leaked + 1, (
                "被取消的线程未计入泄漏计数"
            )

        _asyncio.run(main())
    finally:
        reg_mod._tool_thread_semaphore = orig_semaphore
        release.set()


def test_async_tool_under_thread_policy_dispatches():
    """GH-374/406 cross-check: async def + THREAD policy must dispatch fine.

    The thread-path fixes must not break async tools that were (incorrectly)
    declared with THREAD policy — they are awaited directly, never handed to
    to_thread, so no semaphore slot is consumed and no coroutine leaks out.
    """
    reg = ToolRegistry()

    @reg.tool(
        name="async_under_thread",
        description="async def declared as THREAD",
        execution_policy="thread",
    )
    async def async_under_thread(x: int = 1):
        await asyncio.sleep(0)
        return {"value": x + 1}

    async def main():
        return await reg.dispatch("async_under_thread", {"x": 1})

    out = asyncio.run(main())
    assert out == {"value": 2}
