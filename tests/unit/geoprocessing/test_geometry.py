import pytest
from app.lib.geoprocessing.geometry import buffer_smart
from app.lib.geoprocessing.interface import GeoAnalysisResult
import json

def test_buffer_smart_wgs84_metric():
    # A point near London
    geojson = {
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [0.0, 51.5]},
            "properties": {"name": "London"}
        }]
    }
    # Buffer by 1000m
    result = buffer_smart(geojson, distance=1000, unit='m')
    
    assert isinstance(result, GeoAnalysisResult)
    assert result.success is True
    assert "Buffered 1 features by 1000m" in result.summary
    assert "UTM" in result.summary
    # Verify geometry is now a Polygon
    data = result.data
    assert data["features"][0]["geometry"]["type"] == "Polygon"

def test_clip_smart_basic():
    target_geojson = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [0.5, 0.5]}, "properties": {"id": 1}},
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [1.5, 1.5]}, "properties": {"id": 2}}
        ]
    }
    mask_geojson = {
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]
            },
            "properties": {}
        }]
    }
    from app.lib.geoprocessing.geometry import clip_smart
    result = clip_smart(target_geojson, mask_geojson)
    assert result.success is True
    assert len(result.data["features"]) == 1
    assert result.data["features"][0]["properties"]["id"] == 1
    assert "Clipped" in result.summary

def test_overlay_smart_intersection():
    poly_a = {
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [2, 0], [2, 2], [0, 2], [0, 0]]]},
            "properties": {"name": "A"}
        }]
    }
    poly_b = {
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "geometry": {"type": "Polygon", "coordinates": [[[1, 1], [3, 1], [3, 3], [1, 3], [1, 1]]]},
            "properties": {"name": "B"}
        }]
    }
    from app.lib.geoprocessing.geometry import overlay_smart
    result = overlay_smart(poly_a, poly_b, how='intersection')
    assert result.success is True
    assert len(result.data["features"]) > 0
    assert "Intersection" in result.summary
