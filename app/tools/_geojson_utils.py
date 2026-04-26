"""GeoJSON/GeoDataFrame shared helpers for spatial analysis tools."""
import json
from typing import Any

import numpy as np
import geopandas as gpd
from shapely.geometry import box, mapping, shape


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


def grid_to_geojson(grid: np.ndarray, grid_x: np.ndarray, grid_y: np.ndarray,
                    cell_size: float, utm_crs: str, value_field: str = "value",
                    max_cells: int = 100_000) -> list[dict]:
    """Convert a 2D grid array to GeoJSON Feature list with cell polygons."""
    ny, nx = grid.shape
    if nx * ny > max_cells:
        raise ValueError(f"Grid too large ({nx}x{ny}={nx*ny}). Max: {max_cells}")

    features = []
    for i in range(ny):
        for j in range(nx):
            x0 = grid_x[j] - cell_size / 2
            x1 = grid_x[j] + cell_size / 2
            y0 = grid_y[i] - cell_size / 2
            y1 = grid_y[i] + cell_size / 2
            cell_geom = box(x0, y0, x1, y1)
            cell_wgs84 = gpd.GeoSeries([cell_geom], crs=utm_crs).to_crs("EPSG:4326").iloc[0]
            features.append({
                "type": "Feature",
                "geometry": mapping(cell_wgs84),
                "properties": {value_field: round(float(grid[i, j]), 4)},
            })
    return features
