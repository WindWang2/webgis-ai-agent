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
