"""高级空间分析工具 (FC)"""
import logging
from typing import Any, List, Dict, Optional
from pydantic import BaseModel, Field

from app.tools.registry import ToolRegistry, tool

logger = logging.getLogger(__name__)

class PathAnalysisArgs(BaseModel):
    network_features: Any = Field(..., description="路网要素集 (GeoJSON 或 ref:xxx)")
    start_point: List[float] = Field(..., description="起点坐标 [lon, lat]")
    end_point: List[float] = Field(..., description="终点坐标 [lon, lat]")

class ZonalStatsArgs(BaseModel):
    geojson: Any = Field(..., description="矢量区域要素 (GeoJSON 或 ref:xxx)")
    raster_path: str = Field(..., description="栅格数据路径或标识")

class OverlayAnalysisArgs(BaseModel):
    layer_a: Any = Field(..., description="图层 A (GeoJSON 或 ref:xxx)")
    layer_b: Any = Field(..., description="图层 B (GeoJSON 或 ref:xxx)")
    how: str = Field("intersection", description="叠加方式: intersection(交集), union(并集), identity(标识), symmetric_difference(对称差异), difference(差异/擦除)")

class AttributeFilterArgs(BaseModel):
    geojson: Any = Field(..., description="输入数据 (GeoJSON 或 ref:xxx)")
    query: str = Field(..., description="Pandas 风格的查询字符串，例如: 'pop > 1000' 或 'type == \"park\"'")

class SpatialJoinArgs(BaseModel):
    left_layer: Any = Field(..., description="左图层 (GeoJSON 或 ref:xxx)")
    right_layer: Any = Field(..., description="右图层 (GeoJSON 或 ref:xxx)")
    join_type: str = Field("inner", description="连接类型: inner, left, right")
    predicate: str = Field("intersects", description="空间谓词: intersects, within, contains, touches, crosses")

def register_advanced_spatial_tools(registry: ToolRegistry):
    """注册高级空间分析工具"""

    @tool(registry, name="path_analysis",
           description="在给定的路网要素中计算起点和终点之间的最短路径。",
           args_model=PathAnalysisArgs)
    def path_analysis(network_features: Any, start_point: List[float], end_point: List[float]) -> dict:
        try:
            from app.services.spatial_tasks import run_path_analysis
            features = network_features.get("features", network_features) if isinstance(network_features, dict) else network_features
            
            task = run_path_analysis.apply_async(
                args=[features, start_point, end_point]
            )
            result = task.get(timeout=120)
            if result.get("success"):
                return {"geojson": result.get("data"), "stats": result.get("stats")}
            return {"error": result.get("error")}
        except Exception as e:
            return {"error": str(e)}

    @tool(registry, name="zonal_stats",
           description="计算矢量区域内的栅格数据统计信息（如平均值、总和等）。",
           args_model=ZonalStatsArgs)
    def zonal_stats(geojson: Any, raster_path: str) -> dict:
        try:
            from app.services.spatial_tasks import run_zonal_stats
            features = geojson.get("features", geojson) if isinstance(geojson, dict) else geojson
            
            task = run_zonal_stats.apply_async(
                args=[features, raster_path]
            )
            result = task.get(timeout=120)
            if result.get("success"):
                return {"zonal_stats": result.get("data").get("zonal_stats")}
            return {"error": result.get("error")}
        except Exception as e:
            return {"error": str(e)}

    @tool(registry, name="overlay_analysis",
           description="对两个几何图层进行空间叠加分析（如求交、合并、擦除等），返回结果及其统计信息",
           args_model=OverlayAnalysisArgs)
    def overlay_analysis(layer_a: Any, layer_b: Any, how: str = "intersection") -> dict:
        try:
            from app.services.spatial_tasks import run_overlay_analysis
            # 解析要素
            features_a = layer_a.get("features", layer_a) if isinstance(layer_a, dict) else layer_a
            features_b = layer_b.get("features", layer_b) if isinstance(layer_b, dict) else layer_b
            
            task = run_overlay_analysis.apply_async(
                args=[features_a, features_b, how]
            )
            result = task.get(timeout=120)
            if result.get("success"):
                return {"geojson": result.get("data"), "stats": result.get("stats")}
            return {"error": result.get("error")}
        except Exception as e:
            return {"error": str(e)}

    @tool(registry, name="attribute_filter",
           description="根据属性条件筛选地理要素。输入 Pandas 风格的查询表达式，返回过滤后的结果。",
           args_model=AttributeFilterArgs)
    def attribute_filter(geojson: Any, query: str) -> dict:
        try:
            from app.services.spatial_tasks import run_attribute_filter
            features = geojson.get("features", geojson) if isinstance(geojson, dict) else geojson
            
            task = run_attribute_filter.apply_async(
                args=[features, query]
            )
            result = task.get(timeout=60)
            if result.get("success"):
                return {"geojson": result.get("data"), "stats": result.get("stats")}
            return {"error": result.get("error")}
        except Exception as e:
            return {"error": str(e)}

    @tool(registry, name="spatial_join",
           description="基于空间拓扑关系将两个图层的属性进行联接。",
           args_model=SpatialJoinArgs)
    def spatial_join(left_layer: Any, right_layer: Any, join_type: str = "inner", predicate: str = "intersects") -> dict:
        try:
            from app.services.spatial_tasks import run_spatial_join
            features_left = left_layer.get("features", left_layer) if isinstance(left_layer, dict) else left_layer
            features_right = right_layer.get("features", right_layer) if isinstance(right_layer, dict) else right_layer
            
            task = run_spatial_join.apply_async(
                args=[features_left, features_right, join_type, predicate]
            )
            result = task.get(timeout=120)
            if result.get("success"):
                return {"geojson": result.get("data"), "stats": result.get("stats")}
            return {"error": result.get("error")}
        except Exception as e:
            return {"error": str(e)}
