import pytest
import json
from app.lib.geo_analysis.statistics import calculate_sde, moran_i_narrated, hotspot_narrated, h3_lisa
from app.lib.geo_analysis.aggregation import spatial_aggregate, generate_fishnet, h3_binning
from app.lib.geo_analysis.network import calculate_isochrones
from app.lib.geo_processor.core import GeoAnalysisResult

@pytest.fixture
def sample_points():
    return {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [116.39, 39.9]}, "properties": {"val": 10}},
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [116.4, 39.91]}, "properties": {"val": 20}},
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [116.41, 39.92]}, "properties": {"val": 30}},
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [116.42, 39.93]}, "properties": {"val": 40}},
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [116.43, 39.94]}, "properties": {"val": 50}}
        ]
    }

@pytest.fixture
def sample_polygons():
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature", 
                "geometry": {
                    "type": "Polygon", 
                    "coordinates": [[[116.38, 39.89], [116.44, 39.89], [116.44, 39.95], [116.38, 39.95], [116.38, 39.89]]]
                },
                "properties": {"id": 1}
            }
        ]
    }

def test_calculate_sde(sample_points):
    result = calculate_sde(sample_points)
    assert isinstance(result, GeoAnalysisResult)
    assert result.success is True
    assert "geometry" in result.data
    assert "Directional Insight" in result.summary

def test_moran_i(sample_points):
    result = moran_i_narrated(sample_points, "val")
    assert isinstance(result, GeoAnalysisResult)
    assert result.success is True
    assert "moran_i" in result.data
    assert "pattern" in result.data

def test_hotspot(sample_points):
    result = hotspot_narrated(sample_points, "val")
    assert isinstance(result, GeoAnalysisResult)
    assert result.success is True
    assert "hot_spots_count" in result.data

def test_spatial_aggregate(sample_points, sample_polygons):
    result = spatial_aggregate(sample_points, sample_polygons)
    assert isinstance(result, GeoAnalysisResult)
    assert result.success is True
    assert "features" in result.data

def test_generate_fishnet():
    bounds = [116.38, 39.89, 116.44, 39.95]
    result = generate_fishnet(bounds, 0.01)
    assert isinstance(result, GeoAnalysisResult)
    assert result.success is True
    assert len(result.data["features"]) > 0

def test_calculate_isochrones(sample_points):
    # Mock network
    network = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "LineString", "coordinates": [[116.39, 39.9], [116.4, 39.91]]},
                "properties": {"length": 1000}
            }
        ]
    }
    result = calculate_isochrones(network, sample_points, 5)
    assert isinstance(result, GeoAnalysisResult)
    assert result.success is True

def test_h3_binning(sample_points):
    # Test count stat
    result = h3_binning(sample_points, resolution=8)
    assert isinstance(result, GeoAnalysisResult)
    assert result.success is True
    assert "features" in result.data
    assert len(result.data["features"]) > 0
    assert "count" in result.data["features"][0]["properties"]
    
    # Test sum stat with stat_field
    result_sum = h3_binning(sample_points, resolution=8, stat_field="val", stat_method="sum")
    assert result_sum.success is True
    assert "sum" in result_sum.data["features"][0]["properties"]


def test_h3_lisa():
    # Create a grid of hexes via points
    import h3
    center = h3.latlng_to_cell(39.9, 116.39, 8)
    hexes = h3.grid_disk(center, 4)
    
    # Give the central hexes high values, outer hexes low values
    features = []
    for h in hexes:
        dist = h3.grid_distance(center, h)
        boundary = h3.cell_to_boundary(h)
        if dist <= 1:
            val = 100 + (int(h, 16) % 5)
        elif dist == 2:
            val = 50 + (int(h, 16) % 5)
        else:
            val = 10 + (int(h, 16) % 5)
            
        # Flip coordinates from lat/lng to lng/lat for GeoJSON
        coords = [[ [lng, lat] for lat, lng in boundary ]]
        # Close the polygon
        coords[0].append(coords[0][0])
        
        features.append({
            "type": "Feature",
            "geometry": {
                "type": "Polygon",
                "coordinates": coords
            },
            "properties": {
                "h3_index": h,
                "value": val
            }
        })
        
    fc = {
        "type": "FeatureCollection",
        "features": features
    }
    
    result = h3_lisa(fc, "value")
    
    assert isinstance(result, GeoAnalysisResult)
    assert result.success is True
    
    # We should have High-High and Low-Low clusters
    clusters = [f["properties"]["lisa_cluster"] for f in result.data["features"]]
    
    assert "HH" in clusters
    assert "LL" in clusters
    assert "summary" in result.__dict__ or "summary" in dir(result)
