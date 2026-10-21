"""Workflow V6 — hooks fail-open 纪律（P4 补强：W4 hooks.py 全文件 0 测试）。

边界纪律（架构 §8）：挂钩绝不阻断/改变既有会话行为 —— 任何异常只记日志；
GIS_WORKFLOW_RUNTIME 开关统一门控，关闭零回归。
"""
from __future__ import annotations

import pytest

from app.services.workflow_runtime import hooks as HK


@pytest.mark.asyncio
async def test_attach_plan_noop_when_runtime_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GIS_WORKFLOW_RUNTIME", "0")
    # 关闭态：不查 owner、不触碰 service —— 直接返回。
    called = {"n": 0}

    async def _boom(*a, **k):  # noqa: ANN002, ANN003
        called["n"] += 1

    monkeypatch.setattr(HK, "owner_scope_for_session", _boom)
    await HK.attach_plan_safe("s1", query="q", recipe_id="r")
    assert called["n"] == 0


@pytest.mark.asyncio
async def test_attach_plan_swallow_service_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GIS_WORKFLOW_RUNTIME", "1")
    # fail-open：service 异常只记日志，绝不向规划路径抛出。
    called = {"n": 0}

    class _S:
        @staticmethod
        async def attach_session_plan(*a, **k):  # noqa: ANN002, ANN003
            called["n"] += 1
            raise RuntimeError("service exploded")

    monkeypatch.setattr(HK, "get_service", lambda: _S())
    await HK.attach_plan_safe("s1", query="q", recipe_id="r", owner_scope="u:x")
    # 服务确被调用过一次且异常被吞（而非中途短路）——纪律可观测。
    assert called["n"] == 1


@pytest.mark.asyncio
async def test_record_tool_result_noop_without_plan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GIS_WORKFLOW_RUNTIME", "1")
    # 无会话计划 → 直接返回（不触碰 service）。
    plan_loads = {"n": 0}
    service_touches = {"n": 0}

    def _counting_service():
        service_touches["n"] += 1
        return None

    monkeypatch.setitem(
        sys_modules(), "app.services.session_plan", _FakePlanModule(plan_loads)
    )
    monkeypatch.setattr(HK, "get_service", _counting_service)
    await HK.record_tool_result_safe("s1", tool_name="buffer", geojson_ref="ref:x")
    # 计划确被查询过一次（路径走到位），service 零触碰。
    assert plan_loads["n"] == 1
    assert service_touches["n"] == 0


def _FakePlanModule(loads=None):
    import types

    mod = types.ModuleType("app.services.session_plan")

    async def load_session_plan(session_id):
        if loads is not None:
            loads["n"] += 1
        return None

    def capabilities_hit_by_tool(plan, tool_name):
        return []

    mod.load_session_plan = load_session_plan
    mod.capabilities_hit_by_tool = capabilities_hit_by_tool
    return mod


def sys_modules():
    import sys

    return sys.modules


@pytest.mark.asyncio
async def test_record_style_change_swallow_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GIS_WORKFLOW_RUNTIME", "1")
    called = {"n": 0}

    class _S:
        @staticmethod
        async def record_style_change(*a, **k):  # noqa: ANN002, ANN003
            called["n"] += 1
            raise RuntimeError("boom")

    monkeypatch.setattr(HK, "get_service", lambda: _S())
    await HK.record_style_change_safe("s1", owner_scope="u:x", target="layer:1")
    # 服务确被调用过一次且异常被吞——纪律可观测。
    assert called["n"] == 1


@pytest.mark.asyncio
async def test_record_tool_result_noop_when_runtime_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GIS_WORKFLOW_RUNTIME", "0")
    # 关闭态：连 owner 查询都不发生——直接返回。
    called = {"n": 0}

    async def _boom(*a, **k):  # noqa: ANN002, ANN003
        called["n"] += 1

    monkeypatch.setattr(HK, "owner_scope_for_session", _boom)
    await HK.record_tool_result_safe("s1", tool_name="buffer", geojson_ref=None)
    assert called["n"] == 0
