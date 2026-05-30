"""Round 4: 选中要素注入"""
import pytest

from app.services.session_data import session_data_manager
from app.services.chat.context_builder import (
    build_map_state_summary,
    format_selected_feature,
)


def test_format_selected_feature_prefers_layer_name():
    sel = {
        "layer_id": "custom-ref:geojson-abc",
        "layer_name": "核心保护区",
        "ref_id": "ref:geojson-abc",
        "point": [116.4, 39.9],
        "properties": {"name": "第一区"},
    }
    out = format_selected_feature(sel)
    assert "核心保护区" in out
    assert "116.4000,39.9000" in out
    assert "第一区" in out


def test_format_selected_feature_falls_back_to_ref_then_layer_id():
    out = format_selected_feature({"layer_id": "custom-x", "point": [0, 0], "properties": {}})
    assert "图层=<untrusted_layer_name>custom-x</untrusted_layer_name>" in out
    out2 = format_selected_feature({"ref_id": "ref:x", "point": [0, 0], "properties": {}})
    assert "ref:x" in out2


def test_format_selected_feature_truncates_long_values():
    long = "x" * 100
    out = format_selected_feature({
        "layer_id": "L",
        "point": [0, 0],
        "properties": {"name": long},
    })
    # 不超过 30 字符 + 省略号
    assert "…" in out
    assert long not in out


def test_format_selected_feature_picks_label_field_over_others():
    out = format_selected_feature({
        "layer_id": "L",
        "point": [0, 0],
        "properties": {"area_km2": 12.5, "pop": 2000, "name": "好名字"},
    })
    # 即便其他字段在前，也优先取 name
    assert "name=好名字" in out


def test_format_selected_feature_fallback_to_first_props():
    out = format_selected_feature({
        "layer_id": "L",
        "point": [0, 0],
        "properties": {"area": 12.5, "pop": 2000},
    })
    # 没有 label 字段时，取前 4 个属性
    assert "area=" in out and "pop=" in out


def test_format_selected_feature_invalid_input_returns_none():
    assert format_selected_feature(None) is None
    assert format_selected_feature("not a dict") is None


def test_format_selected_feature_no_properties_ok():
    out = format_selected_feature({"layer_id": "L", "point": [1, 2], "properties": {}})
    assert "图层=<untrusted_layer_name>L</untrusted_layer_name>" in out
    assert "属性" not in out


async def test_summary_renders_selected_feature():
    sid = "r4-sel-summary"
    await session_data_manager.set_map_state(sid, "viewport", {"center": [0, 0], "zoom": 5})
    await session_data_manager.set_map_state(sid, "base_layer", "OSM 地图")
    await session_data_manager.set_map_state(sid, "selected_feature", {
        "layer_id": "custom-ref:x",
        "layer_name": "测试层",
        "point": [10, 20],
        "properties": {"name": "AAA"},
    })
    out = await build_map_state_summary(sid)
    assert "选中要素" in out
    assert "测试层" in out
    assert "AAA" in out
    await session_data_manager.clear_session(sid)


async def test_summary_omits_selected_feature_when_absent():
    sid = "r4-sel-none"
    await session_data_manager.set_map_state(sid, "viewport", {"center": [0, 0], "zoom": 5})
    await session_data_manager.set_map_state(sid, "base_layer", "OSM 地图")
    out = await build_map_state_summary(sid)
    assert "选中要素" not in out
    await session_data_manager.clear_session(sid)
