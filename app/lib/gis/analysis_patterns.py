"""GIS Analysis Pattern Library (ADR-0092 C3).

Metadata-only registry of professional analysis patterns. A pattern is NOT a
planner and owns NO execution path: it declares which semantic roles a
question needs, which capabilities usually serve it, which output facets the
product should carry, normalization guidance, and the classic GIS pitfalls.
Everything flows through the existing chain:

    SessionPlan → CapabilityRegistry → AlgorithmResolver → ToolRegistry

本模块的消费者是 pattern projection（给 Pi 的方法论建议/披露）与评估层 ——
不是第二 SessionPlan。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from app.lib.gis.semantic_profile import SemanticFieldRole as R


@dataclass(frozen=True)
class AnalysisPattern:
    id: str
    name_zh: str
    description: str
    # intent.task 词表对齐（触发别名；pattern 投影用它匹配，不另立词表）
    task_aliases: Tuple[str, ...] = ()
    # 触发关键词（查询词面；元数据，仅投影用）
    query_keywords: Tuple[str, ...] = ()
    # 语义角色需求
    required_roles: Tuple[R, ...] = ()
    optional_roles: Tuple[R, ...] = ()
    recommended_capabilities: Tuple[str, ...] = ()
    optional_capabilities: Tuple[str, ...] = ()
    required_output_facets: Tuple[str, ...] = ()
    # 归一化指引（专业 GIS 方法判断的核心）
    normalization_guidance: str = ""
    common_pitfalls: Tuple[str, ...] = ()
    # Semantic V2（ADR-0098）：决策族规划期义务披露 —— 一等 task 命中即
    # 触发（非关键词门控）：准则/权重/受体等决策要素在规划期必然未确认，
    # 披露「先声明、不合成」的边界是这些产品族的诚实底线。
    decision_disclosures: Tuple[str, ...] = ()
    # 本模式的一等 intent task（词表内值）。决策族义务披露只在
    # intent.task **精确等于**该值时触发 —— task_aliases 里的借位别名
    # （proximity/accessibility/administrative_statistic 等前 V2 时期的
    # 匹配面）只参与角色/能力建议，不触发义务披露（否则每个邻近查询
    # 都会背上风险披露的噪声）。
    first_class_task: str = ""


PATTERNS: Tuple[AnalysisPattern, ...] = (
    AnalysisPattern(
        id="distribution",
        name_zh="空间分布",
        description="主体在哪里、如何散布：点分布 + 行政汇总 + 视觉密度。",
        task_aliases=("distribution_overview", "simple_view"),
        query_keywords=("分布", "散布", "格局", "疏密"),
        recommended_capabilities=("poi_query", "admin_aggregation", "admin_boundary_query"),
        optional_capabilities=("kde_density", "hotspot"),
        required_output_facets=("map", "title"),
        normalization_guidance="纯分布描述不做归一化；若要比较『密集程度』，改用面积归一化的密度而非裸计数。",
        common_pitfalls=(
            "把点数直接当『密度』比较不同大小的行政区",
            "点数受采集完备性影响，不一定等于真实分布",
        ),
    ),
    AnalysisPattern(
        id="density",
        name_zh="密度分析",
        description="单位面积的量：KDE 连续面或格网聚合。",
        task_aliases=("analytical_density",),
        query_keywords=("密度", "每平方公里", "热力"),
        recommended_capabilities=("kde_density", "grid_binning"),
        optional_capabilities=("admin_aggregation",),
        required_output_facets=("map", "legend", "title"),
        normalization_guidance="密度必须除以面积（或格网面积）；视觉热力与定量密度要区分表述。",
        common_pitfalls=(
            "把 KDE 插值面说成『真实密度分布』（它是核平滑估计）",
            "样本点过少时 KDE 无意义（资格门 min_points 会拒绝）",
        ),
    ),
    AnalysisPattern(
        id="administrative_comparison",
        name_zh="行政区比较",
        description="跨行政区对比指标：聚合 + 排名 + 分级统计图。",
        task_aliases=("administrative_statistic", "categorical_distribution"),
        query_keywords=("各区", "各县", "对比", "排名", "统计"),
        required_roles=(),
        recommended_capabilities=("admin_aggregation", "admin_boundary_query", "poi_query"),
        optional_capabilities=("zonal_statistics",),
        required_output_facets=("map", "statistics", "legend", "title"),
        normalization_guidance=(
            "比较『资源多少』用裸计数；比较『资源公平/充足』必须归一化"
            "（人均 needs population_measure，地均 needs area_measure）。"
            "缺 denominator 时只能给出数量结论，不得下公平性判断。"
        ),
        common_pitfalls=(
            "大区天然计数多，直接比总数会把『面积大』误读成『资源好』",
            "choropleth 用裸计数着色是经典制图错误（应用比率/人均）",
        ),
    ),
    AnalysisPattern(
        id="accessibility",
        name_zh="可达性分析",
        description="真实路网上的时间/距离可达：等时圈、服务区。",
        task_aliases=("accessibility_analysis",),
        query_keywords=("可达", "服务区", "等时圈", "分钟", "通勤时间"),
        recommended_capabilities=("service_area", "closest_facility"),
        optional_capabilities=("network_accessibility", "poi_query"),
        required_output_facets=("map", "legend", "title"),
        normalization_guidance="可达性用真实路网（步行/驾车 profile），不要用欧氏缓冲冒充。",
        common_pitfalls=(
            "用直线缓冲区冒充步行可达范围",
            "路网数据的连通性错误会让服务区虚假断裂",
        ),
    ),
    AnalysisPattern(
        id="service_coverage",
        name_zh="服务覆盖",
        description="设施服务覆盖了谁、漏了谁：覆盖区与需求面叠加。",
        task_aliases=("accessibility_analysis",),
        query_keywords=("覆盖", "盲区", "缺口", "欠覆盖"),
        required_roles=(),
        optional_roles=(R.POPULATION_MEASURE,),
        recommended_capabilities=("service_area", "spatial_join"),
        optional_capabilities=("network_accessibility", "admin_aggregation"),
        required_output_facets=("map", "legend", "title"),
        normalization_guidance="覆盖评价的分子是『被覆盖的需求量』而非『设施数』；有 population_measure 才能算覆盖率。",
        common_pitfalls=(
            "只画服务区不算覆盖率，无法回答『多少人被漏掉』",
        ),
    ),
    AnalysisPattern(
        id="spatial_equity",
        name_zh="空间公平",
        description="资源分配是否均衡：必须有分母（人均/地均）。",
        # Semantic V2（ADR-0098）：spatial_equity 现在是一等 intent task，
        # 不再只经 administrative_statistic 别名间接匹配。
        task_aliases=("spatial_equity", "administrative_statistic"),
        # VNext §15：警告关键词门与 intent 规则同词面（含英文与 分布合理/
        # 教育资源不足 变体）—— task 命中而关键词缺席会把披露静默吞掉。
        query_keywords=("均衡", "公平", "是否合理", "分布合理", "教育资源不足",
                        "资源不足", "差异", "人均", "差距",
                        "equity", "equitable", "fairness", "fairly", "balanced",
                        "underserved"),
        required_roles=(R.NORMALIZATION_DENOMINATOR,),
        optional_roles=(R.POPULATION_MEASURE, R.AREA_MEASURE),
        recommended_capabilities=("admin_aggregation", "admin_boundary_query"),
        optional_capabilities=("global_morans_i", "local_morans_i"),
        required_output_facets=("map", "statistics", "chart", "legend", "title"),
        normalization_guidance=(
            "公平性结论必须基于归一化比率（人均/地均），并与人口分布对照。"
        ),
        common_pitfalls=(
            "『学校多的区资源好』——没有人口分母时这只是数量差异",
            "把数量差异直接表述为『不公平』",
        ),
    ),
    AnalysisPattern(
        id="site_selection",
        name_zh="选址分析",
        description="多准则叠加选优：缓冲/可达/约束相交 + MCDA 候选评价。",
        # Semantic V2（ADR-0098）：一等 task；MCDA 候选评价进入推荐能力。
        task_aliases=("site_selection", "proximity_analysis"),
        query_keywords=("选址", "适合", "评估", "布局"),
        recommended_capabilities=("admin_boundary_query", "proximity_buffer",
                                  "spatial_join", "mcda_evaluation"),
        optional_capabilities=("service_area", "admin_aggregation"),
        required_output_facets=("map", "legend", "title"),
        normalization_guidance="多准则要先统一量纲（打分/分级）再叠加，不要把原始值直接相加；权重来源（用户指定/假设）必须显式记录。",
        common_pitfalls=("准则权重无依据地拍定", "把『离得近』当唯一准则",
                         "未声明硬约束（禁建区/保护区）就推荐候选"),
        decision_disclosures=(
            "准则与权重尚未声明：MCDA 评价前必须由用户确认准则集合与权重，"
            "或显式标记为默认假设 —— 绝不合成准则值。",
            "硬约束（禁建区/保护区/规划红线）未确认前，推荐结果只能作为"
            "初筛候选，不得表述为『最优选址』。",
        ),
        first_class_task="site_selection",
    ),
    AnalysisPattern(
        id="risk_exposure",
        name_zh="风险暴露",
        description="危险源与受体的空间叠加：影响范围内有多少暴露。",
        # Semantic V2（ADR-0098）：一等 task。
        task_aliases=("risk_exposure", "proximity_analysis", "accessibility_analysis"),
        query_keywords=("风险", "暴露", "影响范围", "安全"),
        recommended_capabilities=("proximity_buffer", "spatial_join", "admin_aggregation"),
        optional_capabilities=("zonal_statistics",),
        required_output_facets=("map", "statistics", "legend", "title"),
        normalization_guidance="暴露量 = 影响区内的受体量（人/户/设施），需要受体数据而不只是危险源；缓冲半径必须给出依据（规范/文献/用户声明）。",
        common_pitfalls=("只画影响范围不统计暴露受体", "缓冲半径无依据"),
        decision_disclosures=(
            "受体数据未确认：暴露评价需要影响范围内的受体（人口/设施）；"
            "只有危险源时只能呈现影响范围，不得表述为暴露量。",
            "缓冲半径依据未声明：半径值必须给出规范/文献/用户声明来源。",
        ),
        first_class_task="risk_exposure",
    ),
    AnalysisPattern(
        id="temporal_change",
        name_zh="时序变化",
        description="两期或多期对比：差值/变化检测/趋势。",
        task_aliases=("change_detection",),
        # Review F2: 「对比」 removed — purely SPATIAL comparisons (各区人口
        # 对比) fired the temporal disclosure; temporal semantics need an
        # explicit time signal (变化/两期/趋势/历年/时序/同比/环比).
        query_keywords=("变化", "两期", "趋势", "历年", "时序", "同比", "环比"),
        required_roles=(R.TEMPORAL_DIMENSION,),
        recommended_capabilities=("raster_change_detection", "temporal_trend", "temporal_aggregate"),
        optional_capabilities=("ndvi",),
        required_output_facets=("map", "legend", "title"),
        normalization_guidance="变化检测要说明基准期与比较期；栅格要配准/一致化后再相减。",
        common_pitfalls=("未配准的两期影像直接相减", "把季节差异说成趋势"),
    ),
    AnalysisPattern(
        id="mobility_flow",
        name_zh="出行流 / OD 分析",
        description="Origin-Destination 联系强度：主流向、走廊、集散地。",
        task_aliases=("mobility_flow",),
        query_keywords=("通勤", "出行", "客流", "od", "流向", "流动"),
        recommended_capabilities=("od_matrix", "od_flow_mapping"),
        optional_capabilities=("admin_boundary_query",),
        required_output_facets=("map", "table", "legend", "title"),
        normalization_guidance=(
            "流向宽度用 OD 权重（人次/班次）；比较区域间联系强度时考虑"
            "「总量规模」与「结构占比」两个口径，不要混用。"
        ),
        common_pitfalls=(
            "对所有 OD 对无差别画线导致视觉灾难（应 top-N/阈值过滤）",
            "把双向流拆成两条重复弧线而不聚合",
            "OD 点无坐标时强行以行政区质心代替并夸大精度",
        ),
    ),
    AnalysisPattern(
        id="suitability",
        name_zh="适宜性评价",
        description="多因子加权适宜性面：因子标准化 + 加权叠加。",
        # Semantic V2（ADR-0098）：一等 task；MCDA 评价进入推荐能力。
        task_aliases=("suitability_assessment", "site_selection", "proximity_analysis"),
        query_keywords=("适宜性", "适应性", "评价"),
        recommended_capabilities=("raster_reclassify", "raster_resample",
                                  "mcda_evaluation"),
        optional_capabilities=("geometry_clip", "proximity_buffer"),
        required_output_facets=("map", "legend", "title"),
        normalization_guidance="各因子重分类到统一等级再加权；权重敏感性要做说明；用户权重与观测证据必须可区分。",
        common_pitfalls=("量纲不一致直接叠加", "遗漏硬约束（禁建区等）"),
        decision_disclosures=(
            "因子与权重来源未声明：适宜性评价前必须确认因子清单、标准化"
            "方案与权重来源（用户指定/默认假设），并在结果中区分观测证据"
            "与假设。",
            "硬约束（禁建区/保护区）未确认前，适宜面只能作为初筛参考。",
        ),
        first_class_task="suitability_assessment",
    ),
)

_BY_ID: Dict[str, AnalysisPattern] = {p.id: p for p in PATTERNS}


def get_pattern(pattern_id: str) -> Optional[AnalysisPattern]:
    return _BY_ID.get(pattern_id)


def all_patterns() -> List[AnalysisPattern]:
    return list(PATTERNS)
