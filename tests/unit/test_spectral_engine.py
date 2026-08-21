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


# ─── #442: NDVI coverage denominator ─────────────────────────────────────────


def _fake_index_result(arr):
    from app.services.rs.band_math import compute_raster_stats

    return RasterAnalysisResult(
        index_type="ndvi",
        array=arr,
        bounds=[116.0, 39.0, 116.5, 39.5],
        stats=compute_raster_stats(arr),
        is_error=False,
    )


@pytest.mark.asyncio
async def test_compute_ndvi_coverage_excludes_nodata(monkeypatch):
    """Issue #442: coverage must be vegetation / *valid* pixels, not
    vegetation / all pixels. NaN is the NDVI nodata convention (denominator
    <= 0), so counting it in the denominator understates coverage."""
    arr = np.array([
        [0.5, 0.6, np.nan],
        [0.2, np.nan, 0.8],
        [np.nan, np.nan, 0.4],
    ])  # 4 vegetation pixels (>0.3), 5 valid, 4 nodata
    engine = SpectralRasterEngine()

    async def fake_compute_index(bbox, date_from, date_to, index_type="ndvi"):
        assert index_type == "ndvi"
        return _fake_index_result(arr)

    monkeypatch.setattr(engine, "compute_index", fake_compute_index)
    res = await engine.compute_ndvi([116.0, 39.0, 116.5, 39.5], "2026-05-01", "2026-06-01")

    assert res["status"] == "ok"
    # 4/5 = 80.0 — the old total-pixel denominator reported 4/9 = 44.4.
    assert res["vegetation_coverage"] == 80.0


@pytest.mark.asyncio
async def test_compute_ndvi_coverage_all_nodata(monkeypatch):
    """Adversarial: entirely-nodata NDVI → coverage 0.0, no ZeroDivisionError."""
    arr = np.full((3, 3), np.nan)
    engine = SpectralRasterEngine()

    async def fake_compute_index(bbox, date_from, date_to, index_type="ndvi"):
        return _fake_index_result(arr)

    monkeypatch.setattr(engine, "compute_index", fake_compute_index)
    res = await engine.compute_ndvi([116.0, 39.0, 116.5, 39.5], "2026-05-01", "2026-06-01")

    assert res["status"] == "ok"
    assert res["vegetation_coverage"] == 0.0


@pytest.mark.asyncio
async def test_compute_ndvi_coverage_no_nodata_unchanged(monkeypatch):
    """No nodata → coverage identical to the previous (correct) behavior."""
    arr = np.array([[0.1, 0.4], [0.35, 0.9]])  # 3 vegetation of 4 valid
    engine = SpectralRasterEngine()

    async def fake_compute_index(bbox, date_from, date_to, index_type="ndvi"):
        return _fake_index_result(arr)

    monkeypatch.setattr(engine, "compute_index", fake_compute_index)
    res = await engine.compute_ndvi([116.0, 39.0, 116.5, 39.5], "2026-05-01", "2026-06-01")

    assert res["vegetation_coverage"] == 75.0


# ─── #444: compute_terrain returns all requested products ───────────────────


def _terrain_dem() -> np.ndarray:
    """Synthetic sloped DEM (5×5) with one nodata cell."""
    dem = np.array([
        [100.0, 110.0, 120.0, 130.0, 140.0],
        [105.0, 115.0, 125.0, 135.0, 145.0],
        [110.0, 120.0, 130.0, 140.0, 150.0],
        [115.0, 125.0, 135.0, 145.0, 155.0],
        [120.0, 130.0, 140.0, 150.0, 160.0],
    ])
    dem[2, 2] = -9999.0  # nodata → NaN after masking
    return dem


