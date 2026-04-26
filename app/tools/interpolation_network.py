"""空间插值与网络分析工具 — IDW/Kriging插值、服务区分析、OD矩阵"""
import json
import logging
from typing import Any

import numpy as np
import geopandas as gpd
from shapely.geometry import shape, Point, box, mapping
from scipy.spatial import distance_matrix

from app.tools.registry import ToolRegistry, tool

logger = logging.getLogger(__name__)


def _safe_parse_geojson(geojson: Any) -> dict | None:
    if isinstance(geojson, dict):
        return geojson
    if isinstance(geojson, str):
        try:
            return json.loads(geojson)
        except json.JSONDecodeError:
            return None
    return None


def _to_utm_gdf(geojson: dict) -> tuple[gpd.GeoDataFrame, str] | None:
    """Convert GeoJSON to UTM GeoDataFrame, return (gdf, utm_crs)."""
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


def _extract_numeric_values(gdf: gpd.GeoDataFrame, field: str) -> np.ndarray | None:
    if field not in gdf.columns:
        return None
    try:
        vals = gdf[field].astype(float).values
        if np.any(np.isnan(vals)):
            return None
        return vals
    except (ValueError, TypeError):
        return None


def _build_regular_grid(gdf: gpd.GeoDataFrame, cell_size: float,
                        bounds: list | None = None) -> tuple:
    """Build regular grid over data extent. Returns grid_x, grid_y, xmin, ymin, xmax, ymax."""
    xmin, ymin, xmax, ymax = gdf.total_bounds
    if bounds and len(bounds) == 4:
        xmin, ymin, xmax, ymax = bounds
    buffer_x = (xmax - xmin) * 0.05
    buffer_y = (ymax - ymin) * 0.05
    xmin -= buffer_x
    xmax += buffer_x
    ymin -= buffer_y
    ymax += buffer_y

    nx = max(int((xmax - xmin) / cell_size), 2)
    ny = max(int((ymax - ymin) / cell_size), 2)
    grid_x = np.linspace(xmin, xmax, nx)
    grid_y = np.linspace(ymin, ymax, ny)
    return grid_x, grid_y, xmin, ymin, xmax, ymax


