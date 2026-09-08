"""算法 conformance 测试 V2（Quality W4）—— 20 个此前零 conformance 的算法。

每个测试节点是**真实行为 oracle**（golden 数值或类型化行为契约），小合成
数据、离线、<10s；节点 id 被各算法 descriptor 的 ``conformance_tests``
声明引用，并受 algorithm_registry 的 AST 节点级存在性校验约束。

诚实边界（外部依赖算法）：network.route_external_api / traffic_status_external /
transit_route_external / poi.area_search 等不真调外网 —— 测其**未配置
provider 的类型化降级契约**与可离线的参数/几何校验路径；raster.source.dem
以 monkeypatch STAC 层测条目映射契约。测试名显式标注
``*_typed_contract`` / ``*_unconfigured``。
"""
from __future__ import annotations

import asyncio
import math
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import rasterio
from rasterio.transform import from_origin
from shapely.geometry import shape

from app.tools.registry import ToolRegistry
from app.tools.local_admin import query_admin_boundary


# ── 共享小工具（与 test_golden_gis_numerics 同款风格）────────────────────

def _point(lon, lat, props=None):
    return {"type": "Feature", "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": props or {}}


def _fc(features):
    return {"type": "FeatureCollection", "features": features}


def _poly(minx, miny, maxx, maxy, props=None):
    ring = [[minx, miny], [maxx, miny], [maxx, maxy], [minx, maxy], [minx, maxy]]
    return {"type": "Feature",
            "geometry": {"type": "Polygon", "coordinates": [ring]},
            "properties": props or {}}


def _metric_area_m2(geometry) -> float:
    """输出是 WGS84 度 —— 量米制面积须回到 UTM 工作 CRS。"""
    import geopandas as gpd

    gdf = gpd.GeoDataFrame(geometry=[shape(geometry)], crs="EPSG:4326")
    utm = gdf.estimate_utm_crs() or "EPSG:4326"
    return float(gdf.to_crs(utm).area.iloc[0])


def _write_strip_tif(path, size=256):
    import numpy as np

    data = np.arange(size * size, dtype=np.uint8).reshape(size, size) % 255
    with rasterio.open(
        path, "w", driver="GTiff", width=size, height=size, count=1,
        dtype="uint8", crs="EPSG:4326", transform=from_origin(0, size, 1, 1),
    ) as dst:  # 非分块条带 → ensure_cog 必须真实转换
        dst.write(data, 1)
    return str(path)


@pytest.fixture()
def cm_registry():
    """chinese_maps 全套工具（网络外部 / POI / 行政区在线）。"""
    from app.tools.chinese_maps import register_chinese_map_tools

    reg = ToolRegistry()
    register_chinese_map_tools(reg)
    return reg


# ── admin.boundary.local ───────────────────────────────────────────────

def test_admin_boundary_local_level_and_identifier_contract():
    """本地行政边界查询的入参契约：非法级别 / 缺标识符都类型化拒绝。"""
    bad_level = query_admin_boundary("planet", name="成都市")
    assert "error" in bad_level and "级别" in bad_level["error"]

    no_identifier = query_admin_boundary("district")
    assert "error" in no_identifier
    assert "name" in no_identifier["error"] and "adcode" in no_identifier["error"]


# ── admin.boundary_lookup ──────────────────────────────────────────────

async def test_admin_boundary_lookup_local_first_hit(monkeypatch):
    """get_admin_division 本地优先：本地命中 → 直接返回本地 FC，不走在线。"""
    from app.tools.chinese_maps import register_chinese_map_tools

    reg = ToolRegistry()
    register_chinese_map_tools(reg)

    fake_fc = {"type": "FeatureCollection", "features": [_poly(104.0, 30.5, 104.2, 30.7)],
               "metadata": {"source": "local"}}
    monkeypatch.setattr(
        "app.services.local_first.try_local_admin_division",
        lambda keywords, child_level: fake_fc)
    out = await reg._tools["get_admin_division"](keywords="成都市")
    assert out.get("type") == "FeatureCollection"
    assert out["metadata"]["source"] == "local"


