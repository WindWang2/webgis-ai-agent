"""空间分析 Celery 任务定义"""
import logging
import math
from typing import List, Dict, Any, Optional
from app.services.task_queue import celery_app
from app.services.spatial_analyzer import SpatialAnalyzer

logger = logging.getLogger(__name__)

@celery_app.task(name="app.services.spatial_tasks.run_buffer_analysis", bind=True)
def run_buffer_analysis(self, features: List[Dict], distance: float, unit: str = "m", dissolve: bool = False):
    """执行缓冲区分析任务"""
    logger.info(f"Starting buffer analysis: distance={distance} {unit}")
    
    def update_progress(current, message):
        self.update_state(state='PROGRESS', meta={'progress': current, 'message': message})

    result = SpatialAnalyzer.buffer(
        features=features,
        distance=distance,
        unit=unit,
        dissolve=dissolve,
        callback=update_progress
    )
    
    if result.success:
        return {
            "success": True, 
            "data": result.data, 
            "stats": result.stats,
            "status_desc": f"已完成缓冲区分析：距离 {distance} {unit}。"
        }
    else:
        return {"success": False, "error": result.error_message}

@celery_app.task(name="app.services.spatial_tasks.run_spatial_stats", bind=True)
def run_spatial_stats(self, features: List[Dict]):
    """执行空间统计任务"""
    logger.info(f"Starting spatial stats: count={len(features)}")
    # 注意：这里的逻辑参考 app/tools/spatial.py 中的实现
    # 因为 SpatialAnalyzer.statistics 实现比较简单，有的统计逻辑在 tool 层
    # 这里我们统一调用封装好的逻辑
    
    from shapely.geometry import shape
    import geopandas as gpd
    
    def update_progress(current, message):
        self.update_state(state='PROGRESS', meta={'progress': current, 'message': message})

    try:
        update_progress(20, "解析几何对象...")
        geometries = [shape(f["geometry"]) for f in features if f.get("geometry")]
        if not geometries:
            return {"success": False, "error": "No valid geometries"}

        update_progress(50, "计算投影统计...")
        gdf = gpd.GeoSeries(geometries, crs="EPSG:4326")
        gdf_proj = gdf.to_crs("ESRI:54009")
        
        stats = {
            "feature_count": len(geometries),
            "total_area_sqkm": round(float(gdf_proj.area.sum()) / 1e6, 4),
            "total_length_km": round(float(gdf_proj.length.sum()) / 1000, 4),
            "centroid": {
                "lat": round(float(gdf.centroid.y.mean()), 6),
                "lon": round(float(gdf.centroid.x.mean()), 6),
            },
            "bounds": {
                "min_lon": round(float(gdf.bounds.minx.min()), 6),
                "min_lat": round(float(gdf.bounds.miny.min()), 6),
                "max_lon": round(float(gdf.bounds.maxx.max()), 6),
                "max_lat": round(float(gdf.bounds.maxy.max()), 6),
            },
        }
        update_progress(100, "完成统计")
        return {
            "success": True, 
            "stats": stats, 
            "status_desc": f"已完成空间统计分析，共处理 {stats['feature_count']} 个要素。"
        }
    except Exception as e:
        logger.error(f"Spatial stats task failed: {e}")
        return {"success": False, "error": str(e)}

@celery_app.task(name="app.services.spatial_tasks.run_nearest_neighbor", bind=True)
def run_nearest_neighbor(self, features: List[Dict]):
    """执行最近邻分析任务"""
    import numpy as np
    from scipy.spatial import distance_matrix
    
    try:
        points = []
        for f in features:
            geom = f.get("geometry") or {}
            if geom.get("type") == "Point" and geom.get("coordinates"):
                coords = geom["coordinates"]
                points.append((coords[0], coords[1]))

        if len(points) < 2:
            return {"success": False, "error": "Need at least 2 points"}

        coords_arr = np.array(points)
        dist = distance_matrix(coords_arr, coords_arr)
        np.fill_diagonal(dist, np.inf)
        nn_distances = dist.min(axis=1)

        return {
            "success": True,
            "data": {
                "point_count": len(points),
                "mean_nearest_distance": round(float(nn_distances.mean()), 4),
                "std_nearest_distance": round(float(nn_distances.std()), 4),
                "min_distance": round(float(nn_distances.min()), 4),
                "max_distance": round(float(nn_distances.max()), 4),
            }
        }
    except Exception as e:
        return {"success": False, "error": str(e)}

