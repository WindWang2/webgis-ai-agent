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
           description=(
               "图论最短路径：给定路网 LineString 集合 + 起终点坐标，返回最短路径线 + 距离。"
               "\n何时用：需要在自定义路网（用户上传 / OSM 抓取）上做最短路径；"
               "出行规划的可控替代（不依赖第三方路径 API）。"
               "\n何时不用：(1) 走真实道路的导航 — 用 search_route_cn (Amap，含转向/限行) 或 search_transit_route；"
               "(2) 要可达范围而非单点路径 — 用 isochrone_network / isochrone_analysis；"
               "(3) 网络规模 > 1 万边 — 性能会退化，先用 attribute_filter 裁子网。"
               "\n关键约束：网络必须是连通的 LineString；起终点会就近吸附到最近节点。"
           ),
           tier=2, domains=["network"],
           args_model=PathAnalysisArgs)
    def path_analysis(network_features: Any, start_point: List[float], end_point: List[float]) -> dict:
        data = safe_parse_geojson(network_features)
        features = data.get("features", [])
        res = SpatialAnalyzer.path_analysis(features, start_point, end_point)
        return res.to_llm_response()

    @tool(registry, name="zonal_stats",
           description=(
               "区域栅格统计：对每个矢量多边形，统计落入其内的栅格像素 (min/max/mean/sum/count) 并回写到 properties。"
               "\n何时用：『每个区县的平均 NDVI / 平均 DEM 高程 / 累积降雨量』；"
               "用 NDVI 或高程图层给区县着色；遥感产物 (compute_ndvi/fetch_dem 的输出) 接入到行政统计。"
               "\n何时不用：(1) 仅做点的栅格采样 — 直接读栅格即可；"
               "(2) 矢量内的矢量统计 (区内 POI 数) — 用 spatial_aggregate；"
               "(3) 没有现成栅格 — 先 fetch_dem / compute_ndvi 再 zonal_stats。"
               "\n关键约束：zones 是 FeatureCollection (面)；raster_path 必须是后端可访问的本地路径或 ref。"
           ),
           tier=2, domains=["raster"],
           args_model=ZonalStatsArgs)
    def zonal_stats(geojson: Any, raster_path: str) -> dict:
        from app.lib.geo_analysis.raster_ops import zonal_statistics
        data = safe_parse_geojson(geojson)
        features = data.get("features", [])
        
        # Ensure we have a valid FeatureCollection for rasterstats
        fc = {"type": "FeatureCollection", "features": features}
        stats = zonal_statistics(fc, raster_path)
        
        # Merge stats back into features
        for i, s in enumerate(stats):
            if i < len(features):
                features[i]["properties"].update(s)
        
        summary = f"Computed zonal statistics for {len(features)} zones against raster {raster_path}."
        return {
            "type": "FeatureCollection",
            "features": features,
            "summary": summary,
            "stats_metadata": {"raster": raster_path}
        }

    @tool(registry, name="idw_interpolation",
           description="反距离加权插值(IDW)：将离散采样点转换为连续的 H3 六边形网格表面。适用于气象、污染等连续变量建模。",
           tier=2, domains=["statistics"],
           param_descriptions={
               "geojson": "输入点要素集 GeoJSON 或引用(ref:xxx)",
               "value_field": "用于插值的数值字段名",
               "resolution": "H3 分辨率（6-9），默认 8",
               "power": "距离权重幂次，默认 2",
           })
    def idw_interpolation(geojson: Any, value_field: str, resolution: int = 8, power: int = 2) -> dict:
        from app.lib.geo_analysis.interpolation import idw_interpolation as _idw_interpolation
        data = safe_parse_geojson(geojson)
        # Use the H3-based IDW implementation
        results = _idw_interpolation(data, value_field, resolution, power)
        
        # Convert H3 results to GeoJSON Features
        import h3
        from shapely.geometry import Polygon, mapping
        features = []
        for res in results:
            cell = res["h3_index"]
            val = res["value"]
            boundary = h3.cell_to_boundary(cell) # [(lat, lng), ...]
            # Shapely expects [(lng, lat), ...]
            poly_coords = [(lng, lat) for lat, lng in boundary]
            features.append({
                "type": "Feature",
                "geometry": mapping(Polygon(poly_coords)),
                "properties": {
                    "h3_index": cell,
                    value_field: round(val, 4)
                }
            })
            
        return {
            "type": "FeatureCollection",
            "features": features,
            "summary": f"Generated IDW interpolation surface with {len(features)} H3 cells (res={resolution}).",
            "metadata": {
                "method": "IDW",
                "h3_resolution": resolution,
                "value_field": value_field
            }
        }

    @tool(registry, name="overlay_analysis",
           description="对两个几何图层进行空间叠加分析（如求交、合并、擦除等），返回结果及其统计信息",
           args_model=OverlayAnalysisArgs)
    def overlay_analysis(layer_a: Any, layer_b: Any, how: str = "intersection") -> dict:
        data_a = safe_parse_geojson(layer_a)
        data_b = safe_parse_geojson(layer_b)
        res = SpatialAnalyzer.overlay(data_a.get("features", []), data_b.get("features", []), how)
        return res.to_llm_response()

    @tool(registry, name="attribute_filter",
           description=(
               "属性筛选：按 Pandas 风格查询表达式从要素集中筛出新的要素集。"
               "✅ 用于：要把筛选结果作为新图层用于后续分析 / 导出。"
               "\n❌ 不要用于：只想临时改现有图层的可见要素 — 用 apply_layer_filter。"
           ),
           args_model=AttributeFilterArgs)
    def attribute_filter(geojson: Any, query: str) -> dict:
        data = safe_parse_geojson(geojson)
        res = SpatialAnalyzer.attribute_filter(data.get("features", []), query)
        return res.to_llm_response()

    @tool(registry, name="spatial_join",
           description=(
               "空间连接：按拓扑关系 (intersects/within/contains 等) 将右图层属性附加到左图层要素。"
               "\n何时用：『把人口属性挂到行政区上』『把 POI 所属街道写回 POI』『判断每个建筑是否在保护区内』；"
               "做主题图（按属性着色）前的属性预处理。"
               "\n何时不用：(1) 只要点数 / 求和 — 用 spatial_aggregate（不返回连接后的全部右属性，更轻量）；"
               "(2) 要保留左图层全部、空匹配补 NaN — join_type='left'；inner 只保留有匹配的。"
               "\n关键约束：predicate 取值 intersects/within/contains/touches/crosses；"
               "左右图层 CRS 必须一致（内部自动按 WGS84 处理）。"
           ),
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
           description=(
               "空间聚合：统计落在每个多边形（如行政区）内的点位（如 POI）数量。"
               "✅ 用于：矢量点要素的计数聚合，返回带统计结果的多边形图层。"
               "\n❌ 不要用于：多边形内的栅格统计（人口/降雨/海拔）— 用 zonal_stats。"
           ),
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

    @tool(registry, name="isochrone_network",
           description="等时线分析（路网模式）：基于路网计算从设施点出发在指定时间内可达的范围。需要输入路网要素。",
           tier=2, domains=["network"],
           args_model=IsochroneAnalysisArgs)
    def isochrone_network(network_layer: Any, facilities: Any, travel_time: float = 15, mode: str = "walking") -> dict:
        from app.lib.geo_analysis.network import calculate_isochrones
        net = safe_parse_geojson(network_layer)
        facs = safe_parse_geojson(facilities)
        res = calculate_isochrones(net, facs, travel_time, mode)
        return res.to_llm_response()

    @tool(registry, name="fishnet_grid",
           description=(
               "鱼网格网生成：在 bbox 内生成正方形或六边形覆盖网格 (空 cell，无属性)。"
               "\n何时用：作为 spatial_aggregate / spatial_join 的底图做空间统计；"
               "需要规则网格做密度可视化但不想用 H3 索引（如要导出兼容 ArcGIS 的 shp）。"
               "\n何时不用：(1) 仅需点的网格聚合 — 直接用 h3_binning（自带 H3 索引、性能更好）；"
               "(2) 要平滑等值面 — 用 kde_contours / idw_interpolation。"
               "\n关键约束：bounds=[west,south,east,north] WGS84；cell_size 单位米；"
               "大 bbox + 小 cell_size 会爆内存（>10⁶ 格警告）。"
           ),
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
           description=(
               "简单服务区分析：按出行模式和时间生成可达范围。"
               "✅ 用于：沿出行速度估算的行程时间/距离可达范围（等时圈），"
               "如『某设施 15 分钟步行圈』。"
               "\n❌ 不要用于：简单直线半径缓冲 — 用 buffer_analysis。"
           ),
           tier=2, domains=["network"],
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
           description=(
               "H3 六边形网格聚合：把点数据聚合到指定分辨率的 H3 网格（代替传统鱼网）。"
               "✅ 用于：需要每个网格的统计值（计数/求和/均值）做数据驱动渲染，"
               "或作为 h3_lisa 空间聚类检验的前置步骤。"
               "\n❌ 不要用于：(1) 只想快速看分布趋势 — 用 heatmap_data(render_type='native')；"
               "(2) 需要平滑的连续密度面 — 用 kde_surface。"
           ),
           tier=2, domains=["statistics"],
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

    @tool(registry, name="dissolve_layer",
           description=(
               "矢量融合 (Dissolve)：把相邻同属性的多边形/线合并为单一几何，可选按字段分组。"
               "\n何时用：(1) 把街道边界合并为区县轮廓；"
               "(2) 把同类用地（如『住宅』『商业』）的相邻地块合并；"
               "(3) overlay/intersect 后清理碎片；"
               "(4) 生成清洁的母图层用于 clip_layer。"
               "\n何时不用：(1) 只想统计每个多边形的属性 — 用 spatial_aggregate；"
               "(2) 要联合两个不同图层 — 用 overlay_analysis(how='union')；"
               "(3) 单纯按属性筛选 — 用 attribute_filter。"
               "\n关键约束：未给 field 时会把整个图层融合成 1 个要素；"
               "给定 field 后会按字段值分组，每组一个融合结果。"
           ),
           param_descriptions={
               "geojson": "输入图层 GeoJSON 或引用(ref:xxx)，几何类型应一致（全部 polygon 或全部 line）",
               "field": "可选属性字段名。若提供，按该字段的不同值分组分别融合；不提供则整体融合为单一要素",
           })
    def dissolve_layer(geojson: Any, field: Optional[str] = None) -> dict:
        from app.lib.geo_processor.geometry import dissolve_smart
        res = dissolve_smart(geojson, field=field)
        return res.to_llm_response()

    @tool(registry, name="nearest_facility",
           description=(
               "最近设施匹配：对每个源点找出目标集合中距离最近的目标，并标注距离（米）。"
               "\n何时用：『每户居民最近的医院/学校』『100 个 POI 最近的地铁站』『每个公交站最近的商圈』 — "
               "**双集合最近邻匹配的唯一工具**。"
               "\n何时不用：(1) 同一集合内的最近邻距离/聚集度 — 用 nearest_neighbor (单集合统计)；"
               "(2) 服务区/可达性 — 用 isochrone_analysis 或 service_area_simple；"
               "(3) 沿路网最近 — 当前是欧氏距离，沿路网需 path_analysis 逐点算。"
               "\n返回：每个源点的副本，properties 新增 nearest_target_id 与 distance_m。"
           ),
           param_descriptions={
               "source_points": "源点要素集 (GeoJSON 或 ref:xxx) — 每个点会找一个最近目标",
               "target_points": "目标点要素集 (GeoJSON 或 ref:xxx) — 候选设施集合",
           })
    def nearest_facility(source_points: Any, target_points: Any) -> dict:
        from app.lib.geo_analysis.network import nearest_neighbor_features
        res = nearest_neighbor_features(source_points, target_points)
        return res.to_llm_response()
