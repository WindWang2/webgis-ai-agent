import json
import geopandas as gpd
from app.lib.geo_processor.core import to_utm_gdf, safe_parse

def buffer_smart(geojson: dict | str, distance: float, unit: str = "meters") -> dict:
    """Buffer GeoJSON with metric precision using UTM projection."""
    gdf, utm_crs = to_utm_gdf(geojson)
    if gdf is None:
        return {"type": "FeatureCollection", "features": []}
    
    # Distance is assumed to be in meters as per to_utm_gdf
    buffered_gdf = gdf.copy()
    buffered_gdf["geometry"] = gdf.buffer(distance)
    
    # Convert back to WGS84
    return json.loads(buffered_gdf.to_crs("EPSG:4326").to_json())

def clip_smart(target_layer: dict | str, mask_layer: dict | str) -> dict:
    """Clip target layer with mask layer ensuring same CRS."""
    t_parsed = safe_parse(target_layer)
    m_parsed = safe_parse(mask_layer)
    
    if not t_parsed or not m_parsed:
        return {"type": "FeatureCollection", "features": []}
        
    tgdf = gpd.GeoDataFrame.from_features(t_parsed.get("features", [t_parsed]) if t_parsed.get("type") in ["FeatureCollection", "Feature"] else [t_parsed], crs="EPSG:4326")
    mgdf = gpd.GeoDataFrame.from_features(m_parsed.get("features", [m_parsed]) if m_parsed.get("type") in ["FeatureCollection", "Feature"] else [m_parsed], crs="EPSG:4326")
    
    # Ensure they are GeoDataFrames and have valid geometry
    if tgdf.empty or mgdf.empty:
         return {"type": "FeatureCollection", "features": []}

    clipped = gpd.clip(tgdf, mgdf)
    return json.loads(clipped.to_json())

def dissolve_smart(geojson: dict | str, field: str = None) -> dict:
    """Dissolve geometries in GeoJSON."""
    parsed = safe_parse(geojson)
    if not parsed:
        return {"type": "FeatureCollection", "features": []}
        
    gdf = gpd.GeoDataFrame.from_features(parsed.get("features", [parsed]) if parsed.get("type") in ["FeatureCollection", "Feature"] else [parsed], crs="EPSG:4326")
    
    if gdf.empty:
        return {"type": "FeatureCollection", "features": []}

    dissolved = gdf.dissolve(by=field).reset_index()
    return json.loads(dissolved.to_json())
