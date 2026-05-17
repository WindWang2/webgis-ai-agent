import pytest
import json
from shapely.geometry import Point, LineString, mapping
from app.lib.geoprocessing.network import calculate_isochrones, nearest_neighbor_features
from app.lib.geoprocessing.interface import GeoAnalysisResult

def test_calculate_isochrones_basic():
    # Create a simple grid network
    # (0,0) -- (1,0) -- (2,0)
    #   |        |        |
    # (0,1) -- (1,1) -- (2,1)
    
    features = []
    # Horizontal lines
    features.append({"type": "Feature", "geometry": mapping(LineString([(0,0), (1,0)])), "properties": {"length": 1}})
    features.append({"type": "Feature", "geometry": mapping(LineString([(1,0), (2,0)])), "properties": {"length": 1}})
    features.append({"type": "Feature", "geometry": mapping(LineString([(0,1), (1,1)])), "properties": {"length": 1}})
    features.append({"type": "Feature", "geometry": mapping(LineString([(1,1), (2,1)])), "properties": {"length": 1}})
    # Vertical lines
    features.append({"type": "Feature", "geometry": mapping(LineString([(0,0), (0,1)])), "properties": {"length": 1}})
    features.append({"type": "Feature", "geometry": mapping(LineString([(1,0), (1,1)])), "properties": {"length": 1}})
    features.append({"type": "Feature", "geometry": mapping(LineString([(2,0), (2,1)])), "properties": {"length": 1}})
    
    network_geojson = {"type": "FeatureCollection", "features": features}
    
    facility_points = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": mapping(Point(0,0)), "properties": {"id": "f1"}}
    ]}
    
    # Travel speed 1 unit per minute, time 1.5 minutes
    # Should reach (0,0), (1,0), (0,1) and partially along lines
    result = calculate_isochrones(network_geojson, facility_points, travel_time_min=1.5, mode='walking')
    
    assert isinstance(result, GeoAnalysisResult)
    assert result.success is True
    assert "features" in result.data
    assert len(result.data["features"]) > 0

def test_nearest_neighbor_features_basic():
    source_points = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "geometry": mapping(Point(0, 0)), "properties": {"name": "S1"}}
        ]
    }
    target_points = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "geometry": mapping(Point(1, 1)), "properties": {"name": "T1"}},
            {"type": "Feature", "geometry": mapping(Point(10, 10)), "properties": {"name": "T2"}}
        ]
    }
    
    result = nearest_neighbor_features(source_points, target_points)
    
    assert isinstance(result, GeoAnalysisResult)
    assert result.success is True
    assert len(result.data["features"]) == 1
    # Check if it found the closest one (T1)
    assert result.data["features"][0]["properties"]["nearest_id"] == "T1"
    assert result.data["features"][0]["properties"]["distance"] == pytest.approx(1.4142, abs=1e-4)

def test_calculate_isochrones_empty_network():
    network_geojson = {"type": "FeatureCollection", "features": []}
    facility_points = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": mapping(Point(0,0)), "properties": {"id": "f1"}}
    ]}
    
    result = calculate_isochrones(network_geojson, facility_points, travel_time_min=1.5)
    assert result.success is True
    assert len(result.data["features"]) == 0

def test_nearest_neighbor_invalid_geometry():
    source_points = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "geometry": mapping(LineString([(0, 0), (1,1)])), "properties": {"name": "S1"}}
        ]
    }
    target_points = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "geometry": mapping(Point(1, 1)), "properties": {"name": "T1"}}
        ]
    }
    
    # LineString doesn't have .x, .y, so it should fail gracefully or handle it
    result = nearest_neighbor_features(source_points, target_points)
    assert result.success is False
    assert "ProcessingError" in result.error_type
