"""V4 制图算法库测试：dot_density / bivariate / raster_render."""
from __future__ import annotations

import math

import numpy as np
import pytest
from shapely.geometry import Polygon, mapping

from app.lib.cartography.bivariate import (
    BIVARIATE_MATRICES,
    bivariate_class_colors,
    bivariate_legend_spec,
    bivariate_match_expression,
    compute_bivariate_classes,
)
from app.lib.cartography.dot_density import (
    MAX_TOTAL_DOTS,
    dots_for_value,
    generate_dot_density_features,
    suggest_unit_value,
    _halton,
)
from app.lib.cartography.raster_render import (
    blend_arrays,
    classify_array,
    equal_interval_breaks,
    hillshade_array,
    normalize_min_max,
)

pytestmark = pytest.mark.cartography


# ── dot_density ───────────────────────────────────────────────────────


def _poly_feat(value: float, coords=None) -> dict:
    c = coords or [(0, 0), (10, 0), (10, 10), (0, 10), (0, 0)]
    return {
        "type": "Feature",
        "geometry": mapping(Polygon(c)),
        "properties": {"pop": value, "pid": "p1"},
    }


def test_halton_deterministic_and_bounded():
    assert _halton(1, 2) == 0.5
    a = [_halton(i, 3) for i in range(1, 50)]
    b = [_halton(i, 3) for i in range(1, 50)]
    assert a == b
    assert all(0.0 <= x < 1.0 for x in a)


def test_dots_for_value_contracts():
    assert dots_for_value(0, 10) == 0
    assert dots_for_value(-5, 10) == 0
    assert dots_for_value(float("nan"), 10) == 0
    assert dots_for_value(25, 10) == 2  # round(2.5)=2（banker's rounding，确定性）
    with pytest.raises(ValueError):
        dots_for_value(10, 0)


def test_dot_generation_deterministic_inside_polygon():
    feats = [_poly_feat(100)]
    r1 = generate_dot_density_features(feats, value_field="pop", unit_value=1.0)
    r2 = generate_dot_density_features(feats, value_field="pop", unit_value=1.0)
    assert r1["features"] == r2["features"]
    meta = r1["__dot_density_meta"]
    assert meta["total_dots"] == len(r1["features"]) == 100
    assert not meta["truncated"]
    assert "示意性重分布" in meta["disclosure_zh"]
    poly = Polygon([(0, 0), (10, 0), (10, 10), (0, 10), (0, 0)])
    for f in r1["features"]:
        x, y = f["geometry"]["coordinates"]
        assert poly.contains(__import__("shapely.geometry", fromlist=["Point"]).Point(x, y))
        assert f["properties"]["__dot_value"] == 1.0


def test_dot_generation_truncation_guard():
    feats = [_poly_feat(10**9)]
    out = generate_dot_density_features(
        feats, value_field="pop", unit_value=1.0)
    assert out["__dot_density_meta"]["truncated"]
    assert out["__dot_density_meta"]["total_dots"] == MAX_TOTAL_DOTS


def test_dot_skips_invalid_and_suggests_unit():
    feats = [_poly_feat(float("nan")), _poly_feat(50)]
    out = generate_dot_density_features(feats, value_field="pop", unit_value=1.0)
    assert out["__dot_density_meta"]["total_dots"] == 50
    assert suggest_unit_value([1, 2, 3]) == 1.0
    # vmax=5000, target 800 → raw 6.25 → 10
    assert suggest_unit_value([5000]) == 10.0
    assert suggest_unit_value([]) == 1.0
    # V4 修复：小量级数据（率/占比）步长 <1，不再退化 1.0
    assert suggest_unit_value([0.1, 0.2, 0.5]) == 0.001


def test_dot_small_value_strict_rounding():
    # <0.5 点的面如实为 0（强制 ≥1 会给小值面系统性超权）
    feats = [_poly_feat(0.4), _poly_feat(0.6)]
    out = generate_dot_density_features(feats, value_field="pop", unit_value=1.0)
    assert out["__dot_density_meta"]["total_dots"] == 1


def test_dot_truncation_value_descending_proportional():
    # 截断按值降序 + 比例分摊：巨值面平分预算，低值面被牺牲（契约语义）
    feats = []
    for i, v in enumerate([1, 10**7, 10**7]):
        feats.append(_poly_feat(v, coords=[
            (i * 20, 0), (i * 20 + 10, 0), (i * 20 + 10, 10), (i * 20, 10), (i * 20, 0)]))
    out = generate_dot_density_features(feats, value_field="pop", unit_value=1.0)
    xs = [f["geometry"]["coordinates"][0] for f in out["features"]]
    assert not any(x < 15 for x in xs)          # 低值面被牺牲
    assert sum(15 <= x < 35 for x in xs) > 5000  # 巨值面 1
    assert sum(x >= 35 for x in xs) > 5000       # 巨值面 2（比例分摊）


# ── bivariate ─────────────────────────────────────────────────────────


