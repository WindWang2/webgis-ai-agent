"""webgis_map_operation 工具测试（M2）：注册/真实 dispatch（registry 注入
session_id）/ preview-commit 双模式 / 错误契约 / 幂等。

工具层是 Pi 与 legacy 两条 host 的共同入口（共享 ToolDispatchService），
在此钉住工具的真实可执行性（BASELINE 动作 21/22 的生产入口验证）。
"""
from __future__ import annotations

import uuid

import pytest

from app.services.mapspec.lifecycle_engine import (
    MapSpecLifecycleEngine,
    UpsertLayerIntent,
)
from app.services.mapspec.store import mapspec_store_instance
from app.tools.map_operations_tools import register_map_operation_tools
from app.tools.registry import ToolRegistry


def _sid() -> str:
    return f"mopt-{uuid.uuid4().hex[:10]}"


@pytest.fixture()
def registry():
    reg = ToolRegistry()
    register_map_operation_tools(reg)
    return reg


async def _seed(sid: str) -> None:
    engine = MapSpecLifecycleEngine()
    res = await engine.apply_mutation(sid, UpsertLayerIntent(layer={
        "id": "heatmap", "type": "heatmap", "source": "src-heat",
        "name": "人口热力图", "paint": {"heatmap-opacity": 0.8},
    }, source_data={"type": "geojson", "inlineData": {
        "type": "FeatureCollection", "features": [
            {"type": "Feature", "geometry": {"type": "Point",
                                             "coordinates": [104.0, 30.6]},
             "properties": {}}]}}))
    assert not res.is_error


async def test_tool_registered_with_schema(registry):
    assert "webgis_map_operation" in registry._tools
    # 显式 args_model 工具不进 _schemas（与 finalize_display 同款注册路径），
    # 校验模型必须登记（dispatch 校验 + Pi surface 依赖）。
    assert registry._models.get("webgis_map_operation") is not None


async def test_tool_dispatch_preview_zero_mutation(registry):
    sid = _sid()
    await _seed(sid)
    result = await registry.dispatch("webgis_map_operation", {
        "mode": "preview",
        "effects": [
            {"kind": "set_layer_visibility", "target": "人口热力图", "visible": False},
        ],
    }, session_id=sid)
    assert not result.get("error")
    assert result["mode"] == "preview"
    assert result["effects"][0]["before"] == {"visible": True}
    # 零突变：层仍可见
    spec = await mapspec_store_instance.get_mapspec(sid)
    layer = next(lyr for lyr in spec["layers"] if lyr["id"] == "heatmap")
    assert (layer.get("layout") or {}).get("visibility", "visible") == "visible"


async def test_tool_dispatch_commit_with_receipt_commands(registry):
    sid = _sid()
    await _seed(sid)
    operation_id = f"mop-tool-{uuid.uuid4().hex[:8]}"
    result = await registry.dispatch("webgis_map_operation", {
        "mode": "commit",
        "operation_id": operation_id,
        "effects": [
            {"kind": "set_layer_visibility", "target": "人口热力图", "visible": False},
            {"kind": "set_viewport", "center": [104.05, 30.65], "zoom": 12},
        ],
    }, session_id=sid)
    assert result["status"] == "committed", result
    assert result["success"] is True
    commands = result["commands"]
    assert any(c["command"] == "map_operation_receipt" for c in commands)
    assert any(c["command"] == "fly_to" for c in commands)
    assert result["mutation_revision"] >= 1

    # 幂等重放（同 operation_id）
    replay = await registry.dispatch("webgis_map_operation", {
        "mode": "commit", "operation_id": operation_id,
        "effects": [
            {"kind": "set_layer_visibility", "target": "人口热力图", "visible": False},
            {"kind": "set_viewport", "center": [104.05, 30.65], "zoom": 12},
        ],
    }, session_id=sid)
    assert replay.get("replayed") is True


async def test_tool_dispatch_sensitive_stops_at_confirmation(registry):
    sid = _sid()
    await _seed(sid)
    result = await registry.dispatch("webgis_map_operation", {
        "mode": "commit",
        "effects": [{"kind": "remove_layer", "target": "heatmap"}],
    }, session_id=sid)
    assert result["status"] == "confirmation_required"
    assert result["success"] is True  # 停在待确认是成功的中止态
    spec = await mapspec_store_instance.get_mapspec(sid)
    assert any(lyr["id"] == "heatmap" for lyr in spec["layers"])


async def test_tool_dispatch_clarification(registry):
    sid = _sid()
    await _seed(sid)
    result = await registry.dispatch("webgis_map_operation", {
        "mode": "commit",
        "effects": [{"kind": "set_layer_opacity", "target_kind": "focused_layer",
                     "opacity": 0.5}],
    }, session_id=sid)
    assert result["status"] == "clarification_needed"
    assert result["clarification"]


async def test_tool_invalid_args_error_contract(registry):
    sid = _sid()
    result = await registry.dispatch("webgis_map_operation", {
        "mode": "commit",
        "effects": [{"kind": "drop_database"}],
    }, session_id=sid)
    # registry 的参数校验或工具内校验二选一拒绝 —— 都必须是 typed error
    assert result.get("error") or result.get("status") == "error"
