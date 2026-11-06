"""B2 已知答案 oracle 小数据集（ADR-0284）—— 精确数值答案，非行为锚。

覆盖任务书点名维度：地理/投影、边界触碰、nodata、无效多边形、数值极值、
网络不连通。答案全部手算可验证（fixture 几何/数值刻意简单）。
与 tests/science_oracles（JSON 回放）正交：本模块是工作流分析缝的
known-answer 面——错误数据在这些用例里**不产成功结果**。
"""
from __future__ import annotations

import numpy as np
import pytest

# ── 1. 边界触碰 + 计数守恒（intersects 多方属约定）───────────────────────


def test_boundary_touch_count_conservation_known_answer():
    """4 点 × 2 区；2 点在共享边界上（多方属）→ 合计 = 2（区内）+ 2（边界
    双计）= 4 行；手工可验。"""
    import geopandas as gpd
    from shapely.geometry import Point, Polygon

    zones = gpd.GeoDataFrame(
        {"zone": ["west", "east"]},
        geometry=[Polygon([(0, 0), (1, 0), (1, 1), (0, 1), (0, 0)]),
                  Polygon([(1, 0), (2, 0), (2, 1), (1, 1), (1, 0)])],
        crs="EPSG:3857")
    pts = gpd.GeoDataFrame(
        {"id": [1, 2, 3, 4]},
        geometry=[Point(0.5, 0.5), Point(1.5, 0.5),
                  Point(1.0, 0.5), Point(1.0, 0.7)],
        crs="EPSG:3857")
    from app.lib.geo_analysis.aggregation import aggregate_with_denominator
    out, _evidence = aggregate_with_denominator(
        pts, zones, denominator_kind="count")
    counts = dict(zip(out["zone"], out["numerator"]))
    # 边界点（x=1.0）与两区都 intersects → 双计；合计 4+2=6 行
    assert counts == {"west": 3, "east": 3}


def test_zero_and_negative_denominator_rate_is_none():
    """零/负分母 → rate=None（绝不产 0/inf 假率）；has_support 区分无数据。"""
    import geopandas as gpd
    from shapely.geometry import Point, Polygon

    zones = gpd.GeoDataFrame(
        {"zone": ["a", "b", "c"], "pop": [0.0, -5.0, 100.0]},
        geometry=[Polygon([(0, 0), (1, 0), (1, 1), (0, 1), (0, 0)])] * 3,
        crs="EPSG:3857")
    pts = gpd.GeoDataFrame(
        {"v": [10.0]},
        geometry=[Point(0.5, 0.5)],
        crs="EPSG:3857")
    from app.lib.geo_analysis.aggregation import aggregate_with_denominator
    out, _ev = aggregate_with_denominator(
        pts, zones, numerator_field="v", denominator="pop",
        denominator_kind="field")
    rates = dict(zip(out["zone"], out["rate"]))
    assert rates["a"] is None and rates["b"] is None   # 零/负分母
    assert rates["c"] == pytest.approx(0.1)             # 10/100


def test_nan_numerator_excluded_and_counted():
    """NaN 分子被剔除并计数（不产 NaN 统计）；有效值合计精确。"""
    import geopandas as gpd
    from shapely.geometry import Point, Polygon

    zones = gpd.GeoDataFrame(
        {"zone": ["a"]},
        geometry=[Polygon([(0, 0), (1, 0), (1, 1), (0, 1), (0, 0)])],
        crs="EPSG:3857")
    pts = gpd.GeoDataFrame(
        {"v": [1.0, float("nan"), 3.0, float("inf")]},
        geometry=[Point(0.1, 0.5), Point(0.2, 0.5),
                  Point(0.3, 0.5), Point(0.4, 0.5)],
        crs="EPSG:3857")
    from app.lib.geo_analysis.aggregation import aggregate_with_denominator
    out, evidence = aggregate_with_denominator(
        pts, zones, numerator_field="v", denominator_kind="count")
    assert out["numerator"].iloc[0] == pytest.approx(4.0)   # 1+3；inf/nan 剔
    assert evidence["nan_numerator_excluded"] >= 1


# ── 2. 无效多边形（bowtie）zonal 修复披露 ────────────────────────────────


