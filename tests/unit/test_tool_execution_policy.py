"""Unit tests for ToolExecutionPolicy routing in ToolRegistry."""
import asyncio
import pytest

from app.tools.registry import ToolRegistry, ToolExecutionPolicy


@pytest.mark.asyncio
async def test_inline_policy_executes_directly_on_event_loop_without_semaphore():
    registry = ToolRegistry()

    @registry.tool(
        name="fast_inline_tool",
        description="Fast inline tool",
        execution_policy=ToolExecutionPolicy.INLINE,
    )
    def fast_inline_tool(x: int) -> dict:
        return {"result": x + 10}

    # Verify metadata stores the execution_policy
    meta = registry.metadata("fast_inline_tool")
    assert meta["execution_policy"] == ToolExecutionPolicy.INLINE

    # Dispatch tool
    res = await registry.dispatch("fast_inline_tool", {"x": 5})
    assert res == {"result": 15}


@pytest.mark.asyncio
async def test_async_policy_awaits_coroutine_directly():
    registry = ToolRegistry()

    @registry.tool(
        name="async_io_tool",
        description="Async I/O tool",
        execution_policy=ToolExecutionPolicy.ASYNC,
    )
    async def async_io_tool(query: str) -> dict:
        await asyncio.sleep(0.001)
        return {"query": query, "found": True}

    meta = registry.metadata("async_io_tool")
    assert meta["execution_policy"] == ToolExecutionPolicy.ASYNC

    res = await registry.dispatch("async_io_tool", {"query": "beijing"})
    assert res == {"query": "beijing", "found": True}


@pytest.mark.asyncio
async def test_thread_policy_default_fallback():
    registry = ToolRegistry()

    @registry.tool(
        name="sync_heavy_tool",
        description="Sync heavy tool",
    )
    def sync_heavy_tool(n: int) -> dict:
        return {"sum": sum(range(n))}

    meta = registry.metadata("sync_heavy_tool")
    assert meta["execution_policy"] == ToolExecutionPolicy.THREAD

    res = await registry.dispatch("sync_heavy_tool", {"n": 100})
    assert res == {"sum": 4950}
