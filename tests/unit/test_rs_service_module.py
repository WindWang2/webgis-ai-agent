"""Unit tests for app.services.rs module-level formulas and SpectralRasterEngine methods."""
import pytest
import numpy as np
from app.services.rs.band_math import (
    INDEX_FORMULAS,
    compute_index_array,
    compute_slope,
    compute_aspect,
    compute_hillshade,
)
from app.services.rs.spectral_engine import spectral_engine, SpectralRasterEngine


def test_index_formulas_exist():
    assert "ndvi" in INDEX_FORMULAS
    assert "ndwi" in INDEX_FORMULAS
    assert "nbr" in INDEX_FORMULAS
    assert "evi" in INDEX_FORMULAS


def test_compute_index_array_ndvi():
    red = np.array([100.0, 200.0])
    nir = np.array([500.0, 200.0])
    result = compute_index_array("ndvi", red=red, nir=nir)
    # [0]: (500-100)/(500+100) = 400/600 = 0.666667
    # [1]: nir+red=400 > 0 -> (200-200)/400 = 0.0
    assert abs(result[0] - (400.0 / 600.0)) < 1e-5
    assert result[1] == 0.0


def test_compute_index_array_unsupported():
    with pytest.raises(ValueError, match="Unsupported index type 'unknown'"):
        compute_index_array("unknown", red=np.array([1.0]))


def test_compute_index_array_nodata_is_nan_not_zero():
    """Sentinel-2 L2A nodata is 0; nir+red==0 must yield NaN, not a
    plausible-looking NDVI 0 (bare soil), so it is excluded from stats
    (audit B-F09)."""
    red = np.array([100.0, 0.0])
    nir = np.array([500.0, 0.0])
    result = compute_index_array("ndvi", red=red, nir=nir)
    assert abs(result[0] - (400.0 / 600.0)) < 1e-5
    assert np.isnan(result[1])  # nodata pixel, not 0.0


def test_compute_slope_horn_matches_known_plane():
    """Reference: a plane tilted only in x at a known angle must recover that
    angle exactly (guards the Horn weights against inversion — audit B-F10
    was verified as a false positive)."""
    import math
    cell = 30.0
    n = 12
    for true_deg in (15.0, 30.0, 45.0):
        ang = math.radians(true_deg)
        z = np.tan(ang) * (np.arange(n) * cell)
        dem = np.tile(z, (n, 1)).astype(float)
        slope = compute_slope(dem, cell)
        interior = slope[1:-1, 1:-1]
        assert abs(interior.mean() - true_deg) < 1e-3
        assert np.abs(interior - true_deg).max() < 1e-3


def test_terrain_kernel_functions():
    dem = np.array([
        [10.0, 15.0, 20.0],
        [10.0, 15.0, 20.0],
        [10.0, 15.0, 20.0],
    ])
    cell_size = 10.0
    slope = compute_slope(dem, cell_size)
    aspect = compute_aspect(dem, cell_size)
    hillshade = compute_hillshade(dem, cell_size)

    assert slope.shape == (3, 3)
    assert aspect.shape == (3, 3)
    assert hillshade.shape == (3, 3)
    assert np.all(hillshade >= 0) and np.all(hillshade <= 255)


def test_spectral_raster_engine_kernel_delegation():
    dem = np.ones((3, 3))
    cell_size = 1.0
    slope = compute_slope(dem, cell_size)
    aspect = compute_aspect(dem, cell_size)
    hillshade = compute_hillshade(dem, cell_size)

    assert slope.shape == (3, 3)
    assert aspect.shape == (3, 3)
    assert hillshade.shape == (3, 3)


@pytest.mark.asyncio
async def test_compute_vegetation_index_unsupported_type():
    res = await spectral_engine.compute_vegetation_index([0, 0, 1, 1], "2023-01-01", "2023-01-02", index_type="invalid")
    assert "error" in res
    assert "不支持的指数类型" in res["error"]


def test_remote_sensing_service_import():
    from app.services.rs import RemoteSensingService
    from app.services.rs.spectral_engine import RemoteSensingService as RSS2
    from app.services.spatial_tasks import SpectralRasterEngine as RSS3

    assert RemoteSensingService is SpectralRasterEngine
    assert RSS2 is SpectralRasterEngine
    assert RSS3 is SpectralRasterEngine

