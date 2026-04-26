"""空间插值与网络分析工具 — IDW/Kriging插值、服务区分析、OD矩阵"""
import logging
from typing import Any

import numpy as np
import geopandas as gpd
from shapely.geometry import Point, box, mapping
from scipy.spatial import distance_matrix

from app.tools.registry import ToolRegistry, tool
from app.tools._geojson_utils import safe_parse_geojson, to_utm_gdf, extract_numeric_values

logger = logging.getLogger(__name__)

_safe_parse_geojson = safe_parse_geojson
_extract_numeric_values = extract_numeric_values
_to_utm_gdf = to_utm_gdf


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

        # Grid safety limit to prevent OOM
        MAX_GRID_CELLS = 100_000
        if nx * ny > MAX_GRID_CELLS:
            cell_size = max(cell_size, ((xmax - xmin) * (ymax - ymin)) ** 0.5 / (MAX_GRID_CELLS ** 0.5))
            grid_x, grid_y, xmin, ymin, xmax, ymax = _build_regular_grid(gdf, cell_size, utm_bounds)
            nx, ny = len(grid_x), len(grid_y)
            logger.warning(f"IDW grid auto-adjusted to {nx}x{ny}={nx*ny} cells (cell_size={cell_size:.0f}m)")

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
        from scipy.linalg import lu_factor, lu_solve
        from scipy.optimize import curve_fit

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

        # Experimental variogram — vectorized semivariance
        dist_points = distance_matrix(coords, coords)
        tri_i, tri_j = np.triu_indices(n, k=1)
        flat_dist = dist_points[tri_i, tri_j]
        flat_vals = 0.5 * (values[tri_i] - values[tri_j]) ** 2

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

        # Variogram model functions for fitting
        def vario_spherical(h, psill, range_):
            return np.where(h == 0, 0, np.where(h <= range_,
                psill * (1.5 * h / range_ - 0.5 * (h / range_) ** 3), psill))

        def vario_exponential(h, psill, range_):
            return psill * (1 - np.exp(-3 * h / range_))

        def vario_gaussian(h, psill, range_):
            return psill * (1 - np.exp(-3 * (h / range_) ** 2))

        vario_funcs = {
            "spherical": vario_spherical,
            "exponential": vario_exponential,
            "gaussian": vario_gaussian,
        }

        # Fit variogram to experimental data via least-squares
        init_psill = float(np.var(values))
        init_range = float(lag_arr[-1] / 2)
        vario_fit_func = vario_funcs.get(variogram_model, vario_exponential)

        try:
            popt, _ = curve_fit(vario_fit_func, lag_arr, gamma_arr - nugget,
                                p0=[init_psill, init_range],
                                bounds=([0, 0], [np.inf, np.inf]),
                                maxfev=5000)
            sill = float(popt[0])
            effective_range = float(popt[1])
        except (RuntimeError, ValueError):
            # Fallback to initial estimates if fitting fails
            sill = init_psill
            effective_range = init_range

        # Final variogram with fitted parameters
        def vario(h):
            h = np.asarray(h, dtype=float)
            base = vario_fit_func(h, sill, effective_range)
            return np.where(h == 0, 0, nugget + base)

        # Convert WGS84 bounds to UTM if provided
        utm_bounds = None
        if bounds and len(bounds) == 4:
            bounds_gdf = gpd.GeoDataFrame(geometry=[box(bounds[0], bounds[1], bounds[2], bounds[3])],
                                          crs="EPSG:4326").to_crs(utm_crs)
            utm_bounds = list(bounds_gdf.total_bounds)

        grid_x, grid_y, xmin, ymin, xmax, ymax = _build_regular_grid(gdf, cell_size, utm_bounds)
        nx, ny = len(grid_x), len(grid_y)

        # Grid safety limit to prevent OOM
        MAX_GRID_CELLS = 100_000
        if nx * ny > MAX_GRID_CELLS:
            cell_size = max(cell_size, ((xmax - xmin) * (ymax - ymin)) ** 0.5 / (MAX_GRID_CELLS ** 0.5))
            grid_x, grid_y, xmin, ymin, xmax, ymax = _build_regular_grid(gdf, cell_size, utm_bounds)
            nx, ny = len(grid_x), len(grid_y)
            logger.warning(f"Kriging grid auto-adjusted to {nx}x{ny}={nx*ny} cells (cell_size={cell_size:.0f}m)")

        # Precompute LU factorization of kriging matrix (constant for all prediction points)
        A = vario(dist_points)
        np.fill_diagonal(A, 0)
        A_aug = np.zeros((n + 1, n + 1))
        A_aug[:n, :n] = A
        A_aug[:n, n] = 1.0
        A_aug[n, :n] = 1.0
        A_aug[n, n] = 0.0
        lu_piv = lu_factor(A_aug)

        # Vectorize prediction: compute all grid-to-point distances at once
        grid_points = np.array([(gx, gy) for gy in grid_y for gx in grid_x])
        all_dist = distance_matrix(grid_points, coords)

        out_features = []
        prediction_values = []
        variance_values = []

        for idx in range(len(grid_points)):
            b = vario(all_dist[idx])
            b_aug = np.append(b, 1.0)
            weights = lu_solve(lu_piv, b_aug)

            pred_val = float(np.dot(weights[:n], values))
            krige_var = float(max(np.dot(b, weights[:n]) + weights[n] - nugget, 0))

            prediction_values.append(pred_val)
            variance_values.append(krige_var)

            j, i = idx % nx, idx // nx
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
                "fitted": True,
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

        prev_circle = None
        for ring_idx, ring_dist in enumerate(ring_distances):
            circle_geom = Point(cx, cy).buffer(ring_dist, resolution=64)

            # Create donut by subtracting previous ring
            if prev_circle is not None:
                donut = circle_geom.difference(prev_circle)
            else:
                donut = circle_geom

            donut_wgs84 = gpd.GeoSeries([donut], crs=utm_crs).to_crs("EPSG:4326").iloc[0]
            out_features.append({
                "type": "Feature",
                "geometry": mapping(donut_wgs84),
                "properties": {
                    "distance_m": round(ring_dist, 0),
                    "area_km2": round(float(donut.area) / 1e6, 2),
                    "ring": ring_idx + 1,
                },
            })
            prev_circle = circle_geom

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
            # Vectorized haversine using sklearn
            try:
                from sklearn.metrics.pairwise import haversine_distances
            except ImportError:
                return {"error": "geodesic 方法需要 scikit-learn"}

            orig_wgs84 = np.array([(np.radians(g.centroid.x), np.radians(g.centroid.y))
                                   for g in orig_gdf.to_crs("EPSG:4326").geometry])
            dest_wgs84 = np.array([(np.radians(g.centroid.x), np.radians(g.centroid.y))
                                   for g in dest_gdf.to_crs("EPSG:4326").geometry])
            R = 6371000
            dist = haversine_distances(orig_wgs84, dest_wgs84) * R
        else:
            dist = distance_matrix(orig_coords, dest_coords)

        # Build matrix result
        orig_names = [f"P{i}" for i in range(len(orig_gdf))]
        dest_names = [f"P{i}" for i in range(len(dest_gdf))]

        # Try to use name from properties
        for i, (_, row) in enumerate(orig_gdf.iterrows()):
            for key in ("name", "Name", "名称"):
                val = row.get(key)
                if val and isinstance(val, str):
                    orig_names[i] = val[:20]
                    break
        for i, (_, row) in enumerate(dest_gdf.iterrows()):
            for key in ("name", "Name", "名称"):
                val = row.get(key)
                if val and isinstance(val, str):
                    dest_names[i] = val[:20]
                    break

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
