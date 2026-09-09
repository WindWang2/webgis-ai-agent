"""Methodology Corpus V4 —— 双语 GIS 方法论语料（Semantic Workflow Compiler V4）。

人工审定的「用户意图 → 专业方法语义计划」期望表，覆盖：

- 12 个方法论族 × 中英双语真实表述（含口语/正式变体）；
- 歧义/欠指定 prompt（"分布情况"既可描述制图也可密度分析 —— 期望是
  **语义计划**（方法族/角色/义务/拒绝集），不是精确工具序列）；
- hard negatives：表述像 A 族但应裁决为 B 族；数据事实上明显不成立的
  方法必须被资格拒绝（如 5 个点的克里金）。

反泄漏红线：期望是**审定工件**（人工写入，非编译产物生成）——评估时
编译器从未见过这些期望；语料不包含 answer 字符串供 prompt 注入。
全部确定性消费，零 LLM。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Tuple


@dataclass(frozen=True)
class MethodologyCase:
    """一个审定语义案例（期望为语义计划，非工具序列）。"""
    case_id: str
    family: str                          # 期望方法族 ⊆ METHODOLOGY_FAMILIES
    utterance_zh: str
    utterance_en: str
    #: 歧义变体：同族内的欠指定表述（确定性默认解析仍应落入本族或其默认方法）
    ambiguous_variants: Tuple[str, ...] = ()
    #: 期望必被考虑的数据角色（编译期角色解析 ∪ 方法 requires_roles）
    expected_roles: Tuple[str, ...] = ()
    #: 数据事实上必须被拒绝的方法（hard negative；如小样本克里金）
    expected_rejected_methods: Tuple[str, ...] = ()
    #: 期望 selected 方法（仅当案例对方法有强审定时非空）
    expected_method: str = ""
    #: 期望至少出现的义务/披露码片段（义务完备性抽查）
    expected_obligation_hints: Tuple[str, ...] = ()
    #: profile 事实（编译输入，非期望）
    profile: tuple = field(default=())


#: 12 族审定的核心案例（zh/en 平行；歧义与负例见各条）。
METHODOLOGY_CORPUS: Tuple[MethodologyCase, ...] = (
    # ── descriptive_mapping ──────────────────────────────────────────
    MethodologyCase(
        case_id="desc.schools_zh",
        family="descriptive_mapping",
        utterance_zh="成都市小学分布情况",
        utterance_en="show me the primary schools in Chengdu",
        ambiguous_variants=("看看成都的小学都在哪里",),
        expected_roles=("subject",),
        expected_obligation_hints=("CARTO_LEGEND_SOURCE",),
        profile=({"featureCount": 120, "geometryTypes": ["Point"]},),
    ),
    MethodologyCase(
        case_id="desc.landuse_en",
        family="descriptive_mapping",
        utterance_zh="把研究区的土地利用类型画出来",
        utterance_en="map the land use categories of the study area",
        expected_roles=("subject",),
        profile=({"featureCount": 300, "geometryTypes": ["Polygon"]},),
    ),
    # ── distribution_density ─────────────────────────────────────────
    MethodologyCase(
        case_id="dens.poi_density_zh",
        family="distribution_density",
        utterance_zh="分析成都便利店的空间密度",
        utterance_en="analyze the spatial density of convenience stores in Chengdu",
        expected_roles=("subject",),
        profile=({"featureCount": 800, "geometryTypes": ["Point"]},),
    ),
    MethodologyCase(
        case_id="dens.heat_negative",
        family="distribution_density",
        utterance_zh="用热力图展示成都奶茶店分布",
        utterance_en="show a heatmap of bubble tea shops in Chengdu",
        expected_roles=("subject",),
        expected_rejected_methods=(),  # 视觉热力图合法候选但必须垫底+披露
        profile=({"featureCount": 200, "geometryTypes": ["Point"]},),
    ),
    # ── interpolation ────────────────────────────────────────────────
    MethodologyCase(
        case_id="interp.krige_negative",
        family="interpolation",
        utterance_zh="用克里金把 5 个土壤采样点插值成污染面",
        utterance_en="kriging interpolation of 5 soil samples into a pollution surface",
        expected_roles=("subject", "measure"),
        expected_rejected_methods=("interp.ordinary_kriging",),
        expected_method="interp.idw",
        profile=({"featureCount": 5, "geometryTypes": ["Point"],
                  "fields": {"pb_mg_kg": {"type": "number"}}},),
    ),
    MethodologyCase(
        case_id="interp.air_quality_en",
        family="interpolation",
        utterance_zh="根据监测站 PM2.5 数据生成全市浓度表面",
        utterance_en="generate a PM2.5 concentration surface from monitoring stations",
        expected_roles=("subject", "measure"),
        profile=({"featureCount": 60, "geometryTypes": ["Point"],
                  "crs": "EPSG:32648",
                  "fields": {"pm25": {"type": "number"}}},),
    ),
    # ── zonal_statistics ─────────────────────────────────────────────
    MethodologyCase(
        case_id="zonal.district_stats_zh",
        family="zonal_statistics",
        utterance_zh="统计各区县的公园数量并制表",
        utterance_en="count parks per district and make a table",
        expected_roles=("subject", "boundary"),
        profile=({"featureCount": 400, "geometryTypes": ["Point"]},),
    ),
    # ── suitability ──────────────────────────────────────────────────
    MethodologyCase(
        case_id="suit.site_zh",
        family="suitability",
        utterance_zh="垃圾填埋场选址适宜性分析",
        utterance_en="landfill site suitability analysis",
        expected_roles=("criteria", "constraint"),
        profile=({},),
    ),
    # ── network ──────────────────────────────────────────────────────
    MethodologyCase(
        case_id="net.route_zh",
        family="network",
        utterance_zh="从火电站到主城区的最短路径分析",
        utterance_en="shortest path from the power plant to the urban core",
        expected_roles=("network",),
        profile=({},),
    ),
    MethodologyCase(
        case_id="net.service_area_en",
        family="network",
        utterance_zh="计算每个医院 15 分钟车程服务区",
        utterance_en="compute 15-minute drive service areas for hospitals",
        expected_roles=("network",),
        profile=({},),
    ),
    # ── terrain_hydrology ────────────────────────────────────────────
    MethodologyCase(
        case_id="terrain.slope_zh",
        family="terrain_hydrology",
        utterance_zh="基于 DEM 计算坡度坡向",
        utterance_en="derive slope and aspect from the DEM",
        expected_roles=("elevation",),
        expected_obligation_hints=("CARTO_LEGEND_SOURCE",),
        profile=({"geometryKinds": ["raster"], "crs": "EPSG:4326"},),
    ),
    MethodologyCase(
        case_id="terrain.watershed_en",
        family="terrain_hydrology",
        utterance_zh="划定该流域的汇水区边界",
        utterance_en="delineate watershed boundaries for this basin",
        expected_roles=("elevation",),
        profile=({"geometryKinds": ["raster"], "crs": "EPSG:4326"},),
    ),
    # ── remote_sensing ───────────────────────────────────────────────
    MethodologyCase(
        case_id="rs.ndvi_zh",
        family="remote_sensing",
        utterance_zh="计算这景影像的 NDVI 植被指数",
        utterance_en="compute the NDVI of this satellite image",
        expected_roles=("subject",),
        profile=({"geometryKinds": ["raster"]},),
    ),
    # ── change_detection ─────────────────────────────────────────────
    MethodologyCase(
        case_id="change.urban_en",
        family="change_detection",
        utterance_zh="对比 2015 和 2024 年城市建设用地变化",
        utterance_en="compare urban land change between 2015 and 2024",
        expected_roles=("baseline", "target_time"),
        profile=({},),
    ),
    # ── spatial_statistics ───────────────────────────────────────────
    MethodologyCase(
        case_id="stats.hotspot_negative",
        family="spatial_statistics",
        utterance_zh="对 6 个样点的价格做热点显著性分析",
        utterance_en="hotspot significance analysis of 6 price samples",
        expected_roles=("subject", "measure"),
        expected_rejected_methods=("stats.local_cluster",),
        profile=({"featureCount": 6, "geometryTypes": ["Point"],
                  "fields": {"price": {"type": "number"}}},),
    ),
    MethodologyCase(
        case_id="stats.moran_en",
        family="spatial_statistics",
        utterance_zh="检验各区房价的空间自相关",
        utterance_en="test spatial autocorrelation of district housing prices",
        expected_roles=("subject", "measure"),
        profile=({"featureCount": 40,
                  "fields": {"price": {"type": "number"}}},),
    ),
    # ── multi_criteria ───────────────────────────────────────────────
    MethodologyCase(
        case_id="mcda.flood_zh",
        family="multi_criteria",
        utterance_zh="暴雨内涝风险评价：危险性乘暴露度",
        utterance_en="flood risk assessment: hazard times exposure",
        expected_roles=("hazard", "receptor"),
        expected_obligation_hints=("CARTO_LEGEND_SOURCE",),
        profile=({},),
    ),
    # ── compositional_mapping ────────────────────────────────────────
    MethodologyCase(
        case_id="compose.report_zh",
        family="compositional_mapping",
        utterance_zh="给报告做一张各区小学数量对比图加统计表",
        utterance_en="make a report map comparing school counts by district with a stats table",
        expected_roles=("measure", "boundary"),
        profile=({"featureCount": 250},),
    ),
    # ── proximity（Epic 11 纯加法族）─────────────────────────────────
    MethodologyCase(
        case_id="prox.buffer_zh",
        family="proximity",
        utterance_zh="分析学校周边500米缓冲范围内的便利店",
        utterance_en="find convenience stores within a 500m buffer of schools",
        expected_roles=("subject",),
        profile=({"featureCount": 60, "geometryTypes": ["Point"]},),
    ),
    MethodologyCase(
        case_id="prox.rings_en",
        family="proximity",
        utterance_zh="做地铁站 1/2/3 公里的多环缓冲覆盖范围分析",
        utterance_en="multi-ring buffer analysis (1/2/3 km) around metro stations",
        expected_roles=("subject",),
        profile=({"featureCount": 150, "geometryTypes": ["Point"]},),
    ),
)


#: 语料覆盖的方法族集合（完整性断言用；缺族 = 语料审计失败）。
METHODOLOGY_FAMILIES_COVERAGE = frozenset(
    c.family for c in METHODOLOGY_CORPUS)


def corpus_cases() -> Tuple[MethodologyCase, ...]:
    """审定语料（frozen；评估器消费的唯一期望事实源）。"""
    return METHODOLOGY_CORPUS