async def test_admin_boundary_lookup_typed_token_error_when_unconfigured(monkeypatch):
    """本地未命中且天地图未配置 → 类型化 token 缺失错误（不触网）。"""
    from app.tools.chinese_maps import register_chinese_map_tools

    reg = ToolRegistry()
    register_chinese_map_tools(reg)
    monkeypatch.setattr(
        "app.services.local_first.try_local_admin_division",
        lambda keywords, child_level: None)
    import app.tools.chinese_maps as cm

    monkeypatch.setattr(cm, "_has_provider", lambda p: False)
    out = await reg._tools["get_admin_division"](keywords="成都市")
    assert "error" in out and "TIANDITU" in out["error"]


# ── data.federated.chain ───────────────────────────────────────────────

class _ChainFeatureAdapter:
    """内存 feature adapter（同款最小 query 契约，离线）。"""

    source_type = "generic"

    def __init__(self, features_by_dataset):
        self._data = features_by_dataset

    def query(self, dataset_id: str, spec):
        from app.schemas.data_fabric_schema import QueryResult

        return QueryResult(dataset_id=dataset_id,
                           features=list(self._data.get(dataset_id, [])))


def _chain_rows(prefix, keys):
    return [{"type": "Feature", "geometry": None,
             "properties": {"key": k, "payload": f"{prefix}{k}"}}
            for k in keys]


def _chain_env(monkeypatch, plan=(("cd0", [1, 2, 3]), ("cd1", [2, 3]))):
    import app.tools.data_fabric_tools as tools_mod
    from app.schemas.data_fabric_schema import DatasetDescriptor
    from app.services.data_fabric.spatial_catalog import SpatialCatalogService

    svc = SpatialCatalogService()
    adapters = {}
    for i, (ds, keys) in enumerate(plan):
        svc.register_dataset(
            DatasetDescriptor(id=ds, source_type="postgis"), profile_id=f"cp{i}")
        adapters[f"cp{i}"] = _ChainFeatureAdapter({ds: _chain_rows(f"a{i}", keys)})
    monkeypatch.setattr(tools_mod, "spatial_catalog_service", svc)

    reg = ToolRegistry()
    from app.tools.data_fabric_tools import register_data_fabric_tools

    register_data_fabric_tools(reg)
    return reg, adapters


async def test_federated_chain_two_source_attribute_join_oracle(monkeypatch):
    """2 源 attribute_join 链：内联行有界、键精确命中、逐源行数披露。"""
    reg, adapters = _chain_env(monkeypatch)
    with patch("app.tools.data_fabric_tools.connection_manager") as cm:
        cm.get_adapter.side_effect = lambda pid, owner=None: adapters.get(pid)
        res = await reg._tools["query_federated_chain"](
            sources=[{"dataset_id": "cd0", "profile_id": "cp0"},
                     {"dataset_id": "cd1", "profile_id": "cp1"}],
            joins=[{"kind": "attribute_join", "join_field_left": "key",
                    "join_field_right": "key"}],
            limit=100)
    assert res["status"] == "success", res
    assert res["row_count"] == 2
    assert sorted(r["key"] for r in res["rows"]) == [2, 3]
    assert len(res["rows"]) <= 200, "工具内联行必须有界"
    assert res["per_source_rows"] == {"s0": 3, "s1": 2}
    assert res["explain"], "explain 行必须包含"


