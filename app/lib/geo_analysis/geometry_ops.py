"""Geometry construction algorithms - Voronoi, convex hull, multi-ring buffer.

Deep math module for geometry-from-points operations. Pure functions: take a
GeoJSON dict, return a :class:`GeoAnalysisResult` whose ``data`` is a
FeatureCollection.

- :func:`voronoi_polygons` - bounded Voronoi tessellation via mirror points.
- :func:`convex_hull` - minimal convex polygon, optionally grouped by a field.
- :func:`multi_ring_buffer` - concentric distance rings around the input.

Extracted from ``app/tools/spatial_stats.py`` (architecture-review F2): these
were orphan algorithms doing deep math in the tool adapter layer.
"""
import logging
from typing import Optional

import numpy as np
import geopandas as gpd
from shapely.geometry import box, mapping, Polygon

from app.lib.geo_processor.core import GeoAnalysisResult, to_utm_gdf
from app.lib.geo_analysis._vector import extract_centroids

logger = logging.getLogger(__name__)


def voronoi_polygons(
    geojson: dict,
    clip_bounds: Optional[list] = None,
) -> GeoAnalysisResult:
    """Bounded Voronoi (Thiessen) tessellation of input points.

    Uses the 4-axis mirror-point technique to produce finite regions for
    boundary points, then clips to the data extent + 50% margin.

    Args:
        geojson: input point FeatureCollection (WGS84).
        clip_bounds: optional ``[xmin, ymin, xmax, ymax]`` clip extent (WGS84).

    Returns:
        ``GeoAnalysisResult`` whose ``data`` is a FeatureCollection of Voronoi
        cells with the original point properties plus ``area_km2``.
    """
    result = to_utm_gdf(geojson)
    if result is None:
        return GeoAnalysisResult(
            False, None, "无法解析矢量数据",
            error_type="ValueError", correction_hint="检查 GeoJSON 是否为有效的点要素集合",
        )
    gdf, utm_crs = result

    if len(gdf) < 3:
        return GeoAnalysisResult(
            False, None, "至少需要3个点要素",
            error_type="ValueError", correction_hint="提供至少 3 个点要素",
        )

    coords = extract_centroids(gdf)

    xmin, ymin, xmax, ymax = gdf.total_bounds
    margin = max(xmax - xmin, ymax - ymin) * 0.5
    mirror_points = np.array([
        coords[:, 0], 2 * ymin - coords[:, 1],
    ]).T
    mirror_points2 = np.array([
        2 * xmax - coords[:, 0], coords[:, 1],
    ]).T
    mirror_points3 = np.array([
        coords[:, 0], 2 * ymax - coords[:, 1],
    ]).T
    mirror_points4 = np.array([
        2 * xmin - coords[:, 0], coords[:, 1],
    ]).T
    all_points = np.vstack([coords, mirror_points, mirror_points2, mirror_points3, mirror_points4])

    from scipy.spatial import Voronoi

    try:
        vor = Voronoi(all_points)
    except (ValueError, TypeError, RuntimeError) as e:
        return GeoAnalysisResult(
            False, None, f"Voronoi 计算失败: {e}",
            error_type=type(e).__name__,
            correction_hint="检查点是否共线或重复",
        )

    out_features = []
    clip_box = box(xmin - margin, ymin - margin, xmax + margin, ymax + margin)
    raw_polys = []  # (poly, props) - batch CRS after loop

    for i in range(len(coords)):
        region_idx = vor.point_region[i]
        region = vor.regions[region_idx]
        if -1 in region or len(region) == 0:
            continue
        polygon_coords = [vor.vertices[v] for v in region]
        try:
            poly = Polygon(polygon_coords)
            if not poly.is_valid:
                poly = poly.buffer(0)
            poly = poly.intersection(clip_box)
            if poly.is_empty:
                continue
            props = {k: v for k, v in gdf.iloc[i].items() if k != "geometry"}
            props["area_km2"] = round(float(poly.area) / 1e6, 4)
            raw_polys.append((poly, props))
        except (ValueError, TypeError):
            continue

    # Batch CRS transform: one GeoSeries instead of N per-polygon calls
    if raw_polys:
        gs = gpd.GeoSeries([p for p, _ in raw_polys], crs=utm_crs).to_crs("EPSG:4326")
        for (poly, props), poly_wgs84 in zip(raw_polys, gs):
            out_features.append({
                "type": "Feature",
                "geometry": mapping(poly_wgs84),
                "properties": props,
            })

    fc = {
        "type": "FeatureCollection",
        "features": out_features,
        "count": len(out_features),
    }
    return GeoAnalysisResult(True, fc, f"Voronoi: {len(out_features)} cells from {len(coords)} points")


