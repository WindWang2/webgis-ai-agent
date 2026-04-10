"""空间分析 FC 工具"""
import json
import logging
from typing import Optional

from app.tools.registry import ToolRegistry, tool

logger = logging.getLogger(__name__)


def _safe_parse_geojson(geojson: str) -> dict | None:
    """安全解析 GeoJSON 字符串，处理截断/格式错误"""
    if isinstance(geojson, dict):
        return geojson
    if not isinstance(geojson, str):
        return None
    geojson = geojson.strip()
    if not geojson:
        return None
    try:
        return json.loads(geojson)
    except json.JSONDecodeError:
        # 尝试修复常见的截断问题
        logger.warning(f"GeoJSON parse failed, attempting repair (length={len(geojson)})")
        # 尝试找到最后一个完整的 feature
        try:
            # 找到最后一个 } 并尝试闭合
            for end_pos in range(len(geojson) - 1, max(len(geojson) - 100, 0), -1):
                if geojson[end_pos] == '}':
                    candidate = geojson[:end_pos + 1] + ']}'
                    try:
                        result = json.loads(candidate)
                        if isinstance(result, dict) and 'features' in result:
                            logger.info(f"GeoJSON repair succeeded, recovered {len(result.get('features', []))} features")
                            return result
                    except json.JSONDecodeError:
                        continue
        except Exception:
            pass
        return None


def register_spatial_tools(registry: ToolRegistry):
    """注册空间分析工具"""

    @tool(registry, name="buffer_analysis",
           description="对几何要素进行缓冲区分析，返回缓冲区多边形",
           param_descriptions={
               "geojson": "输入 GeoJSON FeatureCollection（JSON字符串）",
               "distance": "缓冲距离（米）",
               "unit": "单位：m/km，默认m"
           })
    def buffer_analysis(geojson: str, distance: float, unit: str = "m") -> dict:
        try:
            data = _safe_parse_geojson(geojson)
            if not data:
                return {"error": "Invalid GeoJSON input"}
            features = data.get("features", data) if isinstance(data, dict) else data
            
            # 使用 Celery 异步执行
            from app.services.task_queue import celery_app
            task = celery_app.send_task(
                "app.services.spatial_tasks.run_buffer_analysis",
                args=[features, distance, unit]
            )
            result = task.get(timeout=120)  # 等待结果，设置超时
            
            if result.get("success"):
                return {"geojson": result.get("data"), "stats": result.get("stats")}
            return {"error": result.get("error")}
        except Exception as e:
            logger.error(f"Buffer analysis error: {e}")
            return {"error": str(e)}

    @tool(registry, name="spatial_stats",
           description="计算几何要素的空间统计信息（面积、长度、中心点等）",
           param_descriptions={
               "geojson": "输入 GeoJSON FeatureCollection（JSON字符串）"
           })
    def spatial_stats(geojson: str) -> dict:
        try:
            import geopandas as gpd
            from shapely.geometry import shape

            data = _safe_parse_geojson(geojson)
            if not data:
                return {"error": "Invalid GeoJSON input"}
            features = data.get("features", [])
            if not features:
                return {"error": "No features in GeoJSON"}

            # 使用 Celery 异步执行
            from app.services.task_queue import celery_app
            task = celery_app.send_task(
                "app.services.spatial_tasks.run_spatial_stats",
                args=[features]
            )
            result = task.get(timeout=60)
            
            if result.get("success"):
                return {"stats": result.get("stats")}
            return {"error": result.get("error")}
        except Exception as e:
            logger.error(f"Spatial stats error: {e}")
            return {"error": str(e)}

    @tool(registry, name="nearest_neighbor",
           description="查找最近的邻近距离和空间分布模式",
           param_descriptions={
               "geojson": "输入点要素 GeoJSON FeatureCollection（JSON字符串）"
           })
            features = data.get("features", [])

            # 使用 Celery 异步执行
            from app.services.task_queue import celery_app
            task = celery_app.send_task(
                "app.services.spatial_tasks.run_nearest_neighbor",
                args=[features]
            )
            result = task.get(timeout=60)
            
            if result.get("success"):
                return result.get("data")
            return {"error": result.get("error")}
        except Exception as e:
            logger.error(f"NN analysis error: {e}")
            return {"error": str(e)}

    @tool(registry, name="heatmap_data",
           description="根据点要素生成热力图数据（网格密度统计）",
           param_descriptions={
               "geojson": "输入点要素 GeoJSON FeatureCollection（JSON字符串）",
               "cell_size": "网格大小（米），默认500",
               "radius": "搜索半径（米），默认1000"
           })
    def heatmap_data(geojson: str, cell_size: int = 500, radius: int = 1000) -> dict:
        try:
            import base64
            import io

            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt
            import numpy as np
            from matplotlib.colors import LinearSegmentedColormap
            from scipy.ndimage import gaussian_filter

            data = _safe_parse_geojson(geojson)
            if not data:
                return {"error": "Invalid GeoJSON input: 无法解析，请确保数据完整"}
            features = data.get("features") or data.get("feature_collection", [])

            # 使用 Celery 异步执行
            from app.services.task_queue import celery_app
            task = celery_app.send_task(
                "app.services.spatial_tasks.run_heatmap_generation",
                kwargs={"features": features, "cell_size": cell_size, "radius": radius}
            )
            result = task.get(timeout=120)
            
            if result.get("success"):
                return result.get("data")
            return {"error": result.get("error")}
        except Exception as e:
            logger.error(f"Heatmap error: {e}")
            return {"error": str(e)}