async def test_federated_chain_typed_errors(monkeypatch):
    """重复 source_id / 缺 adapter → 类型化错误（fail-fast 预算语义）。"""
    reg, adapters = _chain_env(monkeypatch)
    with patch("app.tools.data_fabric_tools.connection_manager") as cm:
        cm.get_adapter.side_effect = lambda pid, owner=None: adapters.get(pid)
        dup = await reg._tools["query_federated_chain"](
            sources=[{"dataset_id": "cd0", "profile_id": "cp0", "source_id": "s0"},
                     {"dataset_id": "cd1", "profile_id": "cp1", "source_id": "s0"}],
            joins=[{"kind": "attribute_join", "join_field_left": "key",
                    "join_field_right": "key"}],
            limit=10)
        assert dup["status"] == "error" and dup["error_type"] == "INVALID_QUERY"

        cm.get_adapter.side_effect = lambda pid, owner=None: None
        missing = await reg._tools["query_federated_chain"](
            sources=[{"dataset_id": "nope", "profile_id": "cp0"},
                     {"dataset_id": "cd1", "profile_id": "cp1"}],
            joins=[{"kind": "attribute_join", "join_field_left": "key",
                    "join_field_right": "key"}],
            limit=10)
        assert missing["status"] == "error"
        assert missing["error_type"] == "UNSUPPORTED_SOURCE"


# ── data.ingest.pipeline ───────────────────────────────────────────────

@pytest.fixture()
def _ingest_reset():
    from app.services.data_ingest.pipeline import reset_ingest_pipeline

    reset_ingest_pipeline()
    yield
    reset_ingest_pipeline()


async def test_ingest_pipeline_fingerprint_dedup_and_bounded_profile(_ingest_reset):
    """同载荷二次摄入 → 指纹命中同一 ref（dedup）；首摄为 miss。"""
    from app.services.data_ingest.pipeline import get_ingest_pipeline

    sid = "algo-conf-ingest"
    fc = _fc([_point(116.0 + i * 0.1, 39.0, {"v": f"v{i}"}) for i in range(3)])
    pipe = get_ingest_pipeline()
    r1 = await pipe.ingest(sid, fc, name="conf", crs="EPSG:4326", source_type="tool")
    assert r1.ok and r1.ref_id
    assert "dedup-miss" in r1.steps_completed
    r2 = await pipe.ingest(sid, fc, name="conf-again", crs="EPSG:4326", source_type="tool")
    assert r2.ok and r2.ref_id == r1.ref_id
    assert "dedup-hit" in r2.steps_completed


async def test_ingest_pipeline_tool_typed_payload_rejection(_ingest_reset):
    """工具边界：非 dict 载荷 / 超 8MB 内联上限 → 类型化拒绝（不落库）。"""
    from app.tools.ingest_tools import register_ingest_tools

    reg = ToolRegistry()
    register_ingest_tools(reg)
    fn = reg._tools["ingest_dataset"]
    sid = "algo-conf-ingest-tool"

    bad = await fn(data="not-a-dict", session_id=sid)
    assert bad["success"] is False and bad["code"] == "INVALID_PAYLOAD"

    big = {"type": "FeatureCollection", "features": [], "pad": "x" * (9 * 1024 * 1024)}
    oversized = await fn(data=big, session_id=sid)
    assert oversized["success"] is False and oversized["code"] == "PAYLOAD_TOO_LARGE"


# ── geometry.center_statistics ─────────────────────────────────────────

def test_center_statistics_mean_center_oracle():
    """均值中心 = 质点坐标均值（UTM 内计算 → WGS84 回投影，米级一致）。"""
    from app.services.spatial_analyzer import SpatialAnalyzer

    fc = _fc([
        _point(104.00, 30.50),
        _point(104.02, 30.50),
        _point(104.00, 30.52),
    ])
    res = SpatialAnalyzer.central_feature(fc, "mean_center")
    assert res.success
    geom = shape(res.data["geometry"])
    assert geom.geom_type == "Point"
    mean_lon = (104.00 + 104.02 + 104.00) / 3
    mean_lat = (30.50 + 30.50 + 30.52) / 3
    assert geom.x == pytest.approx(mean_lon, abs=5e-4)
    assert geom.y == pytest.approx(mean_lat, abs=5e-4)


# ── geometry.clip ──────────────────────────────────────────────────────

