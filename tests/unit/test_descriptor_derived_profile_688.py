"""#688: 授权路径 descriptor→profile 派生——dispatch 全量遍历计数 + 等价性。"""
from unittest.mock import patch

import pytest

from app.services.spatial_meta_profiler import (
    profile_geojson_source,
    profile_from_descriptor,
)


def _fc(n: int, start_lng: float = 116.0):
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [start_lng + i * 0.001, 39.9 + (i % 50) * 0.001]},
                "properties": {"v": i, "name": f"p{i}"},
            }
            for i in range(n)
        ],
    }


def test_derived_profile_shape_matches_full_profiler_contract():
    fc = _fc(50)
    full = profile_geojson_source(fc)
    derived = profile_from_descriptor({
        "feature_count": 50,
        "bbox": full["bbox"],
        "geometry_types": ["Point"],
    })
    assert derived is not None
    # 授权消费面的等价：view/要素数/几何类型
    assert derived["featureCount"] == full["featureCount"] == 50
    assert derived["geometryTypes"] == full["geometryTypes"] == ["Point"]
    assert derived["bbox"] == full["bbox"]
    # view 语义对齐 profiler 的保守门：无显式 CRS 双方都给空 view；
    # descriptor 不携带 CRS，派生路径恒空（投影坐标上给 view 不安全）
    assert derived["suggestedView"] == {} and full["suggestedView"] == {}
    # 富信息缺席走预留契约：fields_status=unknown（semantic review 不得当失败）
    assert derived["fields_status"] == "unknown"
    assert full["fields_status"] == "explicit"


def test_derived_profile_rejects_incomplete_descriptor():
    assert profile_from_descriptor(None) is None
    assert profile_from_descriptor({}) is None
    assert profile_from_descriptor({"bbox": [0, 0, 1, 1]}) is None  # 无 feature_count
    assert profile_from_descriptor({"feature_count": "x", "bbox": [0, 0, 1, 1], "geometry_types": []}) is None


def test_derived_profile_antimeridian_wrap_matches_profiler():
    fc = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [170.0, 10.0]}, "properties": {}},
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [-170.0, 10.0]}, "properties": {}},
        ],
    }
    full = profile_geojson_source(fc)
    derived = profile_from_descriptor({
        "feature_count": 2,
        "bbox": full["bbox"],
        "geometry_types": ["Point"],
    })
    assert derived["suggestedView"] == full["suggestedView"], "跨经度回绕必须与全量 profiler 同解"


@pytest.mark.asyncio
async def test_author_dispatch_does_not_rescan_with_descriptor(monkeypatch):
    """带 descriptor 的大结果授权：dispatch 路径 profile_geojson_source 调用次数 == 0。"""
    import app.services.tool_dispatch_service as tds
    import app.services.spatial_meta_profiler as smp

    calls = {"n": 0}
    real = smp.profile_geojson_source

    def counting(*a, **kw):
        calls["n"] += 1
        return real(*a, **kw)

    fc = _fc(100)
    descriptor = {"feature_count": 100, "bbox": [116.0, 39.9, 116.099, 39.949], "geometry_types": ["Point"]}

    service = tds.ToolDispatchService.__new__(tds.ToolDispatchService)
    with patch.object(smp, "profile_geojson_source", side_effect=counting):
            # 直接驱动 _author_display_result（converter 走真实现）
            from app.services.mapspec_store import mapspec_store

            async def fake_upsert(sid, layer, source_data):
                return {"success": True, "layer": layer, "mapspec": {"layers": [layer]}}

            monkeypatch.setattr(mapspec_store, "layer_upsert", fake_upsert)
            result = await service._author_display_result(
                session_id="s-688",
                tool_call_id="tc-1",
                tool_name="query_osm_poi",
                result={"type": "FeatureCollection", "features": fc["features"]},
                target_data=fc,
                result_ref="ref:geojson:test-688",
                descriptor=descriptor,
            )
    assert "error" not in result or not result.get("error"), result.get("error")
    assert calls["n"] == 0, (
        f"descriptor 命中时 dispatch 路径不得全量 re-profile（实际 {calls['n']} 次）"
    )


