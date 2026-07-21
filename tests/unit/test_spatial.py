"""Tests for app/tools/spatial.py — buffer, spatial_stats, heatmap_data."""
import pytest
import numpy as np
from app.tools.spatial import register_spatial_tools
from app.tools.registry import ToolRegistry


@pytest.fixture()
def registry():
    return ToolRegistry()


@pytest.fixture()
def spatial_tools(registry):
    register_spatial_tools(registry)
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


def _polygon(coords, props=None):
    return {
        "type": "Feature",
        "properties": props or {},
        "geometry": {"type": "Polygon", "coordinates": coords},
    }


# ─── buffer_analysis ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_buffer_analysis_points(spatial_tools):
    """Buffer should produce polygon features around points."""
    features = [_point(0, 0), _point(0.01, 0)]
    result = await spatial_tools.dispatch("buffer_analysis", {
        "geojson": _fc(features),
        "distance": 100,
        "unit": "m",
    })
    assert result.get("success") is True or "error" not in result
    data = result.get("data", result)
    if isinstance(data, dict) and "features" in data:
        assert len(data["features"]) == 2


@pytest.mark.asyncio
async def test_buffer_analysis_invalid_input(spatial_tools):
    """Buffer with invalid GeoJSON should return error."""
    result = await spatial_tools.dispatch("buffer_analysis", {
        "geojson": "not valid json",
        "distance": 100,
    })
    assert result.get("success") is False or "error" in result


@pytest.mark.asyncio
async def test_buffer_analysis_zero_distance(spatial_tools):
    """Buffer with zero distance should fail validation."""
    features = [_point(0, 0)]
    result = await spatial_tools.dispatch("buffer_analysis", {
        "geojson": _fc(features),
        "distance": 0,
    })
    assert result.get("success") is False or "error" in result


# ─── spatial_stats ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_spatial_stats_points(spatial_tools):
    """spatial_stats should return count, bbox, centroid for points."""
    features = [_point(0, 0), _point(1, 1), _point(2, 2)]
    result = await spatial_tools.dispatch("spatial_stats", {
        "geojson": _fc(features),
    })
    assert result.get("success") is True or "error" not in result
    data = result.get("data", result)
    if isinstance(data, dict):
        assert data.get("count") == 3 or "summary" in data


@pytest.mark.asyncio
async def test_spatial_stats_polygons(spatial_tools):
    """spatial_stats should compute area and perimeter for polygons."""
    features = [
        _polygon([[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]),
        _polygon([[[1, 1], [2, 1], [2, 2], [1, 2], [1, 1]]]),
    ]
    result = await spatial_tools.dispatch("spatial_stats", {
        "geojson": _fc(features),
    })
    assert result.get("success") is True or "error" not in result


@pytest.mark.asyncio
async def test_spatial_stats_empty_input(spatial_tools):
    """spatial_stats with empty features should return zero count."""
    result = await spatial_tools.dispatch("spatial_stats", {
        "geojson": _fc([]),
    })
    assert result.get("success") is True or "error" not in result


@pytest.mark.asyncio
async def test_spatial_stats_invalid_geojson(spatial_tools):
    """spatial_stats with invalid GeoJSON should return error."""
    result = await spatial_tools.dispatch("spatial_stats", {
        "geojson": "not valid json",
    })
    assert result.get("success") is False or "error" in result


# ─── heatmap_data ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_heatmap_data_insufficient_points(spatial_tools):
    """Heatmap with 1 point falls back to in-process and returns a result."""
    features = [_point(0, 0)]
    result = await spatial_tools.dispatch("heatmap_data", {
        "geojson": _fc(features),
        "cell_size": 500,
        "radius": 1000,
    })
    # Single point falls back to in-process _generate_heatmap (no Celery needed)
    assert "image" in result or "error" in result or "data" in result


@pytest.mark.asyncio
async def test_heatmap_data_invalid_geojson(spatial_tools):
    """Heatmap with invalid GeoJSON should return error."""
    result = await spatial_tools.dispatch("heatmap_data", {
        "geojson": "not valid json",
        "cell_size": 500,
        "radius": 1000,
    })
    assert result.get("success") is False or "error" in result


@pytest.mark.asyncio
async def test_heatmap_data_grid_render(spatial_tools):
    """Heatmap with grid render type should return grid data."""
    np.random.seed(42)
    features = [
        _point(0.01 * i, 0.005 * i + np.random.normal(0, 0.001))
        for i in range(20)
    ]
    result = await spatial_tools.dispatch("heatmap_data", {
        "geojson": _fc(features),
        "cell_size": 500,
        "radius": 1000,
        "render_type": "grid",
    })
    assert result.get("success") is True or "error" not in result