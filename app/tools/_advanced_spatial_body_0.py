"""高级空间分析工具 (FC)"""
import logging
from typing import Any, List, Optional
from pydantic import BaseModel, Field

from app.tools.registry import ToolRegistry, tool
from app.tools._utils import cached_tool, trim_features
from app.services.spatial_analyzer import SpatialAnalyzer
from app.lib.geo_processor.core import safe_parse as safe_parse_geojson

logger = logging.getLogger(__name__)


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

class RasterReclassifyArgs(BaseModel):
    raster_path: str = Field(..., description="输入栅格文件路径（data/ 内）")
    scheme: List[dict] = Field(..., description="重分类方案，每项含 min/max/value/label，如 [{\"min\":0,\"max\":0.2,\"value\":1,\"label\":\"低\"},...]")
    nodata: Optional[float] = Field(None, description="输出 NoData 值（默认继承输入栅格）")

class RasterCalculatorArgs(BaseModel):
    raster_a: str = Field(..., description="主栅格文件路径（data/ 内）")
    raster_b: Optional[str] = Field(None, description="副栅格文件路径（data/ 内）；留空则用 constant")
    expression: str = Field("A + B", description="运算表达式，用 A/B 指代栅格，如 A+B, (A-B)/(A+B), where(A>0,A,0)")
    constant: Optional[float] = Field(None, description="当 raster_b 留空时的常数")
    nodata: Optional[float] = Field(None, description="输出 NoData 值")
    resampling: Optional[str] = Field(None, description="B 对齐到 A 的重采样方法（bilinear 默认；分类栅格必须 nearest）")

class RasterResampleArgs(BaseModel):
    raster_path: str = Field(..., description="输入栅格文件路径（data/ 内）")
    target_resolution: float = Field(..., description="目标像元大小（米或度，取决于 CRS）")
    target_crs: Optional[str] = Field(None, description="目标 CRS（如 EPSG:3857），留空则保持原 CRS")
    resampling: str = Field("bilinear", description="重采样方法: bilinear(默认), nearest, cubic, mode, average")

def register_advanced_spatial_tools(registry: ToolRegistry):
    """注册高级空间分析工具"""

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
        data = safe_parse_geojson(geojson)
        # GIS-682: forward the whole FeatureCollection so a declared `crs`
        # member survives the tool boundary — previously only the features
        # list was forwarded and the FC-level crs was silently dropped
        # (mirrors the pre-#599 clip/overlay half-stack).
        res = SpatialAnalyzer.zonal_stats(data, raster_path)
        return res.to_llm_response()

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
        from app.lib.geo_analysis.interpolation import h3_to_geojson
        data = safe_parse_geojson(geojson)
        # Use the H3-based IDW implementation
        results = _idw_interpolation(data, value_field, resolution, power)
        
        # Convert H3 results to GeoJSON Features (审计：消除重复的 H3-to-GeoJSON 转换)
        geojson_result = h3_to_geojson(results, value_field)
        geojson_result["summary"] = f"Generated IDW interpolation surface with {len(geojson_result['features'])} H3 cells (res={resolution})."
        return geojson_result

    @tool(registry, name="overlay_analysis",
           description="对两个几何图层进行空间叠加分析（如求交、合并、擦除等），返回结果及其统计信息",
           args_model=OverlayAnalysisArgs)
    def overlay_analysis(layer_a: Any, layer_b: Any, how: str = "intersection") -> dict:
        data_a = safe_parse_geojson(layer_a)
        data_b = safe_parse_geojson(layer_b)
        # #765: forward the parsed FeatureCollection (not the bare features
        # list) so a declared `crs` member survives the tool boundary — the
        # deep operators (overlay_smart -> gdf_from_features) honor it.
        res = SpatialAnalyzer.overlay(data_a, data_b, how)
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
        # #1110: forward the parsed FeatureCollection (not the bare features
        # list) so a declared `crs` member survives the tool boundary —
        # mirrors #765 overlay_analysis / GIS-682 zonal_stats.
        res = SpatialAnalyzer.attribute_filter(data, query)
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
        # #765: forward the parsed FeatureCollections (not bare features
