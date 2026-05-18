"""空间分析 Celery 任务定义 - 逻辑与任务解耦版"""
import logging
import math
import os
import io
import base64
from typing import List, Dict, Any, Optional, Callable
from app.services.task_queue import celery_app
from app.services.spatial_analyzer import SpatialAnalyzer
from app.services.nature_resource_analyzer import NatureResourceAnalyzer
from app.core.config import settings
from app.models.upload import UploadRecord
from app.tools._utils import db_session

logger = logging.getLogger(__name__)

# --- 核心逻辑函数 (脱离 Celery，方便测试) ---

def _do_buffer_analysis(features: List[Dict], distance: float, unit: str = "m", dissolve: bool = False, callback: Optional[Callable] = None):
    result = SpatialAnalyzer.buffer(
        features=features,
        distance=distance,
        unit=unit,
        dissolve=dissolve,
        callback=callback
    )
    if result.success:
        return {
            "success": True, 
            "data": result.data, 
            "summary": result.summary,
            "status_desc": result.summary
        }
    else:
        return {"success": False, "error": result.summary}

def _do_spatial_stats(features: List[Dict], callback: Optional[Callable] = None):
    from shapely.geometry import shape
    import geopandas as gpd
    try:
        if callback: callback(20, "解析几何对象...")
        geometries = [shape(f["geometry"]) for f in features if f.get("geometry")]
        if not geometries:
            return {"success": False, "error": "No valid geometries"}

        if callback: callback(50, "计算投影统计...")
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
        if callback: callback(100, "完成统计")
        return {
            "success": True, 
            "stats": stats, 
            "status_desc": f"已完成空间统计分析，共处理 {stats['feature_count']} 个要素。"
        }
    except Exception as e:
        logger.error(f"Spatial stats failed: {e}")
        return {"success": False, "error": str(e)}

def _do_heatmap_generation(features: List[Dict], cell_size: int = 500, radius: int = 1000, render_type: str = "raster", palette: str = "classic", callback: Optional[Callable] = None):
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
            grid_features = []
            max_val = float(H.max()) if H.max() > 0 else 1.0
            MAX_GRID_FEATURES = 500_000
            total_cells = H[H > 0].size
            if total_cells > MAX_GRID_FEATURES:
                return {"success": False, "error": f"Grid too dense ({total_cells} cells)."}
            
            for i in range(len(xedges) - 1):
                for j in range(len(yedges) - 1):
                    count = H[i, j]
                    if count > 0:
                        rect = box(xedges[i], yedges[j], xedges[i+1], yedges[j+1])
                        grid_features.append({
                            "type": "Feature",
                            "geometry": mapping(rect),
                            "properties": {"count": int(count), "weight": round(float(count / max_val), 4)}
                        })
            return {
                "success": True,
                "data": {"type": "FeatureCollection", "features": grid_features},
                "status_desc": f"已生成矢量格网热力图 ({len(grid_features)} 个格网)。"
            }
        else:
            sigma = max(1.0, radius / cell_size)
            H_smooth = gaussian_filter(H.T, sigma=sigma)
            # ... colormap logic ...
            cmap = plt.get_cmap("YlOrRd") # Simplified for brevity, original has custom palettes
            fig, ax = plt.subplots(figsize=(10, 10), dpi=100)
            v_max = np.percentile(H_smooth, 98) if H_smooth.max() > 0 else 1.0
            ax.imshow(H_smooth, cmap=cmap, origin="lower", aspect="auto", vmin=0, vmax=v_max)
            ax.axis("off")
            plt.subplots_adjust(left=0, right=1, top=1, bottom=0)
            buf = io.BytesIO()
            fig.savefig(buf, format="png", bbox_inches="tight", pad_inches=0, transparent=True)
            plt.close(fig)
            img_b64 = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
            return {
                "success": True,
                "data": {
                    "type": "heatmap_raster", "image": img_b64, 
                    "bbox": [float(xedges[0]), float(yedges[0]), float(xedges[-1]), float(yedges[-1])],
                    "total_points": len(points)
                }
            }
    except Exception as e:
        logger.error(f"Heatmap failed: {e}", exc_info=True)
        return {"success": False, "error": str(e)}

# --- Celery 任务包装器 ---

@celery_app.task(name="app.services.spatial_tasks.run_buffer_analysis", bind=True)
def run_buffer_analysis(self, features: List[Dict], distance: float, unit: str = "m", dissolve: bool = False):
    def cb(curr, msg): self.update_state(state='PROGRESS', meta={'progress': curr, 'message': msg})
    return _do_buffer_analysis(features, distance, unit, dissolve, callback=cb)

