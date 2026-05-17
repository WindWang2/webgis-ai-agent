import pytest
import geopandas as gpd
from app.lib.geo_processor.core import (
    safe_parse, to_utm_gdf,
    wgs84_to_gcj02, gcj02_to_wgs84,
    gcj02_to_bd09, bd09_to_gcj02
)

def test_safe_parse():
    # Test string input
    assert safe_parse('{"type": "Point", "coordinates": [0, 0]}') == {"type": "Point", "coordinates": [0, 0]}
    # Test dict input
    assert safe_parse({"type": "Point", "coordinates": [0, 0]}) == {"type": "Point", "coordinates": [0, 0]}
    # Test invalid input
    assert safe_parse("invalid") is None
    assert safe_parse(None) is None

def test_coord_transform_smoke():
    # Beijing coordinates
    lng, lat = 116.404, 39.915
    
    # WGS84 -> GCJ-02
    gcj_lng, gcj_lat = wgs84_to_gcj02(lng, lat)
    assert gcj_lng != lng
    assert gcj_lat != lat
    
    # GCJ-02 -> WGS84
    wgs_lng, wgs_lat = gcj02_to_wgs84(gcj_lng, gcj_lat)
    assert abs(wgs_lng - lng) < 1e-5
    assert abs(wgs_lat - lat) < 1e-5

    # GCJ-02 -> BD-09
    bd_lng, bd_lat = gcj02_to_bd09(gcj_lng, gcj_lat)
    assert bd_lng != gcj_lng
    assert bd_lat != gcj_lat

    # BD-09 -> GCJ-02
    gcj_lng2, gcj_lat2 = bd09_to_gcj02(bd_lng, bd_lat)
    assert abs(gcj_lng2 - gcj_lng) < 1e-5
    assert abs(gcj_lat2 - gcj_lat) < 1e-5

def test_to_utm_gdf():
    geojson = {
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature", 
            "geometry": {"type": "Point", "coordinates": [116.4, 39.9]}, 
            "properties": {"name": "Beijing"}
        }]
    }
    gdf, utm_crs = to_utm_gdf(geojson)
    assert isinstance(gdf, gpd.GeoDataFrame)
    assert utm_crs.startswith("EPSG:326") # Northern hemisphere, zone 50 (approx)
    assert gdf.crs == utm_crs
    assert len(gdf) == 1
    assert gdf.iloc[0]["name"] == "Beijing"

def test_buffer_smart():
    from app.lib.geo_processor.geometry import buffer_smart
    geojson = {"type": "Point", "coordinates": [116.4, 39.9]}
    # 100 meters buffer
    buffered = buffer_smart(geojson, distance=100)
    assert buffered["type"] == "FeatureCollection"
    # Check if area is roughly pi * 100^2
    import geopandas as gpd
    from shapely.geometry import shape
    # Convert back to UTM to check area
    from app.lib.geo_processor.core import to_utm_gdf
    gdf, _ = to_utm_gdf(buffered)
    assert abs(gdf.area.iloc[0] - 3.14159 * 100**2) < 500 # Allowing some tolerance

def test_clip_smart():
    from app.lib.geo_processor.geometry import clip_smart
    target = {
        "type": "FeatureCollection",
        "features": [{"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [[[0,0], [10,0], [10,10], [0,10], [0,0]]]}, "properties": {}}]
    }
    mask = {
        "type": "FeatureCollection",
        "features": [{"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [[[5,5], [15,5], [15,15], [5,15], [5,5]]]}, "properties": {}}]
    }
    clipped = clip_smart(target, mask)
    assert clipped["type"] == "FeatureCollection"
    # Result should be a 5x5 square
    from app.lib.geo_processor.core import to_utm_gdf
    gdf, _ = to_utm_gdf(clipped)
    assert len(gdf) > 0

def test_dissolve_smart():
    from app.lib.geo_processor.geometry import dissolve_smart
    geojson = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [[[0,0], [1,0], [1,1], [0,1], [0,0]]]}, "properties": {"group": 1}},
            {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [[[1,0], [2,0], [2,1], [1,1], [1,0]]]}, "properties": {"group": 1}}
        ]
    }
    dissolved = dissolve_smart(geojson, field="group")
    assert len(dissolved["features"]) == 1

def test_overlay_smart():
    from app.lib.geo_processor.overlay import overlay_smart
    poly1 = {
        "type": "FeatureCollection",
        "features": [{"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [[[0,0], [2,0], [2,2], [0,2], [0,0]]]}, "properties": {"id": 1}}]
    }
    poly2 = {
        "type": "FeatureCollection",
        "features": [{"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [[[1,1], [3,1], [3,3], [1,3], [1,1]]]}, "properties": {"id": 2}}]
    }
    
    # Intersection
    res_int = overlay_smart(poly1, poly2, how="intersection")
    assert len(res_int["features"]) > 0
    
    # Union
    res_uni = overlay_smart(poly1, poly2, how="union")
    assert len(res_uni["features"]) > 0
