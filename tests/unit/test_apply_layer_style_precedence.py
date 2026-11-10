"""PR-C 回归锁：apply_layer_style 的颜色进 MapSpec 层 paint，用户样式编辑胜出。

缺陷现场：apply_layer_style 把颜色逐要素烙进 ``properties.fill_color``。
- committed MapSpec 层：编译器只认 ``paint.color``，烙进去的颜色读不到，
  层按转换器默认色显示（agent 说"已改成红色"，地图仍是默认蓝）；
- HUD-only 层：adapter 的 ``coalesce(fill_color, style.color)`` 让要素色永远
  压过用户在样式面板改的颜色。
修复后：要素不被改写，``style_applied`` 由 dispatcher 授权进层 paint；
用户 ``patch_layer_style`` 合并在其上（user wins）。
"""
from __future__ import annotations

import shutil
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.mapspec.lifecycle_engine import (
    MapSpecLifecycleEngine,
    PatchLayerStyleIntent,
)
from app.services.mapspec.store import BASE_STORAGE_DIR, mapspec_store_instance
from app.services.session_data import session_data_manager
from app.services.tool_dispatch_service import (
    ToolDispatchService,
    _base_layer_with_explicit_paint,
)
from app.tools.cartography import register_cartography_tools
from app.tools.registry import ToolRegistry


@pytest.fixture
async def clean_session():
    sid = "test-apply-layer-style-precedence"
    await session_data_manager.clear_session(sid)
    shutil.rmtree(BASE_STORAGE_DIR / sid, ignore_errors=True)
    yield sid
    await session_data_manager.clear_session(sid)
    shutil.rmtree(BASE_STORAGE_DIR / sid, ignore_errors=True)


def _polygons() -> dict:
    ring = [[104.0, 30.6], [104.1, 30.6], [104.1, 30.7], [104.0, 30.6]]
    return {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [ring]},
             "properties": {"name": f"p{i}"}}
            for i in range(3)
        ],
    }


async def _real_apply_layer_style() -> dict:
    reg = ToolRegistry()
    register_cartography_tools(reg)
    return await reg.dispatch("apply_layer_style", {
        "geojson": _polygons(), "color": "#cc3333", "opacity": 0.6, "stroke_width": 1.5,
    })


def test_explicit_paint_projection_is_bounded_and_typed():
    base = {"id": "x"}
    assert _base_layer_with_explicit_paint(base, None) is base
    assert _base_layer_with_explicit_paint(base, {"opacity": 7, "stroke_width": True}) is base
    out = _base_layer_with_explicit_paint(
        base, {"color": "#112233", "opacity": 0.5, "stroke_width": 2})
    assert out == {"id": "x", "paint": {"color": "#112233", "opacity": 0.5, "width": 2.0}}
    assert "paint" not in base  # 不改入参


@pytest.mark.asyncio
async def test_apply_layer_style_color_lands_in_layer_paint_and_user_edit_wins(clean_session):
    tool_result = await _real_apply_layer_style()
    for feature in tool_result["geojson"]["features"]:
        assert "fill_color" not in feature["properties"]

    service = ToolDispatchService(registry=MagicMock(dispatch=AsyncMock(return_value=tool_result)))
    dispatched = await service.dispatch(
        {"id": "call_style", "function": {"name": "apply_layer_style",
                                          "arguments": {"geojson": "...", "color": "#cc3333"}}},
        clean_session, set(),
    )
    assert dispatched.status == "ok"

    spec = await mapspec_store_instance.get_mapspec(clean_session)
    layer = next(lyr for lyr in spec["layers"] if str(lyr.get("id", "")).startswith("result-"))
    # agent 的显式颜色进了权威 paint（修复前：转换器默认色）
    assert layer["paint"]["color"] == "#cc3333"
    assert layer["paint"]["opacity"] == pytest.approx(0.6)

    state = await session_data_manager.get_map_state(clean_session)
    revision = int(state.get("_cartographic_mutation_revision") or 0)
    res = await MapSpecLifecycleEngine().apply_mutation(
        clean_session,
        PatchLayerStyleIntent(layer_id=layer["id"], paint={"color": "#00aa00"}),
        origin="user",
        expected_revision=revision,
    )
    assert res.is_error is False, res.error_msg
    spec2 = await mapspec_store_instance.get_mapspec(clean_session)
    layer2 = next(lyr for lyr in spec2["layers"] if lyr["id"] == layer["id"])
    assert layer2["paint"]["color"] == "#00aa00"  # user wins
    assert layer2["paint"]["opacity"] == pytest.approx(0.6)  # agent 其余键保留
