"""Tests for app/tools/spatial_stats.py — cluster, SDE, Moran's I, hotspot, KDE."""
import pytest
import numpy as np
from app.tools.spatial_stats import register_spatial_stats_tools
from app.tools.registry import ToolRegistry


@pytest.fixture()
def registry():
    return ToolRegistry()


@pytest.fixture()
def stats_tools(registry):
    register_spatial_stats_tools(registry)
    return registry


# ─── Helpers ──────────────────────────────────────────────────────


def _fc(features):
    return {"type": "FeatureCollection", "features": features}


def _point(lng, lat, props=None):
    return {
        "type": "Feature",
        "properties": props or {},
        "geometry": {"type": "Point", "coordinates": [lng, lat]},
    }


# ─── spatial_cluster ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_spatial_cluster_dbscan(stats_tools):
    """DBSCAN should assign cluster labels to each point."""
    features = [_point(0, 0), _point(0.01, 0), _point(0.02, 0), _point(10, 10)]
    result = await stats_tools.dispatch("spatial_cluster", {
        "geojson": _fc(features),
        "method": "dbscan",
        "eps": 2000,
        "min_samples": 2,
    })
    assert result.get("success") is True, result
    data = result.get("data", result)
    # cluster_narrated 写入的是 cluster_id（不是 cluster_label）
    labels = [f["properties"].get("cluster_id") for f in data["features"]]
    assert len(labels) == 4


@pytest.mark.asyncio
async def test_spatial_cluster_kmeans(stats_tools):
    """K-Means should assign cluster labels with n_clusters=2."""
    features = [_point(0, 0), _point(0.01, 0), _point(10, 10), _point(10.01, 10)]
    result = await stats_tools.dispatch("spatial_cluster", {
        "geojson": _fc(features),
        "method": "kmeans",
        "n_clusters": 2,
    })
    assert result.get("success") is True, result


@pytest.mark.asyncio
async def test_spatial_cluster_insufficient_points(stats_tools):
    """Too few points should return an error."""
    features = [_point(0, 0)]
    result = await stats_tools.dispatch("spatial_cluster", {
        "geojson": _fc(features),
        "method": "dbscan",
        "eps": 1000,
        "min_samples": 2,
    })
    assert result.get("success") is False or "error" in result


# ─── standard_deviational_ellipse ────────────────────────────────


@pytest.mark.asyncio
async def test_standard_deviational_ellipse(stats_tools):
    """SDE should return a polygon with angle and area properties."""
    np.random.seed(42)
    features = [
        _point(0.01 * i, 0.005 * i + np.random.normal(0, 0.001))
        for i in range(20)
    ]
    result = await stats_tools.dispatch("standard_deviational_ellipse", {
        "geojson": _fc(features),
    })
    assert result.get("success") is True, result
    data = result.get("data", result)
    # SDE 成功时 data 是 Feature（椭圆多边形 + 方向性属性），不是 FeatureCollection
    assert data["geometry"]["type"] == "Polygon"
    assert data["properties"]["angle_deg"] is not None
    assert data["properties"]["area_km2"] is not None


# ─── moran_i ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_moran_i_clustered(stats_tools):
    """Moran's I should detect spatial clustering in high-value points."""
    # Two clusters: (0,0) high values, (10,10) low values
    # （低值簇纬度用 10+0.01i：10.01*i 会在 i=9 时算出 90.09>90 的非法纬度，
    # 使 to_utm_gdf 的 make_valid 抛 GEOSException → success=False → 恒真断言掩盖）
    features = (
        [_point(0, 0.01 * i, {"val": 100}) for i in range(10)]
        + [_point(10, 10 + 0.01 * i, {"val": 1}) for i in range(10)]
    )
    result = await stats_tools.dispatch("moran_i", {
        "geojson": _fc(features),
        "value_field": "val",
    })
    assert result.get("success") is True, result
    data = result.get("data", result)
    assert "moran_i" in data or "stats" in data or "summary" in data


@pytest.mark.asyncio
async def test_moran_i_missing_field(stats_tools):
    """Missing value_field should produce an error."""
    features = [_point(0, 0), _point(1, 1)]
    result = await stats_tools.dispatch("moran_i", {
        "geojson": _fc(features),
        "value_field": "nonexistent_field",
    })
    assert result.get("success") is False or "error" in result


# ─── hotspot_analysis ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_hotspot_analysis(stats_tools):
    """Hotspot analysis should identify high-value clusters."""
    features = (
        [_point(0, 0.01 * i, {"val": 100}) for i in range(10)]
        + [_point(10, 10 + 0.01 * i, {"val": 1}) for i in range(10)]
    )
    result = await stats_tools.dispatch("hotspot_analysis", {
        "geojson": _fc(features),
        "value_field": "val",
        "distance_band": 50000,
    })
    assert result.get("success") is True, result
    data = result.get("data", result)
    assert len(data["features"]) == 20


# ─── kde_surface ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_kde_surface_minimum_points(stats_tools):
    """KDE should fail gracefully with fewer than 3 points."""
    features = [_point(0, 0), _point(1, 1)]
    result = await stats_tools.dispatch("kde_surface", {
        "geojson": _fc(features),
        "bandwidth": 0,
        "cell_size": 500,
    })
    assert result.get("success") is False or "error" in result


@pytest.mark.asyncio
async def test_kde_surface_invalid_geojson(stats_tools):
    """KDE should return error for invalid GeoJSON."""
    result = await stats_tools.dispatch("kde_surface", {
        "geojson": "not valid json",
        "bandwidth": 0,
        "cell_size": 500,
    })
    assert result.get("success") is False or "error" in result