@celery_app.task(name="app.services.spatial_tasks.run_heatmap_generation", bind=True)
def run_heatmap_generation(self, features: List[Dict], cell_size: int = 500, radius: int = 1000, render_type: str = "raster"):
    """执行热力图任务 - 支持栅格(raster)和矢量格网(grid)模式"""
    import base64
    import io
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np
    from matplotlib.colors import LinearSegmentedColormap
    from scipy.ndimage import gaussian_filter
    from shapely.geometry import box, mapping

    fig = None
    try:
        points = []
        for f in features or []:
            if not isinstance(f, dict): continue
            geom = f.get("geometry") or {}
            if geom.get("type") == "Point":
                coords = geom.get("coordinates")
                if coords and len(coords) >= 2:
                    try:
                        lon, lat = float(coords[0]), float(coords[1])
                        if not math.isnan(lon) and not math.isnan(lat):
                            points.append((lon, lat))
                    except (ValueError, TypeError):
                        continue

        if not points:
            return {"success": False, "error": "No valid point features found"}

        xs = [p[0] for p in points]
        ys = [p[1] for p in points]

        # 1度约等于 111000m
        cell_deg = cell_size / 111000
        margin = cell_deg * 2
        x_min, x_max = min(xs) - margin, max(xs) + margin
        y_min, y_max = min(ys) - margin, max(ys) + margin
        
        if x_min == x_max: x_max += cell_deg
        if y_min == y_max: y_max += cell_deg

        x_bins = np.arange(x_min, x_max + cell_deg, cell_deg)
        y_bins = np.arange(y_min, y_max + cell_deg, cell_deg)
        
        if len(x_bins) > 5000 or len(y_bins) > 5000:
             return {"success": False, "error": "Resolution too high for the data extent"}

        H, xedges, yedges = np.histogram2d(xs, ys, bins=[x_bins, y_bins])

        if render_type == "grid":
            # 矢量格网模式
            grid_features = []
            max_val = float(H.max()) if H.max() > 0 else 1.0

            # OOM protection: cap total grid cells
            MAX_GRID_FEATURES = 500_000
            total_cells = H[H > 0].size
            if total_cells > MAX_GRID_FEATURES:
                return {"success": False, "error": f"Grid too dense ({total_cells} cells). Increase cell_size or reduce data extent. Max allowed: {MAX_GRID_FEATURES}"}
            
            for i in range(len(xedges) - 1):
                for j in range(len(yedges) - 1):
                    count = H[i, j]
                    if count > 0:
                        # 创建正方形
                        rect = box(xedges[i], yedges[j], xedges[i+1], yedges[j+1])
                        grid_features.append({
                            "type": "Feature",
                            "geometry": mapping(rect),
                            "properties": {
                                "count": int(count),
                                "weight": round(float(count / max_val), 4)
                            }
                        })
            
            return {
                "success": True,
                "data": {
                    "type": "FeatureCollection",
                    "features": grid_features,
                    "metadata": {
                        "render_type": "grid",
                        "field": "weight",
                        "cell_size": cell_size,
                        "point_count": len(points)
                    }
                },
                "status_desc": f"已生成矢量格网热力图，共包含 {len(grid_features)} 个有效格网单元。"
            }

        else:
            # 栅格模式 (原有逻辑增强)
            sigma = max(1.0, radius / cell_size)
            H_smooth = gaussian_filter(H.T, sigma=sigma)

            # 优化配色方案：标准热力图色带 (Cyan -> Green -> Yellow -> Red)
            cmap = LinearSegmentedColormap.from_list("classic_heat", [
                (0.00, (0.0, 0.0, 0.0, 0.0)),      # 完全透明
                (0.15, (0.0, 1.0, 1.0, 0.4)),      # 蓝青色 (低密度)
                (0.40, (0.0, 1.0, 0.0, 0.6)),      # 亮绿色
                (0.70, (1.0, 1.0, 0.0, 0.8)),      # 鲜黄色
                (0.90, (1.0, 0.5, 0.0, 0.9)),      # 橙色
                (1.00, (1.0, 0.0, 0.0, 1.0)),      # 纯红色 (最高密度)
            ], N=256)

            fig, ax = plt.subplots(figsize=(10, 10), dpi=100)
            # 使用 98 分位数抑制离群值，增强对比度
            v_max = np.percentile(H_smooth, 98) if H_smooth.max() > 0 else 1.0
            if v_max <= 0: v_max = H_smooth.max() or 1.0
            
            ax.imshow(
                H_smooth,
                cmap=cmap,
                origin="lower",
                aspect="auto",
                vmin=0,
                vmax=v_max,
                interpolation="bilinear",
            )
            ax.axis("off")
            plt.subplots_adjust(left=0, right=1, top=1, bottom=0)

            buf = io.BytesIO()
            fig.savefig(buf, format="png", bbox_inches="tight", pad_inches=0, transparent=True)
            buf.seek(0)
            img_b64 = "data:image/png;base64," + base64.b64encode(buf.read()).decode()

            return {
                "success": True,
                "data": {
                    "type": "heatmap_raster",
                    "image": img_b64,
                    "bbox": [float(xedges[0]), float(yedges[0]), float(xedges[-1]), float(yedges[-1])],
                    "total_points": len(points),
                    "metadata": {
                        "render_type": "raster",
                        "point_count": len(points)
                    }
                },
                "status_desc": f"已生成增强对照度栅格热力图，覆盖范围包含 {len(points)} 个要素点。"
            }
    except Exception as e:
        logger.error(f"Heatmap conversion failed: {e}", exc_info=True)
        return {"success": False, "error": str(e)}
    finally:
        if fig:
            plt.close(fig)