def test_bowtie_zone_zonal_strict_reject_and_repair_disclosure(tmp_path):
    """自交 zone：strict=True → InvalidGeometry typed 拒绝（不产错误统计）；
    strict=False → make_valid 修复 + geometry_repair 披露（修复永不静默）。"""
    import rasterio
    from rasterio.transform import from_origin
    from shapely.geometry import mapping

    data = np.full((2, 2), 5.0, dtype="float32")
    path = str(tmp_path / "flat.tif")
    with rasterio.open(
            path, "w", driver="GTiff", height=2, width=2, count=1,
            dtype="float32", crs="EPSG:4326",
            transform=from_origin(0.0, 1.0, 0.5, 0.5)) as dst:
        dst.write(data, 1)
    from shapely.geometry import Polygon as ShapelyPolygon

    bowtie = ShapelyPolygon([(0, 0), (1, 1), (1, 0), (0, 1), (0, 0)])
    fc = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"z": "bowtie"},
         "geometry": mapping(bowtie)}]}
    from app.lib.geo_analysis.raster_ops import zonal_statistics
    with pytest.raises(Exception):   # strict 默认拒绝（InvalidGeometry 族）
        zonal_statistics(fc, path)
    rows = zonal_statistics(fc, path, strict=False)
    assert rows and rows[0].get("geometry_repair"), rows
    assert rows[0]["mean"] == pytest.approx(5.0)   # 平坦栅格：修复后均值恒 5


# ── 3. 网络不连通：最近设施不产欧氏替代 ─────────────────────────────────


def test_disconnected_network_closest_facility_honest_unmatched():
    """两组件路网：需求点在主网、设施只在孤立组件 → 无匹配（unmatched
    显性列出），绝无欧氏直线替代冒充网络距离。"""
    from app.services.network.engine import NetworkGraphEngine

    main_net = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {},
         "geometry": {"type": "LineString", "coordinates": [
             [104.00, 30.60], [104.01, 30.60]]}},
        {"type": "Feature", "properties": {},
         "geometry": {"type": "LineString", "coordinates": [
             [104.01, 30.60], [104.01, 30.61]]}},
    ]}
    isolated = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {},
         "geometry": {"type": "LineString", "coordinates": [
             [104.50, 30.61], [104.52, 30.61]]}},
    ]}
    net = {"type": "FeatureCollection",
           "features": main_net["features"] + isolated["features"]}
    engine = NetworkGraphEngine()
    import asyncio

    res = asyncio.run(engine.solve_closest_facility(
        network=net,
        incidents=[{"type": "Feature", "properties": {"id": "d0"},
                    "geometry": {"type": "Point",
                                 "coordinates": [104.005, 30.602]}}],
        facilities=[{"type": "Feature", "properties": {"id": "f0"},
                     "geometry": {"type": "Point",
                                  "coordinates": [104.51, 30.61]}}],
        profile=None,
    ))
    summary = res.summary
    # 孤立设施不可达：路由 0 条 + unmatched 显性（绝不以欧氏 ~47km 冒充）
    assert summary.get("routes_found", 0) == 0
    assert summary.get("unmatched_demand_count", 0) >= 1
    assert not res.routes


# ── 4. 经度跨 ±180：聚合行为锚（已知约定，非环绕解）─────────────────────


def test_antimeridian_zone_aggregation_behavior_anchor():
    """跨 180° 的 zone（RFC 7946 §3.1.9 建议拆分）当前按线性坐标处理——
    行为锚（与 edge_cases.recommend_metric_crs_8 同纪律）：线性解释下该
    「跨经线」多边形是**绕世界大反带**（179.99→-179.99 经 0° 的长路径），
    ±179.995 两个点（真正的经线邻域）不落入 → 计数恒 0。这不是环绕解——
    环绕语义需要 §3.1.9 的拆分约定（未来工作，锚定当前行为防漂移）。"""
    import geopandas as gpd
    from shapely.geometry import Point, Polygon

    zone = Polygon([(179.99, 0.0), (-179.99, 0.0),
                    (-179.99, 1.0), (179.99, 1.0), (179.99, 0.0)])
    zones = gpd.GeoDataFrame({"zone": ["am"]}, geometry=[zone],
                             crs="EPSG:4326")
    pts = gpd.GeoDataFrame(
        {"id": [1, 2]},
        geometry=[Point(179.995, 0.5), Point(-179.995, 0.5)],
        crs="EPSG:4326")
    from app.lib.geo_analysis.aggregation import aggregate_with_denominator
    out, _ev = aggregate_with_denominator(pts, zones, denominator_kind="count")
    # 精确行为锚（实测钉扎）：线性语义 = 0——跨经线环绕带点不被计入
    assert int(out["numerator"].iloc[0]) == 0
    assert bool(out["has_support"].iloc[0]) is False