@pytest.mark.asyncio
async def test_author_dispatch_falls_back_to_full_profile_without_descriptor(monkeypatch):
    """无 descriptor：降级全量 profile（富信息场景语义保持）。"""
    import app.services.tool_dispatch_service as tds
    import app.services.spatial_meta_profiler as smp

    calls = {"n": 0}
    real = smp.profile_geojson_source

    def counting(*a, **kw):
        calls["n"] += 1
        return real(*a, **kw)

    fc = _fc(20)
    service = tds.ToolDispatchService.__new__(tds.ToolDispatchService)
    from app.services.mapspec_store import mapspec_store

    async def fake_upsert(sid, layer, source_data):
        return {"success": True, "layer": layer, "mapspec": {"layers": [layer]}}

    monkeypatch.setattr(mapspec_store, "layer_upsert", fake_upsert)
    with patch.object(smp, "profile_geojson_source", side_effect=counting):
        await service._author_display_result(
            session_id="s-688b",
            tool_call_id="tc-2",
            tool_name="query_osm_poi",
            result={"type": "FeatureCollection", "features": fc["features"]},
            target_data=fc,
            result_ref="ref:geojson:test-688b",
            descriptor=None,
        )
    assert calls["n"] == 1, "无 descriptor 必须恰好降级一次全量 profile"


@pytest.mark.asyncio
async def test_author_dispatch_converter_skips_geojson_traversal(monkeypatch):
    """#688 收尾：descriptor 命中时 converter 不再对 FC 自行遍历——
    几何类别/点数经 payload.profile 零遍历供给（原扫描 3 消除）。"""
    import app.services.analysis_cartography_converter as conv_mod
    import app.services.tool_dispatch_service as tds
    from app.services.mapspec_store import mapspec_store

    fc = _fc(5000)
    descriptor = {"feature_count": 5000, "bbox": [116.0, 39.9, 116.099, 39.949], "geometry_types": ["Point"]}

    traversals = {"n": 0}
    real_iter = conv_mod._iter_features

    def counting_iter(gj):
        traversals["n"] += 1
        return real_iter(gj)

    async def fake_upsert(sid, layer, source_data):
        return {"success": True, "layer": layer, "mapspec": {"layers": [layer]}}

    service = tds.ToolDispatchService.__new__(tds.ToolDispatchService)
    monkeypatch.setattr(mapspec_store, "layer_upsert", fake_upsert)
    with patch.object(conv_mod, "_iter_features", side_effect=counting_iter):
        await service._author_display_result(
            session_id="s-688c",
            tool_call_id="tc-3",
            tool_name="query_osm_poi",
            result={"type": "FeatureCollection", "features": fc["features"]},
            target_data=fc,
            result_ref="ref:geojson:test-688c",
            descriptor=descriptor,
        )
    # converter 内部零遍历（_extract_geojson 不遍历；heatmap 守卫的
    # point_count 走 profile.featureCount）
    assert traversals["n"] == 0, (
        f"converter still walked the FC {traversals['n']} times with a profile present"
    )


def test_converter_without_profile_still_infers():
    """向后兼容：无 profile 的调用方走既有自遍历推断。"""
    from app.services.analysis_cartography_converter import (
        _category_from_geometry_types,
        convert_analysis_to_mapspec_layer,
    )

    fc = _fc(3)
    layer, _, warnings = convert_analysis_to_mapspec_layer(
        {"geojson": fc, "algorithm": "query_osm_poi"},
        {"id": "L1", "source": "s1"},
    )
    assert layer.get("type") == "circle", "3 个 Point 应推断 circle（无 profile 路径）"

    # helper 语义：混合几何 polygon 优先（与 _infer_geometry_category 同映射）
    assert _category_from_geometry_types(["Point", "Polygon"]) == "polygon"
    assert _category_from_geometry_types(["LineString"]) == "line"
    assert _category_from_geometry_types([]) is None
    assert _category_from_geometry_types(None) is None


