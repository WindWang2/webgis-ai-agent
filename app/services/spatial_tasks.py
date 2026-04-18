"""空间分析 Celery 任务定义"""
import logging
import math
import os
import io
import base64
from typing import List, Dict, Any, Optional
from app.services.task_queue import celery_app
from app.services.spatial_analyzer import SpatialAnalyzer
from app.services.nature_resource_analyzer import NatureResourceAnalyzer
from app.core.config import settings
from app.core.database import SessionLocal
from app.models.upload import UploadRecord

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
def run_heatmap_generation(self, features: List[Dict], cell_size: int = 500, radius: int = 1000, render_type: str = "raster", palette: str = "classic"):
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
                        "point_count": len(points),
                        "palette": palette
                    }
                },
                "status_desc": f"已生成矢量格网热力图，共包含 {len(grid_features)} 个有效格网单元。"
            }

        else:
            # 栅格模式
            sigma = max(1.0, radius / cell_size)
            H_smooth = gaussian_filter(H.T, sigma=sigma)

            # 调色板定义
            PALETTES = {
                "classic": [
                    (0.00, (0.0, 0.0, 0.0, 0.0)),      # 完全透明
                    (0.15, (0.0, 1.0, 1.0, 0.4)),      # 蓝青色
                    (0.40, (0.0, 1.0, 0.0, 0.6)),      # 亮绿色
                    (0.70, (1.0, 1.0, 0.0, 0.8)),      # 鲜黄色
                    (0.90, (1.0, 0.5, 0.0, 0.9)),      # 橙色
                    (1.00, (1.0, 0.0, 0.0, 1.0)),      # 纯红色
                ],
                "magma": [
                    (0.00, (0.0, 0.0, 0.0, 0.0)),
                    (0.20, (0.2, 0.04, 0.48, 0.5)),    # 深紫
                    (0.50, (0.7, 0.13, 0.45, 0.7)),    # 品红
                    (0.80, (0.99, 0.55, 0.35, 0.85)),  # 橙亮
                    (1.00, (0.98, 0.94, 0.60, 1.0)),   # 浅黄
                ],
                "viridis": [
                    (0.00, (0.0, 0.0, 0.0, 0.0)),
                    (0.25, (0.27, 0.0, 0.33, 0.5)),    # 深蓝
                    (0.50, (0.13, 0.57, 0.55, 0.7)),   # 墨绿
                    (0.75, (0.37, 0.79, 0.36, 0.85)),  # 草绿
                    (1.00, (0.99, 0.9, 0.14, 1.0)),    # 亮黄
                ],
                "thermal": [
                    (0.00, (0.0, 0.0, 0.0, 0.0)),
                    (0.33, (0.0, 0.0, 1.0, 0.5)),      # 蓝
                    (0.66, (1.0, 1.0, 0.0, 0.8)),      # 黄
                    (1.00, (1.0, 0.0, 0.0, 1.0)),      # 红
                ]
            }

            colors = PALETTES.get(palette, PALETTES["classic"])
            cmap = LinearSegmentedColormap.from_list("dynamic_heat", colors, N=256)

            fig, ax = plt.subplots(figsize=(10, 10), dpi=100)
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
                        "point_count": len(points),
                        "palette": palette
                    }
                },
                "status_desc": f"已生成增强对照度栅格热力图 (配色: {palette})，覆盖范围包含 {len(points)} 个要素点。"
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
@celery_app.task(name="app.services.spatial_tasks.run_ndvi_analysis", bind=True)
def run_ndvi_analysis(self, raster_path: str, nir_band: Optional[int] = None, red_band: Optional[int] = None, session_id: Optional[str] = None):
    """
    执行 NDVI 植被指数分析并持久化结果
    """
    import rasterio
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    
    logger.info(f"Starting NDVI analysis for {raster_path}")
    self.update_state(state='PROGRESS', meta={'progress': 10, 'message': '正在读取影像数据...'})
    
    output_dir = os.path.join(settings.DATA_DIR, "analysis_results")
    
    # 1. 执行计算
    result = NatureResourceAnalyzer.calculate_ndvi(
        tif_path=raster_path,
        red_band=red_band,
        nir_band=nir_band,
        output_dir=output_dir
    )
    
    if not result.get("success"):
        return result
    
    self.update_state(state='PROGRESS', meta={'progress': 60, 'message': '计算完成，正在生成渲染预览...'})
    
    # 2. 生成 PNG 预览 (用于前端 Immediate Map Render)
    # 这一步将 NDVI 矩阵转换为彩色 PNG，采用 RdYlGn (红-黄-绿) 色带
    preview_base64 = ""
    try:
        with rasterio.open(result["result_path"]) as src:
            ndvi_data = src.read(1)
            # 极简渲染方案：使用 matplotlib 快速出图
            fig, ax = plt.subplots(figsize=(10, 10))
            ax.set_axis_off()
            # NDVI 范围限定 [-1, 1], RdYlGn 是植被分析标配
            im = ax.imshow(ndvi_data, cmap='RdYlGn', vmin=-1.0, vmax=1.0)
            
            buf = io.BytesIO()
            plt.savefig(buf, format='png', transparent=True, bbox_inches='tight', pad_inches=0)
            plt.close(fig)
            buf.seek(0)
            preview_base64 = "data:image/png;base64," + base64.b64encode(buf.read()).decode('utf-8')
    except Exception as e:
        logger.error(f"Failed to generate NDVI preview: {e}")

    # 3. 持久化到数据库 (让 Agent 在资产管理器中能“感知”到它)
    db = SessionLocal()
    try:
        file_size = os.path.getsize(result["result_path"])
        record = UploadRecord(
            filename=f"analysis_results/{result['filename']}",
            original_name=result["filename"],
            file_type="raster",
            format="geotiff",
            crs=result.get("crs", "EPSG:4326"),
            geometry_type="raster_analysis",
            bbox=result.get("bbox"),
            file_size=file_size,
            session_id=session_id
        )
        db.add(record)
        db.commit()
        db.refresh(record)
        asset_id = record.id
    except Exception as e:
        db.rollback()
        logger.error(f"Failed to log NDVI result to DB: {e}")
        asset_id = None
    finally:
        db.close()

    return {
        "success": True,
        "type": "ndvi_result",
        "asset_id": asset_id,
        "filename": result["filename"],
        "bbox": result["bbox"],
        "image": preview_base64, # 图层立即回显
        "stats": result["stats"],
        "detected_bands": result["detected_bands"],
        "message": f"植被指数 (NDVI) 分析执行成功。结果已存入资产库：{result['filename']}"
    }
