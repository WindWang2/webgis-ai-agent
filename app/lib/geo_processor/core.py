import json
from typing import Any
import geopandas as gpd
from shapely.geometry import shape

def safe_parse(geojson: Any) -> dict | None:
    """Robust parsing of GeoJSON string or dict."""
    if isinstance(geojson, dict):
        return geojson
    if isinstance(geojson, str):
        try:
            return json.loads(geojson)
        except (json.JSONDecodeError, TypeError):
            return None
    return None

def to_utm_gdf(geojson: dict | str) -> tuple[gpd.GeoDataFrame, str] | None:
    """Convert GeoJSON to UTM GeoDataFrame with automatic zone detection.
    
    Returns:
        tuple[gpd.GeoDataFrame, str]: (projected_gdf, utm_crs_string) or (None, None)
    """
    parsed = safe_parse(geojson)
    if not parsed:
        return None, None
        
    # Handle both FeatureCollection and single Feature/Geometry
    if parsed.get("type") == "FeatureCollection":
        features = parsed.get("features", [])
    elif parsed.get("type") == "Feature":
        features = [parsed]
    else:
        # Assume it's a bare geometry
        features = [{"type": "Feature", "geometry": parsed, "properties": {}}]

    if not features:
        return None, None

    rows = []
    for f in features:
        geom = f.get("geometry")
        if not geom:
            continue
        try:
            s = shape(geom)
            if s.is_empty:
                continue
            props = f.get("properties", {}) or {}
            rows.append({"geometry": s, **props})
        except (ValueError, TypeError):
            continue
            
    if not rows:
        return None, None
        
    gdf = gpd.GeoDataFrame(rows, crs="EPSG:4326")
    
    # Calculate UTM zone from centroid
    centroid = gdf.geometry.unary_union.centroid
    zone_number = int((centroid.x + 180) / 6) + 1
    hemisphere = 32600 if centroid.y >= 0 else 32700
    utm_crs = f"EPSG:{hemisphere + zone_number}"
    
    return gdf.to_crs(utm_crs), utm_crs
