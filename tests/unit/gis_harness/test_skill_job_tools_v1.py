"""G30 — Pi 技能作业工具面测试（注册 → 经 registry.dispatch 真实调用）。

场景 #20：Pi 经发现入口选择并运行 —— gis_skill_job_* 与 webgis_execute
同一条执行管线（registry.dispatch）；typed 回执（不是裸异常）、有界输出、
prepared 一次性消费。
"""
from __future__ import annotations

import pytest

from app.core.database import Base, get_engine


def _points(n: int = 12):
    return {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature",
             "geometry": {"type": "Point",
                          "coordinates": [104.0 + i * 0.01, 30.6 + i * 0.005]},
             "properties": {"name": f"p{i}", "score": i * 1.5}}
            for i in range(n)
        ],
    }


@pytest.fixture()
def registry():
    from app.tools import init_tools
    from app.tools.registry import ToolRegistry
    from app.agent_pi_bridge import set_tool_registry

    reg = ToolRegistry()
    init_tools(reg)
    set_tool_registry(reg)
    return reg


@pytest.fixture(autouse=True)
def _tables():
    Base.metadata.create_all(bind=get_engine())
    yield


@pytest.fixture(autouse=True)
def _rt_ctx():
    """绑定 runtime ctx（工具内 session 解析用；Pi 端由回调上下文提供）。"""
    from app.lib.runtime.context import bind_runtime_context

    with bind_runtime_context(session_id="t-tools-1"):
        yield


@pytest.mark.asyncio
async def test_tools_registered_on_pi_surface(registry):
    """注册面可见（tool_search/list_available_tools 可发现 → webgis_execute 可呼）。"""
    from app.services.chat.pi_native_surface import registered_surface_names

    names = set(registered_surface_names(registry))
    assert {"gis_skill_job_prepare", "gis_skill_job_start",
            "gis_skill_job_status"} <= names


@pytest.mark.asyncio
async def test_prepare_start_status_via_dispatch(registry):
    from app.services.session_data import session_data_manager

    ref = await session_data_manager.store("t-tools-1", _points(), prefix="geojson")

    async def call(tool: str, args: dict) -> dict:
        return await registry.dispatch(tool, args, session_id="t-tools-1")

    # prepare（typed 参数 + 绑定）
    r1 = await call("gis_skill_job_prepare", {
        "skill_id": "validate_spatial_data",
        "params": {},
        "bindings": [{"role": "subject", "ref": ref}],
        "session_id": "t-tools-1",
    })
    assert r1.get("success") is True, r1
    assert r1["prepared_id"].startswith("skp-")
    assert r1["preview"]["evidence_covered"]
    # start（有界等待 → completed）
    r2 = await call("gis_skill_job_start", {
        "prepared_id": r1["prepared_id"], "session_id": "t-tools-1",
        "wait_timeout_s": 60,
    })
    assert r2.get("success") is True, r2
    assert r2["status"] == "completed"
    assert r2["result_ref"].startswith("ref:skillreport-")
    # status（job 定位 + 会话列表）
    r3 = await call("gis_skill_job_status", {
        "job_id": r2["job_id"], "session_id": "t-tools-1"})
    assert r3["success"] is True
    assert r3["run"]["status"] == "completed"
    r4 = await call("gis_skill_job_status", {"session_id": "t-tools-1"})
    assert r4["success"] is True
    assert any(x["job_id"] == r2["job_id"] for x in r4["runs"])


@pytest.mark.asyncio
async def test_typed_rejections_are_receipts_not_exceptions(registry):
    """typed 拒绝 = 结构化回执（Pi 模型可读 code），不是 500/裸异常。"""
    r1 = await registry.dispatch("gis_skill_job_prepare", {
        "skill_id": "no_such_skill", "params": {}, "bindings": [],
        "session_id": "t-tools-1",
    }, session_id="t-tools-1")
    assert r1.get("success") is False
    assert r1.get("code") == "SKILL_NOT_FOUND"

    r2 = await registry.dispatch("gis_skill_job_start", {
        "prepared_id": "skp-nonexistent", "session_id": "t-tools-1",
    }, session_id="t-tools-1")
    assert r2.get("success") is False
    assert r2.get("code") == "PREPARED_EXPIRED"


@pytest.mark.asyncio
async def test_selection_evidence_recorded(registry):
    """prepare 记录 skill_selected 选择证据（审计面；ADR-0220 缺口接线）。"""
    from app.services.session_data import session_data_manager
    from app.tools.skill_library_tools import (
        get_skill_evidence_recorder,
        reset_skill_evidence_recorder,
    )

    reset_skill_evidence_recorder()
    ref = await session_data_manager.store("t-tools-1", _points(), prefix="geojson")
    await registry.dispatch("gis_skill_job_prepare", {
        "skill_id": "validate_spatial_data", "params": {},
        "bindings": [{"role": "subject", "ref": ref}],
        "session_id": "t-tools-1",
    }, session_id="t-tools-1")
    events = [r.to_bounded_dict() for r in
              get_skill_evidence_recorder()._records]
    assert any(e["event"] == "skill_selected"
               and e["skill_id"] == "validate_spatial_data"
               and "explicit_user_selection" in e["selection_reason"]
               for e in events)
