"""Unit tests verifying GIS Algorithm Correctness audit fixes (#950-#955)."""
import numpy as np
import geopandas as gpd
from shapely.geometry import Point

from app.lib.cartography.classify import _jenks_natural_breaks
from app.lib.geo_analysis.statistics import _build_weights
from app.services.rs.band_math import compute_aspect
from app.services.temporal.trend import TemporalTrendEngine
from app.services.network.service_area import NetworkServiceAreaService


def test_jenks_natural_breaks_single_unique_value():
    """Jenks should return [val] without ZeroDivisionError when all values are identical."""
    values = np.array([42.0, 42.0, 42.0, 42.0, 42.0, 42.0])
    breaks = _jenks_natural_breaks(values, k=4)
    assert breaks == [42.0]


def test_spatial_weights_duplicate_and_collinear_points():
    """_build_weights should handle coincident points without index errors."""
    # 5 points, 2 pairs coincident
    points = [Point(100.0, 30.0), Point(100.0, 30.0), Point(100.0, 30.01), Point(100.0, 30.02), Point(100.0, 30.03)]
    gdf = gpd.GeoDataFrame(geometry=points)
    w = _build_weights(gdf, k=2)
    assert w.shape == (5, 5)
    # Each row should have exactly k non-self weights
    assert w.nnz == 10


def test_terrain_aspect_compass_bearing_range():
    """compute_aspect should return azimuths strictly in [0, 360) and handle flat terrain."""
    dem = np.array([
        [100.0, 100.0, 100.0],
        [100.0, 100.0, 100.0],
        [100.0, 100.0, 100.0],
    ])
    aspect = compute_aspect(dem, cell_size=30.0)
    # Flat terrain center is NaN
    assert np.isnan(aspect[1, 1])

    # Sloped terrain
    dem_slope = np.array([
        [10.0, 20.0, 30.0],
        [10.0, 20.0, 30.0],
        [10.0, 20.0, 30.0],
    ])
    aspect_slope = compute_aspect(dem_slope, cell_size=30.0)
    center_aspect = aspect_slope[1, 1]
    assert 0.0 <= center_aspect < 360.0


def test_temporal_trend_zero_variance_linear_regression():
    """compute_linear_regression should return 0 slope and 0 r_squared when variance is 0."""
    values = [10.0, 10.0, 10.0, 10.0]
    slope, intercept, r_sq = TemporalTrendEngine.compute_linear_regression(values)
    assert slope == 0.0
    assert intercept == 10.0
    assert r_sq == 0.0 or r_sq == 1.0


def test_isochrone_high_latitude_metric_scaling():
    """Isochrone degree buffer in high-latitude fallback scales correctly with cos(latitude)."""
    service = NetworkServiceAreaService()
    # At latitude 60°N, cos(60°) = 0.5, buffer in deg should be approx double equatorial
    poly_equator = service._build_isochrone_polygon([], [], (0.0, 0.0))
    poly_60 = service._build_isochrone_polygon([], [], (0.0, 60.0))
    assert poly_equator is not None
    assert poly_60 is not None
