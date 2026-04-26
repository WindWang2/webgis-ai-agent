"""GeoJSON/GeoDataFrame shared helpers for spatial analysis tools."""
import json
from typing import Any

import numpy as np
import geopandas as gpd
from shapely.geometry import shape


def safe_parse_geojson(geojson: Any) -> dict | None:
    if isinstance(geojson, dict):
        return geojson
    if isinstance(geojson, str):
        try:
            return json.loads(geojson)
        except json.JSONDecodeError:
            return None
    return None


def to_utm_gdf(geojson: dict) -> tuple[gpd.GeoDataFrame, str] | None:
    """Convert GeoJSON to UTM GeoDataFrame. Returns (gdf, utm_crs) or None."""
    features = geojson.get("features", [])
    if not features:
        return None
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
        except Exception:
            continue
    if not rows:
        return None
    gdf = gpd.GeoDataFrame(rows, crs="EPSG:4326")
    centroid = gdf.geometry.unary_union.centroid
    zone_number = int((centroid.x + 180) / 6) + 1
    hemisphere = 32600 if centroid.y >= 0 else 32700
    utm_crs = f"EPSG:{hemisphere + zone_number}"
    return gdf.to_crs(utm_crs), utm_crs


def extract_numeric_values(gdf: gpd.GeoDataFrame, field: str) -> np.ndarray | None:
    if field not in gdf.columns:
        return None
    try:
        vals = gdf[field].astype(float).values
        if np.any(np.isnan(vals)):
            return None
        return vals
    except (ValueError, TypeError):
        return None
