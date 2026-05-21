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


# ─── /review P1-6 regression: prefix-match wipe defense ─────────────────


@pytest.mark.asyncio
async def test_reorder_layer_rejects_empty_layer_ref(registry, session_with_layer):
    """Empty layer_ref would prefix-match every custom-* sublayer on the
    frontend, wiping the whole map's z-order."""
    sid, _ref = session_with_layer
    out = await registry.dispatch(
        "reorder_layer",
        {"layer_ref": "", "position": "top"},
        session_id=sid,
    )
    assert "error" in out, f"expected reject, got {out}"
    assert "不能为空" in out["error"]


@pytest.mark.asyncio
async def test_reorder_layer_rejects_unknown_layer_ref(registry, session_with_layer):
    """An LLM-emitted ref that doesn't exist in this session must be rejected
    rather than passed through to the frontend's prefix-match handler. This
    catches short refs like 'ref:' that resolve to themselves."""
    sid, _ref = session_with_layer
    for bad in ["ref:", "abc", "ref:does-not-exist-xyz"]:
        out = await registry.dispatch(
            "reorder_layer",
            {"layer_ref": bad, "position": "top"},
            session_id=sid,
        )
        assert "error" in out, f"expected reject for layer_ref={bad!r}, got {out}"
        assert "未在当前会话" in out["error"]


@pytest.mark.asyncio
async def test_reorder_layer_rejects_unknown_before_ref(registry, session_with_layer):
    sid, ref = session_with_layer
    out = await registry.dispatch(
        "reorder_layer",
        {"layer_ref": ref, "position": "before", "before_ref": "ref:unknown"},
        session_id=sid,
    )
    assert "error" in out
    assert "未在当前会话" in out["error"]


@pytest.mark.asyncio
async def test_remove_layer_rejects_empty_layer_ref(registry, session_with_layer):
    sid, _ref = session_with_layer
    out = await registry.dispatch(
        "remove_layer",
        {"layer_ref": ""},
        session_id=sid,
    )
    assert "error" in out
    assert "不能为空" in out["error"]


@pytest.mark.asyncio
async def test_remove_layer_rejects_unknown_layer_ref(registry, session_with_layer):
    sid, _ref = session_with_layer
    for bad in ["ref:", "ref:not-in-this-session-xyz"]:
        out = await registry.dispatch(
            "remove_layer",
            {"layer_ref": bad},
            session_id=sid,
        )
        assert "error" in out, f"expected reject for layer_ref={bad!r}"
        assert "未在当前会话" in out["error"]


@pytest.mark.asyncio
async def test_reorder_layer_accepts_valid_session_ref(registry, session_with_layer):
    """Sanity: the valid-input happy path (Chinese alias) still works after guards."""
    sid, _ref = session_with_layer
    out = await registry.dispatch(
        "reorder_layer",
        {"layer_ref": "我的层", "position": "top"},
        session_id=sid,
    )
    assert out.get("success") is True, f"expected success, got {out}"
    assert out["command"] == "REORDER_LAYER"


@pytest.mark.asyncio
async def test_remove_layer_accepts_valid_session_ref(registry, session_with_layer):
    sid, _ref = session_with_layer
    out = await registry.dispatch(
        "remove_layer",
        {"layer_ref": "我的层"},
        session_id=sid,
    )
    assert out.get("success") is True, f"expected success, got {out}"
    assert out["command"] == "REMOVE_LAYER"
