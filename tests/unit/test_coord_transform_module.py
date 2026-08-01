"""Unit tests for the deepened GeoCoordinateTransformer module (app/utils/coord_transform.py)."""
import copy
import pytest
from app.utils.coord_transform import (
    transform_geojson,
    normalize_chinese_crs,
    wgs84_to_gcj02,
    gcj02_to_wgs84,
    gcj02_to_bd09,
    bd09_to_gcj02,
    wgs84_to_bd09,
    bd09_to_wgs84,
)


def test_normalize_chinese_crs():
    # Candidate #2: normalize_chinese_crs is the single authority for "what
    # counts as a Chinese CRS" — the tool adapter's policy gate depends on it.
    assert normalize_chinese_crs("wgs84") == "wgs84"
    assert normalize_chinese_crs("WGS-84") == "wgs84"
    assert normalize_chinese_crs("GCJ 02") == "gcj02"
    assert normalize_chinese_crs("BD09") == "bd09"
    # Non-Chinese CRS (including EPSG) → None, the rejection signal.
    assert normalize_chinese_crs("EPSG:4326") is None
    assert normalize_chinese_crs("utm") is None
    assert normalize_chinese_crs("") is None


def test_scalar_math_helpers():
    lng, lat = 116.404, 39.915
    gcj = wgs84_to_gcj02(lng, lat)
    assert gcj != (lng, lat)
    
    wgs = gcj02_to_wgs84(gcj[0], gcj[1])
    assert abs(wgs[0] - lng) < 1e-5
    assert abs(wgs[1] - lat) < 1e-5

    bd = gcj02_to_bd09(gcj[0], gcj[1])
    assert bd != gcj

    gcj_back = bd09_to_gcj02(bd[0], bd[1])
    assert abs(gcj_back[0] - gcj[0]) < 1e-5
    assert abs(gcj_back[1] - gcj[1]) < 1e-5


def test_transform_geojson_chinese_point():
    input_fc = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [116.404, 39.915]
                },
                "properties": {"name": "Beijing"}
            }
        ]
    }
    
    input_copy = copy.deepcopy(input_fc)
    out = transform_geojson(input_fc, "wgs84", "gcj02")

    # Assert immutability
    assert input_fc == input_copy

    # Assert transformation happened
    coords = out["features"][0]["geometry"]["coordinates"]
    assert coords[0] != 116.404
    assert coords[1] != 39.915
    assert out["features"][0]["properties"]["name"] == "Beijing"


def test_transform_geojson_z_dimension_retention():
    input_geom = {
        "type": "LineString",
        "coordinates": [
            [116.404, 39.915, 100.0],
            [116.405, 39.916, 120.0]
        ]
    }
    
    out = transform_geojson(input_geom, "wgs84", "bd09")
    coords = out["coordinates"]

    assert len(coords[0]) == 3
    assert coords[0][2] == 100.0
    assert coords[1][2] == 120.0


def test_transform_geojson_epsg_reprojection():
    input_fc = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [0.0, 0.0]
                },
                "properties": {}
            }
        ]
    }

    out = transform_geojson(input_fc, "EPSG:4326", "EPSG:3857")
    coords = out["features"][0]["geometry"]["coordinates"]
    assert abs(coords[0]) < 1e-5
    assert abs(coords[1]) < 1e-5


def test_transform_geojson_same_crs_returns_copy():
    input_data = {"type": "Point", "coordinates": [120.0, 30.0]}
    out = transform_geojson(input_data, "wgs84", "wgs84")
    
    assert out == input_data
    assert out is not input_data
