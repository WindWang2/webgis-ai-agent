"""高级空间分析工具 (FC)"""
import logging
from typing import Any, List, Dict, Optional
from pydantic import BaseModel, Field

from app.tools.registry import ToolRegistry, tool
from app.services.spatial_analyzer import SpatialAnalyzer
from app.lib.geo_processor.core import safe_parse as safe_parse_geojson

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

class IsochroneAnalysisArgs(BaseModel):
    network_layer: Any = Field(..., description="路网数据 (GeoJSON 或 ref:xxx)")
    facilities: Any = Field(..., description="设施点 (GeoJSON 或 ref:xxx)")
    travel_time: float = Field(15, description="行驶/步行时间（单位由路网权重决定，通常为分钟或米）")
    mode: str = Field("walking", description="出行模式: walking, driving, cycling")

class FishnetGridArgs(BaseModel):
    bounds: List[float] = Field(..., description="网格范围 [xmin, ymin, xmax, ymax]")
    cell_size: float = Field(..., description="网格大小（米）")
    type: str = Field("square", description="网格类型: square(正方形), hexagon(六边形)")

def register_advanced_spatial_tools(registry: ToolRegistry):
    """注册高级空间分析工具"""

    @tool(registry, name="path_analysis",
           description="在给定的路网要素中计算起点和终点之间的最短路径。",
           args_model=PathAnalysisArgs)
    def path_analysis(network_features: Any, start_point: List[float], end_point: List[float]) -> dict:
        data = safe_parse_geojson(network_features)
        features = data.get("features", [])
        res = SpatialAnalyzer.path_analysis(features, start_point, end_point)
        return res.to_llm_response()

    @tool(registry, name="zonal_stats",
           description="计算矢量区域内的栅格数据统计信息（如平均值、总和等）。",
           args_model=ZonalStatsArgs)
    def zonal_stats(geojson: Any, raster_path: str) -> dict:
        data = safe_parse_geojson(geojson)
        features = data.get("features", [])
        # Note: SpatialAnalyzer.zonal_statistics is not fully implemented in the thin wrapper yet, 
        # but let's keep the tool definition calling it.
        from app.lib.geo_processor.core import GeoAnalysisResult
        return GeoAnalysisResult(False, None, "Zonal statistics not yet implemented").to_llm_response()

    @tool(registry, name="overlay_analysis",
           description="对两个几何图层进行空间叠加分析（如求交、合并、擦除等），返回结果及其统计信息",
           args_model=OverlayAnalysisArgs)
    def overlay_analysis(layer_a: Any, layer_b: Any, how: str = "intersection") -> dict:
        data_a = safe_parse_geojson(layer_a)
        data_b = safe_parse_geojson(layer_b)
        res = SpatialAnalyzer.overlay(data_a.get("features", []), data_b.get("features", []), how)
        return res.to_llm_response()

    @tool(registry, name="attribute_filter",
           description="根据属性条件筛选地理要素。输入 Pandas 风格的查询表达式，返回过滤后的结果。",
           args_model=AttributeFilterArgs)
    def attribute_filter(geojson: Any, query: str) -> dict:
        data = safe_parse_geojson(geojson)
        res = SpatialAnalyzer.attribute_filter(data.get("features", []), query)
        return res.to_llm_response()

    @tool(registry, name="spatial_join",
           description="基于空间拓扑关系将两个图层的属性进行联接。",
           args_model=SpatialJoinArgs)
    def spatial_join(left_layer: Any, right_layer: Any, join_type: str = "inner", predicate: str = "intersects") -> dict:
        data_left = safe_parse_geojson(left_layer)
        data_right = safe_parse_geojson(right_layer)
        from app.services.spatial_analyzer import SpatialAnalyzer
        res = SpatialAnalyzer.spatial_join(
            data_left.get("features", []), 
            data_right.get("features", []), 
            join_type=join_type, 
            predicate=predicate
        )
        return res.to_llm_response()

    @tool(registry, name="clip_layer",
           description="裁剪图层：仅保留位于指定遮罩图层（通常是行政边界）范围内的要素。适合解决『搜索结果超出了行政区范围』的问题，实现精准区域分析。",
           param_descriptions={
               "target_layer": "待裁剪的图层（点、线、面）GeoJSON 或引用(ref:xxx)",
               "mask_layer": "裁剪遮罩（通常是一个行政区面）GeoJSON 或引用(ref:xxx)",
           })
    def clip_layer(target_layer: Any, mask_layer: Any) -> dict:
        target = safe_parse_geojson(target_layer)
        mask = safe_parse_geojson(mask_layer)
        res = SpatialAnalyzer.clip(target.get("features", []), mask)
        return res.to_llm_response()

    @tool(registry, name="spatial_aggregate",
           description="空间聚合分析：统计落在每个多边形（如行政区）内的点位（如POI）数量。返回包含统计结果的多边形图层。",
           param_descriptions={
               "points": "点要素集 GeoJSON 或引用(ref:xxx)",
               "polygons": "多边形要素集（如行政区）GeoJSON 或引用(ref:xxx)",
               "count_field": "存储统计数量的字段名，默认 'point_count'",
           })
    def spatial_aggregate(points: Any, polygons: Any, count_field: str = "point_count") -> dict:
        pts = safe_parse_geojson(points)
        polys = safe_parse_geojson(polygons)
        res = SpatialAnalyzer.aggregate(
            pts.get("features", []), 
            polys.get("features", []), 
            stats=['count'], 
            value_field=count_field
        )
        return res.to_llm_response()

    @tool(registry, name="isochrone_analysis",
           description="等时线分析：基于路网计算从设施点出发在指定时间内可达的范围。",
           args_model=IsochroneAnalysisArgs)
    def isochrone_analysis(network_layer: Any, facilities: Any, travel_time: float = 15, mode: str = "walking") -> dict:
        from app.lib.geoprocessing.network import calculate_isochrones
        net = safe_parse_geojson(network_layer)
        facs = safe_parse_geojson(facilities)
        res = calculate_isochrones(net, facs, travel_time, mode)
        return res.to_llm_response()

    @tool(registry, name="fishnet_grid",
           description="生成鱼网格网：在指定范围内创建正方形或六边形网格。",
           args_model=FishnetGridArgs)
    def fishnet_grid(bounds: List[float], cell_size: float, type: str = "square") -> dict:
        from app.lib.geo_analysis.aggregation import generate_fishnet
        res = generate_fishnet(bounds, cell_size, type)
        return res.to_llm_response()

    @tool(registry, name="central_feature",
           description="中心分析：寻找点集的中心位置。支持计算平均中心(mean_center)或寻找距离所有点最近的中心要素(central_feature)。",
           param_descriptions={
               "geojson": "点要素集 GeoJSON 或引用(ref:xxx)",
               "method": "方法: 'mean_center'(平均中心) 或 'central_feature'(中心要素)",
           })
    def central_feature(geojson: Any, method: str = "mean_center") -> dict:
        data = safe_parse_geojson(geojson)
        res = SpatialAnalyzer.central_feature(data.get("features", []), method)
        return res.to_llm_response()

    @tool(registry, name="service_area_simple",
           description="简单服务区分析：根据出行模式和时间生成服务范围。适合分析『某设施 15 分钟步行圈』。",
           param_descriptions={
               "geojson": "设施点要素集 GeoJSON 或引用(ref:xxx)",
               "travel_time_min": "出行时间（分钟），默认 15",
               "mode": "出行方式: 'walking'(默认, 5km/h), 'cycling'(15km/h), 'driving'(40km/h)",
               "dissolve": "是否合并所有点的服务区，默认 True",
           })
    def service_area_simple(geojson: Any, travel_time_min: float = 15, mode: str = "walking", dissolve: bool = True) -> dict:
        speeds = {"walking": 5.0, "cycling": 15.0, "driving": 40.0}
        speed = speeds.get(mode.lower(), 5.0)
        distance_m = (speed * 1000) * (travel_time_min / 60.0)
        data = safe_parse_geojson(geojson)
        res = SpatialAnalyzer.buffer(data.get("features", []), distance=distance_m, unit="m", dissolve=dissolve)
        return res.to_llm_response()

    @tool(registry, name="h3_binning",
           description="H3网格聚合：将点数据聚合到指定分辨率的H3六边形网格中（代替传统的鱼网格网）。适用于生成高性能的点密度分布数据驱动渲染。",
           param_descriptions={
               "geojson": "点要素集 GeoJSON 或引用(ref:xxx)",
               "resolution": "H3分辨率（通常 6 到 9 之间，越大网格越小），例如 8",
               "stat_field": "可选：参与统计的字段名",
               "stat_method": "统计方法，如 'count'（默认）, 'sum', 'mean'",
           })
    def h3_binning(geojson: Any, resolution: int = 8, stat_field: str = None, stat_method: str = 'count') -> dict:
        from app.lib.geo_analysis.aggregation import h3_binning as _h3_binning
        data = safe_parse_geojson(geojson)
        res = _h3_binning(data, resolution, stat_field, stat_method)
        return res.to_llm_response()
