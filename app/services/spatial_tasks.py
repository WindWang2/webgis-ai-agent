"""空间分析 Celery 任务定义"""
import logging
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
        return {"success": True, "data": result.data, "stats": result.stats}
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
        return {"success": True, "stats": stats}
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
def run_heatmap_generation(self, features: List[Dict], cell_size: int = 500, radius: int = 1000):
    """执行热加图任务"""
    import base64
    import io
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np
    from matplotlib.colors import LinearSegmentedColormap
    from scipy.ndimage import gaussian_filter

    try:
        points = []
        for f in features:
            geom = f.get("geometry") or {}
            if geom.get("type") == "Point" and geom.get("coordinates"):
                coords = geom["coordinates"]
                points.append((coords[0], coords[1]))

        if not points:
            return {"success": False, "error": "No point features"}

        xs = [p[0] for p in points]
        ys = [p[1] for p in points]

        cell_deg = cell_size / 111000
        margin = cell_deg * 3
        x_min, x_max = min(xs) - margin, max(xs) + margin
        y_min, y_max = min(ys) - margin, max(ys) + margin

        x_bins = np.arange(x_min, x_max + cell_deg, cell_deg)
        y_bins = np.arange(y_min, y_max + cell_deg, cell_deg)
        H, xedges, yedges = np.histogram2d(xs, ys, bins=[x_bins, y_bins])

        sigma = max(1.0, radius / cell_size)
        H_smooth = gaussian_filter(H.T, sigma=sigma)

        cmap = LinearSegmentedColormap.from_list("heat", [
            (0.00, (0.00, 0.00, 0.50, 0.00)),
            (0.10, (0.00, 0.10, 0.90, 0.65)),
            (0.30, (0.00, 0.75, 1.00, 0.80)),
            (0.55, (0.10, 1.00, 0.20, 0.88)),
            (0.75, (1.00, 0.90, 0.00, 0.93)),
            (0.88, (1.00, 0.40, 0.00, 0.97)),
            (1.00, (0.90, 0.00, 0.00, 1.00)),
        ], N=256)

        dpi = 150
        w = H_smooth.shape[1] / dpi * 2
        h = H_smooth.shape[0] / dpi * 2
        fig, ax = plt.subplots(figsize=(max(w, 2), max(h, 2)), dpi=dpi)
        ax.imshow(
            H_smooth,
            cmap=cmap,
            origin="lower",
            aspect="auto",
            vmin=0,
            vmax=H_smooth.max(),
            interpolation="bilinear",
        )
        ax.axis("off")
        plt.subplots_adjust(left=0, right=1, top=1, bottom=0)

        buf = io.BytesIO()
        fig.savefig(buf, format="png", bbox_inches="tight", pad_inches=0, transparent=True)
        plt.close(fig)
        buf.seek(0)
        img_b64 = "data:image/png;base64," + base64.b64encode(buf.read()).decode()

        return {
            "success": True,
            "data": {
                "type": "heatmap_raster",
                "image": img_b64,
                "bbox": [float(xedges[0]), float(yedges[0]), float(xedges[-1]), float(yedges[-1])],
                "total_points": len(points),
            }
        }
    except Exception as e:
        return {"success": False, "error": str(e)}
