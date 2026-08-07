"""Tests that sync CPU-bound tools are offloaded off the asyncio event loop.

Regression guard for the Phase 3 perf optimization: a sync (def) tool that
sleeps must NOT block the event loop — other coroutines must keep running
concurrently while the tool executes in the thread pool.
"""
import asyncio
import time

import pytest

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
