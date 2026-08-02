"""Unit tests for SpectralRasterEngine and band math primitives."""
import pytest
import numpy as np

from app.services.rs.band_math import (
    RasterAnalysisResult,
    compute_index_array,
    compute_slope,
    compute_aspect,
    compute_hillshade,
    compute_raster_stats,
)
from app.services.rs.spectral_engine import SpectralRasterEngine


def test_raster_analysis_result_serialization():
    res = RasterAnalysisResult(
        index_type="ndvi",
        array=np.array([[0.2, 0.5], [0.8, -0.1]]),
        bounds=[116.0, 39.0, 116.5, 39.5],
        stats={"mean": 0.35, "min": -0.1, "max": 0.8},
        is_error=False,
    )
    payload = res.to_llm_response()
    assert payload["success"] is True
    assert payload["index_type"] == "ndvi"
    assert "完成 NDVI 栅格分析" in payload["summary"]


def test_raster_analysis_result_error_degradation():
    res = RasterAnalysisResult(
        index_type="ndvi",
        is_error=True,
        error_msg="No Sentinel-2 imagery found",
        correction_hint="Try expanding the date window.",
    )
    payload = res.to_llm_response()
    assert payload["success"] is False
    assert payload["error"] == "No Sentinel-2 imagery found"
    assert payload["correction_hint"] == "Try expanding the date window."


def test_compute_index_array_ndvi():
    red = np.array([[0.1, 0.2], [0.3, 0.4]])
    nir = np.array([[0.5, 0.6], [0.7, 0.8]])
    ndvi = compute_index_array("ndvi", red=red, nir=nir)
    expected = (nir - red) / (nir + red)
    np.testing.assert_allclose(ndvi, expected)


def test_compute_raster_stats_with_nan():
    arr = np.array([[1.0, 2.0], [np.nan, 4.0]])
    stats = compute_raster_stats(arr)
    assert stats["min"] == 1.0
    assert stats["max"] == 4.0
    assert stats["mean"] == 2.3333
    assert stats["valid_pixels"] == 3
    assert stats["total_pixels"] == 4
    assert stats["valid_pixel_pct"] == "75.0%"


def test_compute_slope_and_aspect_basic():
    dem = np.array([
        [100.0, 100.0, 100.0],
        [100.0, 150.0, 100.0],
        [100.0, 100.0, 100.0],
    ])
    cell_size = 30.0
    slope = compute_slope(dem, cell_size)
    aspect = compute_aspect(dem, cell_size)
    hillshade = compute_hillshade(dem, cell_size)

    assert slope.shape == (3, 3)
    assert aspect.shape == (3, 3)
    assert hillshade.shape == (3, 3)
    assert np.all(hillshade >= 0) and np.all(hillshade <= 255)


@pytest.mark.asyncio
async def test_spectral_engine_unsupported_index():
    engine = SpectralRasterEngine()
    res = await engine.compute_index([116.0, 39.0, 116.5, 39.5], "2026-05-01", "2026-06-01", index_type="unknown")
    assert res.is_error is True
    assert "不支持的指数类型" in res.error_msg