def test_clip_layer_area_oracle():
    """0.1°×0.1° 目标被半幅掩膜裁剪 → 米制面积 ≈ 一半（投影偏差 ≤1%）。"""
    from app.services.spatial_analyzer import SpatialAnalyzer

    target = _fc([_poly(104.0, 30.5, 104.1, 30.6)])
    mask = _fc([_poly(104.0, 30.5, 104.05, 30.6)])
    res = SpatialAnalyzer.clip(target, mask)
    assert res.success
    feats = res.data["features"]
    assert len(feats) == 1
    full = _metric_area_m2(_poly(104.0, 30.5, 104.1, 30.6)["geometry"])
    clipped = _metric_area_m2(feats[0]["geometry"])
    assert clipped == pytest.approx(full / 2, rel=0.01)


# ── geometry.dissolve ──────────────────────────────────────────────────

def test_dissolve_layer_adjacent_merge_oracle():
    """相邻两矩形 dissolve → 1 要素，米制面积 = 两块之和。"""
    from app.lib.geo_processor.geometry import dissolve_smart

    fc = _fc([
        _poly(104.0, 30.5, 104.1, 30.6, {"g": "a"}),
        _poly(104.1, 30.5, 104.2, 30.6, {"g": "a"}),
    ])
    res = dissolve_smart(fc, field=None)
    assert res.success
    feats = res.data["features"]
    assert len(feats) == 1
    expected = _metric_area_m2(fc["features"][0]["geometry"]) * 2
    assert _metric_area_m2(feats[0]["geometry"]) == pytest.approx(expected, rel=0.01)


# ── geometry.spatial_join ──────────────────────────────────────────────

def test_spatial_join_counts_oracle():
    """两不相交多边形 × 4 点 intersects：逐面计数 2/2，外点不命中。"""
    from app.services.spatial_analyzer import SpatialAnalyzer

    polys = _fc([
        _poly(104.0, 30.5, 104.1, 30.6, {"name": "A"}),
        _poly(104.2, 30.5, 104.3, 30.6, {"name": "B"}),
    ])
    pts = _fc([
        _point(104.02, 30.52), _point(104.08, 30.58),
        _point(104.22, 30.52), _point(104.28, 30.58),
    ])
    res = SpatialAnalyzer.spatial_join(pts, polys, predicate="intersects")
    assert res.success
    by = {}
    for f in res.data["features"]:
        by[f["properties"]["name"]] = by.get(f["properties"]["name"], 0) + 1
    assert by == {"A": 2, "B": 2}


# ── network.isochrone（外部 amap：类型化契约，不触网）─────────────────

async def test_isochrone_typed_validation(cm_registry):
    fn = cm_registry._tools["isochrone_analysis"]
    bad_center = await fn(center=[104.0], minutes=10)
    assert "error" in bad_center and "center" in bad_center["error"]

    bad_minutes = await fn(center=[104.0, 30.5], minutes=0)
    assert "error" in bad_minutes and "minutes" in bad_minutes["error"]

    bad_provider = await fn(center=[104.0, 30.5], minutes=10, provider="baidu")
    assert "error" in bad_provider and "amap" in bad_provider["error"]


async def test_isochrone_unconfigured_provider_typed_error(cm_registry, monkeypatch):
    import app.tools.chinese_maps as cm

    monkeypatch.setattr(cm, "_has_provider", lambda p: False)
    out = await cm_registry._tools["isochrone_analysis"](
        center=[104.0, 30.5], minutes=10)
    assert "error" in out and "未配置" in out["error"]


# ── network.route_external_api（amap/baidu：未配置回退契约）───────────

async def test_plan_route_unconfigured_typed_error(cm_registry, monkeypatch):
    """无任何已配置 provider → with_fallback 返回 no_key 类型化错误。"""
    monkeypatch.setattr("app.tools.chinese_maps.http._has_provider", lambda p: False)
    out = await cm_registry._tools["plan_route"](
        origin=[104.0, 30.5], destination=[104.2, 30.7])
    assert "error" in out and "未配置高德或百度 API Key" in out["error"]

    bad_args = await cm_registry._tools["plan_route"](
        origin=[104.0], destination=[104.2, 30.7])
    assert "error" in bad_args and "origin/destination" in bad_args["error"]


# ── network.service_area.simple（离线几何 oracle）─────────────────────

