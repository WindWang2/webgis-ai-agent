"""GIS-106 regression: kriging must honor the string-form GeoJSON ``crs``
member (the object-form-only parsing silently defaulted to WGS84).
"""
import asyncio

from app.tools.advanced_spatial import (
    _resolve_kriging_declared_crs,
    register_advanced_spatial_tools,
)
from app.tools.registry import ToolRegistry


def test_string_form_crs_is_extracted():
    assert _resolve_kriging_declared_crs({"crs": "EPSG:32650"}) == "EPSG:32650"
    assert _resolve_kriging_declared_crs({"crs": "EPSG:4490"}) == "EPSG:4490"
    assert _resolve_kriging_declared_crs({"crs": "EPSG:3857"}) == "EPSG:3857"
    assert _resolve_kriging_declared_crs(
        {"crs": {"type": "name", "properties": {"name": "EPSG:4326"}}}
    ) == "EPSG:4326"
    assert _resolve_kriging_declared_crs({}) is None


def test_unknown_declared_crs_is_returned_verbatim_for_driver_rejection():
    assert _resolve_kriging_declared_crs({"crs": "EPSG:999999"}) == "EPSG:999999"


def _utm_feature_collection():
    features = []
    for i in range(3):
        for j in range(3):
            features.append({
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [500000.0 + i * 500.0, 4000000.0 + j * 500.0],
                },
                "properties": {"v": float(i + j)},
            })
    return {
        "type": "FeatureCollection",
        "crs": "EPSG:32650",
        "features": features,
    }


def test_kriging_uses_string_form_declared_crs():
    reg = ToolRegistry()
    register_advanced_spatial_tools(reg)

    res = asyncio.run(reg.dispatch(
        "kriging_interpolation",
        {
            "geojson": _utm_feature_collection(),
            "value_field": "v",
            "resolution": 9,
            "variogram_model": "spherical",
            "neighbors": 8,
            "cross_validate": False,
        },
        session_id="",
    ))
    assert res.get("type") != "error", res
    meta = res["kriging_metadata"]
    assert meta["declared_crs"] == "EPSG:32650"
    assert meta["working_crs"] == "EPSG:32650"
    assert meta["n_samples"] == 9


def test_kriging_rejects_unsupported_string_crs():
    reg = ToolRegistry()
    register_advanced_spatial_tools(reg)

    fc = _utm_feature_collection()
    fc["crs"] = "EPSG:999999"
    res = asyncio.run(reg.dispatch(
        "kriging_interpolation",
        {
            "geojson": fc,
            "value_field": "v",
            "resolution": 9,
            "variogram_model": "spherical",
            "neighbors": 8,
            "cross_validate": False,
        },
        session_id="",
    ))
    assert res["success"] is False
    assert "EPSG:999999" in res["message"]
