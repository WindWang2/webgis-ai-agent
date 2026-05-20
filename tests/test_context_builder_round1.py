"""Round 1 上下文注入增强：图层 schema / 专题图样式 / 底图目录"""
import pytest

from app.services.session_data import session_data_manager
from app.services.chat.context_builder import (
    build_layer_schema,
    format_layer_schema,
    format_style_summary,
    build_map_state_summary,
)
from app.core.base_layers import get_base_layer_names, format_base_layer_catalog


@pytest.fixture
def session_with_polygon_layer():
    sid = "ctx-round1-session"
    gj = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature",
             "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]},
             "properties": {"pop": 1000, "name": "A", "active": True, "note": None}},
            {"type": "Feature",
             "geometry": {"type": "Polygon", "coordinates": [[[2, 0], [3, 0], [3, 1], [2, 1], [2, 0]]]},
             "properties": {"pop": 2500, "name": "B", "active": False, "note": "x"}},
        ],
    }
    ref = session_data_manager.store(sid, gj, prefix="t")
    session_data_manager.set_alias(sid, ref, "街道")
    session_data_manager.set_map_state(sid, "viewport", {"center": [116.4, 39.9], "zoom": 10})
    session_data_manager.set_map_state(sid, "base_layer", "OSM 地图")
    session_data_manager.set_map_state(sid, "layers", [
        {"id": ref, "visible": True, "type": "fill", "style": {"color": "#3b82f6"}}
    ])
    yield sid, ref
    session_data_manager.clear_session(sid)


def test_build_layer_schema_infers_types(session_with_polygon_layer):
    sid, ref = session_with_polygon_layer
    schema = build_layer_schema(sid, ref)
    assert schema is not None
    assert schema["geom"] == "Polygon"
    assert schema["count"] == 2
    assert schema["fields"]["pop"] == "number"
    assert schema["fields"]["name"] == "string"
    assert schema["fields"]["active"] == "bool"
    # note has 1 string + 1 null → null discarded → string
    assert schema["fields"]["note"] == "string"


def test_format_layer_schema_truncates_long_field_list(session_with_polygon_layer):
    sid, _ = session_with_polygon_layer
    schema = {"geom": "Point", "count": 100, "fields": {f"f{i}": "number" for i in range(12)}}
    s = format_layer_schema(schema)
    assert "n=100" in s
    assert "...(+4)" in s  # 12 - 8 = 4


def test_format_style_summary_choropleth():
    style = {"type": "choropleth", "field": "pop", "breaks": [0, 100, 200, 500], "colors": []}
    out = format_style_summary(style)
    assert out is not None
    assert "field=pop" in out
    assert "分级=3" in out
    assert "0.00~500.00" in out


def test_format_style_summary_lisa():
    out = format_style_summary({"type": "lisa", "field": "income"})
    assert out is not None
    assert "LISA" in out and "field=income" in out


def test_format_style_summary_plain_color():
    assert format_style_summary({"color": "#abcdef"}) == "色=#abcdef"


def test_format_style_summary_none_input():
    assert format_style_summary(None) is None
    assert format_style_summary({}) is None


def test_summary_includes_base_layer_catalog(session_with_polygon_layer):
    sid, _ = session_with_polygon_layer
    summary = build_map_state_summary(sid)
    assert "可切换底图" in summary
    for name in get_base_layer_names():
        assert name in summary


def test_summary_includes_schema_line(session_with_polygon_layer):
    sid, _ = session_with_polygon_layer
    summary = build_map_state_summary(sid)
    assert "geom=Polygon" in summary
    assert "pop:number" in summary


def test_summary_renders_thematic_style(session_with_polygon_layer):
    sid, ref = session_with_polygon_layer
    session_data_manager.set_map_state(sid, "layers", [
        {"id": ref, "visible": True, "type": "fill",
         "style": {"type": "choropleth", "field": "pop", "breaks": [0, 1000, 2000], "colors": []}}
    ])
    summary = build_map_state_summary(sid)
    assert "专题图" in summary
    assert "field=pop" in summary


def test_base_layer_catalog_strings_match_canonical_list():
    names = get_base_layer_names()
    # Catalog 中前 7 个必须与 switch_base_layer 工具历史上硬编码的列表一致
    must_have = {"Carto 深色", "OSM 地图", "ESRI 影像", "Carto 浅色", "ESRI 地形", "OpenTopoMap", "高德影像"}
    assert must_have.issubset(set(names))


def test_format_base_layer_catalog_compact():
    text = format_base_layer_catalog()
    assert "|" in text  # multiple providers separated by pipe
    assert "(" in text and ")" in text  # keywords in parens
