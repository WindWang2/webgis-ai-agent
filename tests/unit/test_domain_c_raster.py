"""Unit tests for Domain C: Raster Math, GCP & Spatial Tools."""
import os
import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin
from rasterio.control import GroundControlPoint

from app.lib.geo_analysis.raster_math import (
    _gtiff_profile,
    reclassify,
    raster_calculator,
    resample_raster,
    _suffix_output_path,
    _validate_scheme,
    RESAMPLING_MAP,
)
from app.lib.geo_analysis.raster_ops import zonal_statistics


def test_crit_05_reclassify_nodata_none(tmp_path):
    """CRIT-05: nodata=None in raster profile shouldn't crash np.can_cast in reclassify."""
    raster_path = str(tmp_path / "nodata_none.tif")
    data = np.array([[1, 2], [3, 4]], dtype=np.float32)
    transform = from_origin(0, 10, 1, 1)

    # Create profile with nodata=None explicitly
    with rasterio.open(
        raster_path, "w", driver="GTiff",
        height=2, width=2, count=1, dtype=np.float32,
        crs="EPSG:4326", transform=transform, nodata=None
    ) as dst:
        dst.write(data, 1)

    scheme = [{"min": 1, "max": 2, "value": 10}, {"min": 3, "max": 4, "value": 20}]
    # Calling reclassify with default nodata=None
    result = reclassify(raster_path, scheme, nodata=None)
    assert os.path.exists(result["output_path"])
    with rasterio.open(result["output_path"]) as out_src:
        out_data = out_src.read(1)
        assert out_data[0, 0] == 10
        assert out_data[1, 1] == 20


def test_crit_06_raster_calculator_float_ndvi(tmp_path):
    """CRIT-06: raster_calculator output profile dtype should match result.dtype (e.g. float for NDVI)."""
    path_a = str(tmp_path / "nir_uint8.tif")
    path_b = str(tmp_path / "red_uint8.tif")
    transform = from_origin(0, 10, 1, 1)

    data_a = np.array([[200, 150], [100, 50]], dtype=np.uint8)
    data_b = np.array([[50, 50], [50, 50]], dtype=np.uint8)

    with rasterio.open(path_a, "w", driver="GTiff", height=2, width=2, count=1, dtype=np.uint8, crs="EPSG:4326", transform=transform) as dst:
        dst.write(data_a, 1)
    with rasterio.open(path_b, "w", driver="GTiff", height=2, width=2, count=1, dtype=np.uint8, crs="EPSG:4326", transform=transform) as dst:
        dst.write(data_b, 1)

    # NDVI expression: (A - B) / (A + B)
    result = raster_calculator(path_a, path_b, expression="(A - B) / (A + B)")
    assert os.path.exists(result["output_path"])

    with rasterio.open(result["output_path"]) as out_src:
        assert np.issubdtype(out_src.dtypes[0], np.floating)
        out_data = out_src.read(1)
        # Pixel (0,0): (200 - 50) / (200 + 50) = 150 / 250 = 0.6
        np.testing.assert_allclose(out_data[0, 0], 0.6, rtol=1e-5)


def test_crit_07_gtiff_profile_count_one():
    """CRIT-07: _gtiff_profile forces count=1 by default for single-band outputs."""
    multi_band_profile = {
        "driver": "GTiff",
        "count": 4,
        "dtype": "uint8",
        "width": 100,
        "height": 100,
    }
    profile = _gtiff_profile(multi_band_profile)
    assert profile["count"] == 1

    profile_multiband = _gtiff_profile(multi_band_profile, count=4)
    assert profile_multiband["count"] == 4


