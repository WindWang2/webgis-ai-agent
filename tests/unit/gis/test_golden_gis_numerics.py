"""Golden GIS 数值用例（V2 §38/§39）—— 小型、可人工推导、断言数值而非 success。

G1 buffer：投影 CRS 下点缓冲 1000m → 面积 ≈ π·1000²（容差 1%）；
G2 spatial join：inside / outside / boundary 三点语义明确；
G3 aggregation：3 面 × 10 点 → 每面计数精确（3/3/4）；
G4 NDVI：小矩阵 (NIR-Red)/(NIR+Red) 手工验证 + nodata 除零守卫 → NaN。
"""
import math

import numpy as np
import pytest
from shapely.geometry import shape

from app.lib.geo_analysis.aggregation import spatial_aggregate
from app.services.rs.band_math import INDEX_FORMULAS
from app.services.spatial_analyzer import SpatialAnalyzer


def _point(lon, lat, props=None):
    return {"type": "Feature", "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": props or {}}


def _fc(features):
    return {"type": "FeatureCollection", "features": features}


def _poly(minx, miny, maxx, maxy, props=None):
    ring = [[minx, miny], [maxx, miny], [maxx, maxy], [minx, maxy], [minx, miny]]
    return {"type": "Feature",
            "geometry": {"type": "Polygon", "coordinates": [ring]},
            "properties": props or {}}


# ── G1 buffer ──────────────────────────────────────────────────────────

def _metric_area_m2(feature_fc_geometry, working_crs: str) -> float:
    """输出 FC 是 WGS84 度 —— 量米制面积须回到分析时的工作 CRS。"""
    import geopandas as gpd

    gdf = gpd.GeoDataFrame(geometry=[shape(feature_fc_geometry)], crs="EPSG:4326")
    return float(gdf.to_crs(working_crs).area.iloc[0])


def test_g1_point_buffer_area_matches_pi_r2():
    res = SpatialAnalyzer.buffer(_fc([_point(104.06, 30.57)]), distance=1000.0, unit="m")
    assert res.success
    feats = res.data["features"]
    assert len(feats) == 1
    geom = shape(feats[0]["geometry"])
    assert geom.geom_type in ("Polygon", "MultiPolygon")
    expected = math.pi * 1000.0 ** 2
    # 输出在 WGS84 度；回到证据声明的米制工作 CRS 量面积（evidence 交叉验证）
    area = _metric_area_m2(feats[0]["geometry"], res.evidence["working_crs"])
    assert area == pytest.approx(expected, rel=0.01)  # ≤1% 投影偏差
    assert res.evidence["working_crs"].startswith("EPSG:")
    assert res.evidence["input_count"] == 1 and res.evidence["output_count"] == 1


def test_g1_km_unit_scales_quadratically():
    res = SpatialAnalyzer.buffer(_fc([_point(104.06, 30.57)]), distance=1.0, unit="km")
    area = _metric_area_m2(res.data["features"][0]["geometry"], res.evidence["working_crs"])
    assert area == pytest.approx(math.pi * 1000.0 ** 2, rel=0.01)


# ── G2 spatial join ────────────────────────────────────────────────────

def test_g2_spatial_join_inside_outside_boundary():
    poly = _poly(104.0, 30.5, 104.5, 31.0)
    points = _fc([
        _point(104.2, 30.7, {"name": "inside"}),
        _point(105.5, 31.5, {"name": "outside"}),
        _point(104.0, 30.5, {"name": "boundary"}),  # 左下角点 —— 精确在边界上
    ])
    res = SpatialAnalyzer.spatial_join(points, _fc([poly]), predicate="intersects")
    assert res.success
    names = sorted(f["properties"].get("name", "") for f in res.data["features"])
    # intersects 语义：边界接触即命中（包含边界点），外部点不命中
    assert names == ["boundary", "inside"]


def test_g2_spatial_join_within_excludes_boundary():
    poly = _poly(104.0, 30.5, 104.5, 31.0)
    points = _fc([
        _point(104.2, 30.7, {"name": "inside"}),
        _point(104.0, 30.5, {"name": "boundary"}),
    ])
    res = SpatialAnalyzer.spatial_join(points, _fc([poly]), predicate="within")
    names = [f["properties"].get("name") for f in res.data["features"]]
    assert names == ["inside"]  # within 语义：边界点不属于内部


# ── G3 aggregation ─────────────────────────────────────────────────────

def test_g3_aggregate_exact_counts():
    polys = _fc([
        _poly(104.0, 30.5, 104.1, 30.6, {"name": "A"}),
        _poly(104.2, 30.5, 104.3, 30.6, {"name": "B"}),
        _poly(104.4, 30.5, 104.5, 30.6, {"name": "C"}),
    ])
    pts = [_point(104.05 + 0.001 * i, 30.55) for i in range(3)] \
        + [_point(104.25 + 0.001 * i, 30.55) for i in range(3)] \
        + [_point(104.45 + 0.001 * i, 30.55) for i in range(4)]
    res = spatial_aggregate(_fc(pts), polys, stats=["count"])
    assert res.success
    by_name = {f["properties"]["name"]: f["properties"]["count"]
               for f in res.data["features"]}
    assert by_name == {"A": 3, "B": 3, "C": 4}
    assert res.evidence["input_count"] == 10
    assert res.evidence["output_count"] == 3


def test_g3_aggregate_value_field_sum():
    polys = _fc([_poly(104.0, 30.5, 104.1, 30.6, {"name": "A"})])
    pts = _fc([
        _point(104.02, 30.55, {"v": 10}),
        _point(104.06, 30.55, {"v": 5}),
    ])
    res = spatial_aggregate(pts, polys, stats=["sum"], value_field="v")
    feats = res.data["features"]
    assert feats[0]["properties"]["sum"] == pytest.approx(15.0)
    assert feats[0]["properties"]["count"] == 2


# ── G4 NDVI ────────────────────────────────────────────────────────────

def test_g4_ndvi_formula_known_values():
    _, fn = INDEX_FORMULAS["ndvi"]
    red = np.array([[0.2], [0.3]])
    nir = np.array([[0.8], [0.6]])
    out = fn(r=red, nir=nir)
    assert out.shape == (2, 1)
    assert out[0, 0] == pytest.approx((0.8 - 0.2) / (0.8 + 0.2))  # 0.6
    assert out[1, 0] == pytest.approx((0.6 - 0.3) / (0.6 + 0.3))  # ≈0.3333


def test_g4_ndvi_nodata_pixels_stay_nan():
    """Sentinel-2 L2A 全零 nodata 波段 → NaN（不是貌似合理的 0.0 指数）。"""
    _, fn = INDEX_FORMULAS["ndvi"]
    red = np.array([[0.0], [0.4]])
    nir = np.array([[0.0], [0.5]])
    out = fn(r=red, nir=nir)
    assert math.isnan(out[0, 0])
    assert out[1, 0] == pytest.approx((0.5 - 0.4) / 0.9)


def test_g4_ndvi_value_range_bounded():
    rng = np.random.default_rng(42)
    red = rng.uniform(0.01, 1.0, (16, 16))
    nir = rng.uniform(0.01, 1.0, (16, 16))
    out = INDEX_FORMULAS["ndvi"][1](r=red, nir=nir)
    valid = out[~np.isnan(out)]
    assert valid.size == 256
    assert float(valid.min()) >= -1.0 and float(valid.max()) <= 1.0
