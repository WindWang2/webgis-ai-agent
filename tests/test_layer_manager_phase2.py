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
async def session_with_layer():
    sid = "test-phase2-session"
    ref = await session_data_manager.store(sid, {"type": "FeatureCollection", "features": []}, prefix="t")
    await session_data_manager.set_alias(sid, ref, "我的层")
    yield sid, ref
    await session_data_manager.clear_session(sid)


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
    other = await session_data_manager.store(sid, {"features": []}, prefix="o")
    await session_data_manager.set_alias(sid, other, "底图")
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


# ─── D2: P1-6 gate extended to the 3 previously-unguarded tools ──────────
# resolve_layer_ref() (architecture-review D2) unified all 5 layer-mutation
# tools behind one resolver with the existence gate. set_layer_status /
# update_layer_appearance / apply_layer_filter previously had NO gate — an
# unresolved LLM ref passed through to the frontend's prefix-match handler
# (renderer.ts:285). These tests lock in that the gate now closes on them too.


@pytest.mark.asyncio
async def test_set_layer_status_rejects_unknown_layer_ref(registry, session_with_layer):
    """D2: the P1-6 gate now covers set_layer_status (previously unguarded)."""
    sid, _ref = session_with_layer
    for bad in ["", "ref:", "ref:not-in-this-session-xyz"]:
        out = await registry.dispatch(
            "set_layer_status",
            {"layer_ref": bad, "visible": False},
            session_id=sid,
        )
        assert "error" in out, f"expected reject for layer_ref={bad!r}, got {out}"


@pytest.mark.asyncio
async def test_update_layer_appearance_rejects_unknown_layer_ref(registry, session_with_layer):
    """D2: the P1-6 gate now covers update_layer_appearance (previously unguarded)."""
    sid, _ref = session_with_layer
    for bad in ["", "ref:", "ref:not-in-this-session-xyz"]:
        out = await registry.dispatch(
            "update_layer_appearance",
            {"layer_ref": bad, "color": "#ff0000"},
            session_id=sid,
        )
        assert "error" in out, f"expected reject for layer_ref={bad!r}, got {out}"


@pytest.mark.asyncio
async def test_apply_layer_filter_rejects_unknown_layer_ref(registry, session_with_layer):
    """D2: the P1-6 gate now covers apply_layer_filter (previously unguarded)."""
    sid, _ref = session_with_layer
    for bad in ["", "ref:", "ref:not-in-this-session-xyz"]:
        out = await registry.dispatch(
            "apply_layer_filter",
            {"layer_ref": bad, "expression": "pop > 1000"},
            session_id=sid,
        )
        assert "error" in out, f"expected reject for layer_ref={bad!r}, got {out}"


@pytest.mark.asyncio
async def test_set_layer_status_accepts_session_owned_ref(registry, session_with_layer):
    """Sanity: the happy path still works after the gate was added."""
    sid, ref = session_with_layer
    out = await registry.dispatch(
        "set_layer_status",
        {"layer_ref": "我的层", "visible": False},
        session_id=sid,
    )
    assert out.get("success") is True, f"expected success, got {out}"
    assert out["params"]["layer_id"] == ref


@pytest.mark.asyncio
async def test_set_layer_status_omits_unset_keys(registry, session_with_layer):
    """#609: 未传的 Optional 参数必须从 params 省略，而不是序列化为 JSON null。

    旧行为把 visible=None 一起发出去 → 前端 `null !== undefined` 判真、
    null 走 falsy 分支把图层隐藏，且后验证读到 'none' 与"预期"一致 → 假收敛
    confirmed。省略键 = 前端看到"该属性未被请求"，只改透明度不会隐藏图层。
    """
    sid, ref = session_with_layer

    # 只传 opacity：visible 键必须整体缺席
    out = await registry.dispatch(
        "set_layer_status",
        {"layer_ref": "我的层", "opacity": 0.5},
        session_id=sid,
    )
    assert out.get("success") is True, f"expected success, got {out}"
    assert out["params"]["layer_id"] == ref
    assert out["params"].get("opacity") == 0.5
    assert "visible" not in out["params"], f"visible=null must be omitted, got {out['params']}"

    # 只传 visible：opacity 键必须整体缺席
    out2 = await registry.dispatch(
        "set_layer_status",
        {"layer_ref": "我的层", "visible": False},
        session_id=sid,
    )
    assert out2.get("success") is True, f"expected success, got {out2}"
    assert out2["params"].get("visible") is False
    assert "opacity" not in out2["params"], f"opacity=null must be omitted, got {out2['params']}"


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


# ─── finalize_display：每轮分析收尾的显示管理钩子（2026-08-26 用户需求）───

@pytest.mark.asyncio
async def test_finalize_display_resolves_and_dedupes(registry, session_with_layer):
    sid, ref = session_with_layer
    out = await registry.dispatch(
        "finalize_display",
        {"show_refs": [ref, "我的层", ref]},  # 同层三种引用形态 → 去重为一个
        session_id=sid,
    )
    assert out["success"] is True
    assert out["command"] == "FINALIZE_DISPLAY"
    assert out["params"]["show_layer_ids"] == [ref]


@pytest.mark.asyncio
async def test_finalize_display_rejects_empty_and_unknown(registry, session_with_layer):
    sid, _ = session_with_layer
    empty = await registry.dispatch("finalize_display", {"show_refs": []}, session_id=sid)
    assert "error" in empty
    unknown = await registry.dispatch(
        "finalize_display", {"show_refs": ["ref:geojson-does-not-exist"]}, session_id=sid
    )
    assert "error" in unknown
