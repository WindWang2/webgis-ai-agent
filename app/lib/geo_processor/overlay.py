import json
import geopandas as gpd
from app.lib.geo_processor.core import safe_parse

def overlay_smart(layer_a: dict | str, layer_b: dict | str, how: str = "intersection") -> dict:
    """Perform overlay operation between two layers."""
    a_parsed = safe_parse(layer_a)
    b_parsed = safe_parse(layer_b)
    
    if not a_parsed or not b_parsed:
        return {"type": "FeatureCollection", "features": []}
        
    gdf_a = gpd.GeoDataFrame.from_features(a_parsed.get("features", [a_parsed]) if a_parsed.get("type") in ["FeatureCollection", "Feature"] else [a_parsed], crs="EPSG:4326")
    gdf_b = gpd.GeoDataFrame.from_features(b_parsed.get("features", [b_parsed]) if b_parsed.get("type") in ["FeatureCollection", "Feature"] else [b_parsed], crs="EPSG:4326")
    
    if gdf_a.empty or gdf_b.empty:
        return {"type": "FeatureCollection", "features": []}

    result = gpd.overlay(gdf_a, gdf_b, how=how)
    return json.loads(result.to_json())