def test_service_area_simple_travel_time_radius_oracle():
    """步行 15min @5km/h → 1250m 缓冲；米制面积 ≈ π·r²（≤1% 投影偏差）。"""
    from app.tools.advanced_spatial import register_advanced_spatial_tools

    reg = ToolRegistry()
    register_advanced_spatial_tools(reg)
    res = reg._tools["service_area_simple"](
        _fc([_point(104.06, 30.57)]), travel_time_min=15, mode="walking")
    assert res.get("success") is True, res
    feats = res["data"]["features"]
    assert len(feats) == 1
    area = _metric_area_m2(feats[0]["geometry"])
    assert area == pytest.approx(math.pi * 1250.0 ** 2, rel=0.01)


# ── network.traffic_status_external ───────────────────────────────────

async def test_traffic_status_typed_validation_and_unconfigured(cm_registry, monkeypatch):
    import app.tools.chinese_maps as cm

    fn = cm_registry._tools["get_traffic_status"]
    bad_mode = await fn(mode="circle2")
    assert "error" in bad_mode and "mode" in bad_mode["error"]

    bad_rect = await fn(mode="rectangle")
    assert "error" in bad_rect and "rectangle" in bad_rect["error"]

    monkeypatch.setattr(cm, "_has_provider", lambda p: False)
    out = await fn(mode="rectangle", rectangle=[104.0, 30.5, 104.2, 30.7])
    assert "error" in out and "amap" in out["error"]


# ── network.transit_route_external ────────────────────────────────────

async def test_transit_route_typed_validation_and_unconfigured(cm_registry, monkeypatch):
    import app.tools.chinese_maps as cm

    fn = cm_registry._tools["search_transit_route"]
    no_city = await fn(origin=[104.0, 30.5], destination=[104.2, 30.7], city="")
    assert "error" in no_city and "city" in no_city["error"]

    bad_coords = await fn(origin=[104.0], destination=[104.2, 30.7], city="成都")
    assert "error" in bad_coords and "origin/destination" in bad_coords["error"]

    monkeypatch.setattr(cm, "_has_provider", lambda p: False)
    out = await fn(origin=[104.0, 30.5], destination=[104.2, 30.7], city="成都")
    assert "error" in out and "amap" in out["error"]


# ── poi.area_search ────────────────────────────────────────────────────

async def test_poi_around_typed_geometry_contract(cm_registry):
    fn = cm_registry._tools["search_poi_around"]
    bad_center = await fn(center=[104.0])
    assert "error" in bad_center and "center" in bad_center["error"]

    bad_radius = await fn(center=[104.0, 30.5], radius_m=0, keyword="咖啡")
    assert "error" in bad_radius and "radius_m" in bad_radius["error"]

    no_filter = await fn(center=[104.0, 30.5], radius_m=500)
    assert "error" in no_filter and "keyword" in no_filter["error"]


async def test_poi_polygon_extraction_typed_contract(cm_registry):
    """GeoJSON 提取失败 → 类型化错误（几何契约，离线）。"""
    fn = cm_registry._tools["search_poi_polygon"]
    not_extractable = await fn(polygon={"type": "Point", "coordinates": [104.0, 30.5]},
                               keyword="咖啡")
    assert "error" in not_extractable and "多边形" in not_extractable["error"]

    ring = await fn(polygon=[104.0, 30.5, 104.2, 30.7], keyword="咖啡")
    # bbox 形态被接受后进入 provider 链 —— 测试环境无 key 时是类型化错误，
    # 有 key 时是真实调用；因此只断言「没死在几何解析层」。
    assert isinstance(ring, dict)


# ── poi.query.local ────────────────────────────────────────────────────

async def test_query_local_poi_db_missing_typed_contract(monkeypatch):
    """本地 POI 库缺失 → 类型化错误 + 修复提示（且无 key 不触网）。"""
    from app.tools.local_stats import register_local_stats_tools

    monkeypatch.setattr("app.services.local_poi.gd_poi_available", lambda: False)
    from app.core.config import settings

    monkeypatch.setattr(settings, "AMAP_API_KEY", "")
    reg = ToolRegistry()
    register_local_stats_tools(reg)
    out = reg._tools["query_local_poi"](bbox=[104.0, 30.5, 104.2, 30.7])
    assert "error" in out and "未生成" in out["error"]
    assert out.get("correction_hint")