def test_bivariate_classes_and_no_data():
    feats = [
        {"properties": {"a": 1.0, "b": 10.0}},
        {"properties": {"a": 5.0, "b": 20.0}},
        {"properties": {"a": 9.0, "b": 30.0}},
        {"properties": {"a": None, "b": 5.0}},   # 无数据
    ]
    payload = compute_bivariate_classes(
        feats, field_a="a", field_b="b", n=3)
    assert payload["matched"] == 3
    assert payload["skipped"] == 1
    assert feats[3]["properties"]["__biv_class"] == -1
    # 单调数据 → 对角类（0, 4, 8）
    assert feats[0]["properties"]["__biv_class"] == 0
    assert feats[2]["properties"]["__biv_class"] == 8
    with pytest.raises(ValueError):
        compute_bivariate_classes(feats, field_a="a", field_b="b", n=5)


def test_bivariate_match_expression_transparent_no_data():
    expr = bivariate_match_expression("__biv_class", "BiPurpleOrange", 3)
    assert expr["property"] == "__biv_class"
    assert len(expr["stops"]) == 18  # 9 × (index, color)
    assert expr["no_data"] == "rgba(0,0,0,0)"


def test_bivariate_matrices_registry():
    for name, colors in BIVARIATE_MATRICES.items():
        assert len(colors) == 9
        assert all(c.startswith("#") and len(c) == 7 for c in colors)
    assert len(bivariate_class_colors("BiTealRose", 3)) == 9
    with pytest.raises(ValueError):
        bivariate_class_colors("no_such_matrix", 3)
    # V4 修复：n=2 取左上子阵（行/列独立截取），不是前 4 元素平铺截断
    assert bivariate_class_colors("BiPurpleOrange", 2) == [
        "#e8e8f0", "#cac2e0", "#f0d9c8", "#cfb0a8"]
    # BiBlueYellow 因 ΔE00 低于系统可分性阈值被移除（不可复活的库存）
    with pytest.raises(ValueError):
        bivariate_class_colors("BiBlueYellow", 3)
    spec = bivariate_legend_spec(
        "BiPurpleOrange", label_a="A", label_b="B",
        breaks_a=[1, 2], breaks_b=[3, 4])
    assert spec["type"] == "bivariate"
    assert spec["label_a"] == "A"


# ── raster_render ─────────────────────────────────────────────────────


def _demo_dem() -> np.ndarray:
    x = np.linspace(-3, 3, 16)
    y = np.linspace(-3, 3, 16)
    xx, yy = np.meshgrid(x, y)
    dem = 100.0 * np.exp(-(xx**2 + yy**2) / 6)
    dem[0, :] = np.nan
    return dem


def test_hillshade_deterministic_and_nan_preserved():
    dem = _demo_dem()
    h1 = hillshade_array(dem, azimuth=315, altitude=45)
    h2 = hillshade_array(dem, azimuth=315, altitude=45)
    assert np.array_equal(h1, h2, equal_nan=True)
    assert np.all(np.isnan(h1[0, :]))          # NaN 边界保持 NaN
    assert np.nanmax(h1) <= 255.0
    assert np.nanmin(h1) >= 0.0
    with pytest.raises(ValueError):
        hillshade_array(np.zeros((3,)))


def test_classify_array_bins_and_nodata():
    a = np.array([[1.0, 4.0], [np.nan, 9.0]])
    cls = classify_array(a, [3.0, 6.0])
    assert cls[0, 0] == 0
    assert cls[0, 1] == 1
    assert cls[1, 1] == 2
    assert cls[1, 0] == -1


def test_blend_and_normalize():
    base = np.array([[0.0, 0.5], [1.0, np.nan]])
    shade = np.array([[0.2, 0.4], [0.8, 0.6]])
    blended = blend_arrays(base, shade, overlay_alpha=0.5)
    assert abs(blended[0, 0] - 0.1) < 1e-12
    # base NaN 处以晕渲兜底（0.6*0.5），双侧 NaN 才是 NaN
    assert abs(blended[1, 1] - 0.3) < 1e-12
    both_nan = blend_arrays(
        np.array([[np.nan]]), np.array([[np.nan]]))
    assert np.isnan(both_nan[0, 0])
    lo, hi = normalize_min_max(base)
    assert lo == 0.0 and hi == 1.0
    lo2, hi2 = normalize_min_max(np.full((2, 2), np.nan))
    assert (lo2, hi2) == (0.0, 1.0)
    brk = equal_interval_breaks(base, 4)
    assert len(brk) == 3


# ── raster converter render modes ─────────────────────────────────────


def test_raster_converter_hillshade_mode():
    from app.services.raster_cartography_converter import (
        build_raster_layer,
        _extract_render_mode,
    )
    dem = _demo_dem()
    layer, legend, png = build_raster_layer(
        "src", [0, 0, 1, 1], dem, render_mode="hillshade",
        render_params={"azimuth": 315.0, "altitude": 45.0},
    )
    assert png is not None and len(png) > 0
    assert legend["palette"] == "Gray"
    assert "方位角" in legend["hillshade_zh"]
    assert layer["provenance"]["render_mode"] == "hillshade"
    assert _extract_render_mode({"render_mode": "hillshade"}) == "hillshade"
    assert _extract_render_mode({"render_mode": "bogus"}) == "continuous"


