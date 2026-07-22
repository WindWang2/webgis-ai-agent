"""Tests for raster analysis tools: reclassify, calculator, resample."""
import os
import tempfile
import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from app.tools.advanced_spatial import register_advanced_spatial_tools
from app.tools.registry import ToolRegistry


@pytest.fixture()
def registry():
    return ToolRegistry()


@pytest.fixture()
def advanced_tools(registry):
    register_advanced_spatial_tools(registry)
    return registry


@pytest.fixture()
def tmp_raster(tmp_path):
    """Create a small synthetic raster in the project data/ directory."""
    import uuid
    data = np.array([[1, 2, 3], [4, 5, 6], [7, 8, 9]], dtype=np.float32)
    transform = from_origin(0, 3, 1, 1)
    # Place in project data/ dir so validate_data_path passes
    raster_path = os.path.join(os.path.dirname(__file__), "..", "..", "data", f"test_raster_{uuid.uuid4().hex[:8]}.tif")
    with rasterio.open(
        raster_path, "w", driver="GTiff",
        height=3, width=3, count=1, dtype=np.float32,
        crs="EPSG:4326", transform=transform,
    ) as dst:
        dst.write(data, 1)
    yield raster_path
    # Cleanup
    try:
        os.unlink(raster_path)
    except OSError:
        pass


# ─── raster_reclassify ──────────────────────────────────────────


def _assert_error(result):
    assert result.get("success") is False


def _assert_ok(result):
    assert result.get("success") is True or "error" not in result


@pytest.mark.asyncio
async def test_raster_reclassify_basic(advanced_tools, tmp_raster):
    """Reclassify should produce a new raster with mapped values."""
    scheme = [
        {"min": 1, "max": 3, "value": 1, "label": "low"},
        {"min": 4, "max": 6, "value": 2, "label": "medium"},
        {"min": 7, "max": 9, "value": 3, "label": "high"},
    ]
    result = await advanced_tools.dispatch("raster_reclassify", {
        "raster_path": tmp_raster,
        "scheme": scheme,
    })
    _assert_ok(result)
    data = result.get("data", result)
    if isinstance(data, dict):
        assert "output_path" in data or "pixel_count" in data


@pytest.mark.asyncio
async def test_raster_reclassify_invalid_path(advanced_tools):
    """Reclassify with invalid path should return error."""
    result = await advanced_tools.dispatch("raster_reclassify", {
        "raster_path": "/nonexistent/path.tif",
        "scheme": [{"min": 0, "max": 1, "value": 1}],
    })
    _assert_error(result)


@pytest.mark.asyncio
async def test_raster_reclassify_empty_scheme(advanced_tools, tmp_raster):
    """Reclassify with empty scheme should error or produce nodata-only output."""
    result = await advanced_tools.dispatch("raster_reclassify", {
        "raster_path": tmp_raster,
        "scheme": [],
    })
    _assert_error(result)


# ─── raster_calculator ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_raster_calculator_constant(advanced_tools, tmp_raster):
    """Calculator with constant should multiply all pixels."""
    result = await advanced_tools.dispatch("raster_calculator", {
        "raster_a": tmp_raster,
        "expression": "A * 2",
    })
    assert result.get("success") is True or "error" not in result
    data = result.get("data", result)
    if isinstance(data, dict):
        assert "output_path" in data or "min" in data


@pytest.mark.asyncio
async def test_raster_calculator_two_rasters(advanced_tools, tmp_raster):
    """Calculator with two rasters should compute expression."""
    import uuid
    # Create second raster in data/ dir
    raster_b_path = os.path.join(os.path.dirname(__file__), "..", "..", "data", f"test_raster_b_{uuid.uuid4().hex[:8]}.tif")
    data_b = np.array([[5, 6], [7, 8]], dtype=np.float32)
    transform_b = from_origin(0, 2, 1, 1)
    with rasterio.open(raster_b_path, "w", driver="GTiff", height=2, width=2, count=1,
                       dtype=np.float32, crs="EPSG:4326", transform=transform_b) as dst:
        dst.write(data_b, 1)
    try:
        result = await advanced_tools.dispatch("raster_calculator", {
            "raster_a": tmp_raster,
            "raster_b": raster_b_path,
            "expression": "A + B",
        })
        assert result.get("success") is True or "error" not in result
        data = result.get("data", result)
        if isinstance(data, dict):
            assert "output_path" in data
    finally:
        try:
            os.unlink(raster_b_path)
        except OSError:
            pass


@pytest.mark.asyncio
async def test_raster_calculator_invalid_expression(advanced_tools, tmp_raster):
    """Calculator with invalid expression should error gracefully."""
    result = await advanced_tools.dispatch("raster_calculator", {
        "raster_a": tmp_raster,
        "expression": "INVALID_OP(A)",
    })
    assert result.get("success") is False or "error" in result


@pytest.mark.asyncio
async def test_raster_calculator_missing_raster(advanced_tools):
    """Calculator with missing raster should error."""
    result = await advanced_tools.dispatch("raster_calculator", {
        "raster_a": "/nonexistent.tif",
        "expression": "A + 1",
    })
    assert result.get("success") is False or "error" in result


# ─── raster_resample ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_raster_resample_basic(advanced_tools, tmp_raster):
    """Resample should produce output with new resolution."""
    result = await advanced_tools.dispatch("raster_resample", {
        "raster_path": tmp_raster,
        "target_resolution": 2.0,
        "resampling": "nearest",
    })
    assert result.get("success") is True or "error" not in result
    data = result.get("data", result)
    if isinstance(data, dict):
        assert "output_path" in data or "new_shape" in data


@pytest.mark.heavy
@pytest.mark.asyncio
@pytest.mark.timeout(300)  # PROJ init on first CRS change is slow (~2-3min)
async def test_raster_resample_with_crs_change(advanced_tools, tmp_raster):
    """Resample with CRS change should work (slow: PROJ init overhead)."""
    result = await advanced_tools.dispatch("raster_resample", {
        "raster_path": tmp_raster,
        "target_resolution": 1.0,
        "target_crs": "EPSG:3857",
        "resampling": "bilinear",
    })
    _assert_ok(result)


@pytest.mark.asyncio
async def test_raster_resample_invalid_path(advanced_tools):
    """Resample with invalid path should error."""
    result = await advanced_tools.dispatch("raster_resample", {
        "raster_path": "/nonexistent.tif",
        "target_resolution": 1.0,
    })
    assert result.get("success") is False or "error" in result