@celery_app.task(name="app.services.spatial_tasks.run_overlay_analysis", bind=True)
def run_overlay_analysis(self, features_a: List[Dict], features_b: List[Dict], how: str = "intersection"):
    """执行叠加分析任务"""
    logger.info(f"Starting overlay analysis: how={how}")
    def update_progress(current, message):
        self.update_state(state='PROGRESS', meta={'progress': current, 'message': message})
    
    result = SpatialAnalyzer.overlay(features_a, features_b, how=how, callback=update_progress)
    if result.success:
        return {
            "success": True, 
            "data": result.data, 
            "stats": result.stats,
            "status_desc": f"已完成 {how} 叠加分析，生成了 {len(result.data.get('features', []))} 个新要素。"
        }
    return {"success": False, "error": result.error_message}

@celery_app.task(name="app.services.spatial_tasks.run_attribute_filter", bind=True)
def run_attribute_filter(self, features: List[Dict], query: str):
    """执行属性过滤任务"""
    logger.info(f"Starting attribute filter: query={query}")
    def update_progress(current, message):
        self.update_state(state='PROGRESS', meta={'progress': current, 'message': message})
    
    result = SpatialAnalyzer.attribute_filter(features, query=query, callback=update_progress)
    if result.success:
        return {"success": True, "data": result.data, "stats": result.stats}
    return {"success": False, "error": result.error_message}

@celery_app.task(name="app.services.spatial_tasks.run_spatial_join", bind=True)
def run_spatial_join(self, left: List[Dict], right: List[Dict], join_type: str = "inner", predicate: str = "intersects"):
    """执行空间连接任务"""
    logger.info(f"Starting spatial join: {join_type} {predicate}")
    def update_progress(current, message):
        self.update_state(state='PROGRESS', meta={'progress': current, 'message': message})
    
    result = SpatialAnalyzer.spatial_join(left, right, join_type=join_type, predicate=predicate, callback=update_progress)
    if result.success:
        return {"success": True, "data": result.data, "stats": result.stats}
    return {"success": False, "error": result.error_message}
@celery_app.task(name="app.services.spatial_tasks.run_path_analysis", bind=True)
def run_path_analysis(self, network_features: List[Dict], start_point: List[float], end_point: List[float]):
    """执行路径分析任务"""
    logger.info(f"Starting path analysis from {start_point} to {end_point}")
    def update_progress(current, message):
        self.update_state(state='PROGRESS', meta={'progress': current, 'message': message})
    
    result = SpatialAnalyzer.path_analysis(
        network_features=network_features,
        start_point=start_point,
        end_point=end_point,
        callback=update_progress
    )
    if result.success:
        return {
            "success": True, 
            "data": result.data, 
            "stats": result.stats,
            "status_desc": "路径分析已完成，成功找到最优路径。"
        }
    return {"success": False, "error": result.error_message}

@celery_app.task(name="app.services.spatial_tasks.run_zonal_stats", bind=True)
def run_zonal_stats(self, zones: List[Dict], raster_path: str):
    """执行区域统计任务"""
    logger.info(f"Starting zonal stats on raster: {raster_path}")
    def update_progress(current, message):
        self.update_state(state='PROGRESS', meta={'progress': current, 'message': message})
    
    result = SpatialAnalyzer.zonal_statistics(
        zones=zones,
        raster_path=raster_path,
        callback=update_progress
    )
    if result.success:
        return {"success": True, "data": result.data}
    return {"success": False, "error": result.error_message}