def test_crit_08_zonal_statistics_reproject_geojson(tmp_path):
    """CRIT-08: zonal_statistics reprojects input GeoJSON to raster's CRS."""
    raster_path = str(tmp_path / "utm_raster.tif")
    data = np.full((10, 10), 42.0, dtype=np.float32)
    # UTM zone 33N (EPSG:32633) bounds: x in [500000, 5000010], y in [5000000, 5000010]
    transform = from_origin(500000, 5000010, 1, 1)

    with rasterio.open(
        raster_path, "w", driver="GTiff",
        height=10, width=10, count=1, dtype=np.float32,
        crs="EPSG:32633", transform=transform
    ) as dst:
        dst.write(data, 1)

    # GeoJSON polygon in WGS84 (EPSG:4326) corresponding to UTM 33N box [500001..500009, 5000001..5000009]
    geojson_wgs84 = {
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "geometry": {
                "type": "Polygon",
                "coordinates": [[
                    [15.0000127, 45.1534862],
                    [15.0001145, 45.1534862],
                    [15.0001145, 45.1535582],
                    [15.0000127, 45.1535582],
                    [15.0000127, 45.1534862]
                ]]
            },
            "properties": {"id": 1}
        }]
    }

    stats = zonal_statistics(geojson_wgs84, raster_path, stats=["mean", "count"])
    assert len(stats) == 1
    assert stats[0]["mean"] == 42.0



def test_high_05_resample_raster_gcp(tmp_path):
    """HIGH-05: resample_raster supports Ground Control Point (GCP) warping."""
    raster_path = str(tmp_path / "gcp_raster.tif")
    data = np.ones((10, 10), dtype=np.float32)

    gcps = [
        GroundControlPoint(row=0, col=0, x=100.0, y=1000.0, z=0.0),
        GroundControlPoint(row=0, col=10, x=200.0, y=1000.0, z=0.0),
        GroundControlPoint(row=10, col=0, x=100.0, y=900.0, z=0.0),
        GroundControlPoint(row=10, col=10, x=200.0, y=900.0, z=0.0)
    ]
    gcps_crs = "EPSG:4326"

    with rasterio.open(
        raster_path, "w", driver="GTiff",
        height=10, width=10, count=1, dtype=np.float32,
    ) as dst:
        dst.write(data, 1)

    # Write GCPs to dataset using rasterio dataset writer if needed, or mock
    with rasterio.open(raster_path, "r+") as dst:
        dst.gcps = (gcps, gcps_crs)

    result = resample_raster(raster_path, target_resolution=5.0)
    assert os.path.exists(result["output_path"])


def test_high_06_resample_raster_nodata_passing(tmp_path):
    """HIGH-06: resample_raster passes src_nodata and dst_nodata to reproject."""
    raster_path = str(tmp_path / "nodata_raster.tif")
    data = np.full((10, 10), -9999.0, dtype=np.float32)
    data[2:8, 2:8] = 5.0
    transform = from_origin(0, 10, 1, 1)

    with rasterio.open(
        raster_path, "w", driver="GTiff",
        height=10, width=10, count=1, dtype=np.float32,
        crs="EPSG:4326", transform=transform, nodata=-9999.0
    ) as dst:
        dst.write(data, 1)

    result = resample_raster(raster_path, target_resolution=0.5)
    assert os.path.exists(result["output_path"])
    with rasterio.open(result["output_path"]) as out_src:
        assert out_src.nodata == -9999.0


def test_high_07_reclassify_first_match_wins(tmp_path):
    """HIGH-07: reclassify retains user scheme order and uses first-match-wins logic."""
    raster_path = str(tmp_path / "reclass_order.tif")
    data = np.array([[5, 8], [12, 18]], dtype=np.float32)
    transform = from_origin(0, 10, 1, 1)

    with rasterio.open(
        raster_path, "w", driver="GTiff",
        height=2, width=2, count=1, dtype=np.float32,
        crs="EPSG:4326", transform=transform
    ) as dst:
        dst.write(data, 1)

    # Overlapping scheme:
    # Rule 0: min=0, max=10 -> value=100
    # Rule 1: min=5, max=15 -> value=200
    # Pixel value 5 and 8 match both Rule 0 and Rule 1. First match (Rule 0 -> 100) must win!
    scheme = [
        {"min": 0, "max": 10, "value": 100},
        {"min": 5, "max": 15, "value": 200},
    ]

    result = reclassify(raster_path, scheme, nodata=0)
    with rasterio.open(result["output_path"]) as out_src:
        out_data = out_src.read(1)
        assert out_data[0, 0] == 100  # 5 matches rule 0 first
        assert out_data[0, 1] == 100  # 8 matches rule 0 first
        assert out_data[1, 0] == 200  # 12 matches rule 1


