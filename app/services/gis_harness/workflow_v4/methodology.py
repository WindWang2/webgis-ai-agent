"""Methodology Family Model V4 —— GIS 专业方法论族（Semantic Workflow Compiler V4）。

把「自然语言 GIS 任务 → 专业方法论」提升为一等模型：12 个方法论族绑定
既有本体任务（gis_ontology.TaskDescriptor）、能力/算法词表
（CapabilityRegistry / AlgorithmRegistry）与数据角色
（workflow_schema.DATA_ROLES），并给出**方法级候选 + 资格排序**：

    methodology family（专业方法透镜）
        → candidate methods（该方法族下的候选专业方法）
        → qualification（按数据/画像事实做资格裁决与确定性排序）
        → selected / eligible / rejected（拒绝带机器可读 reason codes）

职责边界（Epic workflow-v4）：

- 本模块决定「这类任务有哪些专业方法、哪个在当前数据事实上成立」；
- 「如何在运行时执行」仍归 Harness runtime；「算法数值语义」归算法层
  （scientific_preconditions / AlgorithmDescriptor —— 本模块只引用）。

红线：

- MethodologyFamily 是本体任务/既有 registry 的**方法论透镜**：只引用
  既有 task_id / capability / algorithm id / DATA_ROLES / DOWNGRADE_CLASSES
  词表，不另造第二套任务/能力词表；
- 资格裁决委托既有事实源：角色状态用 data_qualification 五态、科学性
  检查用 scientific_preconditions.evaluate_precondition（联动不重复）；
  unknown（画像缺事实）≠ 不满足，不虚构资格；
- 排序确定性：同输入同排序；rejected 候选完整保留 + 机器可读拒绝码，
  LLM/调用方不得绕过资格裁决自由选择方法；
- 全部零 LLM、零 I/O、产物可序列化且有界。
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field

#: 方法论族 schema 版本（进入 registry 指纹）。
METHODOLOGY_SCHEMA_VERSION = 4

#: 12 个方法论族（稳定词表，纯加法演进）。覆盖 ADR-0118 的方法论面：
#: 描述制图 / 分布密度 / 插值 / 分区统计 / 适宜性 / 网络 / 地形水文 /
#: 遥感 / 变化检测 / 空间统计 / 多准则 / 组合制图。
METHODOLOGY_FAMILIES = (
    "descriptive_mapping",
    "distribution_density",
    "interpolation",
    "zonal_statistics",
    "suitability",
    "network",
    "terrain_hydrology",
    "remote_sensing",
    "change_detection",
    "spatial_statistics",
    "multi_criteria",
    "compositional_mapping",
)

#: 方法资格裁决状态（selected 供编译器直接消费；与 rejected 并列完整保留）。
METHOD_QUALIFICATION_STATUSES = ("selected", "eligible", "rejected")

#: 候选方法硬准则的稳定拒绝码前缀（机器可读，进 evidence/reason codes）。
REJECT_GEOMETRY = "METHOD_GEOMETRY_MISMATCH"
REJECT_MIN_SAMPLE = "METHOD_MIN_SAMPLE_UNMET"
REJECT_ROLE_BLOCKED = "METHOD_ROLE_BLOCKED"
REJECT_PRECONDITION = "METHOD_PRECONDITION_UNSATISFIED"

#: 资格事实未知时资格维的中性分（与 plan_candidates unknown=0.5 同语义：
#: 缺事实不奖不罚）。
_UNKNOWN_NEUTRAL_SCORE = 0.5


class MethodCandidate(BaseModel):
    """一个方法论族下的候选专业方法（资格准则 + 回退语义声明）。

    ``capabilities`` / ``algorithm_ids`` 是 CapabilityRegistry /
    AlgorithmRegistry 的引用（注册期校验，悬空 fatal）；``preconditions``
    引用算法层 scientific precondition id（评估期委托，不重复实现）；
    ``approximate=True`` 的候选可入选但必须披露（proxy/approximation 语义）。
    """
    method_id: str
    family_id: str                      # ⊆ METHODOLOGY_FAMILIES
    label_zh: str
    label_en: str = ""
    description: str = ""
    capabilities: Tuple[str, ...] = ()  # ⊆ CapabilityRegistry（校验）
    algorithm_ids: Tuple[str, ...] = () # ⊆ AlgorithmRegistry（校验）
    # ── 资格硬准则（事实在场才裁决；unknown ≠ 拒绝）──────────────────
    geometry_kinds: Tuple[str, ...] = ()   # 空 = 不限；⊆ point/line/polygon/raster/table/network
    min_sample_size: Optional[int] = None  # 画像 featureCount 已知且更小 → 拒绝
    requires_roles: Tuple[str, ...] = ()   # ⊆ DATA_ROLES；状态 blocked → 拒绝
    preconditions: Tuple[str, ...] = ()    # 算法层 precondition id（委托评估）
    # ── 排序与披露 ────────────────────────────────────────────────────
    priority: int = 50                  # 越小越优先（专业首选）
    approximate: bool = False           # 近似/代理语义 → 降分 + 强制披露
    downgrade_class: str = ""           # ⊆ DOWNGRADE_CLASSES（approximate 时必填）
    disclosures: Tuple[str, ...] = ()
    # 声明的方法级产出（⊆ ArtifactTypeRegistry，校验）
    output_artifacts: Tuple[str, ...] = ()

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "method_id": self.method_id[:64],
            "family_id": self.family_id[:40],
            "label_zh": self.label_zh[:60],
            "capabilities": list(self.capabilities[:6]),
            "algorithm_ids": list(self.algorithm_ids[:6]),
            "priority": self.priority,
            "approximate": self.approximate,
            "downgrade_class": self.downgrade_class[:24],
        }


class MethodologyFamily(BaseModel):
    """方法论族：本体任务的方法论透镜（引用既有事实，不新造任务词表）。

    ``ontology_task_ids`` 全部 ⊆ gis_ontology.ONTOLOGY_TASKS（注册期校验）；
    一个任务可属多个族（透镜正交于任务分类）；族可覆盖零任务吗？——不可，
    空族无意义（校验拦截）。
    """
    family_id: str                      # ⊆ METHODOLOGY_FAMILIES
    label_zh: str
    label_en: str = ""
    description: str = ""
    ontology_task_ids: Tuple[str, ...] = ()
    candidate_methods: Tuple[MethodCandidate, ...] = ()
    # 家族级数据角色诉求（成员任务 required_data_roles 并集的投影，载入期
    # 从 ontology 派生 —— 不手写，防第二事实源）。
    data_role_demands: Tuple[str, ...] = ()

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "family_id": self.family_id[:40],
            "label_zh": self.label_zh[:60],
            "ontology_task_ids": list(self.ontology_task_ids[:12]),
            "method_ids": [m.method_id for m in self.candidate_methods[:8]],
            "data_role_demands": list(self.data_role_demands[:8]),
        }


# ── 资格裁决模型 ─────────────────────────────────────────────────────────

class MethodQualification(BaseModel):
    """一个候选方法的资格裁决（确定性、可解释、有界）。"""
    method_id: str
    family_id: str
    status: str = "eligible"            # ⊆ METHOD_QUALIFICATION_STATUSES
    score: float = 0.0                  # 0-1 确定性分（排序键之一）
    rank: int = 0                       # 1-based；rejected 排在 eligible 后
    reason_codes: List[str] = Field(default_factory=list)
    disclosures: List[str] = Field(default_factory=list)
    evidence: Dict[str, Any] = Field(default_factory=dict)

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "method_id": self.method_id[:64],
            "family_id": self.family_id[:40],
            "status": self.status,
            "score": round(self.score, 3),
            "rank": self.rank,
            "reason_codes": [c[:64] for c in self.reason_codes[:6]],
            "disclosures": [d[:200] for d in self.disclosures[:4]],
            "evidence": {
                str(k)[:32]: v for k, v in list(self.evidence.items())[:6]
            },
        }


class MethodQualificationSet(BaseModel):
    """一个方法论族的候选资格全集（selected/eligible/rejected 全保留）。"""
    family_id: str
    qualifications: List[MethodQualification] = Field(default_factory=list)
    selected_id: str = ""               # rank-1 eligible；空 = 全被拒/无候选
    # 全部候选都被拒绝时的诚实语义：方法族在该数据事实上不成立
    # （≠ 空 unknown —— 每个拒绝都有 reason code）。
    all_rejected: bool = False

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "family_id": self.family_id[:40],
            "selected_id": self.selected_id[:64],
            "all_rejected": self.all_rejected,
            "qualifications": [
                q.to_bounded_dict() for q in self.qualifications[:10]
            ],
        }


# ── 审定候选方法表（capability/algorithm id 全部经注册期校验）────────────

_GEO_POINT = "point"
_GEO_POLY = "polygon"
_GEO_RASTER = "raster"


def _c(method_id: str, family_id: str, label_zh: str, **kw: Any) -> MethodCandidate:
    kw.setdefault("label_en", "")
    return MethodCandidate(
        method_id=method_id, family_id=family_id, label_zh=label_zh, **kw)


_CURATED_CANDIDATES: Tuple[MethodCandidate, ...] = (
    # ── descriptive_mapping（描述制图）────────────────────────────────
    _c("descriptive.category_breakdown", "descriptive_mapping", "分类构成制图",
       capabilities=("category_breakdown",), algorithm_ids=("stats.category.breakdown",),
       geometry_kinds=(_GEO_POINT, _GEO_POLY), requires_roles=("subject",),
       priority=10, output_artifacts=("stats_table", "chart_spec")),
    _c("descriptive.simple_display", "descriptive_mapping", "要素直接展示",
       capabilities=("poi_query",), geometry_kinds=(_GEO_POINT,),
       requires_roles=("subject",), priority=20,
       output_artifacts=("point_feature_set",)),
    _c("descriptive.choropleth_display", "descriptive_mapping", "分区填色展示",
       capabilities=("admin_aggregation",), algorithm_ids=("spatial.aggregate.admin",),
       geometry_kinds=(_GEO_POLY,), requires_roles=("subject", "boundary"),
       priority=30, output_artifacts=("admin_aggregate_table",)),
    # ── distribution_density（分布与密度）────────────────────────────
    _c("density.kernel_surface", "distribution_density", "核密度面",
       capabilities=("kde_density", "analytical_density"),
       algorithm_ids=("spatial.kde.surface",),
       geometry_kinds=(_GEO_POINT,), requires_roles=("subject",),
       min_sample_size=10, priority=10, output_artifacts=("density_surface",)),
    _c("density.admin_rate", "distribution_density", "行政区率/密度制图",
       capabilities=("admin_aggregation", "rate_aggregation"),
       algorithm_ids=("spatial.aggregate.rates", "stats.rate_smoothing"),
       requires_roles=("subject", "boundary", "denominator"), priority=20,
       output_artifacts=("admin_aggregate_table",)),
    _c("density.grid_binning", "distribution_density", "网格聚合密度",
       capabilities=("grid_binning",), algorithm_ids=("spatial.grid.fishnet",),
       geometry_kinds=(_GEO_POINT,), requires_roles=("subject",), priority=30,
       output_artifacts=("grid_aggregate",)),
    _c("density.visual_heatmap", "distribution_density", "视觉热力图（非定量）",
       capabilities=("density_surface",), algorithm_ids=("density.visual.heatmap",),
       geometry_kinds=(_GEO_POINT,), requires_roles=("subject",),
       approximate=True, downgrade_class="proxy", priority=60,
       disclosures=("视觉热力图为渲染代理，非定量密度估计；定量结论请用核密度面或行政区率。",),
       output_artifacts=("density_surface",)),
    # ── interpolation（空间插值）──────────────────────────────────────
    _c("interp.ordinary_kriging", "interpolation", "普通克里金",
       capabilities=("block_kriging",), algorithm_ids=("interpolation.kriging",),
       geometry_kinds=(_GEO_POINT,), requires_roles=("subject", "measure"),
       min_sample_size=30, preconditions=("numeric_field_required", "min_numeric_samples:30"),
       priority=10, output_artifacts=("raster_surface",)),
    _c("interp.regression_kriging", "interpolation", "回归克里金",
       capabilities=("regression_kriging",), algorithm_ids=("interpolation.regression_kriging",),
       geometry_kinds=(_GEO_POINT,), requires_roles=("subject", "measure"),
       min_sample_size=30, priority=15, output_artifacts=("raster_surface",)),
    _c("interp.idw", "interpolation", "反距离权重",
       capabilities=("spatial_interpolation",), algorithm_ids=("interpolation.idw",),
       geometry_kinds=(_GEO_POINT,), requires_roles=("subject", "measure"),
       min_sample_size=5, approximate=True, downgrade_class="approximation",
       priority=20, disclosures=("IDW 为确定性近似插值，不提供估计不确定性。",),
       output_artifacts=("raster_surface",)),
    _c("interp.trend_surface", "interpolation", "趋势面（粗尺度）",
       capabilities=("trend_surface",), algorithm_ids=("interpolation.trend_surface",),
       geometry_kinds=(_GEO_POINT,), requires_roles=("subject", "measure"),
       min_sample_size=8, approximate=True, downgrade_class="degraded",
       priority=40, output_artifacts=("raster_surface",)),
    # ── zonal_statistics（分区统计）──────────────────────────────────
    _c("zonal.admin_stats", "zonal_statistics", "行政区统计",
       capabilities=("zonal_statistics", "admin_aggregation"),
       algorithm_ids=("spatial.aggregate.admin",),
       requires_roles=("subject", "boundary"), priority=10,
       output_artifacts=("admin_aggregate_table", "stats_table")),
    _c("zonal.grid_stats", "zonal_statistics", "网格分区统计",
       capabilities=("grid_binning",), algorithm_ids=("spatial.grid.fishnet",),
       geometry_kinds=(_GEO_POINT, _GEO_RASTER), priority=20,
       output_artifacts=("grid_aggregate",)),
    _c("zonal.areal_crosswalk", "zonal_statistics", "面插值/跨线统计",
       capabilities=("areal_interpolation",), algorithm_ids=("interpolation.dasymetric",),
       priority=30, output_artifacts=("admin_aggregate_table",)),
    # ── suitability（适宜性分析）────────────────────────────────────
    _c("suit.weighted_overlay", "suitability", "多因子加权叠加",
       capabilities=("mcda_evaluation",), algorithm_ids=("decision.mcda.wsm",),
       requires_roles=("criteria",), priority=10,
       output_artifacts=("raster_surface",)),
    _c("suit.constraint_filter", "suitability", "约束过滤选址",
       capabilities=("geometry_overlay",), algorithm_ids=("geometry.buffer",),
       requires_roles=("constraint",), priority=20,
       output_artifacts=("proximity_zone", "polygon_feature_set")),
    # ── network（网络分析）──────────────────────────────────────────
    _c("network.shortest_path", "network", "最短路径",
       capabilities=("shortest_path", "route_optimization"),
       algorithm_ids=("network.shortest_path", "network.route_optimization"),
       requires_roles=("network",), priority=10,
       output_artifacts=("line_feature_set",)),
    _c("network.service_area", "network", "服务区分析",
       capabilities=("service_area",), algorithm_ids=("network.service_area.multi",),
       requires_roles=("network",), priority=20,
       output_artifacts=("service_area",)),
    _c("network.gravity_accessibility", "network", "引力可达性",
       capabilities=("gravity_accessibility", "accessibility"),
       requires_roles=("subject", "network"), priority=25,
       output_artifacts=("stats_table",)),
    _c("network.od_flow", "network", "OD 流动分析",
       capabilities=("od_flow_mapping", "od_matrix"), algorithm_ids=("flow.od_arc_build",),
       requires_roles=("subject", "network"), priority=30,
       output_artifacts=("od_matrix", "od_table")),
    _c("network.facility_location", "network", "设施选址（区位配置）",
       capabilities=("location_allocation",),
       requires_roles=("subject", "population", "network"), priority=35,
       output_artifacts=("point_feature_set", "stats_table")),
    # ── terrain_hydrology（地形与水文）──────────────────────────────
    _c("terrain.slope_aspect", "terrain_hydrology", "坡度坡向衍生",
       capabilities=("terrain_slope", "terrain_aspect"), algorithm_ids=("terrain.slope",),
       geometry_kinds=(_GEO_RASTER,), requires_roles=("elevation",),
       preconditions=("local_metric_crs_required",), priority=10,
       output_artifacts=("terrain_surface",)),
    _c("terrain.watershed", "terrain_hydrology", "流域划分",
       capabilities=("terrain_hydrology",), algorithm_ids=("terrain.watershed",),
       geometry_kinds=(_GEO_RASTER,), requires_roles=("elevation",),
       preconditions=("local_metric_crs_required",), priority=20,
       output_artifacts=("polygon_feature_set",)),
    _c("terrain.stream_network", "terrain_hydrology", "河网提取",
       capabilities=("terrain_hydrology_advanced",),
       algorithm_ids=("terrain.streams", "terrain.strahler"),
       geometry_kinds=(_GEO_RASTER,), requires_roles=("elevation",), priority=25,
       output_artifacts=("line_feature_set",)),
    _c("terrain.indices", "terrain_hydrology", "地形指数（TWI/LS）",
       capabilities=("terrain_wetness_indices",),
       algorithm_ids=("terrain.twi", "terrain.ls_factor"),
       geometry_kinds=(_GEO_RASTER,), requires_roles=("elevation",),
       preconditions=("local_metric_crs_required",), priority=30,
       output_artifacts=("terrain_surface",)),
    # ── remote_sensing（遥感解译）───────────────────────────────────
    _c("rs.spectral_index", "remote_sensing", "光谱指数制图",
       capabilities=("spectral_index", "ndvi"),
       algorithm_ids=("remote.spectral_index", "remote.ndvi"),
       geometry_kinds=(_GEO_RASTER,), requires_roles=("subject",),
       preconditions=("raster_band_required:2", "band_semantics_required"),
       priority=10, output_artifacts=("remote_sensing_index",)),
    _c("rs.classification", "remote_sensing", "影像分类",
       capabilities=("image_segmentation",), algorithm_ids=("remote.segmentation",),
       geometry_kinds=(_GEO_RASTER,), requires_roles=("subject",), priority=20,
       output_artifacts=("raster_surface",)),
    _c("rs.anomaly_detection", "remote_sensing", "遥感异常检测",
       capabilities=("rx_anomaly_detection",), algorithm_ids=("remote.rx_anomaly",),
       geometry_kinds=(_GEO_RASTER,), requires_roles=("subject",), priority=40,
       output_artifacts=("raster_surface",)),
    _c("rs.sar_calibration", "remote_sensing", "SAR 辐射定标链",
       capabilities=("sar_radiometric_calibration", "sar_speckle_filtering"),
       algorithm_ids=("sar.radiometric_calibration", "sar.speckle_filter"),
       geometry_kinds=(_GEO_RASTER,), requires_roles=("subject",), priority=30,
       output_artifacts=("raster_surface",)),
    # ── change_detection（变化检测）─────────────────────────────────
    _c("change.bi_temporal_raster", "change_detection", "双时相栅格变化",
       capabilities=("raster_change_detection",),
       algorithm_ids=("remote.change.raster", "remote.cva"),
       geometry_kinds=(_GEO_RASTER,), requires_roles=("baseline", "target_time"),
       priority=10, output_artifacts=("change_set",)),
    _c("change.post_classification", "change_detection", "分类后比较",
       capabilities=("image_segmentation", "raster_change_detection"),
       geometry_kinds=(_GEO_RASTER,), requires_roles=("baseline", "target_time"),
       priority=20, output_artifacts=("change_set", "stats_table")),
    _c("change.temporal_trend", "change_detection", "时序趋势/断点",
       capabilities=("temporal_trend", "temporal_profile"),
       algorithm_ids=("temporal.trend", "temporal.changepoint"),
       preconditions=("temporal_field_required",), priority=25,
       output_artifacts=("stats_table", "chart_spec")),
    _c("change.sar_log_ratio", "change_detection", "SAR 对数比变化",
       capabilities=("sar_coherence",), algorithm_ids=("sar.log_ratio_change",),
       geometry_kinds=(_GEO_RASTER,), requires_roles=("baseline", "target_time"),
       priority=35, output_artifacts=("change_set",)),
    # ── spatial_statistics（空间统计）───────────────────────────────
    _c("stats.global_autocorrelation", "spatial_statistics", "全局空间自相关",
       capabilities=("global_morans_i", "global_gearys_c"),
       algorithm_ids=("stats.morans_i", "stats.gearys_c"),
       requires_roles=("subject", "measure"), min_sample_size=8,
       preconditions=("numeric_field_required", "min_numeric_samples:8"),
       priority=10, output_artifacts=("stats_table",)),
    _c("stats.local_cluster", "spatial_statistics", "局部聚类/热点",
       capabilities=("local_morans_i", "getis_ord_gi_star", "hotspot"),
       algorithm_ids=("spatial.hotspot.local",),
       requires_roles=("subject", "measure"), min_sample_size=20,
       preconditions=("numeric_field_required",), priority=15,
       output_artifacts=("hotspot_result",)),
    _c("stats.spatial_regression", "spatial_statistics", "空间回归",
       capabilities=("spatial_regression", "gwr"),
       algorithm_ids=("spatial.ols_regression", "spatial.gwr"),
       requires_roles=("subject", "measure"), min_sample_size=30,
       preconditions=("numeric_field_required", "min_numeric_samples:30"),
       priority=25, output_artifacts=("stats_table",)),
    _c("stats.weights_diagnostics", "spatial_statistics", "空间权重诊断",
       capabilities=("spatial_weights_diagnostics",),
       algorithm_ids=("stats.weights_diagnostics",),
       requires_roles=("subject", "measure"), priority=30,
       output_artifacts=("stats_table",)),
    # ── multi_criteria（多准则决策）─────────────────────────────────
    _c("mcda.wsm", "multi_criteria", "加权求和多准则评价",
       capabilities=("mcda_evaluation",), algorithm_ids=("decision.mcda.wsm",),
       requires_roles=("criteria",), priority=10,
       output_artifacts=("raster_surface", "stats_table")),
    _c("mcda.risk_exposure", "multi_criteria", "风险-暴露叠合",
       capabilities=("mcda_evaluation", "geometry_overlay"),
       requires_roles=("hazard", "receptor"), priority=20,
       output_artifacts=("raster_surface", "admin_aggregate_table")),
    _c("mcda.vulnerability", "multi_criteria", "脆弱性评价",
       capabilities=("mcda_evaluation",),
       requires_roles=("population", "criteria"), priority=25,
       output_artifacts=("raster_surface",)),
    _c("mcda.spatial_equity", "multi_criteria", "空间公平性评价",
       capabilities=("mcda_evaluation", "rate_aggregation"),
       algorithm_ids=("stats.rate_smoothing",),
       requires_roles=("subject", "boundary", "denominator"), priority=30,
       output_artifacts=("admin_aggregate_table",)),
    # ── compositional_mapping（组合制图）────────────────────────────
    _c("compose.comparison_map", "compositional_mapping", "对比双图",
       capabilities=("admin_aggregation",),
       requires_roles=("baseline", "target_time"), priority=10,
       output_artifacts=("admin_aggregate_table",)),
    _c("compose.statistical_map", "compositional_mapping", "统计地图+图表",
       capabilities=("admin_aggregation", "category_breakdown"),
       algorithm_ids=("stats.category.breakdown",),
       requires_roles=("measure", "boundary"), priority=15,
       output_artifacts=("stats_table", "chart_spec")),
    _c("compose.report_map", "compositional_mapping", "报告主图",
       capabilities=("poi_query",), requires_roles=("subject",), priority=20,
       output_artifacts=("point_feature_set",)),
)


# ── 审定族表（family → 本体任务透镜；task_id 全部注册期校验）─────────────

def _f(family_id: str, label_zh: str, label_en: str, description: str,
       tasks: Tuple[str, ...], methods: Tuple[MethodCandidate, ...]) -> MethodologyFamily:
    return MethodologyFamily(
        family_id=family_id, label_zh=label_zh, label_en=label_en,
        description=description, ontology_task_ids=tasks,
        candidate_methods=methods,
    )


_CURATED_FAMILIES: Tuple[MethodologyFamily, ...] = (
    _f("descriptive_mapping", "描述制图", "Descriptive Mapping",
       "要素/分类的直接地图表达：是什么、在哪里、构成如何。",
       ("distribution.point_distribution", "distribution.category_breakdown",
        "cartographic.report_map"),
       tuple(m for m in _CURATED_CANDIDATES if m.family_id == "descriptive_mapping")),
    _f("distribution_density", "分布与密度", "Distribution & Density",
       "点/事件的空间分布刻画与密度估计：核密度、行政区率、网格聚合。",
       ("distribution.point_distribution", "distribution.density_quantitative",
        "distribution.regional_aggregation", "distribution.ranking_comparison"),
       tuple(m for m in _CURATED_CANDIDATES if m.family_id == "distribution_density")),
    _f("interpolation", "空间插值", "Spatial Interpolation",
       "点观测 → 连续预测面：克里金族、IDW、趋势面与不确定性。",
       ("interpolation.deterministic_surface", "interpolation.geostatistical_kriging",
        "interpolation.regression_kriging", "interpolation.trend_surface",
        "interpolation.uncertainty_surface"),
       tuple(m for m in _CURATED_CANDIDATES if m.family_id == "interpolation")),
    _f("zonal_statistics", "分区统计", "Zonal Statistics",
       "按行政/网格分区的聚合统计与率计算，含跨分区面插值。",
       ("distribution.regional_aggregation", "cartographic.statistical_map",
        "decision.spatial_equity"),
       tuple(m for m in _CURATED_CANDIDATES if m.family_id == "zonal_statistics")),
    _f("suitability", "适宜性分析", "Suitability Analysis",
       "约束过滤 + 因子叠加的选址/适宜性评价。",
       ("decision.suitability", "decision.site_selection"),
       tuple(m for m in _CURATED_CANDIDATES if m.family_id == "suitability")),
    _f("network", "网络分析", "Network Analysis",
       "路径/服务区/可达性/OD/区位配置等网络方法。",
       ("network.route", "network.accessibility", "network.service_area",
        "network.od_analysis", "network.centrality", "network.facility_location"),
       tuple(m for m in _CURATED_CANDIDATES if m.family_id == "network")),
    _f("terrain_hydrology", "地形与水文", "Terrain & Hydrology",
       "DEM 衍生（坡度坡向/地形指数）与水文（流域/河网）方法。",
       ("terrain_hydrology.slope_aspect", "terrain_hydrology.hillshade_viewshed",
        "terrain_hydrology.composite_analysis", "terrain_hydrology.watershed",
        "terrain_hydrology.stream_network", "terrain_hydrology.indices"),
       tuple(m for m in _CURATED_CANDIDATES if m.family_id == "terrain_hydrology")),
    _f("remote_sensing", "遥感解译", "Remote Sensing",
       "光谱指数/分类/异常检测与 SAR 预处理链。",
       ("remote_sensing.spectral_index", "remote_sensing.classification",
        "remote_sensing.anomaly_detection", "remote_sensing.temporal_analysis",
        "sar.radiometric_calibration", "sar.speckle_filtering",
        "sar.interpretation"),
       tuple(m for m in _CURATED_CANDIDATES if m.family_id == "remote_sensing")),
    _f("change_detection", "变化检测", "Change Detection",
       "双时相/时序变化：栅格变化、分类后比较、时序趋势、SAR 变化。",
       ("remote_sensing.change_detection", "remote_sensing.temporal_analysis",
        "cartographic.comparison_map"),
       tuple(m for m in _CURATED_CANDIDATES if m.family_id == "change_detection")),
    _f("spatial_statistics", "空间统计", "Spatial Statistics",
       "空间自相关/局部聚类/热点显著性与空间回归。",
       ("spatial_statistics.global_autocorrelation", "spatial_statistics.local_cluster",
        "spatial_statistics.hotspot_significance", "spatial_statistics.heterogeneity",
        "spatial_statistics.spatial_regression"),
       tuple(m for m in _CURATED_CANDIDATES if m.family_id == "spatial_statistics")),
    _f("multi_criteria", "多准则决策", "Multi-Criteria Decision",
       "多准则/风险-暴露/脆弱性/公平性等综合评价方法。",
       ("decision.multi_criteria", "decision.risk_exposure",
        "decision.vulnerability", "decision.spatial_equity"),
       tuple(m for m in _CURATED_CANDIDATES if m.family_id == "multi_criteria")),
    _f("compositional_mapping", "组合制图", "Compositional Mapping",
       "多产品组合输出：对比图/统计图/报告图的多图层组合。",
       ("cartographic.comparison_map", "cartographic.statistical_map",
        "cartographic.report_map", "distribution.ranking_comparison"),
       tuple(m for m in _CURATED_CANDIDATES if m.family_id == "compositional_mapping")),
)


# ── 注册表（进程内单例，与 RecipeRegistry 同形态）────────────────────────

class MethodologyRegistry:
    """方法论族注册表：审定表载入 + 引用完整性校验 + 查询。

    校验谓词注入（与 workflow_schema.validate_workflow_profile 同模式，避免
    循环 import）：capability/algorithm/artifact 存在性由调用方传入；
    ``validate(strict_vocab=True)`` 时用真实 registry 对账。
    """
    def __init__(self) -> None:
        self._families: Dict[str, MethodologyFamily] = {}
        self._methods: Dict[str, MethodCandidate] = {}
        self._fingerprint: str = ""

    # ── 载入 ──────────────────────────────────────────────────────────
    def load_curated(self) -> None:
        """载入审定族表（幂等：重复载入覆盖同 id）。"""
        for fam in _CURATED_FAMILIES:
            self._families[fam.family_id] = fam
            for m in fam.candidate_methods:
                self._methods[m.method_id] = m
        self._fingerprint = self._compute_fingerprint()

    def _compute_fingerprint(self) -> str:
        payload = json.dumps(
            {"v": METHODOLOGY_SCHEMA_VERSION,
             "families": [
                 self._families[f].model_dump(mode="json")
                 for f in sorted(self._families)
             ]},
            sort_keys=True, ensure_ascii=False, separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    # ── 查询 ──────────────────────────────────────────────────────────
    @property
    def fingerprint(self) -> str:
        return self._fingerprint

    def family(self, family_id: str) -> Optional[MethodologyFamily]:
        return self._families.get(family_id)

    def families(self) -> List[MethodologyFamily]:
        """按 METHODOLOGY_FAMILIES 词表序返回（稳定）。"""
        return [self._families[f] for f in METHODOLOGY_FAMILIES
                if f in self._families]

    def method(self, method_id: str) -> Optional[MethodCandidate]:
        return self._methods.get(method_id)

    def family_for_task(self, task_id: str) -> List[MethodologyFamily]:
        """一个本体任务的方法论族透镜（词表序，稳定）。"""
        return [f for f in self.families() if task_id in f.ontology_task_ids]

    def family_count(self) -> int:
        return len(self._families)

    def method_count(self) -> int:
        return len(self._methods)

    # ── 校验（悬空引用 fatal；registry_validation 消费）────────────────
    def validate(
        self, *,
        ontology_task_exists=None,
        capability_exists=None,
        algorithm_exists=None,
        artifact_type_exists=None,
    ) -> List[str]:
        violations: List[str] = []
        known_tasks: set = set()
        if ontology_task_exists is not None:
            pass
        else:
            from app.services.gis_harness.gis_ontology import ONTOLOGY_TASKS
            known_tasks = {t.task_id for t in ONTOLOGY_TASKS}
        seen_methods: set = set()
        for fam in self._families.values():
            tag = f"methodology[{fam.family_id}]"
            if fam.family_id not in METHODOLOGY_FAMILIES:
                violations.append(f"{tag}: unknown family_id")
            if not fam.candidate_methods:
                violations.append(f"{tag}: no candidate methods")
            for tid in fam.ontology_task_ids:
                if ontology_task_exists is not None:
                    if not ontology_task_exists(tid):
                        violations.append(f"{tag}: ontology task {tid} 不存在")
                elif tid not in known_tasks:
                    violations.append(f"{tag}: ontology task {tid} 不存在")
            for m in fam.candidate_methods:
                mtag = f"{tag}.method[{m.method_id}]"
                if m.method_id in seen_methods:
                    violations.append(f"{mtag}: duplicate method_id")
                seen_methods.add(m.method_id)
                if m.family_id != fam.family_id:
                    violations.append(f"{mtag}: family_id mismatch")
                if capability_exists and not all(
                        capability_exists(c) for c in m.capabilities):
                    missing = [c for c in m.capabilities if not capability_exists(c)]
                    violations.append(f"{mtag}: capability {missing} 不存在")
                if algorithm_exists and not all(
                        algorithm_exists(a) for a in m.algorithm_ids):
                    missing = [a for a in m.algorithm_ids if not algorithm_exists(a)]
                    violations.append(f"{mtag}: algorithm {missing} 不存在")
                if artifact_type_exists and m.output_artifacts and not all(
                        artifact_type_exists(a) for a in m.output_artifacts):
                    missing = [a for a in m.output_artifacts
                               if not artifact_type_exists(a)]
                    violations.append(f"{mtag}: artifact type {missing} 未注册")
                if m.approximate and not m.downgrade_class:
                    violations.append(f"{mtag}: approximate 候选需要 downgrade_class")
                if m.downgrade_class:
                    from app.services.gis_harness.workflow_schema import (
                        DOWNGRADE_CLASSES,
                    )
                    if m.downgrade_class not in DOWNGRADE_CLASSES:
                        violations.append(f"{mtag}: unknown downgrade_class")
                if m.min_sample_size is not None and m.min_sample_size < 1:
                    violations.append(f"{mtag}: min_sample_size 必须 ≥ 1")
        return violations


#: 进程内单例（与 get_recipe_registry 同形态）。
_METHODOLOGY_REGISTRY: Optional[MethodologyRegistry] = None


def get_methodology_registry() -> MethodologyRegistry:
    global _METHODOLOGY_REGISTRY
    if _METHODOLOGY_REGISTRY is None:
        reg = MethodologyRegistry()
        reg.load_curated()
        _METHODOLOGY_REGISTRY = reg
    return _METHODOLOGY_REGISTRY


def reset_methodology_registry() -> None:
    global _METHODOLOGY_REGISTRY
    _METHODOLOGY_REGISTRY = None


def resolve_methodology_family(
    ontology_task_id: str,
) -> Optional[MethodologyFamily]:
    """本体任务 → 主方法族（词表序第一个；确定性）。"""
    matches = get_methodology_registry().family_for_task(ontology_task_id)
    return matches[0] if matches else None


# ── 资格引擎（Wave 2：事实驱动的候选排序，替代任意选择）──────────────────

def _role_fit_score(role_state: str) -> float:
    """角色状态 → 资格分（引用 plan_candidates.DATA_FIT_SCORE 单一事实源）。"""
    from app.services.gis_harness.plan_candidates import DATA_FIT_SCORE
    return DATA_FIT_SCORE.get(role_state, _UNKNOWN_NEUTRAL_SCORE)


def _sample_fact(profile: Optional[Dict[str, Any]]) -> Optional[int]:
    if not profile:
        return None
    n = profile.get("featureCount")
    if isinstance(n, (int, float)):
        return int(n)
    return None


def _geometry_fact(profile: Optional[Dict[str, Any]]) -> Optional[str]:
    if not profile:
        return None
    kinds = profile.get("geometryKinds")
    if isinstance(kinds, list) and kinds:
        return str(kinds[0])
    gts = profile.get("geometryTypes")
    if isinstance(gts, list) and gts:
        from app.services.gis_harness.data_qualification import (
            _geometry_category,
        )
        return _geometry_category([str(g) for g in gts])
    return None


def _evaluate_precondition_pass(
    precondition_id: str, profile: Optional[Dict[str, Any]],
) -> Optional[bool]:
    """委托算法层 precondition（单一事实源）。None = 事实不足（unknown）。"""
    if profile is None:
        return None
    from app.lib.gis.scientific_preconditions import evaluate_precondition
    result = evaluate_precondition(precondition_id, profile)
    if result.facts_used:
        return result.verdict in ("PASS", "PASS_WITH_WARNINGS")
    if result.verdict in ("INSUFFICIENT_DATA", "INVALID_METHOD"):
        return False
    return None


def qualify_method_candidates(
    family_id: str,
    *,
    role_states: Optional[Dict[str, str]] = None,
    profile: Optional[Dict[str, Any]] = None,
    registry: Optional[MethodologyRegistry] = None,
) -> MethodQualificationSet:
    """方法族候选的确定性资格裁决与排序。

    - 硬准则（事实在场才裁决）：几何类别不匹配 / 样本量不足 / 必需角色
      blocked / 算法层 precondition 不满足 → rejected（带稳定 reason code）；
    - 软排序：role_fit(0.40) + method_quality(0.30) + priority(0.20)
      + evidence(0.10)；approximate 候选降质并强制披露；
    - 排序键 (-score, priority, method_id)：全确定性，同输入同序；
    - selected = rank-1 eligible；全 rejected 时 all_rejected=True
      （诚实语义：该方法族在当前事实上不成立）。
    """
    reg = registry or get_methodology_registry()
    fam = reg.family(family_id)
    if fam is None:
        return MethodQualificationSet(family_id=family_id, all_rejected=False)
    states = role_states or {}
    sample = _sample_fact(profile)
    geometry = _geometry_fact(profile)

    quals: List[MethodQualification] = []
    for m in fam.candidate_methods:
        reasons: List[str] = []
        disclosures: List[str] = list(m.disclosures)
        evidence: Dict[str, Any] = {}

        # ── 硬准则 1：几何类别（事实在场才裁决）──────────────────────
        if m.geometry_kinds and geometry is not None:
            evidence["geometry"] = geometry
            if geometry not in m.geometry_kinds:
                reasons.append(
                    f"{REJECT_GEOMETRY}:{geometry}:"
                    f"{'+'.join(m.geometry_kinds)}")

        # ── 硬准则 2：样本量 ──────────────────────────────────────────
        if m.min_sample_size is not None and sample is not None:
            evidence["sample"] = sample
            evidence["min_sample"] = m.min_sample_size
            if sample < m.min_sample_size:
                reasons.append(
                    f"{REJECT_MIN_SAMPLE}:{sample}<{m.min_sample_size}")

        # ── 硬准则 3：必需角色 blocked（unknown/degraded 不拒）────────
        role_fit_values: List[float] = []
        for role in m.requires_roles:
            st = states.get(role, "unknown")
            role_fit_values.append(_role_fit_score(st))
            if st == "blocked":
                from app.services.gis_harness.workflow_schema import (
                    role_reason_code,
                )
                reasons.append(f"{REJECT_ROLE_BLOCKED}:{role}"
                               f":{role_reason_code(role)}")
        role_fit = (
            sum(role_fit_values) / len(role_fit_values)
            if role_fit_values else _UNKNOWN_NEUTRAL_SCORE
        )

        # ── 硬准则 4：算法层 precondition（委托，不重复科学语义）──────
        precondition_score = _UNKNOWN_NEUTRAL_SCORE
        if m.preconditions:
            results = [
                (pid, _evaluate_precondition_pass(pid, profile))
                for pid in m.preconditions
            ]
            evidence["preconditions"] = {
                pid: ("unknown" if p is None else ("pass" if p else "fail"))
                for pid, p in results
            }
            precondition_score = (
                1.0 if all(p is True for _, p in results)
                else (0.0 if any(p is False for _, p in results)
                      else _UNKNOWN_NEUTRAL_SCORE)
            )
            for pid, p in results:
                if p is False:
                    reasons.append(f"{REJECT_PRECONDITION}:{pid}")

        # ── 软排序分量 ─────────────────────────────────────────────────
        method_quality = (
            0.55 if m.approximate else
            (0.8 if m.downgrade_class else 1.0)
        )
        priority_norm = max(0.0, min(1.0, (100 - m.priority) / 100.0))
        score = (
            0.40 * role_fit + 0.30 * method_quality
            + 0.20 * priority_norm + 0.10 * precondition_score
        )
        if m.approximate and not any("近似" in d or "代理" in d for d in disclosures):
            disclosures.append("近似/代理方法：结果语义弱于首选方法，须显式披露。")

        quals.append(MethodQualification(
            method_id=m.method_id, family_id=family_id,
            status="eligible", score=round(score, 6),
            reason_codes=reasons, disclosures=disclosures, evidence=evidence,
        ))

    # rejected / eligible 划分 + 全序（确定性：score desc → priority → id）
    eligible: List[MethodQualification] = []
    rejected: List[MethodQualification] = []
    for q in quals:
        (rejected if q.reason_codes else eligible).append(q)
    # 排序键需要 priority —— 从注册表取回（q 上不冗余存）。
    def _sort_key(q: MethodQualification) -> Tuple[float, int, str]:
        m = reg.method(q.method_id)
        return (-q.score, m.priority if m else 50, q.method_id)
    eligible.sort(key=_sort_key)
    rejected.sort(key=_sort_key)
    eligible_ids = {q.method_id for q in eligible}
    ordered = eligible + rejected
    for i, q in enumerate(ordered, start=1):
        q.rank = i
        if q.method_id not in eligible_ids:
            q.status = "rejected"
        elif i == 1:
            q.status = "selected"
    return MethodQualificationSet(
        family_id=family_id,
        qualifications=ordered,
        selected_id=(eligible[0].method_id if eligible else ""),
        all_rejected=bool(quals) and not eligible,
    )
