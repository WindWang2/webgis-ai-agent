"""空间统计与聚类分析工具 — DBSCAN/K-Means聚类、Moran's I、Getis-Ord Gi*、核密度估计"""
import logging
from typing import Any, Optional

import numpy as np
import geopandas as gpd
from shapely.geometry import box, mapping
from scipy.spatial import distance_matrix

from app.tools.registry import ToolRegistry, tool
from app.lib.geo_processor.core import safe_parse as safe_parse_geojson, to_utm_gdf
from app.lib.geo_analysis.statistics import _extract_numeric_values as extract_numeric_values
from app.services.spatial_analyzer import SpatialAnalyzer
from app.tools._utils import cached_tool, trim_features, std_error_response

logger = logging.getLogger(__name__)

def register_spatial_stats_tools(registry: ToolRegistry):

    @tool(registry, name="spatial_cluster",
           description="空间聚类分析（DBSCAN密度聚类或K-Means分割），返回每个要素的聚类标签",
           tier=2, domains=["statistics"],
           param_descriptions={
               "geojson": "输入点要素 GeoJSON FeatureCollection 或数据引用(ref:xxx)",
               "method": "聚类方法: 'dbscan'(密度聚类, 默认) 或 'kmeans'(K均值)",
               "n_clusters": "K-Means聚类数，默认5",
               "eps": "DBSCAN邻域半径（米），默认1000",
               "min_samples": "DBSCAN最小样本数，默认5",
               "value_field": "可选：参与聚类的数值字段名，将作为额外聚类维度",
           })
    def spatial_cluster(geojson: Any, method: str = "dbscan", n_clusters: int = 5,
                        eps: float = 1000, min_samples: int = 5,
                        value_field: str = "") -> dict:
        data = safe_parse_geojson(geojson)
        features = data.get("features", [])
        res = SpatialAnalyzer.cluster(
            features, method=method, n_clusters=n_clusters, eps=eps, 
            min_samples=min_samples, value_field=value_field
        )
        return res.to_llm_response()

    @tool(registry, name="standard_deviational_ellipse",
           description="计算标准离差椭圆（SDE），用于分析地理要素的空间分布趋势和方向性。",
           tier=2, domains=["statistics"],
           param_descriptions={
               "geojson": "输入点要素 GeoJSON FeatureCollection 或数据引用(ref:xxx)",
           })
    def standard_deviational_ellipse(geojson: Any) -> dict:
        data = safe_parse_geojson(geojson)
        features = data.get("features", [])
        res = SpatialAnalyzer.statistics(features, spatial_stats=True)
        return res.to_llm_response()

    @tool(registry, name="moran_i",
           description="全局 Moran's I 空间自相关检验，判断空间分布模式（聚集/离散/随机）",
           tier=2, domains=["statistics"],
           param_descriptions={
               "geojson": "输入 GeoJSON FeatureCollection 或数据引用(ref:xxx)",
               "value_field": "待检验的数值字段名",
           })
    def moran_i(geojson: Any, value_field: str) -> dict:
        data = safe_parse_geojson(geojson)
        features = data.get("features", [])
        res = SpatialAnalyzer.statistics(features, field=value_field, spatial_stats=True)
        return res.to_llm_response()

    @tool(registry, name="hotspot_analysis",
           description="Getis-Ord Gi* 热点分析，识别统计显著的高值聚集区（热点）和低值聚集区（冷点）",
           tier=2, domains=["statistics"],
           param_descriptions={
               "geojson": "输入 GeoJSON FeatureCollection 或数据引用(ref:xxx)",
               "value_field": "待分析的数值字段名",
               "distance_band": "空间权重距离阈值（米），0表示自动计算（默认）",
           })
    def hotspot_analysis(geojson: Any, value_field: str, distance_band: float = 0) -> dict:
        from app.lib.geo_analysis.statistics import hotspot_narrated
        data = safe_parse_geojson(geojson)
        res = hotspot_narrated(data, value_field, distance_band)
        return res.to_llm_response()

    @tool(registry, name="kde_surface",
           description=(
               "高斯核密度估计：生成覆盖全域的连续概率密度格网。"
               "✅ 用于：作为后续叠加分析 / 选址建模的输入数据层。"
               "\n❌ 不要用于：首选可视化——该格网铺满分析范围、不做阈值过滤会遮挡底图；"
               "看分布趋势用 heatmap_data，要矢量等值面用 kde_contours。"
           ),
           tier=2, domains=["statistics"],
           param_descriptions={
               "geojson": "输入点要素 GeoJSON FeatureCollection 或数据引用(ref:xxx)",
               "bandwidth": "核函数带宽（米），0表示自动计算（Silverman法则）",
               "cell_size": "网格单元大小（米），默认500",
               "value_field": "可选：作为权重的数值字段",
               "bounds": "可选：分析范围 [xmin, ymin, xmax, ymax]（WGS84），默认数据范围+10%缓冲",
           })
    def kde_surface(geojson: Any, bandwidth: float = 0, cell_size: float = 500,
                    value_field: str = "", bounds: Optional[list] = None) -> dict:
        res = SpatialAnalyzer.kde_surface(
            geojson, bandwidth=bandwidth, cell_size=cell_size,
            value_field=value_field, bounds=bounds,
        )
        return res.to_llm_response()

    @tool(registry, name="kde_contours",
           description=(
               "高斯核密度估计（等值面模式）：生成矢量等值线/面。"
               "✅ 用于：制图与导出——平滑的等值面成果，便于叠加展示。"
               "\n❌ 不要用于：快速看分布趋势 — 用 heatmap_data。"
           ),
           tier=2, domains=["statistics"],
           param_descriptions={
               "geojson": "点要素集 GeoJSON 或引用(ref:xxx)",
               "levels": "等值面级数，默认 8",
               "bandwidth": "搜索半径（米），0表示自动",
           })
    @cached_tool(ttl=86400)
    def kde_contours(geojson: Any, levels: int = 8, bandwidth: float = 0) -> dict:
        res = SpatialAnalyzer.kde_contours(geojson, levels=levels, bandwidth=bandwidth)
        if not res.success:
            return std_error_response(
                res.summary, code="VALIDATION_ERROR",
                error_type=res.error_type or "ValueError",
                correction_hint=res.correction_hint,
            )
        # Return the FC dict directly (not to_llm_response): the dispatch layer
        # matches type=="FeatureCollection" and the cartography converters read
        # legend_spec as a top-level analysis marker on this dict.
        result_dict = res.data
        if isinstance(result_dict, dict) and result_dict.get("type") == "FeatureCollection":
            result_dict = trim_features(result_dict)
        return result_dict

    @tool(registry, name="voronoi_polygons",
           description="生成 Voronoi (泰森多边形/Thiessen多边形)，将空间按最近邻原则划分为势力范围",
           tier=2, domains=["statistics"],
           param_descriptions={
               "geojson": "输入点要素 GeoJSON FeatureCollection 或数据引用(ref:xxx)",
               "clip_bounds": "可选：裁剪范围 [xmin, ymin, xmax, ymax]（WGS84），默认使用数据范围+10%缓冲",
           })
    def voronoi_polygons(geojson: Any, clip_bounds: list = None) -> dict:
        res = SpatialAnalyzer.voronoi_polygons(geojson, clip_bounds=clip_bounds)
        return res.to_llm_response()

    @tool(registry, name="convex_hull",
           description=(
               "凸包计算：包住整组要素的最小凸多边形，附 area_km2 与 feature_count。可选 group_by 分组。"
               "\n何时用：『XX 类设施的服务范围大致是多大』；做点群空间范围的快速包络；"
               "聚类预处理 (找出几个 group 的大致边界)。"
               "\n何时不用：(1) 要紧贴形状的边界 — 用 alpha shape (需自定义) 或 concave hull (未实现)；"
               "(2) 仅需 bbox — 用 spatial_stats 看 bbox 字段；"
               "(3) 圈出 DBSCAN 聚类的核心 — 用 spatial_cluster 后再 convex_hull 配合 group_by。"
               "\n关键约束：至少 3 个要素；输出始终 Polygon (即使输入是线/面)。"
           ),
           param_descriptions={
               "geojson": "输入 GeoJSON FeatureCollection 或数据引用(ref:xxx)",
               "group_by": "可选属性字段名。若提供，每个唯一值生成一个独立凸包",
           })
    def convex_hull(geojson: Any, group_by: str = "") -> dict:
        res = SpatialAnalyzer.convex_hull(geojson, group_by=group_by)
        return res.to_llm_response()

    @tool(registry, name="multi_ring_buffer",
           description=(
               "多环缓冲：围绕要素生成多个同心距离环 (含 ring 属性)，适合做距离分级影响圈。"
               "\n何时用：『学校 500/1000/1500m 三档影响圈』『地铁站 300/800m 步行/接驳圈』『加油站 1/3/5km 服务范围分级』；"
               "做距离衰减分析的母图层（每环+spatial_aggregate 统计落入数量）。"
               "\n何时不用：(1) 只要单一距离 — 用 buffer_analysis；"
               "(2) 时间维而非距离维 — 用 isochrone_analysis (按时间路网计算)；"
               "(3) 想要叠加而非环带 — merge_rings=False 拿到独立同心圆。"
               "\n关键约束：distances 升序列表（米）；merge_rings=True 时返回 ring 字段标识第几环。"
           ),
           param_descriptions={
               "geojson": "输入 GeoJSON FeatureCollection 或数据引用(ref:xxx)",
               "distances": "缓冲距离列表（米），升序，例如 [500, 1000, 1500]",
               "merge_rings": "True=同心环带 (默认)；False=独立同心圆（每个完整覆盖到内圈）",
           })
    def multi_ring_buffer(geojson: Any, distances: list = None,
                           merge_rings: bool = True) -> dict:
        res = SpatialAnalyzer.multi_ring_buffer(geojson, distances=distances, merge_rings=merge_rings)
        return res.to_llm_response()

    @tool(registry, name="h3_lisa",
           description="H3网格LISA空间自相关分析：基于H3网格的Local Moran's I热点和冷点聚类分析（如识别显著的高-高或低-低聚集区）。必须传入带有数值字段的H3网格数据（如通过 h3_binning 得到的数据）。",
           tier=2, domains=["statistics"],
           param_descriptions={
               "h3_geojson": "带有属性值的H3网格 GeoJSON 数据或引用(ref:xxx)",
               "value_field": "参与LISA分析的数值字段名",
           })
    def h3_lisa(h3_geojson: Any, value_field: str) -> dict:
        from app.lib.geo_analysis.statistics import h3_lisa as _h3_lisa
        data = safe_parse_geojson(h3_geojson)
        res = _h3_lisa(data, value_field)
        return res.to_llm_response()