@pytest.mark.asyncio
async def test_compute_terrain_default_returns_all_three_products(monkeypatch):
    """Issue #444: the tool documents 'products 可选 slope/aspect/hillshade，
    默认全部' and '一步生成多产品' — the default call must return all three
    products (previously only the first supported one, silently dropping the
    rest)."""
    from app.services.rs.band_math import compute_aspect, compute_hillshade, compute_slope

    dem = _terrain_dem()
    engine = SpectralRasterEngine()

    async def fake_fetch(**kwargs):
        return {"bands": {"dem": dem.copy()}, "cell_size_m": 30.0, "bounds": [0.0, 0.0, 1.0, 1.0]}

    monkeypatch.setattr(engine.stac, "fetch_stac_items_and_bands", fake_fetch)
    res = await engine.compute_terrain([0.0, 0.0, 1.0, 1.0])  # default products

    assert res.is_error is False
    # Every requested product is computed and returned.
    assert set(res.product_arrays.keys()) == {"slope", "aspect", "hillshade"}
    assert set(res.stats["terrain_products"].keys()) == {"slope", "aspect", "hillshade"}
    # Per-product stats describe that product's array, not copies of one.
    dem_nan = dem.copy()
    dem_nan[dem_nan <= -9999] = np.nan
    expected_slope = compute_raster_stats(compute_slope(dem_nan, 30.0))
    assert res.stats["terrain_products"]["slope"] == expected_slope
    assert res.stats["terrain_products"]["aspect"] == compute_raster_stats(
        compute_aspect(dem_nan, 30.0), circular=True
    )
    assert res.stats["terrain_products"]["hillshade"] == compute_raster_stats(
        compute_hillshade(dem_nan, 30.0)
    )
    # Primary (array/stats/index_type) stays the first requested product.
    assert res.index_type == "slope"
    assert res.array is res.product_arrays["slope"]
    assert res.stats["terrain_product"] == "slope"
    assert res.stats["products"] == ["slope", "aspect", "hillshade"]


@pytest.mark.asyncio
async def test_compute_terrain_subset_and_ordering(monkeypatch):
    """Explicit product subsets are honored; primary = first requested."""
    dem = _terrain_dem()
    engine = SpectralRasterEngine()

    async def fake_fetch(**kwargs):
        return {"bands": {"dem": dem.copy()}, "cell_size_m": 30.0}

    monkeypatch.setattr(engine.stac, "fetch_stac_items_and_bands", fake_fetch)
    res = await engine.compute_terrain([0.0, 0.0, 1.0, 1.0], products=["hillshade", "slope"])

    assert res.is_error is False
    assert set(res.product_arrays.keys()) == {"hillshade", "slope"}
    assert res.index_type == "hillshade"  # first requested, not a fixed order
    assert res.stats["products"] == ["hillshade", "slope"]


@pytest.mark.asyncio
async def test_compute_terrain_unsupported_products_fall_back_to_dem(monkeypatch):
    """Adversarial: no supported derivative requested (or empty list) returns
    the raw DEM labeled 'dem', as before, and ignores nothing silently when a
    mix is given."""
    dem = _terrain_dem()
    engine = SpectralRasterEngine()

    async def fake_fetch(**kwargs):
        return {"bands": {"dem": dem.copy()}, "cell_size_m": 30.0}

    monkeypatch.setattr(engine.stac, "fetch_stac_items_and_bands", fake_fetch)
    res = await engine.compute_terrain([0.0, 0.0, 1.0, 1.0], products=["dem"])

    assert res.is_error is False
    assert res.index_type == "dem"
    assert res.product_arrays == {}
    assert "terrain_products" not in res.stats

    res_mixed = await engine.compute_terrain([0.0, 0.0, 1.0, 1.0], products=["aspect", "curvature"])
    assert res_mixed.index_type == "aspect"
    assert res_mixed.stats["unsupported_products"] == ["curvature"]


def test_compute_raster_stats_masks_inf():
    """#712: ±Inf pixels are invalid data — stats must be finite and the
    valid-pixel count must exclude them (matching the renderer's mask)."""
    import numpy as np
    arr = np.array([[1.0, 2.0], [np.inf, -np.inf]])
    stats = compute_raster_stats(arr)
    assert stats["valid_pixels"] == 2
    assert stats["min"] == 1.0 and stats["max"] == 2.0
    assert np.isfinite(stats["mean"])