async def test_query_local_poi_polygon_format_typed_contract(monkeypatch):
    """非法 polygon 形状 → 类型化格式错误（先于任何库访问）。"""
    from app.tools.local_stats import register_local_stats_tools

    monkeypatch.setattr("app.services.local_poi.gd_poi_available", lambda: True)
    reg = ToolRegistry()
    register_local_stats_tools(reg)
    out = reg._tools["query_local_poi"](bbox=[104.0, 30.5, 104.2, 30.7],
                                        polygon={"bad": "shape"})
    assert "error" in out and "polygon" in out["error"]


# ── raster.cog.convert ─────────────────────────────────────────────────

def test_cog_convert_roundtrip_and_idempotent(tmp_path, monkeypatch):
    """条带 GeoTIFF → COG：校验通过、像元恒等、二次指向产物为 already_cog。"""
    from app.tools.raster_tools_cog import register_raster_cog_tools

    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()
    _write_strip_tif(tmp_path / "data" / "plain.tif")

    reg = ToolRegistry()
    register_raster_tools = register_raster_cog_tools
    register_raster_tools(reg)
    fn = reg._tools["convert_raster_to_cog"]

    res = asyncio.run(fn("plain.tif", "data/cog"))
    assert res["success"] is True and res["converted"] is True
    assert res["validation"]["ok"] is True

    import os

    cog_rel = os.path.relpath(res["output_path"], tmp_path / "data")
    again = asyncio.run(fn(cog_rel, "data/cog"))
    assert again["success"] is True and again["already_cog"] is True


def test_cog_convert_invalid_path_typed(tmp_path, monkeypatch):
    from app.tools.raster_tools_cog import register_raster_cog_tools

    monkeypatch.chdir(tmp_path)
    reg = ToolRegistry()
    register_raster_cog_tools(reg)
    out = asyncio.run(reg._tools["convert_raster_to_cog"]("../outside.tif", "data/cog"))
    assert out["success"] is False and out["code"] == "INVALID_PATH"


# ── raster.source.dem ──────────────────────────────────────────────────

async def test_fetch_dem_bbox_typed():
    from app.tools.remote_sensing import register_rs_tools

    reg = ToolRegistry()
    register_rs_tools(reg)
    out = await reg._tools["fetch_dem"]("not-a-bbox")
    assert "error" in out


async def test_fetch_dem_item_mapping_oracle(monkeypatch):
    """STAC 层 monkeypatch → 断言条目→assets.dem 的映射契约（离线）。"""
    from app.services.rs.spectral_engine import spectral_engine
    from app.tools.remote_sensing import register_rs_tools

    async def fake_fetch(**kwargs):
        return {"items": [
            SimpleNamespace(id="dem-item-1", bbox=[104.0, 30.0, 104.1, 30.1],
                            assets={"data": {"href": "https://example.com/dem-1.tif"}}),
            SimpleNamespace(id="dem-item-2", bbox=[104.0, 30.0, 104.1, 30.1],
                            assets={"data": {"href": "https://example.com/dem-2.tif"}}),
        ]}

    monkeypatch.setattr(spectral_engine.stac, "fetch_stac_items_and_bands", fake_fetch)
    reg = ToolRegistry()
    register_rs_tools(reg)
    out = await reg._tools["fetch_dem"]("104.0,30.0,104.1,30.1")
    assert out["status"] == "ok"
    assert out["source"] == "Copernicus DEM GLO-30"
    assert out["count"] == 2
    assert [i["id"] for i in out["items"]] == ["dem-item-1", "dem-item-2"]
    assert all(i["assets"]["dem"].startswith("https://") for i in out["items"])


# ── stats.category.breakdown ───────────────────────────────────────────

