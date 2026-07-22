"""Spatial interpolation utilities."""
import logging
import h3
import numpy as np
from scipy.spatial import cKDTree
from shapely.geometry import Polygon, mapping
from app.lib.geo_processor.core import GeoAnalysisResult

logger = logging.getLogger(__name__)

def idw_interpolation(points_geojson: dict | str, value_field: str, resolution: int = 8, power: float = 2.0) -> GeoAnalysisResult:
    """
    IDW interpolation to H3 grid covering the full bounding box.
    
    审计：之前只对输入点周围 distance-2 的 H3 单元插值，导致两点之间的
    区域完全没有结果。现在改为对边界框内的所有 H3 单元进行插值，
    生成完整的连续表面。
    """
    coords = []
    values = []
    for feature in points_geojson['features']:
        lon, lat = feature['geometry']['coordinates']
        coords.append([lat, lon])
        values.append(feature['properties'][value_field])
    
    coords = np.array(coords)
    values = np.array(values)
    
    # Get bounding box with small buffer
    min_lat, min_lon = np.min(coords, axis=0)
    max_lat, max_lon = np.max(coords, axis=0)
    # Get bounding box with ~1km buffer (≈0.009 degrees) to avoid edge effects
    BUF_DEG = 0.009
    min_lat = max(min_lat - BUF_DEG, -90)
    max_lat = min(max_lat + BUF_DEG, 90)
    min_lon = max(min_lon - BUF_DEG, -180)
    max_lon = min(max_lon + BUF_DEG, 180)
    
    # Get ALL H3 cells in bounding box (complete surface coverage)
    # h3 v4: use geo_to_cells (polyfill was removed in v4)
    polygon = {
        "type": "Polygon",
        "coordinates": [[
            [min_lon, min_lat], [max_lon, min_lat], [max_lon, max_lat],
            [min_lon, max_lat], [min_lon, min_lat]
        ]]
    }
    target_cells = h3.geo_to_cells(polygon, resolution)
    
    # cKDTree for fast nearest-neighbor queries (O(n log n))
    tree = cKDTree(coords)
    k = min(5, len(coords))

    # Batch query all H3 cells at once instead of per-cell Python loop
    cell_coords = np.array([h3.cell_to_latlng(cell) for cell in target_cells])
    dist, idx = tree.query(cell_coords, k=k)

    results = []
    for i, cell in enumerate(target_cells):
        d = dist[i]
        j = idx[i]
        if np.any(d < 1e-10):
            val = float(values[j[d < 1e-10][0]])
        else:
            weights = 1.0 / (d ** power)
            val = float(np.sum(weights * values[j]) / np.sum(weights))
        results.append({"h3_index": cell, "value": val})
    
    return results


def h3_to_geojson(results: dict, value_field: str = "value") -> dict:
    """Convert IDW H3 results to GeoJSON FeatureCollection.
    
    审计：消除 advanced_spatial.py 中重复的 H3-to-GeoJSON 转换逻辑。
    """
    features = []
    for res in results:
        cell = res["h3_index"]
        val = res["value"]
        boundary = h3.cell_to_boundary(cell)  # [(lat, lng), ...]
        # Shapely expects [(lng, lat), ...]
        poly_coords = [(lng, lat) for lat, lng in boundary]
        features.append({
            "type": "Feature",
            "geometry": mapping(Polygon(poly_coords)),
            "properties": {
                "h3_index": cell,
                value_field: round(val, 4)
            }
        })
    
    return {
        "type": "FeatureCollection",
        "features": features,
    }