@pytest.mark.asyncio
async def test_kde_surface_with_value_field(stats_tools):
    """KDE with value_field should weight points by their values."""
    np.random.seed(42)
    features = [
        _point(0.01 * i, 0.005 * i, {"weight": float(i + 1)})
        for i in range(20)
    ]
    result = await stats_tools.dispatch("kde_surface", {
        "geojson": _fc(features),
        "bandwidth": 0,
        "cell_size": 1000,
        "value_field": "weight",
    })
    assert result.get("success") is True, result


@pytest.mark.asyncio
async def test_kde_surface_with_custom_bounds(stats_tools):
    """KDE with custom bounds should respect the bounding box."""
    np.random.seed(42)
    features = [
        _point(0.01 * i, 0.005 * i + np.random.normal(0, 0.001))
        for i in range(20)
    ]
    result = await stats_tools.dispatch("kde_surface", {
        "geojson": _fc(features),
        "bandwidth": 0,
        "cell_size": 500,
        "bounds": [0, 0, 0.1, 0.05],
    })
    assert result.get("success") is True, result


@pytest.mark.asyncio
async def test_kde_surface_explicit_bandwidth(stats_tools):
    """KDE with explicit bandwidth should use it directly."""
    np.random.seed(42)
    features = [
        _point(0.01 * i, 0.005 * i + np.random.normal(0, 0.001))
        for i in range(20)
    ]
    result = await stats_tools.dispatch("kde_surface", {
        "geojson": _fc(features),
        "bandwidth": 2000,
        "cell_size": 1000,
    })
    assert result.get("success") is True, result


# ─── Additional edge cases ───────────────────────────────────────


@pytest.mark.asyncio
async def test_spatial_cluster_empty_geojson(stats_tools):
    """Empty FeatureCollection should return error."""
    result = await stats_tools.dispatch("spatial_cluster", {
        "geojson": _fc([]),
        "method": "dbscan",
    })
    assert result.get("success") is False or "error" in result


@pytest.mark.asyncio
async def test_standard_deviational_ellipse_insufficient_points(stats_tools):
    """SDE with a single point should return error."""
    features = [_point(0, 0)]
    result = await stats_tools.dispatch("standard_deviational_ellipse", {
        "geojson": _fc(features),
    })
    assert result.get("success") is False or "error" in result


@pytest.mark.asyncio
async def test_moran_i_insufficient_points(stats_tools):
    """Moran's I with fewer than 2 points should return error."""
    features = [_point(0, 0, {"val": 10})]
    result = await stats_tools.dispatch("moran_i", {
        "geojson": _fc(features),
        "value_field": "val",
    })
    assert result.get("success") is False or "error" in result


@pytest.mark.asyncio
async def test_hotspot_analysis_missing_field(stats_tools):
    """Hotspot analysis with nonexistent value_field should error."""
    features = [_point(0, 0), _point(1, 1)]
    result = await stats_tools.dispatch("hotspot_analysis", {
        "geojson": _fc(features),
        "value_field": "nonexistent",
    })
    assert result.get("success") is False or "error" in result


# ─── distance_band 参数契约接线（#1735）─────────────────────────────
# moran_i / geary_c / general_g / hotspot_analysis 的 distance_band 曾绕过
# apply_contract 直达分析层（float(distance_band or 0) 用 LLM 原始值），
# 契约声明的 minimum=0 在这条工具路径上是死代码。四个工具的契约
# （moran_i_analysis / geary_c_analysis / general_g_analysis /
# gi_star_analysis）都声明了 distance_band —— 接线后负值必须被闸拦下。


def _clustered_fc():
    """两个高/低值簇（构造同 test_moran_i_clustered）。"""
    return _fc(
        [_point(0, 0.01 * i, {"val": 100}) for i in range(10)]
        + [_point(10, 10 + 0.01 * i, {"val": 1}) for i in range(10)]
    )


@pytest.mark.parametrize(
    "tool_name",
    ["moran_i", "geary_c", "general_g", "hotspot_analysis"],
)
@pytest.mark.asyncio
async def test_distance_band_negative_rejected_by_contract(stats_tools, tool_name):
    """#1735：distance_band 负值被 apply_contract 的 minimum=0 拦下，
    返回机器可读 parameter_contract_violation，不再绕过契约直达分析层。"""
    result = await stats_tools.dispatch(tool_name, {
        "geojson": _clustered_fc(),
        "value_field": "val",
        "distance_band": -500,
    })
    assert result.get("success") is False, result
    assert result.get("code") == "VALIDATION_ERROR"
    assert "parameter_contract_violation" in result.get("message", "")


@pytest.mark.parametrize(
    "tool_name",
    ["moran_i", "geary_c", "general_g", "hotspot_analysis"],
)
@pytest.mark.asyncio
async def test_distance_band_valid_value_passes_contract(stats_tools, tool_name):
    """#1735：合法 distance_band 经契约归一化后照常生效（无回归）。"""
    result = await stats_tools.dispatch(tool_name, {
        "geojson": _clustered_fc(),
        "value_field": "val",
        "distance_band": 50000,
    })
    assert result.get("success") is True, result


@pytest.mark.asyncio
async def test_hotspot_analysis_evidence_uses_contract_normalized_band(stats_tools):
    """#1735：证据块 parameters_applied.distance_band 取契约归一化后的值
    （与真正送进分析层的参数一致，而非 LLM 原始值）。"""
    result = await stats_tools.dispatch("hotspot_analysis", {
        "geojson": _clustered_fc(),
        "value_field": "val",
        "distance_band": 1500.0,
    })
    assert result.get("success") is True, result
    ev = result.get("scientific_evidence") or {}
    assert ev.get("parameters_applied", {}).get("distance_band") == 1500.0
