"""空间统计与聚类分析工具 — DBSCAN/K-Means聚类、Moran's I、Getis-Ord Gi*、核密度估计"""
import logging
from typing import Any

import numpy as np
import geopandas as gpd
from shapely.geometry import box, mapping
from scipy.spatial import distance_matrix

from app.tools.registry import ToolRegistry, tool
from app.lib.geo_processor.core import safe_parse as safe_parse_geojson, to_utm_gdf
from app.lib.geo_analysis.statistics import _extract_numeric_values as extract_numeric_values
from app.services.spatial_analyzer import SpatialAnalyzer

logger = logging.getLogger(__name__)

def register_spatial_stats_tools(registry: ToolRegistry):

    @tool(registry, name="spatial_cluster",
           description="空间聚类分析（DBSCAN密度聚类或K-Means分割），返回每个要素的聚类标签",
           param_descriptions={
               "geojson": "输入点要素 GeoJSON FeatureCollection 或数据引用(ref:xxx)",
               "method": "聚类方法: 'dbscan'(密度聚类, 默认) 或 'kmeans'(K均值)",
               "n_clusters": "K-Means聚类数，默认5",
               "eps": "DBSCAN邻域半径（米），默认1000",
               "min_samples": "DBSCAN最小样本数，默认5",
               "value_field": "可选：参与聚类的数值字段名，将作为额外聚类维度",
           })
    def spatial_cluster(geojson: Any, method: str = "dbscan", n_clusters: int = 5,
                        eps: float = 1000, min_samples: int = 5,
                        value_field: str = "") -> dict:
        data = safe_parse_geojson(geojson)
        features = data.get("features", [])
        res = SpatialAnalyzer.cluster(
            features, method=method, n_clusters=n_clusters, eps=eps, 
            min_samples=min_samples, value_field=value_field
        )
        return res.to_llm_response()

    @tool(registry, name="standard_deviational_ellipse",
           description="计算标准离差椭圆（SDE），用于分析地理要素的空间分布趋势和方向性。",
           param_descriptions={
               "geojson": "输入点要素 GeoJSON FeatureCollection 或数据引用(ref:xxx)",
           })
    def standard_deviational_ellipse(geojson: Any) -> dict:
        data = safe_parse_geojson(geojson)
        features = data.get("features", [])
        res = SpatialAnalyzer.statistics(features, spatial_stats=True)
        return res.to_llm_response()

    @tool(registry, name="moran_i",
           description="全局 Moran's I 空间自相关检验，判断空间分布模式（聚集/离散/随机）",
           param_descriptions={
               "geojson": "输入 GeoJSON FeatureCollection 或数据引用(ref:xxx)",
               "value_field": "待检验的数值字段名",
           })
    def moran_i(geojson: Any, value_field: str) -> dict:
        data = safe_parse_geojson(geojson)
        features = data.get("features", [])
        res = SpatialAnalyzer.statistics(features, field=value_field, spatial_stats=True)
        return res.to_llm_response()

    @tool(registry, name="hotspot_analysis",
           description="Getis-Ord Gi* 热点分析，识别统计显著的高值聚集区（热点）和低值聚集区（冷点）",
           param_descriptions={
               "geojson": "输入 GeoJSON FeatureCollection 或数据引用(ref:xxx)",
               "value_field": "待分析的数值字段名",
               "distance_band": "空间权重距离阈值（米），0表示自动计算（默认）",
           })
    def hotspot_analysis(geojson: Any, value_field: str, distance_band: float = 0) -> dict:
        from app.lib.geo_analysis.statistics import hotspot_narrated
        data = safe_parse_geojson(geojson)
        res = hotspot_narrated(data, value_field, distance_band)
        return res.to_llm_response()

    @tool(registry, name="kde_surface",
           description="高斯核密度估计，生成连续密度面。适用于深度密度建模和选址分析基础。注意：该工具生成的是覆盖分析范围的完整矢量格网，如果不进行阈值过滤，在大范围内会遮挡底图，单纯查看'分布热度'建议优先使用 heatmap_data。",
           param_descriptions={
               "geojson": "输入点要素 GeoJSON FeatureCollection 或数据引用(ref:xxx)",
               "bandwidth": "核函数带宽（米），0表示自动计算（Silverman法则）",
               "cell_size": "网格单元大小（米），默认500",
               "value_field": "可选：作为权重的数值字段",
               "bounds": "可选：分析范围 [xmin, ymin, xmax, ymax]（WGS84），默认数据范围+10%缓冲",
           })
    def kde_surface(geojson: Any, bandwidth: float = 0, cell_size: float = 500,
                    value_field: str = "", bounds: list = []) -> dict:
        from scipy.stats import gaussian_kde

        data = safe_parse_geojson(geojson)
        if not data:
            return {"error": "无效的 GeoJSON 输入"}

        result = to_utm_gdf(data)
        if result is None:
             return {"error": "无法解析矢量数据"}
        gdf, utm_crs = result
        
        if len(gdf) < 3:
            return {"error": "至少需要3个有效点要素"}

        coords = np.array([(g.centroid.x, g.centroid.y) for g in gdf.geometry])

        if value_field:
            weights = extract_numeric_values(gdf, value_field)
            if weights is None:
                numeric_cols = [c for c in gdf.columns if c != "geometry" and gdf[c].dtype in ("float64", "int64", "float32", "int32")]
                return {"error": f"字段 '{value_field}' 不是数值类型。可用字段: {numeric_cols}"}
            weights = np.abs(weights)
            # Weighted: repeat points by weight
            repeat_factors = np.maximum((weights / weights.min()).astype(int), 1)
            weighted_coords = np.repeat(coords, repeat_factors, axis=0)
            kde_data = weighted_coords.T
        else:
            kde_data = coords.T

        # Bandwidth — always compute in CRS units (meters)
        data_std = np.mean(np.std(kde_data, axis=1))
        if data_std == 0:
            data_std = 1.0

        if bandwidth <= 0:
            kde = gaussian_kde(kde_data, bw_method="scott")
            scott_factor = kde.factor
            bw = float(scott_factor * data_std)
        else:
            bw_factor = float(bandwidth / data_std)
            kde = gaussian_kde(kde_data, bw_method=bw_factor)
            bw = bandwidth

        # Bounds
        if bounds and len(bounds) == 4:
            bounds_gdf = gpd.GeoDataFrame(geometry=[box(bounds[0], bounds[1], bounds[2], bounds[3])],
                                          crs="EPSG:4326").to_crs(utm_crs)
            xmin, ymin, xmax, ymax = bounds_gdf.total_bounds
        else:
            xmin, ymin, xmax, ymax = gdf.total_bounds
            buffer_x = (xmax - xmin) * 0.1
            buffer_y = (ymax - ymin) * 0.1
            xmin -= buffer_x
            xmax += buffer_x
            ymin -= buffer_y
            ymax += buffer_y

        # Grid
        nx = max(int((xmax - xmin) / cell_size), 2)
        ny = max(int((ymax - ymin) / cell_size), 2)

        # Grid safety limit to prevent OOM
        MAX_GRID_CELLS = 100_000
        if nx * ny > MAX_GRID_CELLS:
            cell_size = max(cell_size, ((xmax - xmin) * (ymax - ymin)) ** 0.5 / (MAX_GRID_CELLS ** 0.5))
            nx = max(int((xmax - xmin) / cell_size), 2)
            ny = max(int((ymax - ymin) / cell_size), 2)
            logger.warning(f"KDE grid auto-adjusted to {nx}x{ny}={nx*ny} cells (cell_size={cell_size:.0f}m)")

        grid_x = np.linspace(xmin, xmax, nx)
        grid_y = np.linspace(ymin, ymax, ny)
        gx, gy = np.meshgrid(grid_x, grid_y)
        grid_coords = np.vstack([gx.ravel(), gy.ravel()])
        density = kde(grid_coords).reshape(ny, nx)

        max_d = density.max()
        threshold = max_d * 0.1
        out_features = []
        for i in range(ny):
            for j in range(nx):
                d_val = float(density[i, j])
                if d_val < threshold:
                    continue
                    
                x0, x1 = grid_x[j] - cell_size / 2, grid_x[j] + cell_size / 2
                y0, y1 = grid_y[i] - cell_size / 2, grid_y[i] + cell_size / 2
                cell_geom = box(x0, y0, x1, y1)
                cell_wgs84 = gpd.GeoSeries([cell_geom], crs=utm_crs).to_crs("EPSG:4326").iloc[0]
                out_features.append({
                    "type": "Feature",
                    "geometry": mapping(cell_wgs84),
                    "properties": {"density": round(d_val, 8)},
                })

        return {
            "type": "FeatureCollection",
            "features": out_features,
            "count": len(out_features),
            "grid_size": [nx, ny],
            "stats": {
                "min_density": round(float(density.min()), 8),
                "max_density": round(float(density.max()), 8),
                "mean_density": round(float(density.mean()), 8),
            },
            "bandwidth_m": round(bw, 1),
        }

    @tool(registry, name="kde_contours",
           description="高斯核密度估计（等值面模式）：生成精美的点密度等值线/面。相比网格模式更平滑且易于叠加分析。",
           param_descriptions={
               "geojson": "点要素集 GeoJSON 或引用(ref:xxx)",
               "levels": "等值面级数，默认 8",
               "bandwidth": "搜索半径（米），0表示自动",
           })
    def kde_contours(geojson: Any, levels: int = 8, bandwidth: float = 0) -> dict:
        try:
            import matplotlib.pyplot as plt
            from scipy.stats import gaussian_kde
        except ImportError:
            return {"error": "需要 matplotlib 和 scipy"}

        data = safe_parse_geojson(geojson)
        result = to_utm_gdf(data)
        if result is None:
             return {"error": "无法解析矢量数据"}
        gdf, utm_crs = result

        if len(gdf) < 5:
            return {"error": "至少需要5个有效点要素进行等值面分析"}

        coords = np.array([(g.centroid.x, g.centroid.y) for g in gdf.geometry])
        kde_data = coords.T
        
        kde = gaussian_kde(kde_data, bw_method="scott" if bandwidth <= 0 else bandwidth/np.std(kde_data))
        
        xmin, ymin, xmax, ymax = gdf.total_bounds
        buf_x, buf_y = (xmax-xmin)*0.2, (ymax-ymin)*0.2
        X, Y = np.mgrid[xmin-buf_x:xmax+buf_x:100j, ymin-buf_y:ymax+buf_y:100j]
        positions = np.vstack([X.ravel(), Y.ravel()])
        Z = np.reshape(kde(positions).T, X.shape)

        fig, ax = plt.subplots()
        cs = ax.contourf(X, Y, Z, levels=levels)
        plt.close(fig)

        out_features = []
        for i, collection in enumerate(cs.collections):
            val = cs.levels[i]
            for path in collection.get_paths():
                for poly_coords in path.to_polygons():
                    if len(poly_coords) < 3: continue
                    from shapely.geometry import Polygon
                    poly = Polygon(poly_coords)
                    if not poly.is_valid: poly = poly.buffer(0)
                    
                    poly_wgs84 = gpd.GeoSeries([poly], crs=utm_crs).to_crs("EPSG:4326").iloc[0]
                    out_features.append({
                        "type": "Feature",
                        "geometry": mapping(poly_wgs84),
                        "properties": {"level": i, "density_value": float(val)}
                    })

        return {
            "type": "FeatureCollection",
            "features": out_features,
            "count": len(out_features),
            "levels_count": len(cs.levels)
        }

    @tool(registry, name="voronoi_polygons",
           description="生成 Voronoi (泰森多边形/Thiessen多边形)，将空间按最近邻原则划分为势力范围",
           param_descriptions={
               "geojson": "输入点要素 GeoJSON FeatureCollection 或数据引用(ref:xxx)",
               "clip_bounds": "可选：裁剪范围 [xmin, ymin, xmax, ymax]（WGS84），默认使用数据范围+10%缓冲",
           })
    def voronoi_polygons(geojson: Any, clip_bounds: list = []) -> dict:
        from scipy.spatial import Voronoi

        data = safe_parse_geojson(geojson)
        result = to_utm_gdf(data)
        if result is None:
            return {"error": "无法解析矢量数据"}
        gdf, utm_crs = result

        if len(gdf) < 3:
            return {"error": "至少需要3个点要素"}

        coords = np.array([(g.centroid.x, g.centroid.y) for g in gdf.geometry])

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

        try:
            vor = Voronoi(all_points)
        except (ValueError, TypeError, RuntimeError) as e:
            return {"error": f"Voronoi 计算失败: {e}"}

        out_features = []
        clip_box = box(xmin - margin, ymin - margin, xmax + margin, ymax + margin)

        for i in range(len(coords)):
            region_idx = vor.point_region[i]
            region = vor.regions[region_idx]
            if -1 in region or len(region) == 0:
                continue
            polygon_coords = [vor.vertices[v] for v in region]
            try:
                from shapely.geometry import Polygon
                poly = Polygon(polygon_coords)
                if not poly.is_valid:
                    poly = poly.buffer(0)
                poly = poly.intersection(clip_box)
                if poly.is_empty:
                    continue
                poly_wgs84 = gpd.GeoSeries([poly], crs=utm_crs).to_crs("EPSG:4326").iloc[0]
                props = {k: v for k, v in gdf.iloc[i].items() if k != "geometry"}
                props["area_km2"] = round(float(poly.area) / 1e6, 4)
                out_features.append({
                    "type": "Feature",
                    "geometry": mapping(poly_wgs84),
                    "properties": props,
                })
            except (ValueError, TypeError):
                continue

        return {
            "type": "FeatureCollection",
            "features": out_features,
            "count": len(out_features),
        }

    @tool(registry, name="convex_hull",
           description="计算要素集合的凸包（最小凸多边形），用于确定点群的空间范围",
           param_descriptions={
               "geojson": "输入 GeoJSON FeatureCollection 或数据引用(ref:xxx)",
               "group_by": "可选：按属性字段分组，每组生成一个凸包",
           })
    def convex_hull(geojson: Any, group_by: str = "") -> dict:
        data = safe_parse_geojson(geojson)
        result = to_utm_gdf(data)
        if result is None:
            return {"error": "无法解析矢量数据"}
        gdf, utm_crs = result

        if len(gdf) < 3:
            return {"error": "至少需要3个要素"}

        out_features = []

        if group_by and group_by in gdf.columns:
            for name, group in gdf.groupby(group_by):
                try:
                    hull = group.geometry.unary_union.convex_hull
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
            hull = gdf.geometry.unary_union.convex_hull
            hull_wgs84 = gpd.GeoSeries([hull], crs=utm_crs).to_crs("EPSG:4326").iloc[0]
            out_features.append({
                "type": "Feature",
                "geometry": mapping(hull_wgs84),
                "properties": {
                    "feature_count": len(gdf),
                    "area_km2": round(float(hull.area) / 1e6, 4),
                },
            })

        return {
            "type": "FeatureCollection",
            "features": out_features,
            "count": len(out_features),
        }

    @tool(registry, name="multi_ring_buffer",
           description="多环缓冲区分析：围绕要素生成多个同心缓冲带（环形区域）",
           param_descriptions={
               "geojson": "输入 GeoJSON FeatureCollection 或数据引用(ref:xxx)",
               "distances": "缓冲距离列表（米），例如 [500, 1000, 1500]",
               "merge_rings": "是否合并为环形区域（默认true），false则生成独立圆",
           })
    def multi_ring_buffer(geojson: Any, distances: list = [500, 1000, 1500],
                           merge_rings: bool = True) -> dict:
        data = safe_parse_geojson(geojson)
        result = to_utm_gdf(data)
        if result is None:
            return {"error": "无法解析矢量数据"}
        gdf, utm_crs = result

        if not distances:
            return {"error": "需要至少一个缓冲距离"}

        distances = sorted([float(d) for d in distances])
        union_geom = gdf.geometry.unary_union
        out_features = []

        prev_buffer = None
        for dist in distances:
            buf = union_geom.buffer(dist, resolution=32)

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

        return {
            "type": "FeatureCollection",
            "features": out_features,
            "count": len(out_features),
            "method": "多环缓冲区" + ("（环形区域）" if merge_rings else ""),
        }
