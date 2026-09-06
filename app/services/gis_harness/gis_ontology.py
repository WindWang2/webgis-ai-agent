"""GIS Task Ontology V3 —— 版本化、可机器消费的 GIS 任务本体（Goal V3）。

把「用户需求 → GIS 任务语义」从扁平 task family 词表升级为结构化本体：
每个任务类型（TaskDescriptor）声明它需要什么数据角色、期望什么几何、
常用什么能力、产出什么 artifact、期望什么制图表达、有什么歧义、
缺数据时按什么顺序降级。

红线（与 workflow_schema / recipes 一致）：

- 本体声明「这类 GIS 任务是什么」，不实现算法、不硬编码工具序列 ——
  capability / artifact / map model 引用经 registry 校验，单一事实源；
- 全部确定性：同输入同输出，零 LLM、零 I/O；匹配是打分不是生成；
- 引用不复制：obligations / preconditions 仍在算法层，本体只声明
  「哪类任务通常带哪些科学义务主题」，不重复科学语义；
- 未实现的任务类型诚实声明 ``semantic_status="planned"``，路由与规划
  不得把 planned 当 native（fallback V3 Blocked 层的事实来源之一）。

消费方：

- workflow compiler 阶段 2（map_task_ontology）：intent → 有序任务匹配；
- Recipe V3 路由：recipe.ontology_tasks 声明其服务的本体任务（opt-in，
  零声明零行为变化 —— 既有 164 recipe 排序不受影响）；
- Typed Planning Pipeline：候选生成按本体任务的 fallback_strategy 与
  cartographic_expectations 展开（plan_candidates 阶段）；
- Data Qualification：required_data_roles / geometry_expectations 是
  per-step 资格判定的需求来源。
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field

#: 本体版本：结构或语义变化必须提升（进入指纹，可被 conformance 锁定）。
ONTOLOGY_VERSION = 3

#: 本体域（9 个顶层域，稳定词表；新增域是纯加法）。
ONTOLOGY_DOMAINS = (
    "distribution",        # 分布 / 描述统计 / 区域聚合
    "spatial_statistics",  # 空间统计（自相关 / 热点 / 异质性 / 回归）
    "interpolation",       # 插值 / 预测面 / 不确定性
    "network",             # 路径 / 可达 / OD / 选址
    "terrain_hydrology",   # 地形衍生 / 水文
    "remote_sensing",      # 光谱 / 分类 / 变化 / 时序
    "sar",                 # 雷达（定标 / 去噪 / 相干 / 时序）
    "decision",            # 多准则决策（选址 / 适宜 / 风险 / 脆弱）
    "cartographic",        # 制图产品型任务（对比图 / 统计图 / 报告图）
)

#: 语义实现状态。planned = 能力未注册 / 部分注册，规划不得假称 native。
SEMANTIC_STATUSES = ("native", "planned")

_FALLBACK_TIERS = ("preferred", "degraded", "minimal", "blocked")


class AmbiguityRule(BaseModel):
    """歧义声明：同一表述的多种解释与消歧依据（机器可读，供披露/规划）。"""
    signal: str                 # 人类可读的歧义条件（审计用）
    default_resolution: str     # 默认解释（确定性，不问用户时走这条）
    alternatives: Tuple[str, ...] = ()   # 备选解释（task_id 或制图语义描述）
    disambiguator: str = ""     # 什么证据可消歧（如 measure 字段在场）


class FallbackTier(BaseModel):
    """任务级 fallback 策略的一层（Fallback V3 的本体侧声明）。

    tier 语义（有序，向前降级）：
    - preferred：数据充足、能力 native —— 最专业输出；
    - degraded：数据不完整仍可给近似结果，必须显式披露；
    - minimal：仅安全、真实、不误导的描述性输出；
    - blocked：不能科学执行 —— 声明缺什么（不虚构）。
    """
    tier: str                              # ⊆ _FALLBACK_TIERS
    condition: str                         # 触发条件（机器可读语义描述）
    capabilities: Tuple[str, ...] = ()     # 该层可用的能力（存在性校验）
    cartography: Tuple[str, ...] = ()      # 该层制图表达（MapModel id）
    disclosure: str = ""                   # degraded/minimal 必须披露的语义
    blocks_completion: bool = False        # blocked 层恒 True


class TaskDescriptor(BaseModel):
    """一个 GIS 任务类型的本体描述（版本化、可序列化、可校验）。"""
    task_id: str                       # 稳定 id："<domain>.<task>"（如 distribution.point_distribution）
    domain: str                        # ⊆ ONTOLOGY_DOMAINS
    label_zh: str
    label_en: str
    description: str = ""
    semantic_status: str = "native"    # ⊆ SEMANTIC_STATUSES

    # ── 数据需求（Data Qualification 的需求来源）─────────────────────
    required_data_roles: Tuple[str, ...] = ()     # ⊆ workflow_schema.DATA_ROLES
    optional_data_roles: Tuple[str, ...] = ()
    geometry_expectations: Tuple[str, ...] = ()   # ⊆ point/line/polygon/raster/table/network/unknown

    # ── 方法与产出（引用，不复制）────────────────────────────────────
    common_capabilities: Tuple[str, ...] = ()     # ⊆ CapabilityRegistry（存在性校验）
    output_artifacts: Tuple[str, ...] = ()        # ⊆ ArtifactTypeRegistry（存在性校验）
    cartographic_expectations: Tuple[str, ...] = ()  # ⊆ MapModelRegistry（存在性校验）
    component_expectations: Tuple[str, ...] = ()  # 组件类型（如 legend/title/chart_panel）

    # ── 歧义与回退 ──────────────────────────────────────────────────
    ambiguity_rules: Tuple[AmbiguityRule, ...] = ()
    fallback_strategy: Tuple[FallbackTier, ...] = ()

    # ── 与既有系统的联动（引用既有词表，不另造第二套任务族）──────────
    family_triggers: Tuple[str, ...] = ()          # ⊆ intent.TaskType（直接映射）
    analysis_intent_triggers: Tuple[str, ...] = () # ⊆ intent.AnalysisIntent
    cartography_triggers: Tuple[str, ...] = ()     # ⊆ intent.CartographyIntent
    keywords_zh: Tuple[str, ...] = ()              # 语义匹配词（紧词表，防过匹配）
    keywords_en: Tuple[str, ...] = ()


class OntologyMatch(BaseModel):
    """一次 intent → 本体任务的确定性匹配结果（打分，非生成）。"""
    task_id: str
    domain: str
    score: float = 0.0
    semantic_status: str = "native"
    matched_signals: List[str] = Field(default_factory=list)

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id[:64],
            "domain": self.domain[:32],
            "score": round(self.score, 2),
            "semantic_status": self.semantic_status,
            "matched_signals": [s[:48] for s in self.matched_signals[:6]],
        }


# ── 本体登记表 ───────────────────────────────────────────────────────────
#
# 审定工件：每条 descriptor 的 capability / artifact / map model 引用由
# validate_ontology 与 registry 单一事实源对账；family_triggers 引用
# intent.TaskType 词表。新增任务 = 追加条目（纯加法），不改动既有条目。


def _t(**kw: Any) -> TaskDescriptor:
    return TaskDescriptor(**kw)


ONTOLOGY_TASKS: Tuple[TaskDescriptor, ...] = (
    # ── distribution / descriptive ───────────────────────────────────
    _t(
        task_id="distribution.point_distribution", domain="distribution",
        label_zh="点分布概览", label_en="Point distribution overview",
        description="主体点要素的空间分布表达；制图形态由数据资格决定"
                    "（点图/分级符号/聚合格网/密度），不固定为热力图。",
        required_data_roles=("subject",),
        optional_data_roles=("boundary",),
        geometry_expectations=("point",),
        common_capabilities=("poi_query", "point_profile"),
        output_artifacts=("poi_feature_set", "point_feature_set", "stats_table"),
        cartographic_expectations=(
            "simple_point_map", "visual_heatmap", "aggregate_grid",
            "proportional_symbol", "point_cluster", "point_overlay",
        ),
        component_expectations=("legend", "title"),
        ambiguity_rules=(
            AmbiguityRule(
                signal="「分布情况」未指明形态与统计意图",
                default_resolution="点分布概览（形态按数据资格选择，不预设热力）",
                alternatives=("密度面", "区域聚合统计", "聚类结构"),
                disambiguator="样本量、边界数据在场、measure 字段在场",
            ),
        ),
        fallback_strategy=(
            FallbackTier(
                tier="preferred", condition="点样本充足且边界可用",
                cartography=("aggregate_grid", "proportional_symbol", "visual_heatmap"),
            ),
            FallbackTier(
                tier="degraded", condition="点样本不足以支撑密度/聚合",
                cartography=("simple_point_map",),
                disclosure="样本不足：降级为点分布展示，不做密度推断。",
            ),
            FallbackTier(
                tier="minimal", condition="主体数据缺失或非点几何",
                cartography=(),
                disclosure="仅提供描述性统计与数据概况，不绘制推断性专题图。",
            ),
            FallbackTier(tier="blocked", condition="无主体数据且无获取通道",
                         blocks_completion=True),
        ),
        family_triggers=("distribution_overview", "simple_view"),
        analysis_intent_triggers=("spatial_distribution", "profile"),
        cartography_triggers=("point_overlay", "density_overview", "simple_point_map"),
        keywords_zh=("分布情况", "分布", "在哪里", "点位"),
        keywords_en=("distribution", "where are"),
    ),
    _t(
        task_id="distribution.regional_aggregation", domain="distribution",
        label_zh="区域聚合统计", label_en="Regional aggregation",
        description="把主体要素聚合到行政/自然单元并产出计数/占比统计。",
        required_data_roles=("subject", "boundary"),
        optional_data_roles=("denominator",),
        geometry_expectations=("point", "polygon"),
        common_capabilities=("poi_query", "admin_boundary_query", "admin_aggregation"),
        output_artifacts=("admin_aggregate_table", "stats_table"),
        cartographic_expectations=("administrative_choropleth", "aggregate_grid"),
        component_expectations=("legend", "title", "chart_panel"),
        fallback_strategy=(
            FallbackTier(tier="preferred", condition="边界与主体齐备",
                         cartography=("administrative_choropleth",)),
            FallbackTier(tier="degraded", condition="边界缺失，格网聚合替代行政聚合",
                         capabilities=("grid_binning",),
                         cartography=("aggregate_grid",),
                         disclosure="行政边界缺失：以格网聚合近似表达空间分异。"),
            FallbackTier(tier="minimal", condition="仅主体数据",
                         cartography=(),
                         disclosure="仅输出计数与描述统计。"),
            FallbackTier(tier="blocked", condition="无主体数据",
                         blocks_completion=True),
        ),
        family_triggers=("administrative_statistic", "distribution_overview"),
        analysis_intent_triggers=("administrative_aggregation", "administrative_summary"),
        cartography_triggers=("administrative_choropleth",),
        keywords_zh=("各区", "各街道", "各乡镇", "按区统计", "各区县"),
        keywords_en=("by district", "per district"),
    ),
    _t(
        task_id="distribution.density_quantitative", domain="distribution",
        label_zh="定量密度", label_en="Quantitative density",
        description="单位面积定量密度（如 个/km²）：需要面积/人口分母归一化。",
        required_data_roles=("subject", "boundary", "denominator"),
        geometry_expectations=("point", "polygon"),
        common_capabilities=("admin_aggregation", "rate_aggregation", "point_profile"),
        output_artifacts=("admin_aggregate_table", "stats_table"),
        cartographic_expectations=("normalized_choropleth", "administrative_choropleth"),
        component_expectations=("legend", "title", "continuous_colorbar"),
        fallback_strategy=(
            FallbackTier(tier="preferred", condition="分母（面积/人口）在场",
                         cartography=("normalized_choropleth",)),
            FallbackTier(tier="degraded", condition="分母缺失",
                         cartography=("aggregate_grid",),
                         disclosure="缺少归一化分母：不得输出每平方公里密度结论，"
                                    "以格网计数近似表达。"),
            FallbackTier(tier="minimal", condition="仅计数可用",
                         disclosure="仅输出计数统计。"),
            FallbackTier(tier="blocked", condition="无主体数据",
                         blocks_completion=True),
        ),
        family_triggers=("analytical_density",),
        analysis_intent_triggers=("analytical_density", "administrative_aggregation"),
        cartography_triggers=("administrative_choropleth",),
        keywords_zh=("密度", "每平方公里", "单位面积"),
        keywords_en=("density per", "population density"),
    ),
    _t(
        task_id="distribution.category_breakdown", domain="distribution",
        label_zh="分类构成", label_en="Categorical breakdown",
        description="按类别字段的构成/占比统计与分类专题表达。",
        required_data_roles=("subject",),
        optional_data_roles=("boundary",),
        geometry_expectations=("point", "polygon"),
        common_capabilities=("poi_query", "category_breakdown", "admin_aggregation"),
        output_artifacts=("stats_table", "chart_spec", "admin_aggregate_table"),
        cartographic_expectations=("categorical_thematic", "administrative_choropleth"),
        component_expectations=("legend", "title", "chart_panel"),
        fallback_strategy=(
            FallbackTier(tier="preferred", condition="类别字段在场",
                         cartography=("categorical_thematic",)),
            FallbackTier(tier="degraded", condition="类别字段缺失",
                         disclosure="缺少类别字段：仅输出总体计数。"),
            FallbackTier(tier="minimal", condition="仅主体数据"),
            FallbackTier(tier="blocked", condition="无主体数据",
                         blocks_completion=True),
        ),
        family_triggers=("categorical_distribution",),
        analysis_intent_triggers=("category_breakdown",),
        cartography_triggers=("categorical_thematic",),
        keywords_zh=("占比", "构成", "各类", "分类"),
        keywords_en=("by category", "share of"),
    ),
    _t(
        task_id="distribution.ranking_comparison", domain="distribution",
        label_zh="排名对比", label_en="Ranking & comparison",
        description="跨区域/类别的数量或比率排名与对比表达。",
        required_data_roles=("subject", "boundary"),
        optional_data_roles=("denominator", "comparison_time"),
        geometry_expectations=("polygon", "point"),
        common_capabilities=("admin_aggregation", "rate_aggregation"),
        output_artifacts=("admin_aggregate_table", "chart_spec"),
        cartographic_expectations=("diverging_choropleth", "bivariate_choropleth",
                                   "proportional_symbol"),
        component_expectations=("legend", "title", "chart_panel"),
        fallback_strategy=(
            FallbackTier(tier="preferred", condition="多单元统计数据齐备",
                         cartography=("diverging_choropleth",)),
            FallbackTier(tier="degraded", condition="单元不足",
                         disclosure="统计单元过少：仅输出排名表，不填色。"),
            FallbackTier(tier="minimal", condition="仅总体计数"),
            FallbackTier(tier="blocked", condition="无主体数据",
                         blocks_completion=True),
        ),
        family_triggers=("administrative_statistic", "distribution_overview"),
        analysis_intent_triggers=("administrative_summary",),
        cartography_triggers=("administrative_choropleth",),
        keywords_zh=("排名", "最多", "最少", "对比"),
        keywords_en=("ranking", "compare", "top"),
    ),
    # ── spatial statistics ───────────────────────────────────────────
    _t(
        task_id="spatial_statistics.global_autocorrelation", domain="spatial_statistics",
        label_zh="全局空间自相关", label_en="Global spatial autocorrelation",
        description="Global Moran / Geary / General G：整体空间格局显著性。",
        required_data_roles=("subject", "measure"),
        optional_data_roles=("boundary",),
        geometry_expectations=("polygon", "point"),
        common_capabilities=("global_morans_i", "global_gearys_c", "general_g"),
        output_artifacts=("stats_table",),
        cartographic_expectations=("diverging_choropleth",),
        component_expectations=("title", "statistics_panel"),
        ambiguity_rules=(
            AmbiguityRule(
                signal="「自相关」未指明 global/local",
                default_resolution="先全局后局部（global 判显著再局部定位）",
                alternatives=("local_cluster",),
                disambiguator="query 明示 莫兰指数(全局) / LISA(局部)",
            ),
        ),
        fallback_strategy=(
            FallbackTier(tier="preferred", condition="measure 字段在场且样本充足",
                         capabilities=("global_morans_i",)),
            FallbackTier(tier="degraded", condition="样本量临界",
                         disclosure="样本量偏小：p 值解释需保守。"),
            FallbackTier(tier="minimal", condition="无 measure 字段",
                         disclosure="缺少数值字段：不能执行自相关，仅描述分布。"),
            FallbackTier(tier="blocked", condition="无空间单元或字段",
                         blocks_completion=True),
        ),
        family_triggers=("spatial_autocorrelation",),
        analysis_intent_triggers=("autocorrelation_analysis",),
        cartography_triggers=(),
        keywords_zh=("莫兰", "自相关", "全局空间"),
        keywords_en=("moran", "autocorrelation", "global spatial"),
    ),
    _t(
        task_id="spatial_statistics.local_cluster", domain="spatial_statistics",
        label_zh="局部聚类（LISA）", label_en="Local cluster (LISA)",
        description="Local Moran / Local Geary：局部聚集/离群定位。",
        required_data_roles=("subject", "measure"),
        optional_data_roles=("boundary",),
        geometry_expectations=("polygon", "point"),
        common_capabilities=("local_morans_i", "local_gearys_c"),
        output_artifacts=("hotspot_result",),
        cartographic_expectations=("hotspot_overlay", "diverging_choropleth"),
        component_expectations=("legend", "title"),
        fallback_strategy=(
            FallbackTier(tier="preferred", condition="measure 字段在场且样本充足",
                         cartography=("hotspot_overlay",)),
            FallbackTier(tier="degraded", condition="样本量临界",
                         disclosure="样本量偏小：局部聚类仅作探索性参考。"),
            FallbackTier(tier="minimal", condition="无 measure 字段",
                         disclosure="缺少数值字段：不能执行局部聚类。"),
            FallbackTier(tier="blocked", condition="无空间单元或字段",
                         blocks_completion=True),
        ),
        family_triggers=("spatial_autocorrelation",),
        analysis_intent_triggers=("autocorrelation_analysis",),
        cartography_triggers=("hotspot_overlay",),
        keywords_zh=("lisa", "局部自相关", "局部聚集"),
        keywords_en=("lisa", "local moran", "local cluster"),
    ),
    _t(
        task_id="spatial_statistics.hotspot_significance", domain="spatial_statistics",
        label_zh="显著性热点（Gi*）", label_en="Hotspot significance (Gi*)",
        description="Getis-Ord Gi* 统计显著性热点；视觉热力不是显著性结论。",
        required_data_roles=("subject", "measure"),
        optional_data_roles=("boundary",),
        geometry_expectations=("point", "polygon"),
        common_capabilities=("getis_ord_gi_star", "hotspot"),
        output_artifacts=("hotspot_result",),
        cartographic_expectations=("hotspot_overlay",),
        component_expectations=("legend", "title"),
        ambiguity_rules=(
            AmbiguityRule(
                signal="「热点/哪里密」可能是视觉密度诉求而非统计检验",
                default_resolution="数据满足统计前提时执行 Gi*，否则视觉密度并披露",
                alternatives=("point_distribution",),
                disambiguator="measure 字段、样本量、边界数据",
            ),
        ),
        fallback_strategy=(
            FallbackTier(tier="preferred", condition="统计前提满足",
                         cartography=("hotspot_overlay",)),
            FallbackTier(tier="degraded", condition="统计前提不满足",
                         cartography=("visual_heatmap",),
                         disclosure="统计前提不足：以视觉密度表达疏密，"
                                    "不构成显著性结论。"),
            FallbackTier(tier="minimal", condition="仅点分布可用",
                         cartography=("simple_point_map",),
                         disclosure="仅展示点位分布。"),
            FallbackTier(tier="blocked", condition="无主体数据",
                         blocks_completion=True),
        ),
        family_triggers=("concentration_analysis", "spatial_autocorrelation"),
        analysis_intent_triggers=("hotspot", "kde_density"),
        cartography_triggers=("hotspot_overlay",),
        keywords_zh=("热点", "显著性", "getis", "gi*"),
        keywords_en=("hotspot", "getis ord"),
    ),
    _t(
        task_id="spatial_statistics.heterogeneity", domain="spatial_statistics",
        label_zh="空间异质性", label_en="Spatial heterogeneity",
        description="地理探测器 q 统计等：解释变量的空间分层异质性。",
        required_data_roles=("subject", "measure"),
        optional_data_roles=("criteria",),
        geometry_expectations=("polygon", "point"),
        common_capabilities=("geographical_detector",),
        output_artifacts=("stats_table",),
        cartographic_expectations=("bivariate_choropleth",),
        component_expectations=("title", "statistics_panel"),
        fallback_strategy=(
            FallbackTier(tier="preferred", condition="分层变量与响应变量在场",
                         capabilities=("geographical_detector",)),
            FallbackTier(tier="degraded", condition="分层变量不足",
                         disclosure="分层变量不足：仅输出描述统计。"),
            FallbackTier(tier="minimal", condition="仅描述统计可用"),
            FallbackTier(tier="blocked", condition="无数据",
                         blocks_completion=True),
        ),
        family_triggers=("spatial_autocorrelation",),
        analysis_intent_triggers=("autocorrelation_analysis",),
        cartography_triggers=(),
        keywords_zh=("地理探测器", "异质性", "分层异质"),
        keywords_en=("geodetector", "heterogeneity", "q statistic"),
    ),
    _t(
        task_id="spatial_statistics.spatial_regression", domain="spatial_statistics",
        label_zh="空间回归", label_en="Spatial regression",
        description="GWR 等空间回归：关系随空间变化的分析。",
        required_data_roles=("subject", "measure"),
        optional_data_roles=("criteria",),
        geometry_expectations=("polygon", "point"),
        common_capabilities=("gwr", "spatial_regression"),
        output_artifacts=("stats_table", "raster_surface"),
        cartographic_expectations=("bivariate_choropleth", "raster_surface"),
        component_expectations=("title", "statistics_panel", "continuous_colorbar"),
        fallback_strategy=(
            FallbackTier(tier="preferred", condition="解释/响应变量在场且样本充足",
                         capabilities=("gwr",)),
            FallbackTier(tier="degraded", condition="样本量临界",
                         disclosure="样本量偏小：GWR 结果需谨慎解释。"),
            FallbackTier(tier="minimal", condition="退化到全局回归/描述统计",
                         disclosure="样本不足：退化为描述统计。"),
            FallbackTier(tier="blocked", condition="无数值字段",
                         blocks_completion=True),
        ),
        family_triggers=("spatial_autocorrelation",),
        analysis_intent_triggers=("autocorrelation_analysis",),
        cartography_triggers=(),
        keywords_zh=("地理加权回归", "gwr", "空间回归"),
        keywords_en=("gwr", "geographically weighted", "spatial regression"),
    ),
    # ── interpolation / surface ──────────────────────────────────────
    _t(
        task_id="interpolation.deterministic_surface", domain="interpolation",
        label_zh="确定性插值面", label_en="Deterministic interpolation surface",
        description="IDW / TIN 等确定性插值生成连续表面（无统计假设声明）。",
        required_data_roles=("subject", "measure"),
        geometry_expectations=("point",),
        common_capabilities=("spatial_interpolation", "triangulation_interpolation"),
        output_artifacts=("raster_surface", "density_surface"),
        cartographic_expectations=("raster_surface", "isoline_contour"),
        component_expectations=("continuous_colorbar", "title"),
        fallback_strategy=(
            FallbackTier(tier="preferred", condition="数值样本充足且分布合理",
                         cartography=("raster_surface",)),
            FallbackTier(tier="degraded", condition="样本空间分布偏斜",
                         disclosure="样本空间分布偏斜：插值面边缘外推不可信，已限制。"),
            FallbackTier(tier="minimal", condition="样本过少",
                         disclosure="样本过少：不生成插值面，仅输出样本点位图。"),
            FallbackTier(tier="blocked", condition="无数值字段",
                         blocks_completion=True),
        ),
        family_triggers=("raster_distribution",),
        analysis_intent_triggers=(),
        cartography_triggers=("raster_surface",),
        keywords_zh=("插值", "反距离权重", "idw", "tin"),
        keywords_en=("idw", "interpolation surface", "tin interpolation"),
    ),
    _t(
        task_id="interpolation.geostatistical_kriging", domain="interpolation",
        label_zh="克里金插值", label_en="Kriging",
        description="地统计克里金：半变异建模 + 最优线性无偏预测。",
        required_data_roles=("subject", "measure"),
        geometry_expectations=("point",),
        common_capabilities=("spatial_interpolation", "interpolation_model_selection"),
        output_artifacts=("raster_surface",),
        cartographic_expectations=("raster_surface", "uncertainty_surface"),
        component_expectations=("continuous_colorbar", "title", "statistics_panel"),
        fallback_strategy=(
            FallbackTier(tier="preferred", condition="样本充足（≥8）且数值字段在场",
                         cartography=("raster_surface",)),
            FallbackTier(tier="degraded", condition="样本临界",
                         disclosure="样本量不足：克里金假设不稳，降级确定性插值并披露。"),
            FallbackTier(tier="minimal", condition="样本极少",
                         disclosure="不生成表面：仅输出点位数值图。"),
            FallbackTier(tier="blocked", condition="无数值字段/投影不合规且无法变换",
                         blocks_completion=True),
        ),
        family_triggers=("raster_distribution",),
        analysis_intent_triggers=(),
        cartography_triggers=("raster_surface",),
        keywords_zh=("克里金", "克里金插值", "kriging", "协克里金", "半变异"),
        keywords_en=("kriging", "semivariogram", "geostatistical"),
    ),
    _t(
        task_id="interpolation.regression_kriging", domain="interpolation",
        label_zh="回归克里金", label_en="Regression kriging",
        description="环境协变量辅助的混合地统计插值。",
        required_data_roles=("subject", "measure"),
        optional_data_roles=("criteria",),
        geometry_expectations=("point", "raster"),
        common_capabilities=("regression_kriging",),
        output_artifacts=("raster_surface",),
        cartographic_expectations=("raster_surface",),
        component_expectations=("continuous_colorbar", "title"),
        fallback_strategy=(
            FallbackTier(tier="preferred", condition="协变量栅格在场",
                         capabilities=("regression_kriging",)),
            FallbackTier(tier="degraded", condition="协变量缺失，退化普通克里金",
                         disclosure="协变量缺失：退化为普通克里金。"),
            FallbackTier(tier="minimal", condition="确定性插值替代",
                         disclosure="样本不足：确定性插值替代。"),
            FallbackTier(tier="blocked", condition="无数值字段",
                         blocks_completion=True),
        ),
        family_triggers=("raster_distribution",),
        analysis_intent_triggers=(),
        cartography_triggers=("raster_surface",),
        keywords_zh=("回归克里金", "协同克里金"),
        keywords_en=("regression kriging",),
    ),
    _t(
        task_id="interpolation.trend_surface", domain="interpolation",
        label_zh="趋势面分析", label_en="Trend surface analysis",
        description="多项式趋势面：全局空间趋势的低阶近似。",
        required_data_roles=("subject", "measure"),
        geometry_expectations=("point",),
        common_capabilities=("trend_surface",),
        output_artifacts=("raster_surface",),
        cartographic_expectations=("raster_surface", "isoline_contour"),
        component_expectations=("continuous_colorbar", "title"),
        fallback_strategy=(
            FallbackTier(tier="preferred", condition="样本充足",
                         cartography=("raster_surface",)),
            FallbackTier(tier="degraded", condition="样本偏少",
                         disclosure="样本偏少：仅低阶趋势，局部波动不可辨。"),
            FallbackTier(tier="minimal", condition="仅描述统计"),
            FallbackTier(tier="blocked", condition="无数值字段",
                         blocks_completion=True),
        ),
        family_triggers=("raster_distribution",),
        analysis_intent_triggers=(),
        cartography_triggers=("raster_surface",),
        keywords_zh=("趋势面",),
        keywords_en=("trend surface",),
    ),
    _t(
        task_id="interpolation.uncertainty_surface", domain="interpolation",
        label_zh="预测不确定性面", label_en="Prediction uncertainty surface",
        description="插值预测方差/不确定性的显式表达（与预测面成对出现）。",
        required_data_roles=("subject", "measure"),
        geometry_expectations=("point",),
        common_capabilities=("spatial_interpolation",),
        output_artifacts=("raster_surface",),
        cartographic_expectations=("uncertainty_surface", "uncertainty_choropleth"),
        component_expectations=("continuous_colorbar", "title"),
        fallback_strategy=(
            FallbackTier(tier="preferred", condition="克里金方差可得",
                         cartography=("uncertainty_surface",)),
            FallbackTier(tier="degraded", condition="仅确定性插值可用",
                         disclosure="确定性插值无预测方差：以样本密度近似表达可信度。"),
            FallbackTier(tier="minimal", condition="仅文字披露局限"),
            FallbackTier(tier="blocked", condition="无插值可行数据",
                         blocks_completion=True),
        ),
        family_triggers=("raster_distribution",),
        analysis_intent_triggers=(),
        cartography_triggers=("raster_surface",),
        keywords_zh=("不确定性", "预测方差", "误差面"),
        keywords_en=("uncertainty", "prediction variance"),
    ),
    # ── network ──────────────────────────────────────────────────────
    _t(
        task_id="network.route", domain="network",
        label_zh="路径规划", label_en="Routing",
        description="最短路径 / 最近设施 / 次优路径。",
        required_data_roles=("subject", "network"),
        geometry_expectations=("line", "point"),
        common_capabilities=("shortest_path", "closest_facility", "route_optimization"),
        output_artifacts=("line_feature_set", "network_graph"),
        cartographic_expectations=("route_map", "categorized_line"),
        component_expectations=("legend", "title"),
        fallback_strategy=(
            FallbackTier(tier="preferred", condition="路网在场",
                         cartography=("route_map",)),
            FallbackTier(tier="degraded", condition="路网缺失，直线/欧氏近似",
                         cartography=("proximity_overlay",),
                         disclosure="路网缺失：以欧氏距离近似，非实际通行路径。"),
            FallbackTier(tier="minimal", condition="仅点位",
                         disclosure="仅输出起终点与直线距离。"),
            FallbackTier(tier="blocked", condition="无起终点数据",
                         blocks_completion=True),
        ),
        family_triggers=("network_route", "proximity_analysis"),
        analysis_intent_triggers=("route_analysis",),
        cartography_triggers=(),
        keywords_zh=("最短路径", "路径规划", "导航", "最近设施"),
        keywords_en=("shortest path", "route", "navigation"),
    ),
    _t(
        task_id="network.accessibility", domain="network",
        label_zh="可达性分析", label_en="Accessibility analysis",
        description="等时圈 / 重力模型 / 公交可达。",
        required_data_roles=("subject", "network"),
        optional_data_roles=("population",),
        geometry_expectations=("point", "line", "polygon"),
        common_capabilities=("accessibility", "gravity_accessibility", "service_area",
                             "transit_routing"),
        output_artifacts=("service_area", "stats_table"),
        cartographic_expectations=("service_area_overlay", "accessibility_network",
                                   "normalized_choropleth"),
        component_expectations=("legend", "title"),
        fallback_strategy=(
            FallbackTier(tier="preferred", condition="路网与供给点齐备",
                         cartography=("service_area_overlay",)),
            FallbackTier(tier="degraded", condition="路网缺失，缓冲区近似",
                         capabilities=("proximity_buffer", "multi_ring_buffer"),
                         cartography=("proximity_overlay",),
                         disclosure="路网缺失：以缓冲区近似等时圈。"),
            FallbackTier(tier="minimal", condition="仅供给点分布"),
            FallbackTier(tier="blocked", condition="无供给/需求数据",
                         blocks_completion=True),
        ),
        family_triggers=("accessibility_analysis",),
        analysis_intent_triggers=("service_area",),
        cartography_triggers=("proximity_overlay",),
        keywords_zh=("可达性", "等时圈", "15分钟", "服务圈", "覆盖范围"),
        keywords_en=("accessibility", "isochrone", "service area", "coverage"),
    ),
    _t(
        task_id="network.service_area", domain="network",
        label_zh="服务区分析", label_en="Service area",
        description="设施服务半径/服务区划分与覆盖评价。",
        required_data_roles=("subject", "network"),
        optional_data_roles=("population",),
        geometry_expectations=("point", "polygon"),
        common_capabilities=("service_area", "multi_ring_buffer"),
        output_artifacts=("service_area",),
        cartographic_expectations=("service_area_overlay", "proximity_overlay"),
        component_expectations=("legend", "title"),
        fallback_strategy=(
            FallbackTier(tier="preferred", condition="网络服务区可得",
                         cartography=("service_area_overlay",)),
            FallbackTier(tier="degraded", condition="缓冲区近似",
                         disclosure="以缓冲区近似服务区（未考虑路网阻隔）。"),
            FallbackTier(tier="minimal", condition="仅设施点图"),
            FallbackTier(tier="blocked", condition="无设施数据",
                         blocks_completion=True),
        ),
        family_triggers=("accessibility_analysis", "proximity_analysis"),
        analysis_intent_triggers=("service_area", "proximity_buffer"),
        cartography_triggers=("proximity_overlay",),
        keywords_zh=("服务区", "服务半径", "覆盖"),
        keywords_en=("service area",),
    ),
    _t(
        task_id="network.od_analysis", domain="network",
        label_zh="OD 流动分析", label_en="OD flow analysis",
        description="出行/通勤 OD 矩阵构建与流线表达。",
        required_data_roles=("subject",),
        optional_data_roles=("network",),
        geometry_expectations=("point", "line"),
        common_capabilities=("od_matrix", "od_flow_mapping", "spatial_interaction"),
        output_artifacts=("od_matrix", "od_table", "line_feature_set"),
        cartographic_expectations=("flow_od_arc",),
        component_expectations=("legend", "title"),
        fallback_strategy=(
            FallbackTier(tier="preferred", condition="OD 数据在场",
                         cartography=("flow_od_arc",)),
            FallbackTier(tier="degraded", condition="仅有出行生成数据",
                         disclosure="OD 明细缺失：仅输出生成/吸引统计。"),
            FallbackTier(tier="minimal", condition="仅点位分布"),
            FallbackTier(tier="blocked", condition="无出行数据",
                         blocks_completion=True),
        ),
        family_triggers=("mobility_flow",),
        analysis_intent_triggers=("route_analysis",),
        cartography_triggers=(),
        keywords_zh=("通勤流", "出行", "od", "客流"),
        keywords_en=("od matrix", "commute flow", "origin destination"),
    ),
    _t(
        task_id="network.centrality", domain="network",
        label_zh="网络中心性", label_en="Network centrality",
        description="路网/图中心性指标（介度/接近度等）。",
        required_data_roles=("network",),
        geometry_expectations=("line",),
        common_capabilities=("network_centrality",),
        output_artifacts=("line_feature_set", "network_graph"),
        cartographic_expectations=("network_centrality_map", "graduated_line"),
        component_expectations=("legend", "title", "continuous_colorbar"),
        fallback_strategy=(
            FallbackTier(tier="preferred", condition="路网在场",
                         cartography=("network_centrality_map",)),
            FallbackTier(tier="degraded", condition="路网简化",
                         disclosure="路网拓扑简化：中心性为近似值。"),
            FallbackTier(tier="minimal", condition="仅路网展示"),
            FallbackTier(tier="blocked", condition="无路网数据",
                         blocks_completion=True),
        ),
        family_triggers=("network_route",),
        analysis_intent_triggers=(),
        cartography_triggers=(),
        keywords_zh=("中心性", "介数", "路网重要性"),
        keywords_en=("centrality", "betweenness"),
    ),
    _t(
        task_id="network.facility_location", domain="network",
        label_zh="设施选址优化", label_en="Facility location",
        description="location-allocation：给定需求下的最优设施布局。",
        required_data_roles=("subject", "population", "network"),
        geometry_expectations=("point", "polygon"),
        common_capabilities=("location_allocation",),
        output_artifacts=("point_feature_set", "stats_table"),
        cartographic_expectations=("site_selection_result", "proportional_symbol"),
        component_expectations=("legend", "title", "chart_panel"),
        fallback_strategy=(
            FallbackTier(tier="preferred", condition="需求与候选点齐备",
                         cartography=("site_selection_result",)),
            FallbackTier(tier="degraded", condition="需求用代理（POI 密度）",
                         disclosure="人口需求以 POI/掩膜代理，精度受限。"),
            FallbackTier(tier="minimal", condition="仅候选点清单"),
            FallbackTier(tier="blocked", condition="无候选与需求",
                         blocks_completion=True),
        ),
        family_triggers=("site_selection",),
        analysis_intent_triggers=("mcda_evaluation",),
        cartography_triggers=(),
        keywords_zh=("选址", "布局优化", "设施配置"),
        keywords_en=("facility location", "location allocation"),
    ),
    # ── terrain / hydrology ──────────────────────────────────────────
    _t(
        task_id="terrain_hydrology.slope_aspect", domain="terrain_hydrology",
        label_zh="坡度坡向", label_en="Slope & aspect",
        description="DEM 衍生坡度/坡向；角度坐标系需先投影。",
        required_data_roles=("elevation",),
        geometry_expectations=("raster",),
        common_capabilities=("terrain_slope", "terrain_aspect", "terrain_derivatives"),
        output_artifacts=("raster_surface", "terrain_surface"),
        cartographic_expectations=("terrain_analytical_surface", "raster_surface"),
        component_expectations=("continuous_colorbar", "title"),
        fallback_strategy=(
            FallbackTier(tier="preferred", condition="投影坐标 DEM 在场",
                         cartography=("terrain_analytical_surface",)),
            FallbackTier(tier="degraded", condition="地理坐标系 DEM（需变换）",
                         disclosure="地理坐标系下坡度失真：已先重投影再计算。"),
            FallbackTier(tier="minimal", condition="仅 DEM 展示"),
            FallbackTier(tier="blocked", condition="无 DEM",
                         blocks_completion=True),
        ),
        family_triggers=("terrain_analysis",),
        analysis_intent_triggers=("terrain_derivatives",),
        cartography_triggers=("raster_surface", "isoline_contour"),
        keywords_zh=("坡度", "坡向",),
        keywords_en=("slope", "aspect"),
    ),
    _t(
        task_id="terrain_hydrology.hillshade_viewshed", domain="terrain_hydrology",
        label_zh="山体阴影/视域", label_en="Hillshade & viewshed",
        description="山体阴影渲染与通视性/视域分析。",
        required_data_roles=("elevation",),
        optional_data_roles=("subject",),
        geometry_expectations=("raster", "point"),
        common_capabilities=("terrain_hillshade", "terrain_viewshed"),
        output_artifacts=("raster_surface", "terrain_surface"),
        cartographic_expectations=("hillshade", "elevation_tint_hillshade"),
        component_expectations=("title", "north_arrow"),
        fallback_strategy=(
            FallbackTier(tier="preferred", condition="DEM 在场",
                         cartography=("hillshade",)),
            FallbackTier(tier="degraded", condition="分辨率不足",
                         disclosure="DEM 分辨率不足：阴影/视域为粗尺度近似。"),
            FallbackTier(tier="minimal", condition="仅等高线"),
            FallbackTier(tier="blocked", condition="无 DEM",
                         blocks_completion=True),
        ),
        family_triggers=("terrain_analysis",),
        analysis_intent_triggers=("terrain_derivatives",),
        cartography_triggers=("raster_surface",),
        keywords_zh=("山体阴影", "晕渲", "视域", "通视"),
        keywords_en=("hillshade", "viewshed", "line of sight"),
    ),
    _t(
        task_id="terrain_hydrology.composite_analysis", domain="terrain_hydrology",
        label_zh="地形综合分析", label_en="Composite terrain analysis",
        description="地貌计量/多地形因子综合（粗糙度、地形位置指数等）。",
        required_data_roles=("elevation",),
        geometry_expectations=("raster",),
        common_capabilities=("terrain_geomorphometry", "terrain_derivatives"),
        output_artifacts=("raster_surface", "terrain_surface"),
        cartographic_expectations=("terrain_analytical_surface",),
        component_expectations=("continuous_colorbar", "title"),
        fallback_strategy=(
            FallbackTier(tier="preferred", condition="DEM 在场",
                         cartography=("terrain_analytical_surface",)),
            FallbackTier(tier="degraded", condition="部分因子不可算",
                         disclosure="部分地形因子因分辨率不可算，已略去。"),
            FallbackTier(tier="minimal", condition="仅基本衍生"),
            FallbackTier(tier="blocked", condition="无 DEM",
                         blocks_completion=True),
        ),
        family_triggers=("terrain_analysis",),
        analysis_intent_triggers=("terrain_derivatives",),
        cartography_triggers=("raster_surface",),
        keywords_zh=("地貌", "地形因子", "粗糙度"),
        keywords_en=("geomorphometry", "terrain factors"),
    ),
    _t(
        task_id="terrain_hydrology.watershed", domain="terrain_hydrology",
        label_zh="流域划分", label_en="Watershed delineation",
        description="填洼/流向/汇流累积/流域提取的水文链条。",
        required_data_roles=("elevation",),
        geometry_expectations=("raster",),
        common_capabilities=("terrain_hydrology", "terrain_hydrology_advanced"),
        output_artifacts=("raster_surface", "polygon_feature_set"),
        cartographic_expectations=("raster_surface", "categorical_thematic"),
        component_expectations=("legend", "title"),
        fallback_strategy=(
            FallbackTier(tier="preferred", condition="水文合规 DEM 在场",
                         cartography=("categorical_thematic",)),
            FallbackTier(tier="degraded", condition="DEM 含大面积洼地",
                         disclosure="DEM 洼地较多：填洼可能改变真实水文路径。"),
            FallbackTier(tier="minimal", condition="仅流向/汇流"),
            FallbackTier(tier="blocked", condition="无 DEM",
                         blocks_completion=True),
        ),
        family_triggers=("watershed_analysis",),
        analysis_intent_triggers=("hydrology_analysis",),
        cartography_triggers=("raster_surface",),
        keywords_zh=("流域", "汇水", "分水岭", "水文"),
        keywords_en=("watershed", "catchment", "hydrology"),
    ),
    _t(
        task_id="terrain_hydrology.stream_network", domain="terrain_hydrology",
        label_zh="河网提取", label_en="Stream network extraction",
        description="汇流阈值提取河网/水系结构。",
        required_data_roles=("elevation",),
        geometry_expectations=("raster", "line"),
        common_capabilities=("terrain_hydrology", "terrain_hydrology_advanced"),
        output_artifacts=("line_feature_set", "raster_surface"),
        cartographic_expectations=("categorized_line", "raster_surface"),
        component_expectations=("legend", "title"),
        fallback_strategy=(
            FallbackTier(tier="preferred", condition="DEM 与阈值合理",
                         cartography=("categorized_line",)),
            FallbackTier(tier="degraded", condition="阈值敏感",
                         disclosure="汇流阈值敏感：河网密度随阈值变化，已披露参数。"),
            FallbackTier(tier="minimal", condition="仅汇流累积面"),
            FallbackTier(tier="blocked", condition="无 DEM",
                         blocks_completion=True),
        ),
        family_triggers=("watershed_analysis",),
        analysis_intent_triggers=("hydrology_analysis",),
        cartography_triggers=(),
        keywords_zh=("河网", "水系提取", "河流提取"),
        keywords_en=("stream network", "river extraction"),
    ),
    _t(
        task_id="terrain_hydrology.indices", domain="terrain_hydrology",
        label_zh="水文指数", label_en="Hydrologic indices",
        description="地形湿度指数（TWI）等水文衍生指数。",
        required_data_roles=("elevation",),
        geometry_expectations=("raster",),
        common_capabilities=("terrain_wetness_indices",),
        output_artifacts=("raster_surface",),
        cartographic_expectations=("raster_surface",),
        component_expectations=("continuous_colorbar", "title"),
        fallback_strategy=(
            FallbackTier(tier="preferred", condition="DEM 在场",
                         cartography=("raster_surface",)),
            FallbackTier(tier="degraded", condition="分辨率不足",
                         disclosure="低分辨率 DEM：TWI 仅作趋势参考。"),
            FallbackTier(tier="minimal", condition="仅坡度替代表达"),
            FallbackTier(tier="blocked", condition="无 DEM",
                         blocks_completion=True),
        ),
        family_triggers=("watershed_analysis", "terrain_analysis"),
        analysis_intent_triggers=("hydrology_analysis",),
        cartography_triggers=("raster_surface",),
        keywords_zh=("湿度指数", "twi", "地形湿度"),
        keywords_en=("topographic wetness", "twi"),
    ),
    # ── remote sensing ───────────────────────────────────────────────
    _t(
        task_id="remote_sensing.spectral_index", domain="remote_sensing",
        label_zh="光谱指数", label_en="Spectral index",
        description="NDVI/NDWI 等光谱指数与缨帽变换。",
        required_data_roles=("subject",),
        geometry_expectations=("raster",),
        common_capabilities=("ndvi", "spectral_index", "tasseled_cap_transformation"),
        output_artifacts=("remote_sensing_index", "raster_surface"),
        cartographic_expectations=("spectral_index_surface", "raster_surface"),
        component_expectations=("continuous_colorbar", "title"),
        fallback_strategy=(
            FallbackTier(tier="preferred", condition="多波段影像在场且波段语义明确",
                         cartography=("spectral_index_surface",)),
            FallbackTier(tier="degraded", condition="波段语义存疑",
                         disclosure="波段映射为推断：指数值需谨慎解释。"),
            FallbackTier(tier="minimal", condition="仅单波段展示"),
            FallbackTier(tier="blocked", condition="无影像",
                         blocks_completion=True),
        ),
        family_triggers=("vegetation_index", "raster_distribution"),
        analysis_intent_triggers=(),
        cartography_triggers=("raster_surface",),
        keywords_zh=("ndvi", "植被指数", "ndwi", "光谱指数", "缨帽"),
        keywords_en=("ndvi", "ndwi", "spectral index", "tasseled cap"),
    ),
    _t(
        task_id="remote_sensing.change_detection", domain="remote_sensing",
        label_zh="变化检测", label_en="Change detection",
        description="两期/多期影像或主题数据的变化识别与表达。",
        required_data_roles=("baseline", "target_time"),
        geometry_expectations=("raster", "polygon"),
        common_capabilities=("change_detection", "raster_change_detection"),
        output_artifacts=("change_set",),
        cartographic_expectations=("change_comparison_map", "before_after_swipe"),
        component_expectations=("legend", "title"),
        ambiguity_rules=(
            AmbiguityRule(
                signal="「变化」未指明两期对比还是多期趋势",
                default_resolution="两期对比（baseline→target）",
                alternatives=("temporal_trend",),
                disambiguator="时间期数 ≥3 时为趋势",
            ),
        ),
        fallback_strategy=(
            FallbackTier(tier="preferred", condition="双期同源数据在场",
                         cartography=("change_comparison_map",)),
            FallbackTier(tier="degraded", condition="两期数据源不一致",
                         disclosure="数据源/传感器不一致：变化结论含系统误差。"),
            FallbackTier(tier="minimal", condition="仅单期",
                         disclosure="仅单期数据：不能做变化结论。"),
            FallbackTier(tier="blocked", condition="无时相数据",
                         blocks_completion=True),
        ),
        family_triggers=("change_detection",),
        analysis_intent_triggers=(),
        cartography_triggers=(),
        keywords_zh=("变化检测", "变化监测", "两期", "扩张"),
        keywords_en=("change detection", "change analysis"),
    ),
    _t(
        task_id="remote_sensing.classification", domain="remote_sensing",
        label_zh="影像分类", label_en="Image classification",
        description="土地利用/覆盖分类制图（监督/非监督/再分类）。",
        required_data_roles=("subject",),
        geometry_expectations=("raster",),
        common_capabilities=("raster_reclassify",),
        output_artifacts=("raster_surface",),
        cartographic_expectations=("classified_raster", "categorical_thematic"),
        component_expectations=("legend", "title"),
        fallback_strategy=(
            FallbackTier(tier="preferred", condition="类别体系与样本明确",
                         cartography=("classified_raster",)),
            FallbackTier(tier="degraded", condition="无训练样本（仅再分类）",
                         disclosure="无训练样本：仅按既有类别体系再分类。"),
            FallbackTier(tier="minimal", condition="仅原始波段展示"),
            FallbackTier(tier="blocked", condition="无影像",
                         blocks_completion=True),
        ),
        family_triggers=("raster_distribution",),
        analysis_intent_triggers=(),
        cartography_triggers=("raster_surface",),
        keywords_zh=("土地利用分类", "分类", "地表覆盖"),
        keywords_en=("land use classification", "land cover"),
    ),
    _t(
        task_id="remote_sensing.temporal_analysis", domain="remote_sensing",
        label_zh="时序分析", label_en="Temporal analysis",
        description="多期指数/植被/水体的时序趋势与突变。",
        required_data_roles=("subject", "target_time"),
        optional_data_roles=("baseline", "comparison_time"),
        geometry_expectations=("raster", "table"),
        common_capabilities=("temporal_trend", "temporal_profile",
                             "temporal_change_point", "temporal_aggregate"),
        output_artifacts=("stats_table", "chart_spec", "raster_surface"),
        cartographic_expectations=("temporal_trend_surface",),
        component_expectations=("chart_panel", "title"),
        fallback_strategy=(
            FallbackTier(tier="preferred", condition="多期（≥3）数据在场",
                         cartography=("temporal_trend_surface",)),
            FallbackTier(tier="degraded", condition="仅两期",
                         disclosure="仅两期：只能做差值对比，不能拟合趋势。"),
            FallbackTier(tier="minimal", condition="单期",
                         disclosure="单期数据：仅当前状态展示。"),
            FallbackTier(tier="blocked", condition="无时相数据",
                         blocks_completion=True),
        ),
        family_triggers=("temporal_trend", "change_detection"),
        analysis_intent_triggers=("trend_analysis",),
        cartography_triggers=(),
        keywords_zh=("时序", "逐年", "多期", "趋势分析"),
        keywords_en=("time series", "temporal trend"),
    ),
    _t(
        task_id="remote_sensing.anomaly_detection", domain="remote_sensing",
        label_zh="异常检测", label_en="Anomaly detection",
        description="相对背景场的空间异常识别（统计/阈值）。",
        required_data_roles=("subject", "measure"),
        geometry_expectations=("raster", "polygon"),
        common_capabilities=(),
        output_artifacts=("raster_surface", "stats_table"),
        cartographic_expectations=("anomaly_surface", "diverging_choropleth"),
        component_expectations=("legend", "title"),
        fallback_strategy=(
            FallbackTier(tier="degraded", condition="背景场可用统计近似",
                         disclosure="异常检测以分位/标准差近似，非专用模型。"),
            FallbackTier(tier="minimal", condition="仅描述统计"),
            FallbackTier(tier="blocked", condition="无数据",
                         blocks_completion=True),
        ),
        family_triggers=("raster_distribution",),
        analysis_intent_triggers=(),
        cartography_triggers=(),
        keywords_zh=("异常", "离群区域"),
        keywords_en=("anomaly",),
    ),
    # ── sar ──────────────────────────────────────────────────────────
    _t(
        task_id="sar.radiometric_calibration", domain="sar",
        label_zh="SAR 辐射定标", label_en="SAR radiometric calibration",
        description="DN→后向散射系数（sigma0/gamma0）的辐射定标。",
        required_data_roles=("subject",),
        geometry_expectations=("raster",),
        common_capabilities=("sar_radiometric_calibration",),
        output_artifacts=("raster_surface",),
        cartographic_expectations=("sar_intensity_surface",),
        component_expectations=("continuous_colorbar", "title"),
        fallback_strategy=(
            FallbackTier(tier="preferred", condition="定标参数在场",
                         cartography=("sar_intensity_surface",)),
            FallbackTier(tier="degraded", condition="定标参数缺失",
                         disclosure="定标参数缺失：仅相对比较，不做绝对散射结论。"),
            FallbackTier(tier="minimal", condition="仅 DN 展示"),
            FallbackTier(tier="blocked", condition="无 SAR 数据",
                         blocks_completion=True),
        ),
        family_triggers=("sar_analysis",),
        analysis_intent_triggers=("sar_interpretation",),
        cartography_triggers=("raster_surface",),
        keywords_zh=("定标", "辐射定标", "后向散射"),
        keywords_en=("radiometric calibration", "sigma naught", "backscatter"),
    ),
    _t(
        task_id="sar.speckle_filtering", domain="sar",
        label_zh="SAR 去噪（斑点滤波）", label_en="SAR speckle filtering",
        description="Lee/Refined-Lee 等斑点噪声抑制。",
        required_data_roles=("subject",),
        geometry_expectations=("raster",),
        common_capabilities=("sar_speckle_filtering",),
        output_artifacts=("raster_surface",),
        cartographic_expectations=("sar_intensity_surface",),
        component_expectations=("title",),
        fallback_strategy=(
            FallbackTier(tier="preferred", condition="SAR 影像在场",
                         cartography=("sar_intensity_surface",)),
            FallbackTier(tier="degraded", condition="滤波窗口敏感",
                         disclosure="滤波窗口/方法影响纹理，参数已披露。"),
            FallbackTier(tier="minimal", condition="未滤波展示"),
            FallbackTier(tier="blocked", condition="无 SAR 数据",
                         blocks_completion=True),
        ),
        family_triggers=("sar_analysis",),
        analysis_intent_triggers=("sar_interpretation",),
        cartography_triggers=("raster_surface",),
        keywords_zh=("斑点滤波", "去噪", "lee滤波"),
        keywords_en=("speckle", "lee filter"),
    ),
    _t(
        task_id="sar.coherence", domain="sar",
        label_zh="InSAR 相干性", label_en="InSAR coherence",
        description="干涉相干性估计（形变/变化的前置）。",
        semantic_status="planned",
        required_data_roles=("baseline", "target_time"),
        geometry_expectations=("raster",),
        common_capabilities=(),
        output_artifacts=("raster_surface",),
        cartographic_expectations=("raster_surface",),
        component_expectations=("continuous_colorbar", "title"),
        fallback_strategy=(
            FallbackTier(tier="degraded", condition="仅强度对比替代",
                         disclosure="InSAR 相干计算未注册：以强度变化近似表达。"),
            FallbackTier(tier="minimal", condition="仅单景展示"),
            FallbackTier(tier="blocked", condition="无双景 SAR",
                         blocks_completion=True),
        ),
        family_triggers=("sar_analysis",),
        analysis_intent_triggers=("sar_interpretation",),
        cartography_triggers=("raster_surface",),
        keywords_zh=("相干", "insar", "干涉"),
        keywords_en=("coherence", "insar", "interferometry"),
    ),
    _t(
        task_id="sar.temporal_analysis", domain="sar",
        label_zh="时序 SAR 分析", label_en="Temporal SAR analysis",
        description="多景 SAR 时序形变/时序散射分析。",
        semantic_status="planned",
        required_data_roles=("subject", "target_time"),
        geometry_expectations=("raster",),
        common_capabilities=(),
        output_artifacts=("raster_surface", "stats_table"),
        cartographic_expectations=("sar_change_detection",),
        component_expectations=("title", "chart_panel"),
        fallback_strategy=(
            FallbackTier(tier="degraded", condition="少景近似",
                         disclosure="时序 SAR 形变解算未注册：以多景强度变化近似。"),
            FallbackTier(tier="minimal", condition="单景展示"),
            FallbackTier(tier="blocked", condition="无时序 SAR",
                         blocks_completion=True),
        ),
        family_triggers=("sar_analysis", "temporal_trend"),
        analysis_intent_triggers=("sar_interpretation",),
        cartography_triggers=(),
        keywords_zh=("时序insar", "形变监测", "ps-insar"),
        keywords_en=("ps-insar", "deformation time series"),
    ),
    _t(
        task_id="sar.interpretation", domain="sar",
        label_zh="SAR 综合解译", label_en="SAR interpretation",
        description="SAR 语义解译产品族（极化/强度/纹理综合）。",
        required_data_roles=("subject",),
        geometry_expectations=("raster",),
        common_capabilities=("sar_analysis", "sar_texture"),
        output_artifacts=("raster_surface",),
        cartographic_expectations=("sar_intensity_surface", "classified_raster"),
        component_expectations=("legend", "title"),
        fallback_strategy=(
            FallbackTier(tier="preferred", condition="定标后的 SAR 在场",
                         cartography=("sar_intensity_surface",)),
            FallbackTier(tier="degraded", condition="未定标",
                         disclosure="未定标数据：仅相对解译。"),
            FallbackTier(tier="minimal", condition="仅展示"),
            FallbackTier(tier="blocked", condition="无 SAR 数据",
                         blocks_completion=True),
        ),
        family_triggers=("sar_analysis",),
        analysis_intent_triggers=("sar_interpretation",),
        cartography_triggers=("raster_surface",),
        keywords_zh=("sar", "雷达", "合成孔径"),
        keywords_en=("sar", "synthetic aperture radar"),
    ),
    # ── decision ─────────────────────────────────────────────────────
    _t(
        task_id="decision.site_selection", domain="decision",
        label_zh="选址评价", label_en="Site selection",
        description="多准则加权评价 + 约束排除的候选选址。",
        required_data_roles=("criteria", "constraint"),
        optional_data_roles=("subject", "boundary"),
        geometry_expectations=("polygon", "raster", "point"),
        common_capabilities=("mcda_evaluation", "weights_sensitivity"),
        output_artifacts=("stats_table", "polygon_feature_set", "raster_surface"),
        cartographic_expectations=("site_selection_result", "mcda_score_map",
                                   "suitability_classes"),
        component_expectations=("legend", "title", "chart_panel"),
        ambiguity_rules=(
            AmbiguityRule(
                signal="「选址」未给权重/准则",
                default_resolution="等权起步 + 敏感性分析，权重需用户确认",
                alternatives=("suitability",),
                disambiguator="用户给定准则与权重",
            ),
        ),
        fallback_strategy=(
            FallbackTier(tier="preferred", condition="准则/约束数据齐备",
                         cartography=("site_selection_result",)),
            FallbackTier(tier="degraded", condition="部分准则缺失",
                         disclosure="部分准则缺失：结果以可用准则子集评价。"),
            FallbackTier(tier="minimal", condition="仅描述可用因子",
                         disclosure="准则数据不足：仅做因子清单与可得性说明。"),
            FallbackTier(tier="blocked", condition="无准则数据",
                         blocks_completion=True),
        ),
        family_triggers=("site_selection",),
        analysis_intent_triggers=("mcda_evaluation", "overlay_weighted"),
        cartography_triggers=(),
        keywords_zh=("选址", "最优位置", "候选评价"),
        keywords_en=("site selection", "best location"),
    ),
    _t(
        task_id="decision.suitability", domain="decision",
        label_zh="适宜性评价", label_en="Suitability assessment",
        description="因子标准化加权的适宜性/适建区评价。",
        required_data_roles=("criteria",),
        optional_data_roles=("constraint", "boundary"),
        geometry_expectations=("polygon", "raster"),
        common_capabilities=("mcda_evaluation",),
        output_artifacts=("raster_surface", "polygon_feature_set", "stats_table"),
        cartographic_expectations=("suitability_classes", "mcda_score_map"),
        component_expectations=("legend", "title"),
        fallback_strategy=(
            FallbackTier(tier="preferred", condition="因子图层齐备",
                         cartography=("suitability_classes",)),
            FallbackTier(tier="degraded", condition="因子不全",
                         disclosure="因子覆盖不全：适宜性为部分评价。"),
            FallbackTier(tier="minimal", condition="仅因子清单"),
            FallbackTier(tier="blocked", condition="无因子数据",
                         blocks_completion=True),
        ),
        family_triggers=("suitability_assessment",),
        analysis_intent_triggers=("overlay_weighted", "mcda_evaluation"),
        cartography_triggers=(),
        keywords_zh=("适宜性", "适建", "宜建"),
        keywords_en=("suitability",),
    ),
    _t(
        task_id="decision.risk_exposure", domain="decision",
        label_zh="风险与暴露", label_en="Risk & exposure",
        description="危险源影响区 × 承灾体暴露的风险评价。",
        required_data_roles=("hazard", "receptor"),
        optional_data_roles=("population", "boundary"),
        geometry_expectations=("polygon", "point", "raster"),
        common_capabilities=("mcda_evaluation", "proximity_buffer", "geometry_overlay"),
        output_artifacts=("polygon_feature_set", "stats_table"),
        cartographic_expectations=("risk_exposure_classes", "vulnerability_index"),
        component_expectations=("legend", "title"),
        fallback_strategy=(
            FallbackTier(tier="preferred", condition="危险源与承灾体齐备",
                         cartography=("risk_exposure_classes",)),
            FallbackTier(tier="degraded", condition="承灾体未知",
                         disclosure="承灾体未确认：仅危险区制图，不做暴露结论。"),
            FallbackTier(tier="minimal", condition="仅危险源清单"),
            FallbackTier(tier="blocked", condition="无危险源数据",
                         blocks_completion=True),
        ),
        family_triggers=("risk_exposure",),
        analysis_intent_triggers=("exposure_assessment",),
        cartography_triggers=(),
        keywords_zh=("风险", "暴露", "危险区", "影响范围"),
        keywords_en=("risk", "exposure", "hazard"),
    ),
    _t(
        task_id="decision.vulnerability", domain="decision",
        label_zh="脆弱性评价", label_en="Vulnerability assessment",
        description="多因子脆弱性/韧性指数构建与分区。",
        required_data_roles=("population", "criteria"),
        optional_data_roles=("boundary",),
        geometry_expectations=("polygon",),
        common_capabilities=("mcda_evaluation", "rate_aggregation"),
        output_artifacts=("stats_table", "polygon_feature_set"),
        cartographic_expectations=("vulnerability_index", "normalized_choropleth"),
        component_expectations=("legend", "title", "chart_panel"),
        fallback_strategy=(
            FallbackTier(tier="preferred", condition="人口与准则齐备",
                         cartography=("vulnerability_index",)),
            FallbackTier(tier="degraded", condition="分母/因子缺失",
                         disclosure="因子缺失：脆弱性指数为部分维度近似。"),
            FallbackTier(tier="minimal", condition="仅描述统计"),
            FallbackTier(tier="blocked", condition="无人口/准则数据",
                         blocks_completion=True),
        ),
        family_triggers=("risk_exposure", "spatial_equity"),
        analysis_intent_triggers=("exposure_assessment", "equity_assessment"),
        cartography_triggers=(),
        keywords_zh=("脆弱性", "韧性"),
        keywords_en=("vulnerability", "resilience"),
    ),
    _t(
        task_id="decision.multi_criteria", domain="decision",
        label_zh="多准则评价", label_en="Multi-criteria evaluation",
        description="通用 MCDA（WSM/TOPSIS）加权评价与敏感性分析。",
        required_data_roles=("criteria",),
        optional_data_roles=("constraint",),
        geometry_expectations=("polygon", "raster", "point"),
        common_capabilities=("mcda_evaluation", "weights_sensitivity"),
        output_artifacts=("stats_table", "raster_surface"),
        cartographic_expectations=("mcda_score_map", "sensitivity_analysis_presentation"),
        component_expectations=("legend", "title", "statistics_panel"),
        fallback_strategy=(
            FallbackTier(tier="preferred", condition="准则与权重明确",
                         cartography=("mcda_score_map",)),
            FallbackTier(tier="degraded", condition="权重未给定（等权+敏感性）",
                         disclosure="未给定权重：等权假设 + 敏感性分析佐证。"),
            FallbackTier(tier="minimal", condition="仅准则统计"),
            FallbackTier(tier="blocked", condition="无准则数据",
                         blocks_completion=True),
        ),
        family_triggers=("site_selection", "suitability_assessment", "spatial_equity"),
        analysis_intent_triggers=("mcda_evaluation",),
        cartography_triggers=(),
        keywords_zh=("多准则", "加权评价", "topsis", "权重"),
        keywords_en=("multi criteria", "topsis", "weighted overlay"),
    ),
    _t(
        task_id="decision.spatial_equity", domain="decision",
        label_zh="空间公平性", label_en="Spatial equity",
        description="分母归一化的服务/资源公平性评价（人均/率）。",
        required_data_roles=("subject", "boundary", "denominator"),
        geometry_expectations=("polygon", "point"),
        common_capabilities=("admin_aggregation", "rate_aggregation", "accessibility"),
        output_artifacts=("admin_aggregate_table", "stats_table"),
        cartographic_expectations=("equity_assessment", "normalized_choropleth",
                                   "diverging_choropleth"),
        component_expectations=("legend", "title", "chart_panel"),
        fallback_strategy=(
            FallbackTier(tier="preferred", condition="分母（人口/面积）在场",
                         cartography=("equity_assessment",)),
            FallbackTier(tier="degraded", condition="分母缺失",
                         disclosure="缺少归一化分母：不得下人均/公平性结论，"
                                    "仅输出数量分布。"),
            FallbackTier(tier="minimal", condition="仅计数统计"),
            FallbackTier(tier="blocked", condition="无主体数据",
                         blocks_completion=True),
        ),
        family_triggers=("spatial_equity",),
        analysis_intent_triggers=("equity_assessment", "administrative_aggregation"),
        cartography_triggers=(),
        keywords_zh=("公平", "均衡", "是否合理"),
        keywords_en=("equity", "fairness", "balanced"),
    ),
    # ── cartographic（产品型任务）────────────────────────────────────
    _t(
        task_id="cartographic.comparison_map", domain="cartographic",
        label_zh="对比图", label_en="Comparison map",
        description="双期/双方案并排或卷帘对比的产品化表达。",
        required_data_roles=("baseline", "target_time"),
        optional_data_roles=("boundary",),
        geometry_expectations=("polygon", "raster", "point"),
        common_capabilities=(),
        output_artifacts=("feature_collection", "raster_surface"),
        cartographic_expectations=("before_after_swipe", "change_comparison_map",
                                   "diverging_choropleth"),
        component_expectations=("legend", "title"),
        fallback_strategy=(
            FallbackTier(tier="preferred", condition="两期数据在场",
                         cartography=("before_after_swipe",)),
            FallbackTier(tier="degraded", condition="仅单期（并排说明差异不可得）",
                         disclosure="仅单期数据：无法构成对比图。"),
            FallbackTier(tier="blocked", condition="无数据",
                         blocks_completion=True),
        ),
        family_triggers=("change_detection", "temporal_trend"),
        analysis_intent_triggers=(),
        cartography_triggers=(),
        keywords_zh=("对比图", "前后对比", "卷帘"),
        keywords_en=("comparison map", "before after", "swipe"),
    ),
    _t(
        task_id="cartographic.statistical_map", domain="cartographic",
        label_zh="统计专题图", label_en="Statistical map",
        description="以统计量（率/指数/偏差）为主题的专题制图。",
        required_data_roles=("measure", "boundary"),
        geometry_expectations=("polygon",),
        common_capabilities=("admin_aggregation", "rate_aggregation"),
        output_artifacts=("admin_aggregate_table",),
        cartographic_expectations=("diverging_choropleth", "bivariate_choropleth",
                                   "normalized_choropleth"),
        component_expectations=("legend", "title", "statistics_panel"),
        fallback_strategy=(
            FallbackTier(tier="preferred", condition="统计字段齐备",
                         cartography=("diverging_choropleth",)),
            FallbackTier(tier="degraded", condition="统计字段部分缺失",
                         disclosure="部分统计字段缺失：以可用字段制图并披露。"),
            FallbackTier(tier="minimal", condition="仅统计表"),
            FallbackTier(tier="blocked", condition="无统计数据",
                         blocks_completion=True),
        ),
        family_triggers=("administrative_statistic",),
        analysis_intent_triggers=("administrative_summary",),
        cartography_triggers=("administrative_choropleth",),
        keywords_zh=("统计图", "专题图"),
        keywords_en=("statistical map", "thematic map"),
    ),
    _t(
        task_id="cartographic.report_map", domain="cartographic",
        label_zh="报告成图", label_en="Report map",
        description="用于报告的版面化成图：图名/图例/指北针/比例尺齐备。",
        required_data_roles=("subject",),
        optional_data_roles=("boundary", "measure"),
        geometry_expectations=("point", "polygon", "line", "raster"),
        common_capabilities=(),
        output_artifacts=("feature_collection",),
        cartographic_expectations=("simple_point_map", "administrative_choropleth",
                                   "raster_surface"),
        component_expectations=("title", "legend", "north_arrow", "scale_bar",
                                "attribution"),
        fallback_strategy=(
            FallbackTier(tier="preferred", condition="成图要素齐备",
                         cartography=()),
            FallbackTier(tier="degraded", condition="部分版面组件缺失",
                         disclosure="版面组件缺失已按可用子集出图。"),
            FallbackTier(tier="blocked", condition="无成图数据",
                         blocks_completion=True),
        ),
        family_triggers=("distribution_overview",),
        analysis_intent_triggers=(),
        cartography_triggers=(),
        keywords_zh=("报告", "出图", "成图"),
        keywords_en=("report map", "for report"),
    ),
)


class TaskOntology:
    """本体登记表：O(1) by task_id + 确定性匹配（加载期建索引）。"""

    def __init__(self, tasks: Tuple[TaskDescriptor, ...] = ONTOLOGY_TASKS) -> None:
        self._by_id: Dict[str, TaskDescriptor] = {}
        self._by_domain: Dict[str, List[TaskDescriptor]] = {}
        self._by_family: Dict[str, List[TaskDescriptor]] = {}
        for task in tasks:
            if task.task_id in self._by_id:
                raise ValueError(f"duplicate ontology task id: {task.task_id}")
            self._by_id[task.task_id] = task
            self._by_domain.setdefault(task.domain, []).append(task)
            for fam in task.family_triggers:
                self._by_family.setdefault(fam, []).append(task)

    # ── 查询 ─────────────────────────────────────────────────────────
    def get(self, task_id: str) -> Optional[TaskDescriptor]:
        return self._by_id.get(task_id)

    def has(self, task_id: str) -> bool:
        return task_id in self._by_id

    @property
    def all_ids(self) -> List[str]:
        return sorted(self._by_id.keys())

    @property
    def count(self) -> int:
        return len(self._by_id)

    def domains(self) -> List[str]:
        return sorted(self._by_domain.keys())

    def tasks_for_domain(self, domain: str) -> List[TaskDescriptor]:
        return list(self._by_domain.get(domain, []))

    def tasks_for_family(self, task_family: str) -> List[TaskDescriptor]:
        """intent task family → 本体任务（注册序，确定性）。"""
        return list(self._by_family.get(task_family, []))

    # ── 确定性匹配（Stage 2：Task Ontology Mapping）──────────────────
    def match_intent(self, intent: Any, *, limit: int = 5) -> List[OntologyMatch]:
        """intent → 有序本体任务匹配（打分制，同分按 task_id 稳定排序）。

        信号权重（确定性）：
        - task family 直接命中 +3（最强语义证据）；
        - analysis intent 命中 +2/个；
        - cartography intent 命中 +1/个；
        - 关键词命中 +1/个（zh 子串 / en 整词，与 RecipeRegistry 同红线）；
        - geometry expectation 命中 +0.5。
        """
        task = str(getattr(intent, "task", "") or "")
        analysis = set(getattr(intent, "analysis_intents", []) or [])
        cartography = set(getattr(intent, "cartography_intents", []) or [])
        geometry = str(getattr(intent, "geometry_expectation", "") or "")
        query = str(getattr(intent, "query", "") or "").lower()

        matches: List[OntologyMatch] = []
        for desc in self._by_id.values():
            signals: List[str] = []
            score = 0.0
            if task and task in desc.family_triggers:
                score += 3.0
                signals.append(f"family:{task}")
            for ai in desc.analysis_intent_triggers:
                if ai in analysis:
                    score += 2.0
                    signals.append(f"analysis:{ai}")
            for ci in desc.cartography_triggers:
                if ci in cartography:
                    score += 1.0
                    signals.append(f"cartography:{ci}")
            for kw in desc.keywords_zh:
                if kw and kw in query:
                    score += 1.0
                    signals.append(f"kw:{kw[:24]}")
            for kw in desc.keywords_en:
                k = kw.lower()
                if not k:
                    continue
                if k.isascii() and k.isalnum():
                    # 整词命中（与 RecipeRegistry._keyword_matches 同红线）
                    import re as _re
                    if _re.search(rf"(?<![a-z]){ _re.escape(k) }(?![a-z])", query):
                        score += 1.0
                        signals.append(f"kw:{k[:24]}")
                elif k in query:
                    score += 1.0
                    signals.append(f"kw:{k[:24]}")
            if geometry and geometry in desc.geometry_expectations:
                score += 0.5
                signals.append(f"geometry:{geometry}")
            if score <= 0.0:
                continue
            matches.append(OntologyMatch(
                task_id=desc.task_id, domain=desc.domain, score=score,
                semantic_status=desc.semantic_status,
                matched_signals=signals,
            ))
        matches.sort(key=lambda m: (-m.score, m.task_id))
        return matches[:limit]

    # ── 校验与指纹 ───────────────────────────────────────────────────
    def validate(
        self,
        *,
        capability_exists=None,
        artifact_type_exists=None,
        map_model_exists=None,
        data_role_vocabulary: Tuple[str, ...] = (),
        family_vocabulary: Tuple[str, ...] = (),
    ) -> List[str]:
        """引用完整性校验（注入谓词，避免 import registry 单例循环依赖）。"""
        violations: List[str] = []
        for desc in self._by_id.values():
            tag = f"ontology[{desc.task_id}]"
            if desc.domain not in ONTOLOGY_DOMAINS:
                violations.append(f"{tag}: unknown domain {desc.domain}")
            if desc.semantic_status not in SEMANTIC_STATUSES:
                violations.append(f"{tag}: unknown semantic_status {desc.semantic_status}")
            for role in desc.required_data_roles + desc.optional_data_roles:
                if data_role_vocabulary and role not in data_role_vocabulary:
                    violations.append(f"{tag}: unknown data role {role}")
            for g in desc.geometry_expectations:
                if g not in ("point", "line", "polygon", "raster", "table",
                             "network", "unknown"):
                    violations.append(f"{tag}: unknown geometry expectation {g}")
            for cap in desc.common_capabilities:
                if capability_exists and not capability_exists(cap):
                    violations.append(f"{tag}: capability {cap} 不存在")
            for at in desc.output_artifacts:
                if artifact_type_exists and not artifact_type_exists(at):
                    violations.append(f"{tag}: artifact type {at} 未注册")
            for mm in desc.cartographic_expectations:
                if map_model_exists and not map_model_exists(mm):
                    violations.append(f"{tag}: map model {mm} 未注册")
            if not desc.fallback_strategy:
                violations.append(f"{tag}: 缺少 fallback_strategy（至少声明 preferred/blocked）")
            else:
                tiers = [t.tier for t in desc.fallback_strategy]
                if len(tiers) != len(set(tiers)):
                    violations.append(f"{tag}: fallback tier 重复")
                for tier in tiers:
                    if tier not in _FALLBACK_TIERS:
                        violations.append(f"{tag}: unknown fallback tier {tier}")
                if "blocked" in tiers:
                    blocked = next(t for t in desc.fallback_strategy if t.tier == "blocked")
                    if not blocked.blocks_completion:
                        violations.append(f"{tag}: blocked 层必须 blocks_completion")
            for fam in desc.family_triggers:
                if family_vocabulary and fam not in family_vocabulary:
                    violations.append(f"{tag}: unknown family trigger {fam}")
        return violations

    def fingerprint(self) -> str:
        """本体内容指纹：canonical JSON SHA256（版本化审计用）。"""
        payload = {
            "version": ONTOLOGY_VERSION,
            "tasks": [t.model_dump() for t in
                      sorted(self._by_id.values(), key=lambda d: d.task_id)],
        }
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                               separators=(",", ":"), default=str)
        return hashlib.sha256(canonical.encode("utf-8"), usedforsecurity=False).hexdigest()


_singleton: Optional[TaskOntology] = None


def get_task_ontology() -> TaskOntology:
    global _singleton
    if _singleton is None:
        _singleton = TaskOntology()
    return _singleton


def reset_task_ontology() -> None:
    global _singleton
    _singleton = None


def match_task_ontology(intent: Any, *, limit: int = 5) -> List[OntologyMatch]:
    """模块级便捷入口（compiler 阶段 2 消费）。"""
    return get_task_ontology().match_intent(intent, limit=limit)


__all__ = [
    "ONTOLOGY_VERSION",
    "ONTOLOGY_DOMAINS",
    "SEMANTIC_STATUSES",
    "AmbiguityRule",
    "FallbackTier",
    "TaskDescriptor",
    "OntologyMatch",
    "ONTOLOGY_TASKS",
    "TaskOntology",
    "get_task_ontology",
    "reset_task_ontology",
    "match_task_ontology",
]
