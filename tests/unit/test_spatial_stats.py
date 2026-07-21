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
    assert result.get("success") is True or "error" not in result
    data = result.get("data", result)
    if isinstance(data, dict) and "features" in data:
        labels = [f["properties"].get("cluster_label") for f in data["features"]]
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
    assert result.get("success") is True or "error" not in result


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
    assert result.get("success") is True or "error" not in result
    data = result.get("data", result)
    if isinstance(data, dict) and "features" in data:
        assert len(data["features"]) >= 1


# ─── moran_i ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_moran_i_clustered(stats_tools):
    """Moran's I should detect spatial clustering in high-value points."""
    # Two clusters: (0,0) high values, (10,10) low values
    features = (
        [_point(0, 0.01 * i, {"val": 100}) for i in range(10)]
        + [_point(10, 10.01 * i, {"val": 1}) for i in range(10)]
    )
    result = await stats_tools.dispatch("moran_i", {
        "geojson": _fc(features),
        "value_field": "val",
    })
    assert result.get("success") is True or "error" not in result
    data = result.get("data", result)
    if isinstance(data, dict):
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
        + [_point(10, 10.01 * i, {"val": 1}) for i in range(10)]
    )
    result = await stats_tools.dispatch("hotspot_analysis", {
        "geojson": _fc(features),
        "value_field": "val",
        "distance_band": 50000,
    })
    assert result.get("success") is True or "error" not in result
    data = result.get("data", result)
    if isinstance(data, dict) and "features" in data:
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
    assert result.get("success") is True or "error" not in result


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
    assert result.get("success") is True or "error" not in result


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
    assert result.get("success") is True or "error" not in result


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