def convex_hull(
    geojson: dict,
    group_by: str = "",
) -> GeoAnalysisResult:
    """Minimal convex polygon enclosing the input features.

    Args:
        geojson: input FeatureCollection (WGS84).
        group_by: optional property field name; if present and in the data,
            produces one hull per unique value of that field.

    Returns:
        ``GeoAnalysisResult`` whose ``data`` is a FeatureCollection of hull
        polygons with ``feature_count`` and ``area_km2``.
    """
    result = to_utm_gdf(geojson)
    if result is None:
        return GeoAnalysisResult(
            False, None, "无法解析矢量数据",
            error_type="ValueError", correction_hint="检查 GeoJSON 是否为有效的要素集合",
        )
    gdf, utm_crs = result

    if len(gdf) < 3:
        return GeoAnalysisResult(
            False, None, "至少需要3个要素",
            error_type="ValueError", correction_hint="提供至少 3 个要素",
        )

    out_features = []

    if group_by and group_by in gdf.columns:
        for name, group in gdf.groupby(group_by):
            try:
                hull = group.geometry.union_all().convex_hull
                if hull.is_empty:
                    continue
                hull_wgs84 = gpd.GeoSeries([hull], crs=utm_crs).to_crs("EPSG:4326").iloc[0]
                out_features.append({
                    "type": "Feature",
                    "geometry": mapping(hull_wgs84),
                    "properties": {
                        group_by: str(name),
                        "feature_count": len(group),
                        "area_km2": round(float(hull.area) / 1e6, 4),
                    },
                })
            except (ValueError, TypeError):
                continue
    else:
        hull = gdf.geometry.union_all().convex_hull
        hull_wgs84 = gpd.GeoSeries([hull], crs=utm_crs).to_crs("EPSG:4326").iloc[0]
        out_features.append({
            "type": "Feature",
            "geometry": mapping(hull_wgs84),
            "properties": {
                "feature_count": len(gdf),
                "area_km2": round(float(hull.area) / 1e6, 4),
            },
        })

    fc = {
        "type": "FeatureCollection",
        "features": out_features,
        "count": len(out_features),
    }
    return GeoAnalysisResult(True, fc, f"Convex hull: {len(out_features)} feature(s)")


def multi_ring_buffer(
    geojson: dict,
    distances: Optional[list] = None,
    merge_rings: bool = True,
) -> GeoAnalysisResult:
    """Concentric distance rings around the input features.

    Args:
        geojson: input FeatureCollection (WGS84).
        distances: ascending list of buffer distances in meters. Defaults to
            ``[500, 1000, 1500]``.
        merge_rings: ``True`` (default) produces ring bands (each excludes the
            inner buffer); ``False`` produces independent concentric circles.

    Returns:
        ``GeoAnalysisResult`` whose ``data`` is a FeatureCollection of ring
        features with ``distance_m`` and ``area_km2``.
    """
    result = to_utm_gdf(geojson)
    if result is None:
        return GeoAnalysisResult(
            False, None, "无法解析矢量数据",
            error_type="ValueError", correction_hint="检查 GeoJSON 是否为有效的要素集合",
        )
    gdf, utm_crs = result

    if distances is None:
        distances = [500, 1000, 1500]

    if not distances:
        return GeoAnalysisResult(
            False, None, "需要至少一个缓冲距离",
            error_type="ValueError", correction_hint="提供至少一个缓冲距离",
        )

    distances = sorted([float(d) for d in distances])
    union_geom = gdf.geometry.union_all()
    out_features = []

    prev_buffer = None
    for dist in distances:
        buf = union_geom.buffer(dist, quad_segs=32)

        if merge_rings and prev_buffer is not None:
            ring = buf.difference(prev_buffer)
        else:
            ring = buf

        if ring.is_empty:
            continue

        ring_wgs84 = gpd.GeoSeries([ring], crs=utm_crs).to_crs("EPSG:4326").iloc[0]
        out_features.append({
            "type": "Feature",
            "geometry": mapping(ring_wgs84),
            "properties": {
                "distance_m": dist,
                "area_km2": round(float(ring.area) / 1e6, 4),
            },
        })
        prev_buffer = buf

    method = "多环缓冲区" + ("（环形区域）" if merge_rings else "")
    fc = {
        "type": "FeatureCollection",
        "features": out_features,
        "count": len(out_features),
        "method": method,
    }
    return GeoAnalysisResult(True, fc, f"Multi-ring buffer: {len(out_features)} rings, {method}")
