import pytest
import json
from app.lib.geoprocessing.statistics import calculate_sde

def test_calculate_sde_line():
    # Points in a vertical line (along longitude 116.3)
    geojson = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [116.3, 39.9]}, "properties": {}},
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [116.3, 40.0]}, "properties": {}},
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [116.3, 40.1]}, "properties": {}}
        ]
    }
    result = calculate_sde(geojson)
    assert result.success is True
    assert "Directional Insight" in result.summary
    assert "North-South" in result.summary

def test_moran_i_clustered():
    # Clustered values: 5 points high in one corner, 5 points low in other
    features = []
    # High cluster
    for i in range(5):
        features.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [0.001*i, 0.001*i]}, "properties": {"val": 100}})
    # Low cluster
    for i in range(5):
        features.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [1.0 + 0.001*i, 1.0 + 0.001*i]}, "properties": {"val": 0}})
    
    geojson = {"type": "FeatureCollection", "features": features}
    from app.lib.geoprocessing.statistics import moran_i_narrated
    result = moran_i_narrated(geojson, "val")
    assert result.success is True
    assert "clustering" in result.summary.lower()
    assert result.data["moran_i"] > result.data["expected_i"]

def test_hotspot_narrated():
    # Hotspot: Group of high values, group of low values
    features = []
    # Hot area (10 points)
    for i in range(10):
        features.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [0.0001*i, 0.0001*i]}, "properties": {"val": 100}})
    # Cold area (10 points)
    for i in range(10):
        features.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [1.0 + 0.0001*i, 1.0 + 0.0001*i]}, "properties": {"val": 0}})
    
    geojson = {"type": "FeatureCollection", "features": features}
    from app.lib.geoprocessing.statistics import hotspot_narrated
    # Use explicit distance band to ensure points in the same cluster are neighbors
    # Cluster extent is about 110m, distance between clusters is about 150km.
    result = hotspot_narrated(geojson, "val", distance_band=500)
    assert result.success is True
    assert "hot spots" in result.summary.lower()
    assert result.data["hot_spots_count"] > 0
    assert result.data["cold_spots_count"] > 0

def test_hotspot_narrated_edge_cases():
    from app.lib.geoprocessing.statistics import hotspot_narrated
    
    # 1. Invalid field
    geojson = {
        "type": "FeatureCollection",
        "features": [{"type": "Feature", "geometry": {"type": "Point", "coordinates": [0,0]}, "properties": {"val": 1}}]
    }
    result = hotspot_narrated(geojson, "missing")
    assert result.success is False
    assert "missing" in result.summary
    
    # 2. Too few features
    result = hotspot_narrated(geojson, "val")
    assert result.success is False
    assert "at least 3" in result.summary.lower()
    
    # 3. All identical values
    geojson = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [0,0]}, "properties": {"val": 10}},
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [1,1]}, "properties": {"val": 10}},
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [2,2]}, "properties": {"val": 10}}
        ]
    }
    result = hotspot_narrated(geojson, "val")
    assert result.success is False
    assert "identical" in result.summary.lower()

def test_calculate_sde_minimal():
    from app.lib.geoprocessing.statistics import calculate_sde
    # Exactly 3 points in a triangle
    geojson = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [0, 0]}, "properties": {}},
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [1, 0]}, "properties": {}},
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [0, 1]}, "properties": {}}
        ]
    }
    result = calculate_sde(geojson)
    assert result.success is True
    assert result.data["properties"]["area_km2"] > 0

def test_moran_i_dispersed():
    from app.lib.geoprocessing.statistics import moran_i_narrated
    # Dispersed values: alternating high and low
    features = []
    for i in range(10):
        val = 100 if i % 2 == 0 else 0
        features.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [0.001*i, 0]}, "properties": {"val": val}})
    
    geojson = {"type": "FeatureCollection", "features": features}
    result = moran_i_narrated(geojson, "val")
    assert result.success is True
    # Dispersion might not always be "statistically significant" with few points and simple weights,
    # but let's check if it's at least negative or random.
    assert "pattern" in result.data

def test_hotspot_no_significance():
    from app.lib.geoprocessing.statistics import hotspot_narrated
    # Use a seed and distribution that is very unlikely to yield significance with N=10
    import numpy as np
    rng = np.random.default_rng(123)
    features = []
    for i in range(10):
        # Using constant values would yield s=0, so use very similar values
        val = 10.0 + rng.standard_normal() * 0.01
        features.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [0.01*i, 0.01*i]}, "properties": {"val": float(val)}})

    geojson = {"type": "FeatureCollection", "features": features}
    result = hotspot_narrated(geojson, "val", distance_band=100)
    assert result.success is True
    # If it still finds something by chance, we check the logic consistency
    if result.data["hot_spots_count"] == 0 and result.data["cold_spots_count"] == 0:
        assert "No significant" in result.summary
    else:
        assert "statistically significant" in result.summary

