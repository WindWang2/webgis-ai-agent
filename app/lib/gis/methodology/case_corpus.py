"""GIS Case Corpus —— 端到端方法案例语料（Epic §9；DoD 验收 oracle）。

21+ 个小型可验证 fixture 案例，覆盖 Epic §9 的全部场景面。每案例：

    intent（双语）→ correct method families / invalid methods
    → required data（fixture profile）→ expected artifacts
    → recommended visualizations → required components

**端到端消费**（Definition of Done）：案例经 KnowledgeService 走完整链
classify → qualify → rank → template plan；断言：

- 正确方法族/方法在类目池排序中登顶（或 ambiguous ∈ valid 集）；
- invalid 方法被资格拒绝或被排序压到不可选位置（oracle 对齐：V4
  拒绝集 / scientific_preconditions / ontology fallback tiers）；
- 产物期望可满足（方法 output_artifacts ⊇ 案例 expected 核心产物）；
- 可视化期望 ∈ taxonomy recommended_visualizations；
- 组件期望由组合计划覆盖（required_components 必须出现在 slot fills）；
- 平行案例不变性（同类别不同主体/规模 → 同裁决）排除 per-query 硬编码。

全部 fixture 为小型 synthetic profile（零大数据、零 LLM、零 I/O）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Tuple

#: 案例规模下限（契约）。
MIN_CASE_CORPUS_SIZE = 21


@dataclass(frozen=True)
class GISCase:
    """一个端到端方法案例（fixture 化，可验证）。"""

    case_id: str
    query_zh: str
    query_en: str
    category_id: str
    correct_family: str
    #: 有序 gold（rank 断言）
    expected_methods: Tuple[str, ...]
    #: 无效方法（oracle 可复核：资格拒绝 或 不得登顶）
    invalid_methods: Tuple[str, ...] = ()
    #: fixture 数据事实（单 profile；多 profile = 平行不变性案例）
    profiles: Tuple[Dict, ...] = ()
    role_states: Tuple[Tuple[str, str], ...] = ()
    measure_kind: str = "unknown"
    expected_artifacts: Tuple[str, ...] = ()
    expected_visualizations: Tuple[str, ...] = ()   # ⊆ map model 词表
    required_components: Tuple[str, ...] = ()
    kind: str = "direct"          # direct | hard_negative | ambiguous


#: 审定案例（Epic §9 全覆盖，顺序 = Epic 列表序）。
GIS_CASE_CORPUS: Tuple[GISCase, ...] = (
    GISCase(
        case_id="case.school_distribution",
        query_zh="成都市小学分布情况",
        query_en="primary schools distribution in Chengdu",
        category_id="spatial_distribution",
        correct_family="descriptive_mapping",
        expected_methods=("descriptive.simple_display",
                          "descriptive.category_breakdown"),
        invalid_methods=("density.kernel_surface", "interp.ordinary_kriging"),
        profiles=({"featureCount": 120, "geometryTypes": ["Point"]},),
        expected_artifacts=("point_feature_set",),
        expected_visualizations=("simple_point_map",),
        required_components=("legend", "title", "scale_bar"),
    ),
    GISCase(
        case_id="case.medical_accessibility",
        query_zh="评估各区居民到医院的 15 分钟车程可达性",
        query_en="15-minute drive accessibility to hospitals by district",
        category_id="accessibility_network",
        correct_family="network",
        expected_methods=("network.service_area",),
        invalid_methods=("proximity.euclidean_buffer",),
        profiles=({},),
        role_states=(("subject", "eligible"), ("network", "eligible")),
        expected_artifacts=("service_area",),
        expected_visualizations=("service_area_overlay",),
        required_components=("legend", "title"),
    ),
    GISCase(
        case_id="case.poi_density",
        query_zh="分析成都便利店的空间密度",
        query_en="spatial density of convenience stores in Chengdu",
        category_id="density",
        correct_family="distribution_density",
        expected_methods=("density.kernel_surface", "density.grid_binning"),
        invalid_methods=("interp.ordinary_kriging",),
        profiles=({"featureCount": 800, "geometryTypes": ["Point"]},),
        expected_artifacts=("density_surface",),
        expected_visualizations=("kernel_density_surface",),
        required_components=("legend", "title", "continuous_colorbar"),
    ),
    GISCase(
        case_id="case.admin_statistics",
        query_zh="统计各区县公园数量并制表",
        query_en="count parks per district",
        category_id="administrative_aggregation",
        correct_family="zonal_statistics",
        expected_methods=("zonal.admin_stats",),
        profiles=({"featureCount": 400, "geometryTypes": ["Point"]},),
        role_states=(("subject", "eligible"), ("boundary", "eligible")),
        expected_artifacts=("admin_aggregate_table",),
        expected_visualizations=("administrative_choropleth",),
        required_components=("legend", "title", "chart_panel"),
    ),
    GISCase(
        case_id="case.raw_vs_normalized",
        query_zh="各区每平方公里人口密度对比",
        query_en="raw counts vs density comparison across districts",
        category_id="density",
        correct_family="distribution_density",
        expected_methods=("density.admin_rate",),
        invalid_methods=(),
        profiles=({"featureCount": 40},),
        role_states=(("subject", "eligible"), ("boundary", "eligible"),
                     ("denominator", "eligible")),
        expected_artifacts=("admin_aggregate_table",),
        expected_visualizations=("normalized_choropleth",),
        required_components=("legend", "continuous_colorbar"),
    ),
    GISCase(
        case_id="case.hotspot_significance",
        query_zh="检验各区房价的显著性热点",
        query_en="statistically significant hotspots of housing prices",
        category_id="hotspot",
        correct_family="spatial_statistics",
        expected_methods=("stats.local_cluster",),
        invalid_methods=("density.visual_heatmap",),
        profiles=({"featureCount": 40, "geometryTypes": ["Polygon"],
                   "fields": {"price": {"type": "number"}}},),
        expected_artifacts=("hotspot_result",),
        expected_visualizations=("hotspot_overlay",),
        required_components=("legend", "title"),
    ),
    GISCase(
        case_id="case.clustering_dbscan",
        query_zh="把这些充电桩聚成几类空间簇",
        query_en="cluster charging stations into spatial groups",
        category_id="clustering",
        correct_family="spatial_statistics",
        expected_methods=("stats.point_cluster_dbscan",),
        invalid_methods=("interp.ordinary_kriging",),
        profiles=({"featureCount": 300, "geometryTypes": ["Point"]},),
        expected_artifacts=("hotspot_result",),
        expected_visualizations=("dbscan_cluster_map",),
        required_components=("legend", "title", "chart_panel"),
    ),
    GISCase(
        case_id="case.interpolation_appropriate",
        query_zh="根据 60 个监测站 PM2.5 数据生成浓度面",
        query_en="PM2.5 surface from 60 monitoring stations",
        category_id="interpolation",
        correct_family="interpolation",
        expected_methods=("interp.ordinary_kriging", "interp.idw"),
        profiles=({"featureCount": 60, "geometryTypes": ["Point"],
                   "crs": "EPSG:32648",
                   "fields": {"pm25": {"type": "number"}}},),
        expected_artifacts=("raster_surface",),
        expected_visualizations=("interpolation_result_map",),
        required_components=("continuous_colorbar", "title"),
    ),
    GISCase(
        case_id="case.interpolation_inappropriate",
        query_zh="把土壤类别采样点插值成连续面",
        query_en="interpolate categorical district population into a surface",
        category_id="interpolation",
        correct_family="interpolation",
        expected_methods=("interp.indicator_kriging",),
        invalid_methods=("interp.ordinary_kriging", "interp.idw"),
        kind="hard_negative",
        measure_kind="categorical",
        profiles=({"featureCount": 80, "geometryTypes": ["Point"],
                   "fields": {"pop_class": {"type": "string"}}},),
        expected_artifacts=("raster_surface",),
        expected_visualizations=(),
        required_components=("continuous_colorbar",),
    ),
    GISCase(
        case_id="case.land_use_change",
        query_zh="对比 2015 与 2024 年土地利用变化",
        query_en="land use change between 2015 and 2024",
        category_id="change_detection",
        correct_family="change_detection",
        expected_methods=("change.bi_temporal_raster",
                          "change.post_classification"),
        profiles=({},),
        role_states=(("baseline", "eligible"), ("target_time", "eligible")),
        expected_artifacts=("change_set",),
        expected_visualizations=("change_comparison_map",),
        required_components=("legend", "title"),
    ),
    GISCase(
        case_id="case.dem_slope",
        query_zh="基于 DEM 计算坡度坡向",
        query_en="derive slope and aspect from DEM",
        category_id="terrain",
        correct_family="terrain_hydrology",
        expected_methods=("terrain.slope_aspect",),
        invalid_methods=("density.kernel_surface",),
        profiles=({"geometryKinds": ["raster"], "crs": "EPSG:4326"},),
        expected_artifacts=("terrain_surface",),
        expected_visualizations=("terrain_analytical_surface",),
        required_components=("continuous_colorbar", "title"),
    ),
    GISCase(
        case_id="case.hydro_risk",
        query_zh="划定汇水区并评估水文风险",
        query_en="delineate watersheds and assess hydrological risk",
        category_id="hydrology",
        correct_family="terrain_hydrology",
        expected_methods=("terrain.watershed",),
        profiles=({"geometryKinds": ["raster"], "crs": "EPSG:4326"},),
        expected_artifacts=("polygon_feature_set",),
        expected_visualizations=("watershed_boundary_map",),
        required_components=("legend", "title"),
    ),
    GISCase(
        case_id="case.road_service_area",
        query_zh="计算消防站 5 分钟车程服务区",
        query_en="5-minute drive service areas of fire stations",
        category_id="accessibility_network",
        correct_family="network",
        expected_methods=("network.service_area",),
        invalid_methods=("proximity.euclidean_buffer",),
        profiles=({},),
        role_states=(("subject", "eligible"), ("network", "eligible")),
        expected_artifacts=("service_area",),
        expected_visualizations=("service_area_overlay",),
        required_components=("legend", "title"),
    ),
    GISCase(
        case_id="case.rs_classification",
        query_zh="对这景影像做土地覆盖分类",
        query_en="land cover classification of this image",
        category_id="remote_sensing_extraction",
        correct_family="remote_sensing",
        expected_methods=("rs.classification",),
        profiles=({"geometryKinds": ["raster"]},),
        expected_artifacts=("raster_surface",),
        expected_visualizations=("classified_raster",),
        required_components=("categorical_legend", "title"),
    ),
    GISCase(
        case_id="case.uncertainty_map",
        query_zh="生成插值预测方差的不确定性地图",
        query_en="uncertainty map of interpolation prediction variance",
        category_id="uncertainty",
        correct_family="interpolation",
        expected_methods=("interp.ordinary_kriging",),
        profiles=({"featureCount": 60, "geometryTypes": ["Point"],
                   "crs": "EPSG:32648",
                   "fields": {"pm25": {"type": "number"}}},),
        expected_artifacts=("raster_surface",),
        expected_visualizations=("uncertainty_surface",),
        required_components=("uncertainty_panel", "continuous_colorbar"),
    ),
    GISCase(
        case_id="case.multi_period_compare",
        query_zh="做两期绿地的并排对比图",
        query_en="side-by-side comparison of two-period green space",
        category_id="comparison",
        correct_family="compositional_mapping",
        expected_methods=("compose.comparison_map",),
        profiles=({},),
        role_states=(("baseline", "eligible"), ("target_time", "eligible")),
        expected_artifacts=("admin_aggregate_table", "feature_collection"),
        expected_visualizations=("before_after_swipe",),
        required_components=("legend", "title"),
    ),
    GISCase(
        case_id="case.mixed_crs_buffer",
        query_zh="在 WGS84 数据上做学校周边 500 米缓冲",
        query_en="500m buffer around schools on WGS84 data",
        category_id="proximity",
        correct_family="proximity",
        expected_methods=("proximity.euclidean_buffer",
                          "proximity.multi_ring_buffer"),
        # 混合 CRS（R1-F1 语义）：欧氏缓冲 GEOGRAPHIC_OK → viable
        #（实现内建投影）；克里金族等 PROJECTED_REQUIRED 方法 →
        # transform 修复链显式化，非静默
        profiles=({"featureCount": 60, "geometryTypes": ["Point"],
                   "crs": "EPSG:4326"},),
        expected_artifacts=("proximity_zone",),
        expected_visualizations=("proximity_overlay",),
        required_components=("legend", "scale_bar"),
    ),
    GISCase(
        case_id="case.antimeridian_aoi",
        query_zh="分析180度经线附近岛屿的缓冲范围",
        query_en="buffer analysis of islands near the antimeridian",
        category_id="proximity",
        correct_family="proximity",
        expected_methods=("proximity.euclidean_buffer",
                          "proximity.multi_ring_buffer"),
        profiles=({"featureCount": 12, "geometryTypes": ["Polygon"],
                   "bbox": [178.5, -20.2, -179.8, -19.5]},),
        expected_artifacts=("proximity_zone",),
        expected_visualizations=("proximity_overlay",),
        required_components=("legend",),
    ),
    GISCase(
        case_id="case.tiny_sample",
        query_zh="对 4 个采样点做空间统计分析",
        query_en="spatial statistical analysis of 4 samples",
        category_id="hotspot",
        correct_family="spatial_statistics",
        expected_methods=(),
        invalid_methods=("stats.local_cluster", "stats.global_autocorrelation",
                         "stats.point_cluster_dbscan", "interp.ordinary_kriging"),
        kind="hard_negative",
        profiles=({"featureCount": 4, "geometryTypes": ["Point"],
                   "fields": {"v": {"type": "number"}}},),
        expected_artifacts=(),
        expected_visualizations=(),
        required_components=(),
    ),
    GISCase(
        case_id="case.invalid_geometry",
        query_zh="对含有无效图形的各区行政面做统计聚合",
        query_en="aggregate statistics on self-intersecting polygons",
        category_id="administrative_aggregation",
        correct_family="zonal_statistics",
        expected_methods=("zonal.admin_stats",),
        profiles=({"featureCount": 20, "geometryTypes": ["Polygon"],
                   "invalidGeometryRatio": 0.4},),
        role_states=(("subject", "eligible"), ("boundary", "eligible")),
        expected_artifacts=("admin_aggregate_table",),
        expected_visualizations=(),
        required_components=("legend",),
    ),
    GISCase(
        case_id="case.no_geometry",
        query_zh="对这份没有坐标的商户名单做空间分布分析",
        query_en="spatial distribution of a merchant list without coordinates",
        category_id="spatial_distribution",
        correct_family="descriptive_mapping",
        expected_methods=(),
        invalid_methods=("density.kernel_surface", "interp.ordinary_kriging"),
        kind="hard_negative",
        profiles=({"featureCount": 500, "geometryTypes": []},),
        expected_artifacts=(),
        expected_visualizations=(),
        required_components=(),
    ),
    GISCase(
        case_id="case.large_layer_sampling",
        query_zh="对千万级轨迹点做密度分析",
        query_en="density analysis of tens of millions of track points",
        category_id="density",
        correct_family="distribution_density",
        # 注：算法层未为 KDE 声明资源硬闸（registry gap，follow-up）——
        # 大层语义下两者均由 canonical 事实判 viable，gold 取集合。
        expected_methods=("density.grid_binning", "density.kernel_surface"),
        profiles=({"featureCount": 5000000, "geometryTypes": ["Point"]},),
        expected_artifacts=("grid_aggregate",),
        expected_visualizations=("aggregate_grid",),
        required_components=("legend", "continuous_colorbar"),
    ),
)


def gis_case_corpus() -> Tuple[GISCase, ...]:
    return GIS_CASE_CORPUS


__all__ = [
    "MIN_CASE_CORPUS_SIZE",
    "GISCase",
    "GIS_CASE_CORPUS",
    "gis_case_corpus",
]