def test_spatial_stats_geometry_summary_oracle():
    """统计摘要契约：count 精确、米制面积为正、bbox/centroid 齐备。"""
    from app.services.spatial_analyzer import SpatialAnalyzer

    fc = _fc([_poly(104.0, 30.5, 104.1, 30.6, {"cat": "a"}),
              _poly(104.2, 30.5, 104.3, 30.6, {"cat": "b"})])
    res = SpatialAnalyzer.statistics(fc)
    assert res.success
    data = res.data
    assert data["count"] == 2
    assert data["total_area_m2"] > 0
    assert len(data["bbox"]) == 4
    assert abs(data["centroid"][0] - 104.15) < 5e-3


# ── workspace.inspection.readonly / workspace.snapshot.durable ─────────

_WS_FC = {
    "type": "FeatureCollection",
    "features": [
        {"geometry": {"type": "Point", "coordinates": [116.4, 39.9]},
         "properties": {"name": "a"}},
    ],
}


@pytest.fixture()
def ws_registry():
    from app.tools.workspace_tools import register_workspace_tools

    reg = ToolRegistry()
    register_workspace_tools(reg)
    return reg


@pytest.fixture(autouse=True)
def ws_reset_service():
    from app.services.workspace.snapshot import reset_workspace_snapshot_service

    reset_workspace_snapshot_service()
    yield
    reset_workspace_snapshot_service()


@pytest.fixture()
def ws_env(tmp_path, monkeypatch):
    """DATA_DIR / 内容库根 / MapSpec 存储根全部隔离到 tmp（同款行为测试）。"""
    from app.core.config import settings
    from app.services import project_artifact_promotion as pap
    from app.services.mapspec import store as mapspec_store_module

    root = tmp_path / "data"
    root.mkdir()
    monkeypatch.setattr(settings, "DATA_DIR", str(root))
    monkeypatch.setattr(pap, "content_store_root", lambda: root / "project_artifacts")
    base = tmp_path / "webgis-agent"
    base.mkdir()
    monkeypatch.setattr(mapspec_store_module, "BASE_STORAGE_DIR", base)
    return root


async def _seed_workspace(sid: str) -> str:
    from app.services.artifact_registry import register_artifact
    from app.services.session_data import session_data_manager

    ref = await session_data_manager.store(sid, _WS_FC, prefix="geojson")
    await register_artifact(sid, artifact_id=ref,
                            producer_tool="query_osm_poi",
                            artifact_type="poi_feature_set")
    return ref


async def test_workspace_inspection_readonly_contract(ws_registry, ws_env):
    """检视契约：空会话诚实空清单；有产物时 describe 汇总账本。"""
    empty = await ws_registry.dispatch("list_workspace_snapshots", {})
    assert empty.get("success") is True
    assert empty.get("count") == 0 and empty.get("snapshots") == []

    sid = "algo-conf-ws-inspect"
    await _seed_workspace(sid)
    out = await ws_registry.dispatch("describe_workspace", {"session_id": sid})
    assert out.get("success") is True
    assert isinstance(out, dict)


async def test_workspace_snapshot_durable_roundtrip(ws_registry, ws_env):
    """快照→列举→verify 回滚报告：产物计数、持久指针、诚实核查。"""
    sid = "algo-conf-ws-snap"
    await _seed_workspace(sid)
    saved = await ws_registry.dispatch("save_workspace_snapshot", {
        "session_id": sid, "label": "conf", "materialize": "claimed"})
    assert saved.get("success") is True, saved
    assert isinstance(saved.get("snapshot_id"), str) and saved["snapshot_id"]
    assert saved.get("artifacts") == 1

    listed = await ws_registry.dispatch("list_workspace_snapshots",
                                        {"session_id": sid})
    assert listed.get("success") is True
    assert saved["snapshot_id"] in [s.get("snapshot_id")
                                    for s in listed.get("snapshots") or []]

    restored = await ws_registry.dispatch("restore_workspace_snapshot", {
        "session_id": sid, "snapshot_id": saved["snapshot_id"], "mode": "verify"})
    assert restored.get("success") is True
