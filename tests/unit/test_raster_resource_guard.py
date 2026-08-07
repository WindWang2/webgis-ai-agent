"""Regression tests for the resample output-grid resource guard.

The guard exists because a unit-confusion request — e.g. target_resolution=1
(meaning 1 m) on a degree-based EPSG:4326 source warped to EPSG:3857 — would
create a ~334k×334k grid (~111 billion pixels, ~400 GiB), hanging or OOMing
the worker. These tests pin both the rejection behavior and the agent-actionable
correction hint, so the pathological case can never become a silent 5-minute
CI benchmark again (see test_raster_tools.py::test_raster_resample_with_crs_change).
"""
import os
import uuid

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from app.lib.geo_analysis.raster_math import _suggested_resolutions
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
    """3×3 px EPSG:4326 raster spanning 3°×3° (the pathological source)."""
    data = np.array([[1, 2, 3], [4, 5, 6], [7, 8, 9]], dtype=np.float32)
    transform = from_origin(0, 3, 1, 1)
    # Place in project data/ dir so validate_data_path passes
    raster_path = os.path.join(os.path.dirname(__file__), "..", "..", "data", f"test_guard_{uuid.uuid4().hex[:8]}.tif")
    with rasterio.open(
        raster_path, "w", driver="GTiff",
        height=3, width=3, count=1, dtype=np.float32,
        crs="EPSG:4326", transform=transform,
    ) as dst:
        dst.write(data, 1)
    yield raster_path
    for path in [raster_path, raster_path[:-4] + "_resampled.tif"]:
        try:
            os.unlink(path)
        except OSError:
            pass


async def _dispatch_resample(tools, raster_path, **kwargs):
    return await tools.dispatch("raster_resample", {"raster_path": raster_path, **kwargs})


def _error_text(result) -> str:
    """Error text lives in `summary` (GeoAnalysisResult) or `message`/`error`."""
    return str(result.get("summary") or result.get("message") or result.get("error") or "")


@pytest.mark.asyncio
async def test_crs_warp_pixel_explosion_rejected(advanced_tools, tmp_raster):
    """3°×3° @ 4326 → EPSG:3857 at 1 m must fail fast with a correction hint.

    Before the guard this request warped 334,035×334,035 px (~111 G pixels,
    ~4.3 GB output file) and took minutes — an accidental benchmark, not a test.
    """
    result = await _dispatch_resample(advanced_tools, tmp_raster,
                                      target_resolution=1.0, target_crs="EPSG:3857")
    assert result.get("success") is False
    message = _error_text(result)
    assert "334,035" in message or "111" in message or "pixels exceeds" in message
    assert "Suggested target_resolution values" in message


@pytest.mark.asyncio
async def test_pixel_explosion_without_crs_change_rejected(advanced_tools, tmp_raster):
    """Same guard must hold for in-CRS resampling (0.00001° → 300k×300k grid)."""
    result = await _dispatch_resample(advanced_tools, tmp_raster, target_resolution=0.00001)
    assert result.get("success") is False
    message = _error_text(result)
    assert "pixels exceeds" in message


@pytest.mark.asyncio
async def test_zero_resolution_rejected(advanced_tools, tmp_raster):
    result = await _dispatch_resample(advanced_tools, tmp_raster, target_resolution=0)
    assert result.get("success") is False
    message = _error_text(result)
    assert "target_resolution must be positive" in message


@pytest.mark.asyncio
async def test_negative_resolution_rejected(advanced_tools, tmp_raster):
    result = await _dispatch_resample(advanced_tools, tmp_raster, target_resolution=-5)
    assert result.get("success") is False
    assert "target_resolution must be positive" in _error_text(result)


@pytest.mark.asyncio
async def test_sane_crs_warp_succeeds(advanced_tools, tmp_raster):
    """A sane warp (10 km target res → 34×34 px) must still work and verify the math."""
    result = await _dispatch_resample(advanced_tools, tmp_raster,
                                      target_resolution=10000.0, target_crs="EPSG:3857")
    assert result.get("success") is True
    data = result.get("data", result)
    assert data["new_shape"] == [34, 34]
    assert data["target_crs"] == "EPSG:3857"


@pytest.mark.asyncio
async def test_guard_rejects_before_writing_output(advanced_tools, tmp_raster):
    """Rejected requests must not leave partial output files behind."""
    result = await _dispatch_resample(advanced_tools, tmp_raster,
                                      target_resolution=1.0, target_crs="EPSG:3857")
    assert result.get("success") is False
    expected_output = tmp_raster[:-4] + "_resampled.tif"
    assert not os.path.exists(expected_output)


def test_suggested_resolutions_for_pathological_case():
    """1 m → 111.6 G pixels should suggest ~50/200/500 m (ascending, nice values)."""
    suggestions = _suggested_resolutions(1.0, 111_579_381_225)
    assert suggestions == ["50", "200", "500"]


def test_suggested_resolutions_skips_budgets_already_under():
    """If output is under a budget tier, that tier must not be suggested."""
    suggestions = _suggested_resolutions(1.0, 10_000_000)
    # only the 2.5e8 tier applies → sqrt(25) = 5
    assert suggestions == ["5"]
