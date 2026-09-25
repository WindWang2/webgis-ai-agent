"""webgis_compile_map_plan 工具面测试（F12 / ADR-0214 D7）。

锁定：additive 注册进 ToolRegistry；compile_and_apply / finalize 两个
操作经真实会话端到端可用；bounded summary 形状稳定。
"""
import shutil
import uuid

import pytest

from app.services.session_data import session_data_manager


@pytest.fixture
async def session():
    sid = f"f12-tool-{uuid.uuid4().hex[:8]}"
    await session_data_manager.clear_session(sid)
    yield sid
    await session_data_manager.clear_session(sid)
    from app.services.mapspec.store import BASE_STORAGE_DIR
    shutil.rmtree(BASE_STORAGE_DIR / sid, ignore_errors=True)


def _tool_fn():
    from app.tools.map_plan_tools import register_map_plan_tools
    from app.tools.registry import ToolRegistry

    registry = ToolRegistry()
    register_map_plan_tools(registry)
    assert "webgis_compile_map_plan" in registry.tool_names()
    return registry._tools["webgis_compile_map_plan"]


@pytest.mark.cartography
@pytest.mark.asyncio
async def test_compile_and_apply_end_to_end(session):
    from app.services.mapspec.lifecycle_engine import (
        InitProjectIntent,
        MapSpecLifecycleEngine,
        UpsertSourceIntent,
    )
    from app.services.map_plan_compiler.receipt import PLAN_RECEIPTS_KEY

    engine = MapSpecLifecycleEngine()
    assert not (await engine.apply_mutation(session, InitProjectIntent())).is_error
    for src in ("src:main", "src:boundary"):
        assert not (await engine.apply_mutation(
            session, UpsertSourceIntent(source_id=src,
                                        source={"type": "geojson"}))).is_error

    fn = _tool_fn()
    summary = await fn(
        query="制作湖北省人口密度分级设色图",
        session_id=session,
        amendments=[{"kind": "add_chart", "chart_type": "chart_panel",
                     "title": "人口结构", "layer_id": "pl-li-01-primary"}],
    )
    assert summary["success"] is True, summary
    assert summary["apply_status"] == "applied"
    assert summary["mutation_count"] >= 1
    assert summary["receipt_id"].startswith("mpr-")
    assert summary["decision_id"].startswith("dec_")
    assert summary["finalization"]["status"] in ("confirmed", "unconfirmed")

    # finalize 操作：从回执环重建期望面并对账
    fin = await fn(query="x" * 4, session_id=session, operation="finalize")
    assert fin["success"] == (fin["finalization"]["status"] == "confirmed")
    assert isinstance(fin["finalization"]["rows"], list)


@pytest.mark.cartography
@pytest.mark.asyncio
async def test_finalize_without_receipt_fails_cleanly(session):
    fn = _tool_fn()
    out = await fn(query="test", session_id=session, operation="finalize")
    assert out["success"] is False
    assert "回执" in out["error"]


@pytest.mark.cartography
@pytest.mark.asyncio
async def test_missing_session_id_and_bad_amendment_fail_closed():
    fn = _tool_fn()
    out = await fn(query="制作一张地图")
    assert out["success"] is False
    assert "session_id" in out["error"]


@pytest.mark.cartography
@pytest.mark.asyncio
async def test_illegal_amendment_rejected(session):
    from app.services.mapspec.lifecycle_engine import InitProjectIntent, MapSpecLifecycleEngine
    engine = MapSpecLifecycleEngine()
    await engine.apply_mutation(session, InitProjectIntent())
    fn = _tool_fn()
    out = await fn(query="制作湖北省人口密度分级设色图", session_id=session,
                   amendments=[{"kind": "teleport"}])
    assert out["success"] is False
    assert "amendments 非法" in out["error"]
