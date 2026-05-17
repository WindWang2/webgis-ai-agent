"""高级空间分析工具 (FC)"""
import logging
from typing import Any, List, Dict, Optional
from pydantic import BaseModel, Field

from app.tools.registry import ToolRegistry, tool
from app.services.spatial_analyzer import SpatialAnalyzer
from app.lib.geoprocessing.geometry import clip_smart as _clip_smart, overlay_smart as _overlay_smart
from app.lib.geoprocessing.aggregation import (
    aggregate_points_to_polygons as _aggregate_pts_to_polys,
    generate_fishnet as _generate_fishnet
)
from app.lib.geoprocessing.network import calculate_isochrones as _calculate_isochrones

logger = logging.getLogger(__name__)

class PathAnalysisArgs(BaseModel):
# ...
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
        except (ImportError, RuntimeError, TimeoutError, OSError) as exc:
            if not isinstance(exc, ImportError):
                logger.warning(f"Celery unavailable for path_analysis: {exc}")
            features = network_features.get("features", network_features) if isinstance(network_features, dict) else network_features
            r = SpatialAnalyzer.path_analysis(features, start_point=start_point, end_point=end_point)
            if r.success:
                return {"geojson": r.data, "stats": r.stats}
            return {"error": r.error_message}

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
        except (ImportError, RuntimeError, TimeoutError, OSError) as exc:
            if not isinstance(exc, ImportError):
                logger.warning(f"Celery unavailable for zonal_stats: {exc}")
            features = geojson.get("features", geojson) if isinstance(geojson, dict) else geojson
            r = SpatialAnalyzer.zonal_statistics(features, raster_path=raster_path)
            if r.success:
                return {"zonal_stats": r.data.get("zonal_stats") if isinstance(r.data, dict) else r.data}
            return {"error": r.error_message}

    @tool(registry, name="overlay_analysis",
           description="对两个几何图层进行空间叠加分析（如求交、合并、擦除等），返回结果及其统计信息",
           args_model=OverlayAnalysisArgs)
    def overlay_analysis(layer_a: Any, layer_b: Any, how: str = "intersection") -> dict:
        from app.tools._geojson_utils import safe_parse_geojson
        data_a = safe_parse_geojson(layer_a)
        data_b = safe_parse_geojson(layer_b)
        res = _overlay_smart(data_a, data_b, how)
        return res.to_llm_response()

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
        except (ImportError, RuntimeError, TimeoutError, OSError) as exc:
            if not isinstance(exc, ImportError):
                logger.warning(f"Celery unavailable for attribute_filter: {exc}")
            features = geojson.get("features", geojson) if isinstance(geojson, dict) else geojson
            r = SpatialAnalyzer.attribute_filter(features, query=query)
            if r.success:
                return {"geojson": r.data, "stats": r.stats}
            return {"error": r.error_message}

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
        except (ImportError, RuntimeError, TimeoutError, OSError) as exc:
            if not isinstance(exc, ImportError):
                logger.warning(f"Celery unavailable for spatial_join: {exc}")
            features_left = left_layer.get("features", left_layer) if isinstance(left_layer, dict) else left_layer
            features_right = right_layer.get("features", right_layer) if isinstance(right_layer, dict) else right_layer
            r = SpatialAnalyzer.spatial_join(features_left, features_right, join_type=join_type, predicate=predicate)
            if r.success:
                return {"geojson": r.data, "stats": r.stats}
            return {"error": r.error_message}

    @tool(registry, name="clip_layer",
           description="裁剪图层：仅保留位于指定遮罩图层（通常是行政边界）范围内的要素。适合解决『搜索结果超出了行政区范围』的问题，实现精准区域分析。",
           param_descriptions={
               "target_layer": "待裁剪的图层（点、线、面）GeoJSON 或引用(ref:xxx)",
               "mask_layer": "裁剪遮罩（通常是一个行政区面）GeoJSON 或引用(ref:xxx)",
           })
    def clip_layer(target_layer: Any, mask_layer: Any) -> dict:
        """仅保留位于指定遮罩图层范围内的要素"""
        from app.tools._geojson_utils import safe_parse_geojson
        target = safe_parse_geojson(target_layer)
        mask = safe_parse_geojson(mask_layer)
        res = _clip_smart(target, mask)
        return res.to_llm_response()

    @tool(registry, name="spatial_aggregate",
           description="空间聚合分析：统计落在每个多边形（如行政区）内的点位（如POI）数量。返回包含统计结果的多边形图层。",
           param_descriptions={
               "points": "点要素集 GeoJSON 或引用(ref:xxx)",
               "polygons": "多边形要素集（如行政区）GeoJSON 或引用(ref:xxx)",
               "count_field": "存储统计数量的字段名，默认 'point_count'",
           })
    def spatial_aggregate(points: Any, polygons: Any, count_field: str = "point_count") -> dict:
        from app.tools._geojson_utils import safe_parse_geojson
        pts = safe_parse_geojson(points)
        polys = safe_parse_geojson(polygons)
        res = _aggregate_pts_to_polys(pts, polys, stats=['count'], value_field=count_field)
        return res.to_llm_response()

    @tool(registry, name="isochrone_analysis",
           description="等时线分析：基于路网计算从设施点出发在指定时间内可达的范围。",
           args_model=IsochroneAnalysisArgs)
    def isochrone_analysis(network_layer: Any, facilities: Any, travel_time: float = 15, mode: str = "walking") -> dict:
        from app.tools._geojson_utils import safe_parse_geojson
        net = safe_parse_geojson(network_layer)
        facs = safe_parse_geojson(facilities)
        res = _calculate_isochrones(net, facs, travel_time, mode)
        return res.to_llm_response()

    @tool(registry, name="fishnet_grid",
           description="生成鱼网格网：在指定范围内创建正方形或六边形网格。",
           args_model=FishnetGridArgs)
    def fishnet_grid(bounds: List[float], cell_size: float, type: str = "square") -> dict:
        res = _generate_fishnet(bounds, cell_size, type)
        return res.to_llm_response()

    @tool(registry, name="central_feature",
           description="中心分析：寻找点集的中心位置。支持计算平均中心(mean_center)或寻找距离所有点最近的中心要素(central_feature)。",
           param_descriptions={
               "geojson": "点要素集 GeoJSON 或引用(ref:xxx)",
               "method": "方法: 'mean_center'(平均中心) 或 'central_feature'(中心要素)",
           })
    def central_feature(geojson: Any, method: str = "mean_center") -> dict:
        try:
            from app.tools._geojson_utils import _safe_parse_geojson
            feats = _safe_parse_geojson(geojson).get("features", [])
            
            r = SpatialAnalyzer.central_feature(feats, method)
            if r.success:
                return {"geojson": r.data}
            raise RuntimeError(r.error_message)
        except Exception as e:
            logger.error(f"Central feature analysis failed: {e}")
            raise RuntimeError(str(e))

    @tool(registry, name="service_area_simple",
           description="简单服务区分析：根据出行模式和时间生成服务范围。适合分析『某设施 15 分钟步行圈』。",
           param_descriptions={
               "geojson": "设施点要素集 GeoJSON 或引用(ref:xxx)",
               "travel_time_min": "出行时间（分钟），默认 15",
               "mode": "出行方式: 'walking'(默认, 5km/h), 'cycling'(15km/h), 'driving'(40km/h)",
               "dissolve": "是否合并所有点的服务区，默认 True",
           })
    def service_area_simple(geojson: Any, travel_time_min: float = 15, mode: str = "walking", dissolve: bool = True) -> dict:
        try:
            # 速度估算 (km/h)
            speeds = {"walking": 5.0, "cycling": 15.0, "driving": 40.0}
            speed = speeds.get(mode.lower(), 5.0)
            
            # 计算半径 (m)
            distance_m = (speed * 1000) * (travel_time_min / 60.0)
            
            from app.tools._geojson_utils import _safe_parse_geojson
            feats = _safe_parse_geojson(geojson).get("features", [])
            
            r = SpatialAnalyzer.buffer(feats, distance=distance_m, unit="m", dissolve=dissolve)
            if r.success:
                res = r.data
                if isinstance(res, dict):
                    res["properties"] = {"mode": mode, "time_min": travel_time_min, "radius_m": distance_m}
                return res
            raise RuntimeError(r.error_message)
        except Exception as e:
            logger.error(f"Service area analysis failed: {e}")
            raise RuntimeError(str(e))
