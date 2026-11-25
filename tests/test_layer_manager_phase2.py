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


# ─── display_layer: P1-6 gate (previously passed the raw LLM ref through) ──


@pytest.mark.asyncio
async def test_display_layer_rejects_unknown_ref(registry, session_with_layer):
    """P1-6: an LLM ref not owned by this session must be refused before it
    reaches the frontend's prefix-match handler."""
    sid, _ref = session_with_layer
    for bad in ["", "ref:", "ref:not-in-this-session-xyz"]:
        out = await registry.dispatch(
            "display_layer",
            {"ref_id": bad, "name": "x"},
            session_id=sid,
        )
        assert "error" in out, f"expected reject for ref_id={bad!r}, got {out}"


@pytest.mark.asyncio
async def test_display_layer_resolves_alias_to_canonical_id(registry, session_with_layer):
    """The frontend must receive the session-owned canonical id, not the raw
    alias the LLM emitted."""
    sid, ref = session_with_layer
    out = await registry.dispatch(
        "display_layer",
        {"ref_id": "我的层", "name": "结果"},
        session_id=sid,
    )
    assert out.get("success") is True, f"expected success, got {out}"
    assert out["command"] == "LAYER_VISIBILITY_UPDATE"
    assert out["params"]["layer_id"] == ref


# ─── P3：finalize_display 未提交批的消息不得误报 not_found ─────────────────


@pytest.mark.asyncio
async def test_finalize_display_noop_batch_does_not_claim_not_found(
    registry, session_with_layer, monkeypatch,
):
    """无 refused / not_found 的未提交批（空批 / superseded）必须给出通用
    原因，而不是谎称"展示集 0 个层在 MapSpec 中不存在"。"""
    from app.services.mapspec.mutation_contracts import MapSpecBatchResult
    import app.services.gis_world_state as gws

    async def _noop_batch(*_args, **_kwargs):
        return MapSpecBatchResult(applied_count=0)

    monkeypatch.setattr(gws, "apply_gis_mutation_batch", _noop_batch)
    sid, ref = session_with_layer
    out = await registry.dispatch(
        "finalize_display", {"show_refs": [ref]}, session_id=sid
    )
    assert out["success"] is True, out
    assert out["final_display"]["unresolved_layer_ids"] == []
    assert out["final_display"]["batch_committed"] is False
    assert "not_found" not in out["message"]
    assert "不存在" not in out["message"]


# ─── P3：inventory_layers 声明 bounded，输出必须有上限 ─────────────────────


@pytest.mark.asyncio
async def test_inventory_layers_is_bounded(registry):
    from app.tools.layer_manager import _INVENTORY_MAX_ITEMS

    sid = "test-inventory-bounded-session"
    await session_data_manager.clear_session(sid)
    try:
        refs = []
        for i in range(_INVENTORY_MAX_ITEMS + 6):
            refs.append(
                await session_data_manager.store(
                    sid, {"type": "FeatureCollection", "features": [], "i": i},
                    prefix=f"inv{i}",
                )
            )
        out = await registry.dispatch("inventory_layers", {}, session_id=sid)
        assert out["success"] is True, out
        assert out["total"] == len(refs)
        assert out["truncated"] is True
        assert out["count"] == _INVENTORY_MAX_ITEMS
        assert len(out["layers"]) == _INVENTORY_MAX_ITEMS
        # 保留最近（dict 尾部）的 ref，与上下文 inventory 的 #992 口径一致。
        assert [item["ref_id"] for item in out["layers"]] == refs[-_INVENTORY_MAX_ITEMS:]
    finally:
        await session_data_manager.clear_session(sid)


# ─── P3：switch_base_layer 空名/未知名不得静默切换或谎报成功 ────────────────


@pytest.mark.asyncio
async def test_switch_base_layer_rejects_empty_name(registry, session_with_layer):
    sid, _ref = session_with_layer
    await session_data_manager.set_map_state(sid, "base_layer", "OSM 地图")
    out = await registry.dispatch("switch_base_layer", {"name": ""}, session_id=sid)
    assert "error" in out, f"expected reject, got {out}"
    state = await session_data_manager.get_map_state(sid) or {}
    assert state.get("base_layer") == "OSM 地图", "empty name must not switch basemap"


@pytest.mark.asyncio
async def test_switch_base_layer_rejects_unknown_name(registry, session_with_layer):
    sid, _ref = session_with_layer
    out = await registry.dispatch(
        "switch_base_layer", {"name": "火星底图XYZ"}, session_id=sid
    )
    assert "error" in out, f"expected reject, got {out}"
    state = await session_data_manager.get_map_state(sid) or {}
    assert "base_layer" not in state, "unrecognized name must not be persisted"