def test_raster_converter_classified_and_bivariate_modes():
    from app.services.raster_cartography_converter import build_raster_layer
    dem = _demo_dem()
    layer, legend, png = build_raster_layer(
        "src", [0, 0, 1, 1], dem, palette="YlOrRd",
        render_mode="classified", render_params={"n_classes": 4},
    )
    assert legend["type"] == "graduated"
    assert len(legend["palette_colors"]) == 4
    assert legend["breaks"] == sorted(legend["breaks"])

    a = np.linspace(0, 10, 64).reshape(8, 8)
    b = np.linspace(10, 0, 64).reshape(8, 8)
    layer2, legend2, png2 = build_raster_layer(
        "src2", [0, 0, 1, 1], a, render_mode="bivariate",
        render_params={"matrix": "BiTealRose"},
        second_array=b,
    )
    assert legend2["type"] == "bivariate"
    assert legend2["matrix"] == "BiTealRose"
    assert len(legend2["colors"]) == 9
    assert png2 is not None

    # bivariate 缺第二波段 → 退化层（不抛异常）
    layer3, legend3, png3 = build_raster_layer(
        "src3", [0, 0, 1, 1], a, render_mode="bivariate")
    assert legend3 is None and png3 is None
    assert layer3["paint"]["opacity"] == 0.0


# ── 矢量 converter V4 分支 ────────────────────────────────────────────


def test_analysis_converter_bivariate_branch():
    from app.services.analysis_cartography_converter import (
        convert_analysis_to_mapspec_layer,
    )
    feats = []
    for i in range(12):
        feats.append({
            "type": "Feature",
            "geometry": mapping(Polygon([(i, 0), (i + 1, 0), (i + 1, 1), (i, 1), (i, 0)])),
            "properties": {"pop": float(i * 10), "job": float(i * 3)},
        })
    result = {
        "type_hint": "bivariate_choropleth",
        "metadata": {"field_a": "pop", "field_b": "job",
                     "matrix": "BiPurpleOrange"},
        "data": {"type": "FeatureCollection", "features": feats},
    }
    layer, geojson, warnings = convert_analysis_to_mapspec_layer(result)
    legend = layer.get("legend_spec")
    assert legend is not None and legend["type"] == "bivariate"
    paint = layer["paint"]["color"]
    assert paint["method"] == "match"
    assert paint["default"] == "rgba(0,0,0,0)"
    # 契约缺失 → 诚实回退（常量色 + 警告）
    bad = dict(result)
    bad["metadata"] = {}
    layer2, _, warnings2 = convert_analysis_to_mapspec_layer(bad)
    legend2 = layer2.get("legend_spec")
    assert legend2 is None or legend2.get("type") != "bivariate"
    assert any("bivariate" in w for w in warnings2)


def test_analysis_converter_dot_density_branch():
    from app.services.analysis_cartography_converter import (
        convert_analysis_to_mapspec_layer,
    )
    result = {
        "type_hint": "dot_density_map",
        "metadata": {"value_field": "pop", "unit_value": 2.0},
        "data": {"type": "FeatureCollection", "features": [_poly_feat(20)]},
    }
    layer, geojson, _ = convert_analysis_to_mapspec_layer(result)
    assert layer["type"] == "circle"
    features = geojson["features"]
    assert len(features) == 10   # 20 / 2（banker's rounding：20/2=10）
    legend = layer.get("legend_spec")
    assert legend is not None
    assert "1 点 = 2" in (legend.get("categories") or [{}])[0].get("label", "")


def test_analysis_converter_cluster_guard_and_route():
    from app.services.analysis_cartography_converter import (
        convert_analysis_to_mapspec_layer,
    )
    # 非点几何 → cluster 诚实回退
    bad = {
        "type_hint": "point_cluster",
        "data": {"type": "FeatureCollection", "features": [_poly_feat(1)]},
    }
    layer, _, warnings = convert_analysis_to_mapspec_layer(bad)
    assert any("point_cluster_guard" in w for w in warnings)

    pts = {
        "type_hint": "point_cluster",
        "metadata": {"cluster_radius": 80},
        "data": {"type": "FeatureCollection", "features": [
            {"type": "Feature",
             "geometry": {"type": "Point", "coordinates": [1.0, 2.0]},
             "properties": {}},
        ]},
    }
    layer, _, warnings = convert_analysis_to_mapspec_layer(pts)
    assert layer["style"]["cluster"] == {"radius": 80.0}

    route = {
        "type_hint": "route_map",
        "data": {"type": "FeatureCollection", "features": [
            {"type": "Feature",
             "geometry": {"type": "LineString",
                          "coordinates": [[0, 0], [1, 1]]},
             "properties": {"route_rank": 0}},
        ]},
    }
    layer, _, _ = convert_analysis_to_mapspec_layer(route)
    assert layer["paint"]["width"]["method"] == "interpolate"
