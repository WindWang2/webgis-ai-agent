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
