"""热力图网格构建原语（E-3 / #894 分层收口）。

`_extract_heatmap_points` / `_build_heatmap_grid` / `_build_grid_features`
自 app/services/spatial_tasks.py 原样搬移——lib/geo_analysis/density.py
此前 lazy import services 层复用它们（注释自述"keep them lazy to avoid a
cycle"）。spatial_tasks 保留 import 引用（Celery 任务路径不变）。
"""
import math
from typing import Dict, List

from app.lib.cancellation import cancellable

import numpy as np
from shapely.geometry import mapping
from shapely import box as sbox


def _extract_heatmap_points(features: List[Dict]) -> tuple[list, list]:
    """Extract valid (lon, lat) points from GeoJSON features."""
    points = []
    for f in cancellable(features or [], every=512):
        if not isinstance(f, dict):
            continue
        geom = f.get("geometry") or {}
        if geom.get("type") != "Point":
            continue
        coords = geom.get("coordinates")
        if not coords or len(coords) < 2:
            continue
        try:
            lon, lat = float(coords[0]), float(coords[1])
            if not math.isnan(lon) and not math.isnan(lat):
                points.append((lon, lat))
        except (ValueError, TypeError):
            continue
    return [p[0] for p in points], [p[1] for p in points]


def _build_heatmap_grid(xs, ys, cell_size: int):
    """Build histogram grid from point coordinates. Returns (H, xedges, yedges, cell_deg).

    ``cell_size`` is meters (tool schema: 10-5000m). The degree-per-meter
    ratio differs between axes: 1 deg lat ≈ 111.32 km everywhere, but 1 deg
    lng ≈ 111.32*cos(lat) km (audit GIS-25: the previous fixed ``cell_size /
    111000`` produced non-square, latitude-dependent cells — e.g. 500 m
    became ~250 m in the lng direction at 60°N). We derive per-axis degree
    widths from the data's mean latitude so cells are square in meters.
    """
    mean_lat = sum(ys) / len(ys)
    meters_per_deg_lat = 111320.0
    meters_per_deg_lng = max(meters_per_deg_lat * math.cos(math.radians(mean_lat)), 1000.0)
    cell_deg_lng = cell_size / meters_per_deg_lng
    cell_deg_lat = cell_size / meters_per_deg_lat
    # Keep the historical 4th return value a single float (cell width in
    # degrees of longitude) so existing tuple-unpacking callers stay stable;
    # the lng/lat widths are both returned via the bin edges themselves.
    cell_deg = cell_deg_lng
    margin_lng = cell_deg_lng * 2
    margin_lat = cell_deg_lat * 2
    x_min, x_max = min(xs) - margin_lng, max(xs) + margin_lng
    y_min, y_max = min(ys) - margin_lat, max(ys) + margin_lat
    if x_min == x_max:
        x_max += cell_deg_lng
    if y_min == y_max:
        y_max += cell_deg_lat
    x_bins = np.arange(x_min, x_max + cell_deg_lng, cell_deg_lng)
    y_bins = np.arange(y_min, y_max + cell_deg_lat, cell_deg_lat)
    if len(x_bins) > 5000 or len(y_bins) > 5000:
        raise ValueError("Resolution too high for the data extent")
    H, xedges, yedges = np.histogram2d(xs, ys, bins=[x_bins, y_bins])
    return H, xedges, yedges, cell_deg


def _build_grid_features(H, xedges, yedges, max_val: float) -> list[dict]:
    """Build GeoJSON features for non-zero histogram cells."""
    MAX_GRID_FEATURES = 500_000
    nonzero = np.argwhere(H > 0)
    total_cells = len(nonzero)
    if total_cells > MAX_GRID_FEATURES:
        raise ValueError(f"Grid too dense ({total_cells} cells). Increase cell_size or reduce data extent. Max allowed: {MAX_GRID_FEATURES}")
    if total_cells == 0:
        return []

    # Vectorized cell construction: np.argwhere is row-major (same order as the
    # scalar loop); shapely 2.x sbox() builds all geometries in one C call.
    i = nonzero[:, 0]
    j = nonzero[:, 1]
    counts = H[i, j]
    rects = sbox(xedges[i], yedges[j], xedges[i + 1], yedges[j + 1])

    return [
        {
            "type": "Feature",
            "geometry": mapping(rect),
            "properties": {
                "count": int(count),
                "weight": round(float(count / max_val), 4),
            },
        }
        for rect, count in zip(rects, counts)
    ]
