"""M1 拆分回归：app/services/chat/sse_helpers + prompt 子模块直测。

确保从巨型 chat_engine.py 拆出的纯函数行为不变，并且 chat_engine 仍以
下划线别名 re-export 它们（外部代码 / 旧测试可继续 import 老路径）。
"""
import json

import pytest

from app.services.chat.sse_helpers import (
    LRUCache,
    MSG_MAX_CHARS,
    calculate_bbox,
    is_error_dict,
    normalize_tool_args,
    parse_minimax_xml_tool_calls,
    slim_event_result,
    slim_tool_result,
    wrap_error_dict_for_llm,
)
from app.services.chat.prompt import (
    SYSTEM_PROMPT,
    construct_self_healing_message,
)


# ─── LRUCache ──────────────────────────────────────────────────


class TestLRUCache:
    def test_evicts_oldest_when_over_capacity(self):
        c = LRUCache(capacity=2)
        c["a"] = 1
        c["b"] = 2
        c["c"] = 3
        assert "a" not in c
        assert list(c.keys()) == ["b", "c"]

    def test_access_promotes_to_recent(self):
        c = LRUCache(capacity=2)
        c["a"] = 1
        c["b"] = 2
        _ = c["a"]  # touch 'a' → becomes most-recent
        c["c"] = 3
        assert "a" in c
        assert "b" not in c


# ─── slim_tool_result / slim_event_result ──────────────────────


class TestSlimToolResult:
    def test_passthrough_when_small(self):
        small = json.dumps({"foo": "bar"})
        assert slim_tool_result({"foo": "bar"}, small, None) == small

    def test_keeps_summary(self):
        result = {"summary": "done", "geojson": {"features": []}, "ref_id": "ignored"}
        out = json.loads(slim_tool_result(result, json.dumps(result), "ref:abc"))
        assert out["summary"] == "done"
        assert out["ref_id"] == "ref:abc"

    def test_extracts_feature_count_and_props_when_oversized(self):
        # 构造一个 > MSG_MAX_CHARS 的 result
        features = [
            {"type": "Feature", "properties": {"name": f"n{i}", "area": i * 100}, "geometry": {"type": "Point", "coordinates": [0, 0]}}
            for i in range(200)
        ]
        result = {
            "type": "FeatureCollection",
            "features": features,
        }
        raw = json.dumps(result)
        assert len(raw) > MSG_MAX_CHARS
        out = json.loads(slim_tool_result(result, raw, "ref:xx"))
        # 大字段被剥
        assert "features" not in out
        # 摘要存在
        gs = out["geojson_summary"]
        assert gs["feature_count"] == 200
        # Round 7: available_properties → typed_properties (字段 + 类型)
        assert set(gs["typed_properties"].keys()) == {"name", "area"}
        assert gs["typed_properties"]["area"] == "number"
        assert gs["typed_properties"]["name"] == "string"


class TestSlimEventResult:
    def test_passthrough_non_dict(self):
        assert slim_event_result(42) == 42

    def test_strips_features_keeps_bbox(self):
        r = {"features": [{"x": 1}] * 10, "title": "y", "bbox": [1.0, 2.0, 3.0, 4.0]}
        out = slim_event_result(r)
        assert "features" not in out
        assert out["bbox"] == [1.0, 2.0, 3.0, 4.0]
        assert out["title"] == "y"

    def test_calculates_bbox_when_absent(self):
        r = {
            "type": "FeatureCollection",
            "features": [
                {"geometry": {"type": "Point", "coordinates": [10, 20]}},
                {"geometry": {"type": "Point", "coordinates": [30, 40]}},
            ],
        }
        out = slim_event_result(r)
        assert out["bbox"] == [10.0, 20.0, 30.0, 40.0]


# ─── error wrapping ────────────────────────────────────────────


def test_is_error_dict():
    assert is_error_dict({"success": False, "code": "NOT_FOUND"})
    assert not is_error_dict({"success": True})
    assert not is_error_dict({"success": False})  # 缺 code
    assert not is_error_dict("string")


def test_wrap_error_dict_uses_self_healing_template():
    msg = wrap_error_dict_for_llm("geocode", {"code": "NOT_FOUND", "message": "未找到", "error_type": "KeyError"})
    assert "geocode" in msg
    assert "未找到" in msg


def test_construct_self_healing_chooses_hint_by_keyword():
    assert "schema" in construct_self_healing_message("x", "类型不对", "校验失败")
    assert "ref" in construct_self_healing_message("x", "无法找到引用数据 abc", "RuntimeError")


# ─── normalize / parse ────────────────────────────────────────


def test_normalize_tool_args_sorts_keys():
    a = normalize_tool_args('{"b": 1, "a": 2}')
    b = normalize_tool_args({"a": 2, "b": 1})
    assert a == b == '{"a": 2, "b": 1}'


def test_parse_minimax_xml_handles_typical_case():
    xml = 'minimax:tool_call <invoke name="geocode"><parameter name="q">北京</parameter><parameter name="limit">3</parameter></invoke>'
    out = parse_minimax_xml_tool_calls(xml)
    assert len(out) == 1
    assert out[0]["function"]["name"] == "geocode"
    args = out[0]["function"]["arguments"]
    assert args["q"] == "北京"
    assert args["limit"] == 3  # JSON 解析数字


def test_parse_minimax_xml_empty_when_no_match():
    assert parse_minimax_xml_tool_calls("just text") == []


# ─── calculate_bbox ───────────────────────────────────────────


class TestCalculateBbox:
    def test_returns_none_for_empty(self):
        assert calculate_bbox({"features": []}) is None
        assert calculate_bbox({}) is None
        assert calculate_bbox("nope") is None

    def test_handles_point_and_polygon(self):
        gc = {
            "features": [
                {"geometry": {"type": "Point", "coordinates": [10, 20]}},
                {"geometry": {"type": "Polygon", "coordinates": [[[0, 0], [30, 0], [30, 25], [0, 25], [0, 0]]]}},
            ]
        }
        assert calculate_bbox(gc) == [0.0, 0.0, 30.0, 25.0]


# ─── 兼容性：旧 chat_engine 别名 ─────────────────────────────


def test_chat_engine_reexports_old_names():
    """老代码 / 旧测试 import 的下划线版本必须仍工作。"""
    from app.services import chat_engine as ce
    assert ce._slim_tool_result is slim_tool_result
    assert ce._calculate_bbox is calculate_bbox
    assert ce._construct_self_healing_message is construct_self_healing_message
    assert ce._parse_minimax_xml_tool_calls is parse_minimax_xml_tool_calls
    assert ce.SYSTEM_PROMPT is SYSTEM_PROMPT
    assert ce.LRUCache is LRUCache


def test_system_prompt_has_skill_placeholder():
    """{skill_list} 占位符必须存在 — ChatEngine._build_system_prompt 依赖它。"""
    assert "{skill_list}" in SYSTEM_PROMPT
