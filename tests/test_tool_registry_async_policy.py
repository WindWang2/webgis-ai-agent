"""Regression tests for GH-374: THREAD/CELERY policies must await async-def tools.

旧实现把 THREAD 与 CELERY 策略都路由到 _execute_sync_in_thread，后者在线程
里「同步」调用 tool_func——async def 工具被当普通对象调用，dispatch 返回
未 await 的 coroutine，下游 json.dumps 抛
``TypeError: Object of type coroutine is not JSON serializable``。

修复分两层：
1. 注册期（ToolRegistry.register）：async def + 显式 THREAD/CELERY 自动按
   函数类型路由到 ASYNC（拒绝会破坏 network/temporal 既有注册）。
2. 派发期（_dispatch_impl._execute_tool）：无论策略元数据如何，async 函数
   一律直接 await（兜底防御，覆盖旧数据/直接改 _metadata 的场景）。
"""
import asyncio
import functools
import inspect
import json

import pytest

from app.tools.registry import ToolExecutionPolicy, ToolRegistry
from app.tools.network_tools import register_network_tools
from app.tools.temporal_tools import register_temporal_tools


# network/temporal 中所有以 async def 注册、却声明 THREAD/CELERY 策略的工具。
# 这些注册在修复前全部返回未 await 的 coroutine，dispatch 结果无法序列化。
ASYNC_DEF_THREAD_CELERY_TOOLS = [
    # network_tools.py: THREAD (tier 2)
    "network_shortest_path",
    "network_od_matrix",
    "network_closest_facility",
    "network_service_area",
    "network_accessibility",
    # network_tools.py: CELERY (tier 3)
    "location_allocation",
    "optimize_route",
    # temporal_tools.py: THREAD (tier 2)
    "temporal_profile",
    "temporal_filter",
    "temporal_aggregate",
    "temporal_change",
    "temporal_trend",
    # temporal_tools.py: CELERY (tier 3)
    "spatiotemporal_hotspot",
]


def _build_network_temporal_registry() -> ToolRegistry:
    reg = ToolRegistry()
    register_network_tools(reg)
    register_temporal_tools(reg)
    return reg


@pytest.mark.parametrize("tool_name", ASYNC_DEF_THREAD_CELERY_TOOLS)
def test_async_def_tool_auto_routed_away_from_thread_policy(tool_name):
    """注册期校验：async def + THREAD/CELERY 必须被自动路由到 ASYNC。

    若策略仍是 THREAD/CELERY，dispatch 会把 coroutine 当普通对象同步调用，
    返回未 await 的 coroutine。同类审计失误曾在 spatial_decision_tools 修复
    （改注册策略），network/temporal 未覆盖——这里锁死注册期行为。
    """
    reg = _build_network_temporal_registry()
    func = reg._tools[tool_name]
    assert inspect.iscoroutinefunction(func), f"{tool_name} 应为 async def"
    policy = reg.metadata(tool_name)["execution_policy"]
    assert policy == ToolExecutionPolicy.ASYNC, (
        f"{tool_name} 注册策略应被自动路由为 ASYNC，实际为 {policy}"
    )


@pytest.mark.parametrize("tool_name", ASYNC_DEF_THREAD_CELERY_TOOLS)
async def test_dispatch_never_returns_coroutine(tool_name):
    """真实 registry dispatch 不返回 coroutine，且结果可 JSON 序列化。

    空参数下这些工具在 Pydantic 校验处返回 VALIDATION_ERROR dict；断言
    返回值不是 coroutine 对象（修复前 THREAD/CELERY 分支在参数合法的执行
    路径里同步调用 async def 并原样返回 coroutine，直接 json.dumps 必炸）。
    """
    reg = _build_network_temporal_registry()
    result = await reg.dispatch(tool_name, {}, session_id=None)
    assert not inspect.iscoroutine(result)
    assert not isinstance(result, asyncio.Future)
    json.dumps(result)  # 不得抛 TypeError: coroutine is not JSON serializable


async def test_async_def_with_explicit_thread_policy_executes_and_awaits():
    """最小复现用例：async def + 显式 THREAD 策略必须返回 await 后的结果。

    修复前：to_thread 同步调用 async def 返回 coroutine 对象；修复后：
    注册期自动路由为 ASYNC，直接 await，结果可序列化。
    """
    reg = ToolRegistry()

    @reg.tool(
        name="async_thread_tool",
        description="async def registered with THREAD policy",
        execution_policy=ToolExecutionPolicy.THREAD,
    )
    async def async_thread_tool(x: int = 1):
        await asyncio.sleep(0)
        return {"value": x * 2}

    assert reg.metadata("async_thread_tool")["execution_policy"] == ToolExecutionPolicy.ASYNC
    result = await reg.dispatch("async_thread_tool", {"x": 21})
    assert result == {"value": 42}
    json.dumps(result)


async def test_async_def_with_explicit_celery_policy_executes_and_awaits():
    """最小复现用例：async def + 显式 CELERY 策略同样必须 await。"""
    reg = ToolRegistry()

    @reg.tool(
        name="async_celery_tool",
        description="async def registered with CELERY policy",
        execution_policy=ToolExecutionPolicy.CELERY,
    )
    async def async_celery_tool(x: int = 1):
        await asyncio.sleep(0)
        return {"value": x}

    assert reg.metadata("async_celery_tool")["execution_policy"] == ToolExecutionPolicy.ASYNC
    result = await reg.dispatch("async_celery_tool", {"x": 7})
    assert result == {"value": 7}


async def test_functools_partial_wrapped_async_def_still_detected():
    """functools.partial 包装的 async def 也要被识别为协程函数。

    asyncio.iscoroutinefunction 会解包 partial（Python 3.8+），注册期
    自动路由与派发期兜底分支都必须走 await 路径。
    """
    reg = ToolRegistry()

    async def _inner(x: int = 1):
        await asyncio.sleep(0)
        return {"partial": x}

    reg.register(
        "partial_async",
        "partial-wrapped async tool",
        functools.partial(_inner),
        execution_policy=ToolExecutionPolicy.THREAD,
    )
    assert reg.metadata("partial_async")["execution_policy"] == ToolExecutionPolicy.ASYNC
    result = await reg.dispatch("partial_async", {"x": 5})
    assert result == {"partial": 5}


async def test_dispatch_defends_when_policy_metadata_tampered_to_thread():
    """防御分支：策略元数据与函数类型不符时仍按函数类型正确执行。

    注册期自动路由只保护经过 register() 的路径；旧元数据/直接改 _metadata
    的场景由 _dispatch_impl 的按函数类型兜底分支覆盖——async 工具仍 await，
    而不是在线程里同步调用后返回 coroutine。
    """
    reg = ToolRegistry()

    @reg.tool(name="guarded_async", description="defensive branch probe")
    async def guarded_async(x: int = 1):
        await asyncio.sleep(0)
        return {"x": x}

    # 模拟修复前/旧数据：把策略元数据改回 THREAD，绕过注册期自动路由
    reg._metadata["guarded_async"]["execution_policy"] = ToolExecutionPolicy.THREAD
    result = await reg.dispatch("guarded_async", {"x": 3})
    assert result == {"x": 3}
    assert not inspect.iscoroutine(result)
    json.dumps(result)
