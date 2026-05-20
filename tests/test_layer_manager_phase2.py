"""Phase 2: reorder_layer / remove_layer 工具测试"""
import pytest

from app.tools.registry import ToolRegistry
from app.tools.layer_manager import register_layer_management_tools
from app.services.session_data import session_data_manager


@pytest.fixture
def registry():
    r = ToolRegistry()
    register_layer_management_tools(r)
    return r


@pytest.fixture
def session_with_layer():
    sid = "test-phase2-session"
    ref = session_data_manager.store(sid, {"type": "FeatureCollection", "features": []}, prefix="t")
    session_data_manager.set_alias(sid, ref, "我的层")
    yield sid, ref
    session_data_manager.clear_session(sid)


@pytest.mark.asyncio
async def test_reorder_layer_emits_command(registry, session_with_layer):
    sid, ref = session_with_layer
    out = await registry.dispatch("reorder_layer", {"layer_ref": "我的层", "position": "top"}, session_id=sid)
    assert out["success"] is True
    assert out["command"] == "REORDER_LAYER"
    assert out["params"]["layer_id"] == ref
    assert out["params"]["position"] == "top"
    assert out["params"]["before_id"] is None


@pytest.mark.asyncio
async def test_reorder_layer_rejects_bad_position(registry, session_with_layer):
    sid, _ = session_with_layer
    out = await registry.dispatch("reorder_layer", {"layer_ref": "我的层", "position": "sideways"}, session_id=sid)
    assert "error" in out


@pytest.mark.asyncio
async def test_reorder_before_requires_before_ref(registry, session_with_layer):
    sid, _ = session_with_layer
    out = await registry.dispatch("reorder_layer", {"layer_ref": "我的层", "position": "before"}, session_id=sid)
    assert "error" in out
    assert "before_ref" in out["error"]


@pytest.mark.asyncio
async def test_reorder_before_resolves_alias(registry, session_with_layer):
    sid, ref = session_with_layer
    other = session_data_manager.store(sid, {"features": []}, prefix="o")
    session_data_manager.set_alias(sid, other, "底图")
    out = await registry.dispatch(
        "reorder_layer",
        {"layer_ref": "我的层", "position": "before", "before_ref": "底图"},
        session_id=sid,
    )
    assert out["success"] is True
    assert out["params"]["before_id"] == other


@pytest.mark.asyncio
async def test_remove_layer_emits_command(registry, session_with_layer):
    sid, ref = session_with_layer
    out = await registry.dispatch("remove_layer", {"layer_ref": "我的层"}, session_id=sid)
    assert out["success"] is True
    assert out["command"] == "REMOVE_LAYER"
    assert out["params"]["layer_id"] == ref


@pytest.mark.asyncio
async def test_remove_layer_requires_session(registry):
    out = await registry.dispatch("remove_layer", {"layer_ref": "x"})
    assert "error" in out