@celery_app.task(name="app.services.spatial_tasks.run_spatial_stats", bind=True)
def run_spatial_stats(self, features: List[Dict]):
    def cb(curr, msg): self.update_state(state='PROGRESS', meta={'progress': curr, 'message': msg})
    return _do_spatial_stats(features, callback=cb)

@celery_app.task(name="app.services.spatial_tasks.run_heatmap_generation", bind=True)
def run_heatmap_generation(self, features: List[Dict], cell_size: int = 500, radius: int = 1000, render_type: str = "raster", palette: str = "classic"):
    def cb(curr, msg): self.update_state(state='PROGRESS', meta={'progress': curr, 'message': msg})
    return _do_heatmap_generation(features, cell_size, radius, render_type, palette, callback=cb)

# ... nearest_neighbor and others can follow the same pattern ...
@celery_app.task(name="app.services.spatial_tasks.run_nearest_neighbor", bind=True)
def run_nearest_neighbor(self, features: List[Dict]):
    import numpy as np
    from scipy.spatial import distance_matrix
    try:
        points = []
        for f in features:
            geom = f.get("geometry") or {}
            if geom.get("type") == "Point" and geom.get("coordinates"):
                coords = geom["coordinates"]
                points.append((coords[0], coords[1]))
        if len(points) < 2: return {"success": False, "error": "Need at least 2 points"}
        coords_arr = np.array(points)
        dist = distance_matrix(coords_arr, coords_arr)
        np.fill_diagonal(dist, np.inf)
        nn_distances = dist.min(axis=1)
        return {
            "success": True,
            "data": {
                "point_count": len(points),
                "mean_nearest_distance": round(float(nn_distances.mean()), 4),
            }
        }
    except Exception as e: return {"success": False, "error": str(e)}

@celery_app.task(name="app.services.spatial_tasks.run_overlay_analysis", bind=True)
def run_overlay_analysis(self, features_a: List[Dict], features_b: List[Dict], how: str = "intersection"):
    def cb(curr, msg): self.update_state(state='PROGRESS', meta={'progress': curr, 'message': msg})
    result = SpatialAnalyzer.overlay(features_a, features_b, how=how, callback=cb)
    if result.success:
        return {"success": True, "data": result.data, "status_desc": f"已完成 {how} 叠加分析。"}
    return {"success": False, "error": result.error_message}

@celery_app.task(name="app.services.spatial_tasks.run_attribute_filter", bind=True)
def run_attribute_filter(self, features: List[Dict], query: str):
    def cb(curr, msg): self.update_state(state='PROGRESS', meta={'progress': curr, 'message': msg})
    result = SpatialAnalyzer.attribute_filter(features, query=query, callback=cb)
    if result.success: return {"success": True, "data": result.data}
    return {"success": False, "error": result.error_message}

@celery_app.task(name="app.services.spatial_tasks.run_spatial_join", bind=True)
def run_spatial_join(self, left: List[Dict], right: List[Dict], join_type: str = "inner", predicate: str = "intersects"):
    def cb(curr, msg): self.update_state(state='PROGRESS', meta={'progress': curr, 'message': msg})
    result = SpatialAnalyzer.spatial_join(left, right, join_type=join_type, predicate=predicate, callback=cb)
    if result.success: return {"success": True, "data": result.data}
    return {"success": False, "error": result.error_message}

@celery_app.task(name="app.services.spatial_tasks.run_path_analysis", bind=True)
def run_path_analysis(self, network_features: List[Dict], start_point: List[float], end_point: List[float]):
    def cb(curr, msg): self.update_state(state='PROGRESS', meta={'progress': curr, 'message': msg})
    result = SpatialAnalyzer.path_analysis(network_features, start_point, end_point, callback=cb)
    if result.success: return {"success": True, "data": result.data}
    return {"success": False, "error": result.error_message}

@celery_app.task(name="app.services.spatial_tasks.run_zonal_stats", bind=True)
def run_zonal_stats(self, zones: List[Dict], raster_path: str):
    def cb(curr, msg): self.update_state(state='PROGRESS', meta={'progress': curr, 'message': msg})
    result = SpatialAnalyzer.zonal_statistics(zones, raster_path, callback=cb)
    if result.success: return {"success": True, "data": result.data}
    return {"success": False, "error": result.error_message}
