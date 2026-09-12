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
    async def _boom(*a, **k):  # noqa: ANN002, ANN003
        raise RuntimeError("service exploded")

    monkeypatch.setattr(HK, "get_service", lambda: type("S", (), {
        "attach_session_plan": staticmethod(_boom)})())

    class _S:
        @staticmethod
        async def attach_session_plan(*a, **k):  # noqa: ANN002, ANN003
            raise RuntimeError("service exploded")

    monkeypatch.setattr(HK, "get_service", lambda: _S())
    # 不抛 = 纪律成立。
    await HK.attach_plan_safe("s1", query="q", recipe_id="r", owner_scope="u:x")


@pytest.mark.asyncio
async def test_record_tool_result_noop_without_plan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GIS_WORKFLOW_RUNTIME", "1")
    # 无会话计划 → 直接返回（不触碰 service）。
    async def _noop_plan(session_id):
        return None

    monkeypatch.setitem(sys_modules(), "app.services.session_plan", _FakePlanModule())
    await HK.record_tool_result_safe("s1", tool_name="buffer", geojson_ref="ref:x")


def _FakePlanModule():
    import types

    mod = types.ModuleType("app.services.session_plan")

    async def load_session_plan(session_id):
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
    class _S:
        @staticmethod
        async def record_style_change(*a, **k):  # noqa: ANN002, ANN003
            raise RuntimeError("boom")

    monkeypatch.setattr(HK, "get_service", lambda: _S())
    await HK.record_style_change_safe("s1", owner_scope="u:x", target="layer:1")


@pytest.mark.asyncio
async def test_record_tool_result_noop_when_runtime_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GIS_WORKFLOW_RUNTIME", "0")
    await HK.record_tool_result_safe("s1", tool_name="buffer", geojson_ref=None)