# ─── store 时 field_schema → 派生 fields 证据（2026-08-25 会话"证据不完整"根因）───

def _descriptor_for_fc(fc):
    from app.schemas.ref_descriptor import compute_descriptor

    return compute_descriptor("ref:geojson:test-schema", fc).to_dict()


def test_compute_descriptor_field_schema_types_and_stats():
    fc = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [1, 2]},
             "properties": {"count": 5, "name": "武侯区", "flag": True, "note": None}},
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [1, 3]},
             "properties": {"count": 9, "name": "锦江区", "flag": False}},
        ],
    }
    d = _descriptor_for_fc(fc)
    schema = d["field_schema"]
    assert schema is not None and d["field_schema_complete"] is True
    assert schema["count"] == {"type": "number", "null_count": 0, "min": 5.0, "max": 9.0,
                               "sampleValues": [5, 9]}
    assert schema["name"]["type"] == "string"
    assert schema["name"]["sampleValues"] == ["武侯区", "锦江区"]
    assert schema["flag"]["type"] == "boolean"
    assert schema["note"]["null_count"] == 1  # null 只计数，不参与类型判定


def test_profile_from_descriptor_fields_evidence():
    fc = _fc(20)
    d = _descriptor_for_fc(fc)
    derived = profile_from_descriptor(d)
    assert derived["fields_status"] == "explicit"
    assert derived["fields"]["v"]["type"] == "number"
    assert derived["fields"]["v"]["min"] == 0 and derived["fields"]["v"]["max"] == 19
    assert derived["fields"]["name"]["type"] == "string"
    # 语义检查的关键消费面：PAINT_FIELD_EXISTS 可评（字段存在）
    assert "v" in derived["fields"]


def test_field_schema_truncation_falls_back_unknown():
    """命中 100 键上限 → complete=False → fields_status 回落 unknown（缺失字段
    不再构成权威缺失），但已收集字段仍是正向证据。"""
    from app.schemas.ref_descriptor import collect_field_schema

    features = [
        {"type": "Feature", "geometry": None,
         # count 排在 120 个键之前，确保截断发生前已被收集
         "properties": {"count": 1} | {f"f{i}": i for i in range(120)}},
    ]
    schema, complete = collect_field_schema(features)
    assert complete is False
    assert len(schema) == 100
    derived = profile_from_descriptor({
        "feature_count": 1, "bbox": None, "geometry_types": [],
        "field_schema": schema, "field_schema_complete": complete,
    })
    assert derived["fields_status"] == "unknown"
    assert "count" in derived["fields"]  # 已收集字段保留


def test_descriptor_roundtrip_preserves_field_schema():
    from app.schemas.ref_descriptor import RefDescriptor

    fc = _fc(3)
    d = _descriptor_for_fc(fc)
    restored = RefDescriptor.from_dict(d)
    assert restored.field_schema == d["field_schema"]
    assert restored.field_schema_complete is True
    # 旧 descriptor（无 field_schema 键）反序列化保持 None + 默认 complete
    legacy = RefDescriptor.from_dict({
        "ref_id": "r", "feature_count": 1, "point_count": 0,
        "geometry_types": [], "bbox": None, "mvt_capable": False,
        "raster_capable": False, "estimated_bytes": 0,
    })
    assert legacy.field_schema is None


def test_legacy_descriptor_without_schema_stays_unknown():
    derived = profile_from_descriptor({
        "feature_count": 10, "bbox": [0, 0, 1, 1], "geometry_types": ["Point"],
    })
    assert derived["fields_status"] == "unknown"
    assert derived["fields"] == {}


def test_mixed_type_field_is_string():
    """混合类型列（数值+字符串）→ "string"（与全量 profiler 的回退一致）。"""
    from app.schemas.ref_descriptor import collect_field_schema

    features = [
        {"type": "Feature", "geometry": None, "properties": {"x": 1}},
        {"type": "Feature", "geometry": None, "properties": {"x": "abc"}},
    ]
    schema, complete = collect_field_schema(features)
    assert complete is True
    assert schema["x"]["type"] == "string"
