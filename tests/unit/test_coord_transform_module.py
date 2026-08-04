"""Unit tests for the deepened GeoCoordinateTransformer module (app/utils/coord_transform.py)."""
import copy
from app.utils.coord_transform import (
    transform_geojson,
    normalize_chinese_crs,
    supported_chinese_crs,
    wgs84_to_gcj02,
    gcj02_to_wgs84,
    gcj02_to_bd09,
    bd09_to_gcj02,
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


def test_supported_chinese_crs():
    # Candidate #2: supported_chinese_crs is the single source the adapter
    # formats its error messages from, so the supported set must live in one
    # place. 调用方可依赖顺序做文案拼接。
    crs = supported_chinese_crs()
    # Stable, ascending tuple — order is part of the contract.
    assert isinstance(crs, tuple)
    assert crs == ("bd09", "gcj02", "wgs84")
    # Content equals the canonical set; callers cannot mutate the private set.
    assert set(crs) == {"bd09", "gcj02", "wgs84"}


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


def test_normalize_chinese_crs_underscores():
    assert normalize_chinese_crs("BD_09") == "bd09"
    assert normalize_chinese_crs("GCJ_02") == "gcj02"
    assert normalize_chinese_crs("WGS_84") == "wgs84"
    assert normalize_chinese_crs(4326) is None


def test_gcj02_to_wgs84_fixed_point_refinement():
    lng, lat = 116.4074, 39.9042
    gcj_lng, gcj_lat = wgs84_to_gcj02(lng, lat)
    back_lng, back_lat = gcj02_to_wgs84(gcj_lng, gcj_lat)
    assert abs(back_lng - lng) < 1e-7
    assert abs(back_lat - lat) < 1e-7


def test_out_of_china_boundary_guards():
    # Overseas coordinates
    overseas_lng, overseas_lat = -73.9857, 40.7484
    assert gcj02_to_bd09(overseas_lng, overseas_lat) == (overseas_lng, overseas_lat)
    assert bd09_to_gcj02(overseas_lng, overseas_lat) == (overseas_lng, overseas_lat)

    # Inf coordinates
    inf_val = float('inf')
    assert gcj02_to_bd09(inf_val, 30.0) == (inf_val, 30.0)
    assert bd09_to_gcj02(inf_val, 30.0) == (inf_val, 30.0)


def test_walk_coords_bool_and_element_validation():
    from app.utils.coord_transform import _walk_coords

    def dummy_fn(x, y):
        return (x + 10, y + 10)
    
    # Booleans should be preserved unchanged
    bool_coords = [True, False]
    assert _walk_coords(bool_coords, dummy_fn) == [True, False]

    mixed_coords = [116.4, True]
    assert _walk_coords(mixed_coords, dummy_fn) == [116.4, True]


def test_composed_transformation_chinese_to_epsg():
    import pyproj
    input_geom = {"type": "Point", "coordinates": [116.404, 39.915]}
    
    # 1. gcj02 -> EPSG:3857
    out = transform_geojson(input_geom, "gcj02", "EPSG:3857")
    wgs_lng, wgs_lat = gcj02_to_wgs84(116.404, 39.915)
    transformer = pyproj.Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)
    expected_x, expected_y = transformer.transform(wgs_lng, wgs_lat)
    
    assert abs(out["coordinates"][0] - expected_x) < 1e-3
    assert abs(out["coordinates"][1] - expected_y) < 1e-3

    # 2. EPSG:3857 -> bd09
    in_3857 = {"type": "Point", "coordinates": [expected_x, expected_y]}
    out_bd09 = transform_geojson(in_3857, "EPSG:3857", "bd09")
    expected_bd09 = gcj02_to_bd09(116.404, 39.915)
    assert abs(out_bd09["coordinates"][0] - expected_bd09[0]) < 1e-3
    assert abs(out_bd09["coordinates"][1] - expected_bd09[1]) < 1e-3


def test_tool_handlers_coercion_and_dependency_error(monkeypatch):
    from app.tools.registry import ToolRegistry
    from app.tools.coord_transform import register_coord_transform_tools, register_epsg_transform_tools

    registry = ToolRegistry()
    register_coord_transform_tools(registry)
    register_epsg_transform_tools(registry)

    transform_coordinates = registry._tools["transform_coordinates"]
    reproject_coordinates = registry._tools["reproject_coordinates"]
    
    input_geom = {"type": "Point", "coordinates": [116.404, 39.915]}
    
    # Test integer EPSG coercion (e.g. 4326, 3857)
    res = reproject_coordinates(input_geom, 4326, 3857)
    assert res["success"] is True

    # Test space and underscore coercion in transform_coordinates
    res_cn = transform_coordinates(input_geom, " BD_09 ", " GCJ_02 ")
    assert res_cn["success"] is True

    # Test ImportError handling as DEPENDENCY_ERROR
    def mock_import(name, *args, **kwargs):
        if name == "pyproj":
            raise ImportError("No module named 'pyproj'")
        return __import__(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", mock_import)
    
    res_dep = reproject_coordinates(input_geom, "EPSG:4326", "EPSG:3857")
    assert res_dep["success"] is False
    assert res_dep["code"] == "DEPENDENCY_ERROR"
    assert res_dep["error_type"] == "ImportError"

