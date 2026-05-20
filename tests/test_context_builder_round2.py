"""Round 2 上下文注入增强：图层 bbox / 视口关系 / 后台任务 / 工具调用历史"""
import pytest

from app.services.session_data import session_data_manager
from app.services.chat.context_builder import (
    build_layer_schema,
    format_layer_schema,
    viewport_layer_relation,
    build_map_state_summary,
    _split_events,
    _format_tool_event,
    _format_pending_event,
)


def test_build_layer_schema_extracts_bbox():
    sid = "r2-bbox-1"
    gj = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature",
             "geometry": {"type": "Point", "coordinates": [100, 30]},
             "properties": {}},
            {"type": "Feature",
             "geometry": {"type": "Point", "coordinates": [101, 31]},
             "properties": {}},
        ],
    }
    ref = session_data_manager.store(sid, gj, prefix="t")
    schema = build_layer_schema(sid, ref)
    assert schema["bbox"] == [100.0, 30.0, 101.0, 31.0]
    session_data_manager.clear_session(sid)


def test_build_layer_schema_bbox_handles_polygon():
    sid = "r2-bbox-poly"
    gj = {"type": "FeatureCollection", "features": [
        {"type": "Feature",
         "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [10, 0], [10, 5], [0, 5], [0, 0]]]},
         "properties": {}}
    ]}
    ref = session_data_manager.store(sid, gj, prefix="t")
    schema = build_layer_schema(sid, ref)
    assert schema["bbox"] == [0.0, 0.0, 10.0, 5.0]
    session_data_manager.clear_session(sid)


def test_viewport_layer_relation_contains():
    assert viewport_layer_relation([0, 0, 100, 100], [10, 10, 50, 50]) == "在视口内"


def test_viewport_layer_relation_outside():
    assert viewport_layer_relation([0, 0, 10, 10], [50, 50, 60, 60]) == "在视口外"


def test_viewport_layer_relation_partial():
    assert viewport_layer_relation([0, 0, 50, 50], [40, 40, 60, 60]) == "局部相交"


def test_viewport_layer_relation_missing_inputs():
    assert viewport_layer_relation(None, [0, 0, 1, 1]) is None
    assert viewport_layer_relation([0, 0, 1, 1], None) is None
    assert viewport_layer_relation([0, 0, 1], [0, 0, 1, 1]) is None


def test_format_layer_schema_includes_bbox_and_relation():
    schema = {"geom": "Point", "count": 2, "fields": {}, "bbox": [100, 30, 101, 31]}
    out = format_layer_schema(schema, viewport_bounds=[99, 29, 102, 32])
    assert "bbox=[100.000,30.000,101.000,31.000]" in out
    assert "在视口内" in out


def test_format_layer_schema_no_relation_when_no_viewport():
    schema = {"geom": "Point", "count": 2, "fields": {}, "bbox": [100, 30, 101, 31]}
    out = format_layer_schema(schema)
    assert "bbox=" in out
    assert "在视口" not in out


def test_split_events_categorizes_correctly():
    log = [
        {"event": "tool_executed", "data": {"tool": "a"}},
        {"event": "map_state_push", "data": {}},
        {"event": "tool_executed", "data": {"tool": "b", "status": "export_task_created"}},
    ]
    tools, users, pending = _split_events(log)
    assert len(tools) == 2
    assert len(users) == 1
    assert len(pending) == 1
    assert pending[0]["data"]["tool"] == "b"


def test_format_tool_event_renders_error_marker():
    out = _format_tool_event({"data": {"tool": "buffer", "is_error": True, "error_msg": "bad geom"}})
    assert "❌" in out
    assert "bad geom" in out


def test_format_tool_event_renders_key_attrs():
    out = _format_tool_event({"data": {"tool": "osm_search", "ref": "ref:abc", "feature_count": 12}})
    assert "osm_search" in out
    assert "ref=ref:abc" in out
    assert "feature_count=12" in out


def test_format_pending_event_warns_against_retrigger():
    out = _format_pending_event({"data": {"tool": "export_thematic_map", "status": "export_task_created", "command": "export_map"}})
    assert "export_thematic_map" in out
    assert "不要重复触发" in out


def test_summary_renders_pending_and_tools_separately():
    sid = "r2-render"
    session_data_manager.set_map_state(sid, "viewport", {"center": [0, 0], "zoom": 5})
    session_data_manager.set_map_state(sid, "base_layer", "OSM 地图")
    session_data_manager.append_event(sid, "tool_executed", {"tool": "osm_search", "ref": "ref:x"})
    session_data_manager.append_event(sid, "tool_executed", {"tool": "export_thematic_map",
                                                              "status": "export_task_created",
                                                              "command": "export_map"})
    session_data_manager.append_event(sid, "map_state_push", {"zoom": 5})
    summary = build_map_state_summary(sid)
    assert "进行中后台任务" in summary
    assert "近期工具调用" in summary
    assert "近期用户操作" in summary
    assert "export_thematic_map" in summary
    session_data_manager.clear_session(sid)
