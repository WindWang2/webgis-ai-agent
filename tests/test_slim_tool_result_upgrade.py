"""Round 7: 工具结果 slim 升级 - 字段类型 / 元数据保留 / 值截断"""
import json

import pytest

from app.services.chat.sse_helpers import (
    slim_tool_result,
    _infer_simple_type,
    _truncate_value,
    _truncate_properties,
    VALUE_MAX_CHARS,
    PROPERTY_KEYS_MAX,
)


def test_infer_simple_type_buckets():
    assert _infer_simple_type(None) == "null"
    assert _infer_simple_type(True) == "bool"
    assert _infer_simple_type(False) == "bool"
    assert _infer_simple_type(12) == "number"
    assert _infer_simple_type(1.5) == "number"
    assert _infer_simple_type("hi") == "string"
    assert _infer_simple_type([1, 2]) == "array"
    assert _infer_simple_type({"a": 1}) == "object"


def test_truncate_value_long_string():
    long = "x" * (VALUE_MAX_CHARS + 50)
    out = _truncate_value(long)
    assert len(out) == VALUE_MAX_CHARS
    assert out.endswith("…")


def test_truncate_value_passes_through_non_string():
    assert _truncate_value(42) == 42
    assert _truncate_value(None) is None
    assert _truncate_value([1, 2]) == [1, 2]


def test_truncate_properties_caps_key_count():
    props = {f"f{i}": i for i in range(PROPERTY_KEYS_MAX + 5)}
    out = _truncate_properties(props)
    # 留下 max_keys 个原始 key + 1 个 __more_keys__
    assert len(out) == PROPERTY_KEYS_MAX + 1
    assert out["__more_keys__"] == 5


def test_summary_path_preserves_meta_keys():
    result = {
        "summary": "找到 12 家商店",
        "feature_count": 12,
        "bbox": [116.0, 39.7, 116.8, 40.1],
        "layer_id": "ref:geojson-abc",
        "alias": "朝阳商店",
    }
    out = json.loads(slim_tool_result(result, json.dumps(result, ensure_ascii=False), "ref:geojson-abc"))
    assert out["summary"] == "找到 12 家商店"
    assert out["bbox"] == [116.0, 39.7, 116.8, 40.1]
    assert out["layer_id"] == "ref:geojson-abc"
    assert out["feature_count"] == 12
    assert out["alias"] == "朝阳商店"
    assert out["ref_id"] == "ref:geojson-abc"


def test_summary_path_does_not_clobber_ref_id():
    # 当 summary 路径里 result 自己也带 ref_id, 不要被 PRESERVED 路径再覆盖一次
    result = {"summary": "x", "ref_id": "ref:from-result"}
    out = json.loads(slim_tool_result(result, "{}", "ref:from-arg"))
    # session_geojson_ref 拼进去会赢；但 PRESERVED_META_KEYS 里的 ref_id 不应再二次覆盖
    assert out["ref_id"] == "ref:from-arg"


def test_geojson_summary_reports_typed_properties():
    features = []
    for i in range(100):
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [i, i]},
            "properties": {"name": f"x{i}" * 20, "pop": 100 + i, "active": (i % 2 == 0)},
        })
    result = {"geojson": {"type": "FeatureCollection", "features": features}}
    out = json.loads(slim_tool_result(result, json.dumps(result, ensure_ascii=False), "ref:t"))
    gs = out["geojson_summary"]
    assert gs["feature_count"] == 100
    assert gs["typed_properties"] == {"name": "string", "pop": "number", "active": "bool"}


def test_geojson_summary_handles_mixed_type():
    features = [
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [0, 0]},
         "properties": {"val": 1}},
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [1, 1]},
         "properties": {"val": "x"}},
    ]
    # 让 result_str 超过 MSG_MAX_CHARS 才会进 geojson_summary 路径
    result = {"geojson": {"type": "FeatureCollection", "features": features}, "padding": "P" * 5000}
    out = json.loads(slim_tool_result(result, json.dumps(result, ensure_ascii=False), "r"))
    assert out["geojson_summary"]["typed_properties"]["val"] == "mixed"


def test_long_property_value_is_truncated_in_sample():
    long = "Y" * (VALUE_MAX_CHARS + 100)
    features = [
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [0, 0]},
         "properties": {"name": "ok", "desc": long}}
    ]
    result = {"geojson": {"type": "FeatureCollection", "features": features}, "padding": "P" * 5000}
    out = json.loads(slim_tool_result(result, json.dumps(result, ensure_ascii=False), "r"))
    desc = out["geojson_summary"]["sample_properties"][0]["properties"]["desc"]
    assert desc.endswith("…")
    assert len(desc) <= VALUE_MAX_CHARS


def test_small_result_passes_through_unchanged():
    small = {"summary_text": "hi", "count": 1}
    s = json.dumps(small, ensure_ascii=False)
    # 没 summary key + 长度小于 3000 → 直接返回原文
    assert slim_tool_result(small, s, None) == s


def test_compression_ratio_on_big_geojson():
    """端到端的压缩效果：300 个要素带长描述，slim 后应 < 5KB。"""
    features = []
    for i in range(300):
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [i / 100, i / 100]},
            "properties": {"name": f"店 {i}", "pop": 1000 + i, "desc": "X" * 400},
        })
    result = {"geojson": {"type": "FeatureCollection", "features": features}}
    rs = json.dumps(result, ensure_ascii=False)
    slim = slim_tool_result(result, rs, "ref:demo")
    assert len(slim) < 5000
    # 但关键元数据要在
    assert "feature_count" in slim
    assert "typed_properties" in slim
