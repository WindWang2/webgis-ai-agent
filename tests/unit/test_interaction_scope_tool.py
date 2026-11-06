"""webgis_selection_scope 工具测试（ADR-0283 D1/D3）。

真实 registry dispatch + 真实 in-memory session store：注册面、当前选择
验证 happy path、previous 多轮指代、无选择/禁用 typed 错误。
"""
from __future__ import annotations

import uuid

import pytest

from app.services.gis_harness.interaction_ir.contracts import LedgerState
from app.services.gis_harness.interaction_ir.ingest import apply_event, LEDGER_KEY
from app.services.session_data import session_data_manager
from app.tools.interaction_scope_tools import register_interaction_scope_tools
from app.tools.registry import ToolRegistry


def _sid() -> str:
    return f"scope-{uuid.uuid4().hex[:10]}"


@pytest.fixture()
def registry():
    reg = ToolRegistry()
    register_interaction_scope_tools(reg)
    return reg


async def _seed_selection(sid: str, events: list) -> None:
    state = LedgerState()
    for e in events:
        apply_event(state, e)
    # Feature scope requires a real desired host/source, not a self-reported ref.
    latest = next((event.get("payload", {}) for event in reversed(events) if event.get("payload", {}).get("dataset_ref")), None)
    if latest:
        from app.services.mapspec.store import mapspec_store_instance
        await mapspec_store_instance.save_mapspec(sid, {"version": "1.0", "sources": {"s": {"type": "geojson", "ref_id": latest["dataset_ref"]}},
                                                     "layers": [{"id": latest["layer_id"], "type": "circle", "source": "s"}]})
    await session_data_manager.set_map_state(sid, LEDGER_KEY, state.events)
    await session_data_manager.set_map_state(sid, "_interaction_ir_meta", {
        "server_seq": state.server_seq, "dropped": 0,
    })


def _ev(seq: int, kind: str, payload: dict) -> dict:
    return {
        "event_id": f"c-{seq:08d}", "client_instance_id": "t-aaaa0001",
        "client_seq": seq, "kind": kind, "payload": payload,
    }


async def _seed_ref(sid: str, features: list) -> str:
    payload = {"geojson": {"type": "FeatureCollection", "features": features}}
    ref_id = await session_data_manager.store(sid, payload)
    return ref_id


async def test_tool_registered_with_schema(registry):
    assert "webgis_selection_scope" in registry._tools
    assert registry._models.get("webgis_selection_scope") is not None


async def test_tool_current_selection_validated_usable(registry):
    sid = _sid()
    ref_id = await _seed_ref(sid, [
        {"type": "Feature", "id": "sch-1", "properties": {"name": "A"}},
        {"type": "Feature", "id": "sch-2", "properties": {"name": "B"}},
    ])
    await _seed_selection(sid, [
        _ev(1, "feature_selected", {
            "layer_id": "schools", "dataset_ref": ref_id,
            "selected_ids": ["sch-1", "sch-2"]}),
    ])
    result = await registry.dispatch("webgis_selection_scope", {
        "selection_ref": "current",
    }, session_id=sid)
    assert result["success"] is True
    assert result["status"] == "usable"
    assert result["dataset_ref"] == ref_id
    assert result["feature_ids"] == ["sch-1", "sch-2"]
    assert result["checks"]["ownership"] == "ok"


async def test_tool_previous_selection_reference(registry):
    sid = _sid()
    ref_id = await _seed_ref(sid, [
        {"type": "Feature", "id": "sch-1", "properties": {}},
        {"type": "Feature", "id": "sch-9", "properties": {}},
    ])
    await _seed_selection(sid, [
        _ev(1, "feature_selected", {
            "layer_id": "schools", "dataset_ref": ref_id,
            "selected_ids": ["sch-1"]}),
        _ev(2, "feature_selected", {
            "layer_id": "schools", "dataset_ref": ref_id,
            "feature_id": "sch-9"}),
    ])
    result = await registry.dispatch("webgis_selection_scope", {
        "selection_ref": "previous",
    }, session_id=sid)
    assert result["success"] is True
    assert result["feature_ids"] == ["sch-1"]


async def test_tool_no_selection_typed_unusable(registry):
    sid = _sid()
    result = await registry.dispatch("webgis_selection_scope", {}, session_id=sid)
    assert result["success"] is False
    assert result["status"] == "unusable"
    assert result["reason"] == "no_selection"
    assert "correction_hint" in result


async def test_tool_ref_unresolvable_cross_session(registry):
    """ref 属于其他会话 → 本会话 store 解析不到 → fail-closed。"""
    sid = _sid()
    other = _sid()
    ref_id = await _seed_ref(other, [
        {"type": "Feature", "id": "sch-1", "properties": {}},
    ])
    await _seed_selection(sid, [
        _ev(1, "feature_selected", {
            "layer_id": "schools", "dataset_ref": ref_id,
            "selected_ids": ["sch-1"]}),
    ])
    result = await registry.dispatch("webgis_selection_scope", {}, session_id=sid)
    assert result["success"] is False
    assert result["reason"] == "ref_unresolvable"


async def test_tool_disabled_flag_typed(monkeypatch, registry):
    monkeypatch.setenv("GIS_SELECTION_SCOPE", "0")
    sid = _sid()
    result = await registry.dispatch("webgis_selection_scope", {}, session_id=sid)
    assert result["success"] is False
    assert result["reason"] == "disabled"


async def test_tool_claimed_only_mode_skips_validation(registry):
    sid = _sid()
    await _seed_selection(sid, [
        _ev(1, "feature_selected", {
            "layer_id": "schools", "dataset_ref": "ref:geojson-gone",
            "selected_ids": ["sch-1"]}),
    ])
    result = await registry.dispatch("webgis_selection_scope", {
        "perform_validation": False,
    }, session_id=sid)
    assert result["claimed"]["feature_ids"] == ["sch-1"]  # 声称面不验证
    assert "status" not in result
