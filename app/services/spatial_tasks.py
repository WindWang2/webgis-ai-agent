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
from app.tools._utils import db_session
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

    # 3. 持久化到数据库 (让 Agent 在资产管理器中能”感知”到它)
    asset_id = None
    try:
        with db_session() as db:
            file_size = os.path.getsize(result[“result_path”])
            record = UploadRecord(
                filename=f”analysis_results/{result['filename']}”,
                original_name=result[“filename”],
                file_type=”raster”,
                format=”geotiff”,
                crs=result.get(“crs”, “EPSG:4326”),
                geometry_type=”raster_analysis”,
                bbox=result.get(“bbox”),
                file_size=file_size,
                session_id=session_id
            )
            db.add(record)
            db.refresh(record)
            asset_id = record.id
    except Exception as e:
        logger.error(f”Failed to log NDVI result to DB: {e}”)

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


@celery_app.task(name="app.services.spatial_tasks.run_change_detection", bind=True)
def run_change_detection(
    self,
    bbox: list,
    t1_from: str,
    t1_to: str,
    t2_from: str,
    t2_to: str,
    index_type: str = "ndvi",
    change_threshold: float = 0.1,
    session_id: Optional[str] = None,
):
    """
    执行双时相植被指数变化检测分析
    """
    import uuid
    import time
    import pystac_client
    import rasterio
    import numpy as np
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from rasterio.enums import Resampling
    from rasterio.transform import from_bounds
    from app.tools._utils import asset_href

    def read_band(item, stac_key: str, out_shape: tuple) -> Optional[np.ndarray]:
        url = asset_href(item.assets, stac_key)
        if not url:
            return None
        with rasterio.open(url) as src:
            return src.read(1, out_shape=out_shape, resampling=Resampling.average).astype(float)

    self.update_state(state='PROGRESS', meta={'progress': 5, 'message': '正在连接 Sentinel-2 数据目录...'})

    try:
        catalog = pystac_client.Client.open("https://earth-search.aws.element84.com/v1")

        # ========== T1 数据获取 ==========
        self.update_state(state='PROGRESS', meta={'progress': 10, 'message': f'正在搜索 T1 时期影像 ({t1_from} ~ {t1_to})...'})
        search1 = catalog.search(
            collections=["sentinel-2-l2a"],
            bbox=bbox,
            datetime=f"{t1_from}/{t1_to}",
            max_items=5,
        )
        items1 = list(search1.items())
        if not items1:
            return {"success": False, "error": f"T1 时期 ({t1_from} ~ {t1_to}) 未找到 Sentinel-2 数据"}

        # 选择云量最少的影像
        item1 = min(items1, key=lambda x: x.properties.get("eo:cloud_cover", 100))

        # ========== T2 数据获取 ==========
        self.update_state(state='PROGRESS', meta={'progress': 20, 'message': f'正在搜索 T2 时期影像 ({t2_from} ~ {t2_to})...'})
        search2 = catalog.search(
            collections=["sentinel-2-l2a"],
            bbox=bbox,
            datetime=f"{t2_from}/{t2_to}",
            max_items=5,
        )
        items2 = list(search2.items())
        if not items2:
            return {"success": False, "error": f"T2 时期 ({t2_from} ~ {t2_to}) 未找到 Sentinel-2 数据"}

        item2 = min(items2, key=lambda x: x.properties.get("eo:cloud_cover", 100))

        self.update_state(state='PROGRESS', meta={'progress': 30, 'message': f'已选定 T1: {item1.id} (云量 {item1.properties.get("eo:cloud_cover", "N/A")}%), T2: {item2.id} (云量 {item2.properties.get("eo:cloud_cover", "N/A")}%)'})

        # ========== 波段映射 ==========
        stac_keys = {
            "blue": "blue",
            "green": "green",
            "red": "red",
            "nir": "nir",
            "swir12": "swir22",
        }

        index_bands = {
            "ndvi": (["red", "nir"], lambda r, nir: np.divide(nir - r, np.where((nir + r) > 0, nir + r, 1), out=np.zeros_like(r), where=(nir + r) > 0)),
            "ndwi": (["green", "nir"], lambda g, nir: np.divide(g - nir, np.where((g + nir) > 0, g + nir, 1), out=np.zeros_like(g), where=(g + nir) > 0)),
            "nbr": (["nir", "swir12"], lambda nir, swir: np.divide(nir - swir, np.where((nir + swir) > 0, nir + swir, 1), out=np.zeros_like(nir), where=(nir + swir) > 0)),
            "evi": (["blue", "red", "nir"], lambda b, r, nir: 2.5 * np.divide(nir - r, np.where((nir + 6 * r - 7.5 * b + 1) > 0, nir + 6 * r - 7.5 * b + 1, 1), out=np.zeros_like(nir), where=(nir + 6 * r - 7.5 * b + 1) > 0)),
        }

        bands_needed, formula = index_bands[index_type]

        # 统一分辨率：以 T1 为基准
        self.update_state(state='PROGRESS', meta={'progress': 35, 'message': '正在读取 T1 波段数据...'})
        with rasterio.open(_asset_href(item1.assets, stac_keys[bands_needed[0]])) as src:
            out_shape = (1, src.height // 4, src.width // 4)
            t1_transform = src.transform
            t1_crs = src.crs

        t1_bands = {}
        for bname in bands_needed:
            arr = read_band(item1, stac_keys[bname], out_shape)
            if arr is None:
                return {"success": False, "error": f"T1 波段 {bname} 不可用", "available": list(item1.assets.keys())}
            t1_bands[bname] = arr

        self.update_state(state='PROGRESS', meta={'progress': 50, 'message': '正在读取 T2 波段数据...'})
        t2_bands = {}
        for bname in bands_needed:
            arr = read_band(item2, stac_keys[bname], out_shape)
            if arr is None:
                return {"success": False, "error": f"T2 波段 {bname} 不可用", "available": list(item2.assets.keys())}
            t2_bands[bname] = arr

        # ========== 计算植被指数 ==========
        self.update_state(state='PROGRESS', meta={'progress': 60, 'message': f'正在计算 {index_type.upper()} 指数...'})
        idx1 = formula(**t1_bands)
        idx2 = formula(**t2_bands)

        # 处理 nodata
        valid_mask = np.isfinite(idx1) & np.isfinite(idx2)

        # ========== 变化检测 ==========
        self.update_state(state='PROGRESS', meta={'progress': 70, 'message': '正在计算变化差异并分类...'})
        change = np.where(valid_mask, idx2 - idx1, np.nan)

        # 5 级分类
        sig_thresh = max(change_threshold * 3, 0.3)  # 显著变化阈值

        classes = {
            "significant_increase": np.nansum(change > sig_thresh),
            "slight_increase": np.nansum((change > change_threshold) & (change <= sig_thresh)),
            "no_change": np.nansum((change >= -change_threshold) & (change <= change_threshold)),
            "slight_decrease": np.nansum((change >= -sig_thresh) & (change < -change_threshold)),
            "significant_decrease": np.nansum(change < -sig_thresh),
        }
        total_pixels = sum(classes.values())

        class_distribution = {
            k: {
                "pixel_count": int(v),
                "percentage": round(float(v / total_pixels * 100), 2) if total_pixels > 0 else 0,
            }
            for k, v in classes.items()
        }

        # 变化统计
        valid_change = change[np.isfinite(change)]
        change_stats = {
            "min": round(float(np.nanmin(change)), 4),
            "max": round(float(np.nanmax(change)), 4),
            "mean": round(float(np.nanmean(change)), 4),
            "std": round(float(np.nanstd(change)), 4),
        }

        # ========== 生成分类栅格 ==========
        self.update_state(state='PROGRESS', meta={'progress': 80, 'message': '正在生成分类结果栅格...'})
        classification = np.full(change.shape, 0, dtype=np.int8)  # 0 = no_change
        classification[(change > change_threshold) & (change <= sig_thresh)] = 1   # slight_increase
        classification[change > sig_thresh] = 2                                       # significant_increase
        classification[(change >= -sig_thresh) & (change < -change_threshold)] = -1 # slight_decrease
        classification[change < -sig_thresh] = -2                                     # significant_decrease
        classification[~valid_mask] = -99  # nodata

        # 保存 GeoTIFF
        output_dir = os.path.join(settings.DATA_DIR, "analysis_results")
        os.makedirs(output_dir, exist_ok=True)
        filename = f"Change_{index_type.upper()}_{int(time.time())}_{uuid.uuid4().hex[:6]}.tif"
        result_path = os.path.join(output_dir, filename)

        # 使用 bbox 计算 transform
        transform = from_bounds(bbox[0], bbox[1], bbox[2], bbox[3], classification.shape[1], classification.shape[0])

        with rasterio.open(
            result_path,
            'w',
            driver='GTiff',
            height=classification.shape[0],
            width=classification.shape[1],
            count=1,
            dtype=classification.dtype,
            crs='EPSG:4326',
            transform=transform,
            nodata=-99,
        ) as dst:
            dst.write(classification, 1)

        # ========== 生成预览图 ==========
        self.update_state(state='PROGRESS', meta={'progress': 90, 'message': '正在生成预览图...'})
        preview_base64 = ""
        try:
            # 颜色映射: 深绿(显著改善) -> 浅绿(轻微改善) -> 灰(无变化) -> 橙(轻微退化) -> 红(显著退化)
            colors = {
                2: (0.0, 0.6, 0.0),    # significant_increase - 深绿
                1: (0.5, 0.9, 0.5),    # slight_increase - 浅绿
                0: (0.7, 0.7, 0.7),    # no_change - 灰色
                -1: (1.0, 0.6, 0.2),   # slight_decrease - 橙色
                -2: (0.8, 0.1, 0.1),   # significant_decrease - 深红
            }

            rgb = np.zeros((*classification.shape, 3), dtype=np.float32)
            for val, (r, g, b) in colors.items():
                mask = classification == val
                rgb[mask, 0] = r
                rgb[mask, 1] = g
                rgb[mask, 2] = b

            fig, ax = plt.subplots(figsize=(12, 10))
            ax.imshow(rgb, extent=[bbox[0], bbox[2], bbox[1], bbox[3]])
            ax.set_title(f"{index_type.upper()} Change Detection: {t1_from} vs {t2_from}")
            ax.set_xlabel("Longitude")
            ax.set_ylabel("Latitude")

            # 添加图例
            from matplotlib.patches import Patch
            legend_elements = [
                Patch(facecolor=colors[2], label=f"显著改善 ({class_distribution['significant_increase']['percentage']:.1f}%)"),
                Patch(facecolor=colors[1], label=f"轻微改善 ({class_distribution['slight_increase']['percentage']:.1f}%)"),
                Patch(facecolor=colors[0], label=f"无变化 ({class_distribution['no_change']['percentage']:.1f}%)"),
                Patch(facecolor=colors[-1], label=f"轻微退化 ({class_distribution['slight_decrease']['percentage']:.1f}%)"),
                Patch(facecolor=colors[-2], label=f"显著退化 ({class_distribution['significant_decrease']['percentage']:.1f}%)"),
            ]
            ax.legend(handles=legend_elements, loc='upper left', bbox_to_anchor=(1.02, 1))

            plt.tight_layout()
            buf = io.BytesIO()
            plt.savefig(buf, format='png', dpi=120, bbox_inches='tight')
            plt.close(fig)
            buf.seek(0)
            preview_base64 = "data:image/png;base64," + base64.b64encode(buf.read()).decode('utf-8')
        except Exception as e:
            logger.error(f"Failed to generate change detection preview: {e}")

        # ========== 持久化到数据库 ==========
        self.update_state(state='PROGRESS', meta={'progress': 95, 'message': '正在保存分析结果...'})
        asset_id = None
        try:
            with db_session() as db:
                file_size = os.path.getsize(result_path)
                record = UploadRecord(
                    filename=f"analysis_results/{filename}",
                    original_name=filename,
                    file_type="raster",
                    format="geotiff",
                    crs="EPSG:4326",
                    geometry_type="raster_analysis",
                    bbox=bbox,
                    file_size=file_size,
                    session_id=session_id,
                )
                db.add(record)
                db.refresh(record)
                asset_id = record.id
        except Exception as e:
            logger.error(f"Failed to log change detection result to DB: {e}")

        # 友好的类别名称映射
        friendly_names = {
            "ndvi": {"significant_increase": "显著改善", "slight_increase": "轻微改善", "no_change": "无变化", "slight_decrease": "轻微退化", "significant_decrease": "显著退化"},
            "ndwi": {"significant_increase": "水体显著增加", "slight_increase": "水体轻微增加", "no_change": "无变化", "slight_decrease": "水体轻微减少", "significant_decrease": "水体显著减少"},
            "nbr": {"significant_increase": "显著恢复", "slight_increase": "轻微恢复", "no_change": "无变化", "slight_decrease": "轻微受损", "significant_decrease": "严重受损"},
            "evi": {"significant_increase": "显著改善", "slight_increase": "轻微改善", "no_change": "无变化", "slight_decrease": "轻微退化", "significant_decrease": "显著退化"},
        }

        summary = {
            k: {**v, "label": friendly_names[index_type].get(k, k)}
            for k, v in class_distribution.items()
        }

        return {
            "success": True,
            "type": "change_detection_result",
            "asset_id": asset_id,
            "filename": filename,
            "bbox": bbox,
            "image": preview_base64,
            "index_type": index_type.upper(),
            "t1": {"item_id": item1.id, "datetime": str(item1.datetime), "cloud_cover": item1.properties.get("eo:cloud_cover", "N/A")},
            "t2": {"item_id": item2.id, "datetime": str(item2.datetime), "cloud_cover": item2.properties.get("eo:cloud_cover", "N/A")},
            "change_stats": change_stats,
            "classification": summary,
            "threshold": {"change": change_threshold, "significant": sig_thresh},
            "message": (
                f"{index_type.upper()} 双时相变化检测完成。"
                f"T1 ({t1_from}) 使用影像 {item1.id}，"
                f"T2 ({t2_from}) 使用影像 {item2.id}。"
                f"变化区域分布：显著改善 {summary['significant_increase']['percentage']:.1f}%，"
                f"轻微改善 {summary['slight_increase']['percentage']:.1f}%，"
                f"无变化 {summary['no_change']['percentage']:.1f}%，"
                f"轻微退化 {summary['slight_decrease']['percentage']:.1f}%，"
                f"显著退化 {summary['significant_decrease']['percentage']:.1f}%。"
            ),
        }

    except ImportError as e:
        return {"success": False, "error": f"缺少依赖: {e}。请安装 pystac-client, rasterio, numpy, matplotlib。"}
    except Exception as e:
        logger.error(f"Change detection task failed: {e}", exc_info=True)
        return {"success": False, "error": str(e)}