def test_high_08_raster_calculator_grid_alignment(tmp_path):
    """HIGH-08: raster_calculator reprojects raster_b in-memory if grids differ."""
    path_a = str(tmp_path / "raster_a.tif")
    path_b = str(tmp_path / "raster_b.tif")

    transform_a = from_origin(0, 10, 1, 1)  # 10x10 grid
    data_a = np.ones((10, 10), dtype=np.float32)

    transform_b = from_origin(0, 10, 2, 2)  # 5x5 grid with different resolution
    data_b = np.full((5, 5), 5.0, dtype=np.float32)

    with rasterio.open(path_a, "w", driver="GTiff", height=10, width=10, count=1, dtype=np.float32, crs="EPSG:4326", transform=transform_a) as dst:
        dst.write(data_a, 1)
    with rasterio.open(path_b, "w", driver="GTiff", height=5, width=5, count=1, dtype=np.float32, crs="EPSG:4326", transform=transform_b) as dst:
        dst.write(data_b, 1)

    result = raster_calculator(path_a, path_b, expression="A + B")
    with rasterio.open(result["output_path"]) as out_src:
        out_data = out_src.read(1)
        assert out_data.shape == (10, 10)
        np.testing.assert_allclose(out_data[0, 0], 6.0)


def test_med_08_reclassify_float64_dem_dtype(tmp_path):
    """MED-08: reclassify float64 DEM raster yields integer dtype output when scheme values are integers."""
    raster_path = str(tmp_path / "dem_float64.tif")
    data = np.array([[100.5, 200.3], [300.7, 400.1]], dtype=np.float64)
    transform = from_origin(0, 10, 1, 1)

    with rasterio.open(
        raster_path, "w", driver="GTiff",
        height=2, width=2, count=1, dtype=np.float64,
        crs="EPSG:4326", transform=transform, nodata=-9999.0
    ) as dst:
        dst.write(data, 1)

    scheme = [
        {"min": 0, "max": 250, "value": 1},
        {"min": 250, "max": 500, "value": 2},
    ]
    result = reclassify(raster_path, scheme, nodata=0)
    with rasterio.open(result["output_path"]) as out_src:
        assert np.issubdtype(out_src.dtypes[0], np.integer)
        out_data = out_src.read(1)
        assert out_data[0, 0] == 1
        assert out_data[1, 1] == 2


def test_med_09_suffix_output_path_extensions():
    """MED-09: _suffix_output_path cleanly handles .tiff, .TIF, .geotiff."""
    assert _suffix_output_path("image.tif", "_reclassified.tif") == "image_reclassified.tif"
    assert _suffix_output_path("image.tiff", "_reclassified.tif") == "image_reclassified.tif"
    assert _suffix_output_path("image.TIF", "_reclassified.tif") == "image_reclassified.tif"
    assert _suffix_output_path("image.geotiff", "_reclassified.tif") == "image_reclassified.tif"
    assert _suffix_output_path("/tmp/path/image.tiff", "_reclassified.tif") == "/tmp/path/image_reclassified.tif"


def test_low_03_04_resampling_map_expansion():
    """LOW-03 & LOW-04: RESAMPLING_MAP includes expanded resampling algorithms."""
    expected_keys = {"nearest", "bilinear", "cubic", "mode", "average", "lanczos", "med", "min", "max", "sum", "q1", "q3"}
    assert expected_keys.issubset(set(RESAMPLING_MAP.keys()))


def test_low_05_validate_scheme_min_greater_than_max():
    """LOW-05: _validate_scheme raises ValueError when min > max."""
    invalid_scheme = [{"min": 100, "max": 50, "value": 1}]
    with pytest.raises(ValueError, match="cannot be greater than"):
        _validate_scheme(invalid_scheme)

