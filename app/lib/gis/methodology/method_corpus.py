"""Method Retrieval Corpus V2 —— 方法级双语检索语料（Epic 11 §5.E）。

**审定工件**（与 app/evaluation/methodology_corpus 同家族）：期望为
方法级语义计划（correct families/methods + invalid methods），不是工具
序列；案例在 ranker 实现之前审定入库（反自证循环：gold 不随 ranker
调参修改——只允许追加新案例，不允许改既有案例期望，测试锁定）。

三层结构：

- ``direct``：明确表述 → 唯一期望方法族/方法；
- ``hard_negative``：词面陷阱（"热力图"≠统计热点、"密度"≠插值、
  "缓冲"≠路网可达……）—— lexical-only 基线会选错、结构化信号救回；
- ``ambiguous``：天然多解（valid 集 ≥2）—— 不当错误也不当满分对。

trap 语义披露：``trap_methods`` 的硬约束（top-k 不得命中）只对
``hard_negative`` 案例强制；``direct`` 案例的 trap 是**排序优劣提示**
（gold 仍须 rank-1，trap 因资格未知而合法在场时不计 invalid——如
m.dens.rate_denom 的 KDE 无几何事实时诚实 unknown）。invalid-method
gold **锚定既有 oracle**（V4 ``qualify_method_candidates``
拒绝集 / ``scientific_preconditions`` / ontology fallback tiers），
不新造 V2 表 gold —— 案例的 profile 事实使拒绝可由既有 oracle 复算
（``expected_rejected_oracle=True`` 的案例必须与 V4 拒绝集一致，测试锁定）。

双语（zh/en 平行）；全部确定性消费，零 LLM、零 I/O。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Tuple

#: 语料规模下限（契约；防静默缩水）。
MIN_METHOD_CORPUS_SIZE = 24


@dataclass(frozen=True)
class MethodRetrievalCase:
    """一条方法级检索案例（期望语义计划，非工具序列）。"""

    case_id: str
    query_zh: str
    query_en: str
    expected_category: str            # ⊆ GIS_TASK_CATEGORIES
    expected_family: str              # ⊆ METHODOLOGY_FAMILIES
    #: 期望方法（有序：rank-1 在前；MRR/Recall@k 的 gold）
    expected_methods: Tuple[str, ...]
    #: 合法替代集（ambiguous 案例的 valid 集）
    valid_methods: Tuple[str, ...] = ()
    #: 词面陷阱方法（lexical 会选、语义上错误；top-k 不得命中）
    trap_methods: Tuple[str, ...] = ()
    kind: str = "direct"              # direct | hard_negative | ambiguous
    #: profile 事实（编译/资格输入；非期望）
    profile: Tuple[Dict, ...] = field(default_factory=tuple)
    #: role_states（资格输入）
    role_states: Tuple[Tuple[str, str], ...] = ()
    measure_kind: str = "unknown"
    #: invalid gold 是否可由既有 oracle 复算（反循环锚定声明）
    expected_rejected_oracle: bool = False


#: 审定语料（ranker 实现前冻结；追加式演进）。
METHOD_CORPUS: Tuple[MethodRetrievalCase, ...] = (
    # ── spatial_distribution ────────────────────────────────────────
    MethodRetrievalCase(
        case_id="m.dist.schools_zh",
        query_zh="成都市小学分布情况",
        query_en="show the distribution of primary schools in Chengdu",
        expected_category="spatial_distribution",
        expected_family="descriptive_mapping",
        expected_methods=("descriptive.simple_display",
                          "descriptive.category_breakdown"),
        trap_methods=("density.kernel_surface", "density.visual_heatmap"),
        profile=({"featureCount": 120, "geometryTypes": ["Point"]},),
    ),
    # ── density（hard negative：热力图 ≠ 密度估计的默认）────────────
    MethodRetrievalCase(
        case_id="m.dens.poi_heat_zh",
        query_zh="用热力图看看成都奶茶店的空间密度",
        query_en="heatmap of bubble tea shop density in Chengdu",
        expected_category="density",
        expected_family="distribution_density",
        expected_methods=("density.kernel_surface", "density.grid_binning"),
        valid_methods=("density.kernel_surface", "density.grid_binning",
                       "density.visual_heatmap"),
        kind="hard_negative",
        trap_methods=("stats.local_cluster",),
        profile=({"featureCount": 800, "geometryTypes": ["Point"]},),
    ),
    MethodRetrievalCase(
        case_id="m.dens.rate_denom",
        query_zh="各区每平方公里人口密度对比",
        query_en="population density per square kilometer by district",
        expected_category="density",
        expected_family="distribution_density",
        expected_methods=("density.admin_rate",),
        trap_methods=("density.kernel_surface",),
        role_states=(("subject", "eligible"), ("boundary", "eligible"),
                     ("denominator", "eligible")),
        profile=({"featureCount": 60},),
    ),
    # ── administrative_aggregation ─────────────────────────────────
    MethodRetrievalCase(
        case_id="m.admin.parks_zh",
        query_zh="统计各区县的公园数量并制表",
        query_en="count parks per district",
        expected_category="administrative_aggregation",
        expected_family="zonal_statistics",
        expected_methods=("zonal.admin_stats",),
        role_states=(("subject", "eligible"), ("boundary", "eligible")),
        profile=({"featureCount": 400, "geometryTypes": ["Point"]},),
    ),
    # ── proximity（hard negative：欧氏缓冲 ≠ 路网可达）─────────────
    MethodRetrievalCase(
        case_id="m.prox.buffer500_zh",
        query_zh="分析学校周边500米缓冲范围内的便利店",
        query_en="convenience stores within a 500m buffer of schools",
        expected_category="proximity",
        expected_family="proximity",
        expected_methods=("proximity.multi_ring_buffer",
                          "proximity.euclidean_buffer"),
        trap_methods=("network.service_area",),
        profile=({"featureCount": 60, "geometryTypes": ["Point"]},),
    ),
    MethodRetrievalCase(
        case_id="m.prox.metro_rings_en",
        query_zh="做地铁站 1/2/3 公里的多环缓冲覆盖范围分析",
        query_en="multi-ring buffer analysis around metro stations",
        expected_category="proximity",
        expected_family="proximity",
        expected_methods=("proximity.multi_ring_buffer",),
        profile=({"featureCount": 150, "geometryTypes": ["Point"]},),
    ),
    # ── accessibility_network（hard negative 反向）─────────────────
    MethodRetrievalCase(
        case_id="m.net.hospital15_zh",
        query_zh="计算每个医院 15 分钟车程服务区",
        query_en="15-minute drive service areas for hospitals",
        expected_category="accessibility_network",
        expected_family="network",
        expected_methods=("network.service_area",),
        trap_methods=("proximity.euclidean_buffer",),
        profile=({},),
        role_states=(("network", "eligible"),),
    ),
    # ── hotspot（hard negative：视觉热力 ≠ 统计显著性）─────────────
    MethodRetrievalCase(
        case_id="m.hot.gi_price_zh",
        query_zh="检验各区房价高值的空间显著性热点",
        query_en="statistically significant hotspots of housing prices",
        expected_category="hotspot",
        expected_family="spatial_statistics",
        expected_methods=("stats.local_cluster",),
        trap_methods=("density.visual_heatmap", "density.kernel_surface"),
        profile=({"featureCount": 40, "geometryTypes": ["Polygon"],
                  "fields": {"price": {"type": "number"}}},),
    ),
    MethodRetrievalCase(
        case_id="m.hot.tiny_negative",
        query_zh="对 6 个样点的价格做热点显著性分析",
        query_en="hotspot significance of 6 price samples",
        expected_category="hotspot",
        expected_family="spatial_statistics",
        expected_methods=("descriptive.simple_display",),
        valid_methods=("descriptive.simple_display", "density.visual_heatmap"),
        trap_methods=("stats.local_cluster",),
        kind="hard_negative",
        expected_rejected_oracle=True,
        profile=({"featureCount": 6, "geometryTypes": ["Point"],
                  "fields": {"price": {"type": "number"}}},),
    ),
    # ── clustering ─────────────────────────────────────────────────
    MethodRetrievalCase(
        case_id="m.clust.dbscan_zh",
        query_zh="把这些充电桩站点聚成几类空间簇",
        query_en="cluster charging stations into spatial groups",
        expected_category="clustering",
        expected_family="spatial_statistics",
        expected_methods=("stats.point_cluster_dbscan",),
        trap_methods=("interp.ordinary_kriging",),
        profile=({"featureCount": 300, "geometryTypes": ["Point"]},),
    ),
    # ── interpolation（hard negative：KDE ≠ 插值；类别数据选指示）──
    MethodRetrievalCase(
        case_id="m.interp.pm25_zh",
        query_zh="根据监测站 PM2.5 数据生成全市浓度表面",
        query_en="PM2.5 concentration surface from monitoring stations",
        expected_category="interpolation",
        expected_family="interpolation",
        expected_methods=("interp.ordinary_kriging", "interp.idw"),
        trap_methods=("density.kernel_surface",),
        profile=({"featureCount": 60, "geometryTypes": ["Point"],
                  "crs": "EPSG:32648",
                  "fields": {"pm25": {"type": "number"}}},),
    ),
    MethodRetrievalCase(
        case_id="m.interp.categorical_kriging",
        query_zh="把土壤类型采样点插值成面",
        query_en="interpolate soil type samples into a surface",
        expected_category="interpolation",
        expected_family="interpolation",
        expected_methods=("interp.indicator_kriging",),
        trap_methods=("interp.ordinary_kriging",),
        kind="hard_negative",
        measure_kind="categorical",
        profile=({"featureCount": 80, "geometryTypes": ["Point"],
                  "fields": {"soil_type": {"type": "string"}}},),
    ),
    # ── suitability ────────────────────────────────────────────────
    MethodRetrievalCase(
        case_id="m.suit.landfill_zh",
        query_zh="垃圾填埋场选址适宜性分析",
        query_en="landfill site suitability analysis",
        expected_category="suitability",
        expected_family="suitability",
        expected_methods=("suit.weighted_overlay",),
        role_states=(("criteria", "eligible"), ("constraint", "eligible")),
        profile=({},),
    ),
    # ── overlay ────────────────────────────────────────────────────
    MethodRetrievalCase(
        case_id="m.overlay.intersect_zh",
        query_zh="把洪水淹没区和土地利用面做叠加相交分析",
        query_en="overlay flood zone and land use polygons",
        expected_category="overlay",
        expected_family="suitability",
        expected_methods=("suit.overlay_composite", "suit.constraint_filter"),
        valid_methods=("suit.overlay_composite", "suit.constraint_filter",
                       "mcda.risk_exposure"),
        profile=({},),
        role_states=(("constraint", "eligible"),),
    ),
    # ── terrain ────────────────────────────────────────────────────
    MethodRetrievalCase(
        case_id="m.terrain.slope_zh",
        query_zh="基于 DEM 计算坡度坡向",
        query_en="derive slope and aspect from the DEM",
        expected_category="terrain",
        expected_family="terrain_hydrology",
        expected_methods=("terrain.slope_aspect",),
        profile=({"geometryKinds": ["raster"], "crs": "EPSG:4326"},),
    ),
    # ── hydrology ──────────────────────────────────────────────────
    MethodRetrievalCase(
        case_id="m.hydro.watershed_en",
        query_zh="划定该流域的汇水区边界",
        query_en="delineate watershed boundaries for this basin",
        expected_category="hydrology",
        expected_family="terrain_hydrology",
        expected_methods=("terrain.watershed",),
        profile=({"geometryKinds": ["raster"], "crs": "EPSG:4326"},),
    ),
    # ── change_detection ───────────────────────────────────────────
    MethodRetrievalCase(
        case_id="m.change.urban_zh",
        query_zh="对比 2015 和 2024 年城市建设用地变化",
        query_en="compare urban land change between 2015 and 2024",
        expected_category="change_detection",
        expected_family="change_detection",
        expected_methods=("change.bi_temporal_raster",
                          "change.post_classification"),
        profile=({},),
        role_states=(("baseline", "eligible"), ("target_time", "eligible")),
    ),
    # ── spatiotemporal_pattern ─────────────────────────────────────
    MethodRetrievalCase(
        case_id="m.st.crime_zh",
        query_zh="分析报警事件的时空聚集格局",
        query_en="spatiotemporal clustering pattern of incident reports",
        expected_category="spatiotemporal_pattern",
        expected_family="spatial_statistics",
        expected_methods=("stats.space_time_pattern",),
        trap_methods=("density.kernel_surface",),
        profile=({"featureCount": 500, "geometryTypes": ["Point"],
                  "hasTimeField": True},),
    ),
    # ── remote_sensing_extraction ──────────────────────────────────
    MethodRetrievalCase(
        case_id="m.rs.ndvi_zh",
        query_zh="计算这景影像的 NDVI 植被指数",
        query_en="compute the NDVI of this satellite image",
        expected_category="remote_sensing_extraction",
        expected_family="remote_sensing",
        expected_methods=("rs.spectral_index",),
        profile=({"geometryKinds": ["raster"]},),
    ),
    # ── uncertainty ────────────────────────────────────────────────
    MethodRetrievalCase(
        case_id="m.unc.variance_zh",
        query_zh="生成插值预测方差的不确定性误差面",
        query_en="produce a prediction variance uncertainty surface",
        expected_category="uncertainty",
        expected_family="interpolation",
        expected_methods=("interp.ordinary_kriging",),
        trap_methods=("density.kernel_surface",),
        profile=({"featureCount": 60, "geometryTypes": ["Point"],
                  "crs": "EPSG:32648",
                  "fields": {"pm25": {"type": "number"}}},),
    ),
    # ── comparison ─────────────────────────────────────────────────
    MethodRetrievalCase(
        case_id="m.comp.before_after_zh",
        query_zh="做一张两期绿地覆盖率的前后对比图",
        query_en="a before-after comparison map of green coverage",
        expected_category="comparison",
        expected_family="compositional_mapping",
        expected_methods=("compose.comparison_map",),
        profile=({},),
        role_states=(("baseline", "eligible"), ("target_time", "eligible")),
    ),
    # ── multi_criteria ─────────────────────────────────────────────
    MethodRetrievalCase(
        case_id="m.mcda.flood_zh",
        query_zh="暴雨内涝风险评价：危险性乘暴露度",
        query_en="flood risk assessment: hazard times exposure",
        expected_category="multi_criteria",
        expected_family="multi_criteria",
        expected_methods=("mcda.risk_exposure",),
        role_states=(("hazard", "eligible"), ("receptor", "eligible")),
        profile=({},),
    ),
    # ── thematic_cartography ───────────────────────────────────────
    MethodRetrievalCase(
        case_id="m.theme.income_en",
        query_zh="做一张各区人均收入的分级统计专题图",
        query_en="choropleth map of per-district income levels",
        expected_category="thematic_cartography",
        expected_family="compositional_mapping",
        expected_methods=("compose.statistical_map",
                          "descriptive.choropleth_display"),
        valid_methods=("compose.statistical_map",
                       "descriptive.choropleth_display"),
        profile=({"featureCount": 20},),
        role_states=(("measure", "eligible"), ("boundary", "eligible")),
    ),
    # ── atlas_reporting ────────────────────────────────────────────
    MethodRetrievalCase(
        case_id="m.atlas.city_zh",
        query_zh="给城市体检做一套多主题专题图集",
        query_en="an atlas of thematic maps for the city report",
        expected_category="atlas_reporting",
        expected_family="compositional_mapping",
        expected_methods=("compose.report_map",),
        profile=({},),
    ),
    # ── ambiguous（天然多解；valid 集 ≥2）──────────────────────────
    MethodRetrievalCase(
        case_id="m.amb.concentration_zh",
        query_zh="分析这些门店在哪里最集中",
        query_en="where are these stores most concentrated",
        expected_category="clustering",
        expected_family="spatial_statistics",
        expected_methods=("stats.point_cluster_dbscan", "stats.local_cluster"),
        valid_methods=("stats.point_cluster_dbscan", "stats.local_cluster",
                       "density.kernel_surface", "density.grid_binning"),
        kind="ambiguous",
        profile=({"featureCount": 250, "geometryTypes": ["Point"]},),
    ),
)


def method_corpus() -> Tuple[MethodRetrievalCase, ...]:
    """审定语料（frozen；评估器消费的唯一期望事实源）。"""
    return METHOD_CORPUS


def corpus_categories_coverage() -> set:
    return {c.expected_category for c in METHOD_CORPUS}


__all__ = [
    "MIN_METHOD_CORPUS_SIZE",
    "MethodRetrievalCase",
    "METHOD_CORPUS",
    "method_corpus",
    "corpus_categories_coverage",
]