def register_interpolation_network_tools(registry: ToolRegistry):

    @tool(registry, name="idw_interpolation",
           description="反距离加权插值(IDW)，根据离散采样点生成连续值表面",
           param_descriptions={
               "geojson": "输入点要素 GeoJSON FeatureCollection，需包含数值字段",
               "value_field": "用于插值的数值字段名",
               "cell_size": "网格单元大小（米），默认500",
               "power": "距离权重幂次，默认2（标准IDW）",
               "bounds": "可选：分析范围 [xmin, ymin, xmax, ymax]（WGS84）",
           })
    def idw_interpolation(geojson: Any, value_field: str, cell_size: float = 500,
                           power: float = 2, bounds: list = []) -> dict:
        data = _safe_parse_geojson(geojson)
        if not data:
            return {"error": "无效的 GeoJSON 输入"}

        result = _to_utm_gdf(data)
        if result is None:
            return {"error": "无法转换 GeoJSON"}
        gdf, utm_crs = result

        if len(gdf) < 3:
            return {"error": "至少需要3个有效点要素进行插值"}

        values = _extract_numeric_values(gdf, value_field)
        if values is None:
            numeric_cols = [c for c in gdf.columns if c != "geometry" and gdf[c].dtype in ("float64", "int64", "float32", "int32")]
            return {"error": f"字段 '{value_field}' 不是数值类型。可用字段: {numeric_cols}"}

        coords = np.array([(g.centroid.x, g.centroid.y) for g in gdf.geometry])

        # Convert WGS84 bounds to UTM if provided
        utm_bounds = None
        if bounds and len(bounds) == 4:
            bounds_gdf = gpd.GeoDataFrame(geometry=[box(bounds[0], bounds[1], bounds[2], bounds[3])],
                                          crs="EPSG:4326").to_crs(utm_crs)
            utm_bounds = list(bounds_gdf.total_bounds)

        grid_x, grid_y, xmin, ymin, xmax, ymax = _build_regular_grid(gdf, cell_size, utm_bounds)
        nx, ny = len(grid_x), len(grid_y)

        # IDW computation using vectorized distance matrix
        grid_points = np.array([(gx, gy) for gy in grid_y for gx in grid_x])
        dist = distance_matrix(grid_points, coords)
        weights = 1.0 / np.power(np.maximum(dist, 1e-10), power)
        weights_sum = weights.sum(axis=1)
        interpolated = (weights * values).sum(axis=1) / weights_sum
        grid = interpolated.reshape(ny, nx)

        out_features = []
        for i in range(ny):
            for j in range(nx):
                x0, x1 = grid_x[j] - cell_size / 2, grid_x[j] + cell_size / 2
                y0, y1 = grid_y[i] - cell_size / 2, grid_y[i] + cell_size / 2
                cell_geom = box(x0, y0, x1, y1)
                cell_wgs84 = gpd.GeoSeries([cell_geom], crs=utm_crs).to_crs("EPSG:4326").iloc[0]
                out_features.append({
                    "type": "Feature",
                    "geometry": mapping(cell_wgs84),
                    "properties": {value_field: round(float(grid[i, j]), 4)},
                })

        return {
            "type": "FeatureCollection",
            "features": out_features,
            "count": len(out_features),
            "grid_size": [nx, ny],
            "stats": {
                "min": round(float(grid.min()), 4),
                "max": round(float(grid.max()), 4),
                "mean": round(float(grid.mean()), 4),
            },
            "method": f"IDW(power={power})",
        }

    @tool(registry, name="kriging_interpolation",
           description="普通克里金插值(Ordinary Kriging)，基于空间变异函数的地统计插值方法",
           param_descriptions={
               "geojson": "输入点要素 GeoJSON FeatureCollection，需包含数值字段",
               "value_field": "用于插值的数值字段名",
               "cell_size": "网格单元大小（米），默认500",
               "variogram_model": "变异函数模型: 'spherical'(球状), 'exponential'(指数), 'gaussian'(高斯)，默认 exponential",
               "nugget": "块金值(C0)，默认0",
               "bounds": "可选：分析范围 [xmin, ymin, xmax, ymax]（WGS84）",
           })
    def kriging_interpolation(geojson: Any, value_field: str, cell_size: float = 500,
                               variogram_model: str = "exponential", nugget: float = 0,
                               bounds: list = []) -> dict:
        data = _safe_parse_geojson(geojson)
        if not data:
            return {"error": "无效的 GeoJSON 输入"}

        result = _to_utm_gdf(data)
        if result is None:
            return {"error": "无法转换 GeoJSON"}
        gdf, utm_crs = result

        if len(gdf) < 5:
            return {"error": "克里金插值至少需要5个采样点"}

        values = _extract_numeric_values(gdf, value_field)
        if values is None:
            numeric_cols = [c for c in gdf.columns if c != "geometry" and gdf[c].dtype in ("float64", "int64", "float32", "int32")]
            return {"error": f"字段 '{value_field}' 不是数值类型。可用字段: {numeric_cols}"}

        coords = np.array([(g.centroid.x, g.centroid.y) for g in gdf.geometry])
        n = len(coords)

        # Experimental variogram
        dist_points = distance_matrix(coords, coords)
        flat_dist = dist_points[np.triu_indices(n, k=1)]
        flat_vals = np.zeros(len(flat_dist))
        idx = 0
        for i in range(n):
            for j in range(i + 1, n):
                flat_vals[idx] = 0.5 * (values[i] - values[j]) ** 2
                idx += 1

        # Bin the variogram
        max_lag = np.max(flat_dist) / 2
        n_lags = min(15, max(5, n // 3))
        lag_edges = np.linspace(0, max_lag, n_lags + 1)
        lag_means = []
        gamma_means = []
        for k in range(n_lags):
            mask = (flat_dist >= lag_edges[k]) & (flat_dist < lag_edges[k + 1])
            if mask.sum() > 0:
                lag_means.append((lag_edges[k] + lag_edges[k + 1]) / 2)
                gamma_means.append(np.mean(flat_vals[mask]))

        if len(gamma_means) < 3:
            return {"error": "采样点空间分布不足以拟合变异函数"}

        lag_arr = np.array(lag_means)
        gamma_arr = np.array(gamma_means)

        # Fit variogram: estimate sill and range
        sill = float(np.var(values))
        effective_range = float(lag_arr[-1])

        # Variogram function
        def vario(h):
            h = np.asarray(h, dtype=float)
            if variogram_model == "spherical":
                return np.where(h == 0, 0, np.where(h <= effective_range,
                    nugget + sill * (1.5 * h / effective_range - 0.5 * (h / effective_range) ** 3),
                    nugget + sill))
            elif variogram_model == "gaussian":
                return nugget + sill * (1 - np.exp(-3 * (h / effective_range) ** 2))
            else:  # exponential
                return nugget + sill * (1 - np.exp(-3 * h / effective_range))

        # Convert WGS84 bounds to UTM if provided
        utm_bounds = None
        if bounds and len(bounds) == 4:
            bounds_gdf = gpd.GeoDataFrame(geometry=[box(bounds[0], bounds[1], bounds[2], bounds[3])],
                                          crs="EPSG:4326").to_crs(utm_crs)
            utm_bounds = list(bounds_gdf.total_bounds)

        grid_x, grid_y, xmin, ymin, xmax, ymax = _build_regular_grid(gdf, cell_size, utm_bounds)
        nx, ny = len(grid_x), len(grid_y)

        # Kriging system: solve for each prediction point
        # Precompute variogram matrix for known points
        A = vario(dist_points)
        np.fill_diagonal(A, 0)
        A_aug = np.zeros((n + 1, n + 1))
        A_aug[:n, :n] = A
        A_aug[:n, n] = 1.0
        A_aug[n, :n] = 1.0
        A_aug[n, n] = 0.0

        out_features = []
        prediction_values = []
        variance_values = []

        for i in range(ny):
            for j in range(nx):
                pred_point = np.array([[grid_x[j], grid_y[i]]])
                dist_to_pred = distance_matrix(pred_point, coords)[0]
                b = vario(dist_to_pred)
                b_aug = np.append(b, 1.0)

                try:
                    weights = np.linalg.solve(A_aug, b_aug)
                except np.linalg.LinAlgError:
                    weights = np.linalg.lstsq(A_aug, b_aug, rcond=None)[0]

                pred_val = float(np.dot(weights[:n], values))
                krige_var = float(max(np.dot(b, weights[:n]) + weights[n] - nugget, 0))

                prediction_values.append(pred_val)
                variance_values.append(krige_var)

                x0, x1 = grid_x[j] - cell_size / 2, grid_x[j] + cell_size / 2
                y0, y1 = grid_y[i] - cell_size / 2, grid_y[i] + cell_size / 2
                cell_geom = box(x0, y0, x1, y1)
                cell_wgs84 = gpd.GeoSeries([cell_geom], crs=utm_crs).to_crs("EPSG:4326").iloc[0]
                out_features.append({
                    "type": "Feature",
                    "geometry": mapping(cell_wgs84),
                    "properties": {
                        value_field: round(pred_val, 4),
                        "variance": round(krige_var, 4),
                    },
                })

        pred_arr = np.array(prediction_values)
        var_arr = np.array(variance_values)

        return {
            "type": "FeatureCollection",
            "features": out_features,
            "count": len(out_features),
            "grid_size": [nx, ny],
            "stats": {
                "min": round(float(pred_arr.min()), 4),
                "max": round(float(pred_arr.max()), 4),
                "mean": round(float(pred_arr.mean()), 4),
                "mean_variance": round(float(var_arr.mean()), 4),
            },
            "variogram": {
                "model": variogram_model,
                "nugget": nugget,
                "sill": round(sill, 4),
                "range": round(effective_range, 1),
            },
            "method": "Ordinary Kriging",
        }

    @tool(registry, name="service_area",
           description="服务区分析：计算从中心点出发，给定距离/时间可达的范围（等值线）",
           param_descriptions={
               "center": "中心点坐标 [lng, lat]",
               "distance": "最大距离（米），默认3000",
               "n_rings": "等值线环数，默认3",
               "resolution": "等值线分辨率（米），默认100",
           })
    def service_area(center: list, distance: float = 3000, n_rings: int = 3,
                     resolution: float = 100) -> dict:
        if not center or len(center) != 2:
            return {"error": "中心点格式错误，需要 [lng, lat]"}

        lng, lat = float(center[0]), float(center[1])

        # Project center to UTM
        center_gdf = gpd.GeoDataFrame(geometry=[Point(lng, lat)], crs="EPSG:4326")
        zone_number = int((lng + 180) / 6) + 1
        hemisphere = 32600 if lat >= 0 else 32700
        utm_crs = f"EPSG:{hemisphere + zone_number}"
        center_utm = center_gdf.to_crs(utm_crs)
        cx, cy = center_utm.geometry.iloc[0].x, center_utm.geometry.iloc[0].y

        # Generate concentric rings as isochrone-like polygons
        ring_distances = [distance * (i + 1) / n_rings for i in range(n_rings)]
        out_features = []

        for ring_dist in ring_distances:
            n_points = max(int(2 * np.pi * ring_dist / resolution), 16)
            angles = np.linspace(0, 2 * np.pi, n_points, endpoint=False)

            # Generate circle points
            ring_x = cx + ring_dist * np.cos(angles)
            ring_y = cy + ring_dist * np.sin(angles)

            circle_geom = Point(cx, cy).buffer(ring_dist, resolution=int(n_points / 4))
            circle_wgs84 = gpd.GeoSeries([circle_geom], crs=utm_crs).to_crs("EPSG:4326").iloc[0]

            out_features.append({
                "type": "Feature",
                "geometry": mapping(circle_wgs84),
                "properties": {
                    "distance_m": round(ring_dist, 0),
                    "area_km2": round(float(circle_geom.area) / 1e6, 2),
                    "ring": ring_distances.index(ring_dist) + 1,
                },
            })

        return {
            "type": "FeatureCollection",
            "features": out_features,
            "count": len(out_features),
            "center": [lng, lat],
            "max_distance_m": distance,
            "method": "欧氏距离缓冲区",
        }

    @tool(registry, name="od_matrix",
           description="起讫点(OD)距离矩阵：计算多组起点和终点之间的距离矩阵",
           param_descriptions={
               "origins": "起点 GeoJSON FeatureCollection（点要素）",
               "destinations": "终点 GeoJSON FeatureCollection（点要素），省略则计算起点间的距离矩阵",
               "method": "距离计算方法: 'euclidean'(欧氏), 'geodesic'(测地线)，默认 euclidean",
           })
    def od_matrix(origins: Any, destinations: Any = "", method: str = "euclidean") -> dict:
        origins_data = _safe_parse_geojson(origins)
        if not origins_data:
            return {"error": "无效的起点 GeoJSON"}

        result = _to_utm_gdf(origins_data)
        if result is None:
            return {"error": "无法转换起点 GeoJSON"}
        orig_gdf, utm_crs = result

        if len(orig_gdf) < 1:
            return {"error": "至少需要1个起点"}

        # Parse destinations (default to same as origins)
        if destinations:
            dest_data = _safe_parse_geojson(destinations)
            if not dest_data:
                return {"error": "无效的终点 GeoJSON"}
            dest_result = _to_utm_gdf(dest_data)
            if dest_result is None:
                return {"error": "无法转换终点 GeoJSON"}
            dest_gdf, _ = dest_result
        else:
            dest_gdf = orig_gdf

        orig_coords = np.array([(g.centroid.x, g.centroid.y) for g in orig_gdf.geometry])
        dest_coords = np.array([(g.centroid.x, g.centroid.y) for g in dest_gdf.geometry])

        if method == "geodesic":
            from scipy.spatial.distance import cdist
            # Convert UTM back to WGS84 for geodesic
            orig_wgs84 = np.array([(g.centroid.x, g.centroid.y)
                                   for g in orig_gdf.to_crs("EPSG:4326").geometry])
            dest_wgs84 = np.array([(g.centroid.x, g.centroid.y)
                                   for g in dest_gdf.to_crs("EPSG:4326").geometry])

            def haversine(o, d):
                R = 6371000
                dlat = np.radians(d[:, 1] - o[:, 1:2])
                dlon = np.radians(d[:, 0] - o[:, 0:1])
                a = np.sin(dlat / 2) ** 2 + np.cos(np.radians(o[:, 1:2])) * np.cos(np.radians(d[:, 1])) * np.sin(dlon / 2) ** 2
                return R * 2 * np.arcsin(np.sqrt(a))

            dist = np.zeros((len(orig_wgs84), len(dest_wgs84)))
            for i in range(len(orig_wgs84)):
                dist[i] = haversine(orig_wgs84[i:i+1], dest_wgs84).flatten()
        else:
            dist = distance_matrix(orig_coords, dest_coords)

        # Build matrix result
        orig_names = [f"P{i}" for i in range(len(orig_gdf))]
        dest_names = [f"P{i}" for i in range(len(dest_gdf))]

        # Try to use name from properties
        for i, row in orig_gdf.iterrows():
            name = row.get("name") or row.get("Name") or row.get("名称")
            if name:
                orig_names[i] = str(name)[:20]
        for i, row in dest_gdf.iterrows():
            name = row.get("name") or row.get("Name") or row.get("名称")
            if name:
                dest_names[i] = str(name)[:20]

        matrix = {}
        for i, oname in enumerate(orig_names):
            matrix[oname] = {dname: round(float(dist[i, j]), 1) for j, dname in enumerate(dest_names)}

        return {
            "matrix": matrix,
            "origins_count": len(orig_gdf),
            "destinations_count": len(dest_gdf),
            "method": method,
            "unit": "meters",
            "stats": {
                "min_distance": round(float(dist[dist > 0].min()), 1) if (dist > 0).any() else 0,
                "max_distance": round(float(dist.max()), 1),
                "mean_distance": round(float(dist[dist > 0].mean()), 1) if (dist > 0).any() else 0,
            },
        }
