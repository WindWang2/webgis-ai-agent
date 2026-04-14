"""空间分析 FC 工具"""
import json
import logging
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field

from app.tools.registry import ToolRegistry, tool

logger = logging.getLogger(__name__)


def _safe_parse_geojson(geojson: Any) -> dict | None:
    """安全解析 GeoJSON，支持字符串或字典"""
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
        logger.warning(f"GeoJSON parse failed, attempting repair (length={len(geojson)})")
        try:
            for end_pos in range(len(geojson) - 1, max(len(geojson) - 100, 0), -1):
                if geojson[end_pos] == '}':
                    candidate = geojson[:end_pos + 1] + ']}'
                    try:
                        result = json.loads(candidate)
                        if isinstance(result, dict) and 'features' in result:
                            return result
                    except json.JSONDecodeError:
                        continue
        except Exception:
            pass
        return None


class BufferAnalysisArgs(BaseModel):
    geojson: Any = Field(..., description="输入 GeoJSON FeatureCollection 或数据引用(ref:xxx)")
    distance: float = Field(..., gt=0, description="缓冲距离（米），必须大于0")
    unit: str = Field("m", description="单位：m/km，默认m")


class HeatmapDataArgs(BaseModel):
    geojson: Any = Field(..., description="输入点要素 GeoJSON 或数据引用(ref:xxx)")
    cell_size: int = Field(500, ge=10, le=5000, description="网格大小（米），范围 10-5000")
    radius: int = Field(1000, ge=10, le=10000, description="搜索半径（米），范围 10-10000")
    render_type: str = Field("raster", description="渲染模式: raster(栅格), grid(格网), native(原生)")
    palette: str = Field("classic", description="配色方案: classic, magma, viridis, thermal")


def register_spatial_tools(registry: ToolRegistry):
    """注册空间分析工具"""

    @tool(registry, name="buffer_analysis",
           description="对几何要素进行缓冲区分析，返回缓冲区多边形",
           args_model=BufferAnalysisArgs)
    def buffer_analysis(geojson: Any, distance: float, unit: str = "m") -> dict:
        try:
            data = _safe_parse_geojson(geojson)
            if not data:
                return {"error": "Invalid GeoJSON input"}
            features = data.get("features", data) if isinstance(data, dict) else data
            from app.services.spatial_tasks import run_buffer_analysis
            task = run_buffer_analysis.apply_async(
                args=[features, distance, unit]
            )
            result = task.get(timeout=120)
            if result.get("success"):
                return {"geojson": result.get("data"), "stats": result.get("stats")}
            return {"error": result.get("error")}
        except Exception as e:
            logger.error(f"Buffer analysis error: {e}")
            return {"error": str(e)}

    @tool(registry, name="spatial_stats",
           description="计算几何要素的空间统计信息（面积、长度、中心点等）")
    def spatial_stats(geojson: Any) -> dict:
        try:
            data = _safe_parse_geojson(geojson)
            if not data:
                return {"error": "Invalid GeoJSON input"}
            features = data.get("features", [])
            from app.services.spatial_tasks import run_spatial_stats
            task = run_spatial_stats.apply_async(
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
           description="查找最近的邻近距离和空间分布模式")
    def nearest_neighbor(geojson: Any) -> dict:
        try:
            data = _safe_parse_geojson(geojson)
            if not data:
                return {"error": "Invalid GeoJSON input"}
            features = data.get("features", [])
            from app.services.spatial_tasks import run_nearest_neighbor
            task = run_nearest_neighbor.apply_async(
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
           description="根据点要素生成热力图。支持 'raster' (栅格图片)、'grid' (矢量格网) 和 'native' (原生渲染) 模式。支持通过 palette 参数切换配色方案。",
           args_model=HeatmapDataArgs)
    def heatmap_data(geojson: Any, cell_size: int = 500, radius: int = 2000, render_type: str = "raster", palette: str = "classic") -> dict:
        try:
            data = _safe_parse_geojson(geojson)
            if not data:
                return {"error": "Invalid GeoJSON input"}
            features = data.get("features") or data.get("feature_collection", [])
            
            # --- 原生热力图模式 ---
            if render_type == "native":
                if isinstance(data, dict):
                    data["metadata"] = {
                        "render_type": "native",
                        "point_count": len(features),
                        "radius": radius,
                        "palette": palette
                    }
                return data

            from app.services.spatial_tasks import run_heatmap_generation
            task = run_heatmap_generation.apply_async(
                kwargs={"features": features, "cell_size": cell_size, "radius": radius, "render_type": render_type, "palette": palette}
            )
            result = task.get(timeout=120)
            if result.get("success"):
                data = result.get("data")
                # 注入 render 指令暗示前端
                if isinstance(data, dict):
                    if render_type == "raster":
                        data["command"] = "add_heatmap_raster"
                    else:
                        data["command"] = "add_layer"
                return data
            return {"error": result.get("error")}
        except Exception as e:
            logger.error(f"Heatmap error: {e}")
            return {"error": str(e)}
