"""Quality Scenario Corpus（Wave 2+3）—— 质量场景语料：数据状态 × 语义族。

与 conformance 语料（C9，语义身份 × 表述扩展）互补：本库在语义族之上引入
**数据状态轴**（DATA_STATE_PROFILES）——同一句请求在不同数据事实（几何类别 /
CRS / 字段 / 样本量 / 方差 / 空值率 / 时序观测 / 规模 / 空集）下的规划契约。
全部离线、确定、零 LLM；plan tier 复用 GISBenchmarkRunner，数据资格 / 回退
契约经 compile_workflow（qualify_data 四态 + fallback_v3）裁决。

设计契约：

- **语义身份**复用已审定的 ``CONFORMANCE_FAMILIES`` 表（task/recipe/能力/
  警告码），本模块不另造第二套任务事实；
- **参考场景（reference）**= 一个 ``(family_id, data_state)`` 契约组合，携带
  机器可查契约：``expected_fallback_tier``（全对）+ ``expected_qualification``
  （角色→状态全图，有数据角色的族）+ 语义身份契约 + DSL 预算 / 工具类别 /
  导出格式 / 离线契约（按族校准附加）；
- **契约冻结表**（``_STATE_CONTRACTS``）是本模块的第二个审定工件：按提交时
  的 frozen compiler（qualify_data / fallback_v3 / plan_candidates）逐对标定。
  编译器行为变化 → 语料变红（这正是回归锁的语义）；更新冻结表必须显式
  重新审计，不得照抄运行时输出；
- **反过度反应稳定契约**：对无数据角色 / 无对应义务的族，数据状态契约
  锁定「无关事实不得降档」（如 POI 概览在零方差事实下仍 preferred）——
  这与 anti-claim（不得噪声披露）互为正反面，非空转断言；
- **spec-labeled 场景**：agent 行为类（cancel / retry / tool-failure /
  no-progress / follow-up / correction / provider-failure）在 plan tier
  只能锁定语义身份与情景标签（执行期 / 取消期语义属于后续 chaos /
  cancellation wave），``scenario_kind`` 照常标注，能力仅在真实成立处断言；
- **排除规则（显式记录）**：
  - `dissolve`（融合）目标项：系统无 dissolve 能力 / recipe，planner 无法
    诚实路由 —— 不虚构案例，待能力落地后补；
  - `too_few_samples` / `zero_variance` / `large_data` 对**无对应科学义务**
    的族不可表达为「状态特异」契约（义务层 degrade_with_disclosure 不改
    回退层）——这些对按稳定契约收录，不声明状态特异期望；
  - agent 行为类在 plan tier 不可诚实验证执行语义 —— 按 spec-labeled 收录。

反垃圾护栏（builder 内断言）：id 全局唯一；(family, state) 查询唯一（数据
状态以查询内显式声明消歧）；每族 profile 变体 ≤ 64；扩展顺序确定（零随机）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Tuple

from app.evaluation.case import GISBenchmarkCase
from app.evaluation.conformance import (
    CONFORMANCE_FAMILIES,
    ConformanceFamily,
    SCOPE_VARIANTS,
    UTTERANCE_VARIANTS,
)

# ── 数据状态画像（resolver camelCase 事实，与 DatasetProfile 同源）─────────

#: 公共基线事实：全事实满足的「好数据」。CRS 取 UTM 48N（成都，局部度量），
#: 字段含数值/分母候选/名称，时序 12 期，双波段带语义标注。
_BASE_FACTS: Dict[str, object] = {
    "featureCount": 200,
    "rowCount": 200,
    "numericSampleCount": 200,
    "crs": "EPSG:32648",
    "fields": {
        "value": {"type": "number"},
        "population": {"type": "number"},
        "name": {"type": "string"},
    },
    "numericFields": ["value", "population"],
    "valueVariance": 1.5,
    "hasTimeField": True,
    "temporalObservationCount": 12,
    "bandCount": 2,
    "bandSemantics": ["red", "nir"],
}


def _facts(**overrides: object) -> Dict[str, object]:
    """基线事实 + 覆盖项（纯函数；None = 删除该键）。"""
    out: Dict[str, object] = dict(_BASE_FACTS)
    for key, val in overrides.items():
        if val is None:
            out.pop(key, None)
        else:
            out[key] = val
    return out


@dataclass(frozen=True)
class DataStateProfile:
    """一个数据状态：画像事实 + 查询内声明后缀 + goal-doc 标签。

    ``query_suffix`` 拼接在族短语后，使每个 (family, state) 的查询字符串
    天然唯一（反垃圾护栏），同时让案例读起来像用户对数据状态的显式声明。
    """

    state_id: str
    label: str                       # 数据状态中文名（案例 description 用）
    query_suffix: str                # 追加在族短语后的声明（空 = 无声明）
    facts: Dict[str, object] = field(default_factory=dict)


#: 数据状态目录（12 态）。前 4 态只变几何类别；后 8 态为语义数据状态。
# ── R1 review 诚实性标注（GIS-science QA）────────────────────────────────
# 以下状态在 plan tier 对全部 59 族**实证惰性**（逐族与 nominal 的资格态
# 逐位相同，0/59 差异）：zero_variance / too_few_samples / large_data。
# 其语义闸位于 execute 层（scientific_preconditions 消费 valueVariance /
# 样本量事实；plan 资格面不读这些画像字段）。语料保留这些行是为了锁
# "惰性本身"（资格面升级时强制重审），但不应把行的存在当成 plan-tier
# 保护。接线后续：方差/样本量/投影义务纳入 qualify_data 输入面。
DATA_STATE_PROFILES: Tuple[DataStateProfile, ...] = (
    DataStateProfile("nominal_point", "点要素数据（全事实满足）", "",
                     _facts(geometryTypes=["Point"])),
    DataStateProfile("nominal_polygon", "面要素数据", "（数据为面要素）",
                     _facts(geometryTypes=["Polygon"])),
    DataStateProfile("nominal_raster", "栅格数据", "（数据为像元矩阵）",
                     _facts(geometryTypes=["Raster"])),
    DataStateProfile("geometry_line", "线要素数据（点/面/栅格族为几何错配态）",
                     "（数据为线要素）", _facts(geometryTypes=["LineString"])),
    DataStateProfile("bad_crs", "地理坐标系（度）数据，遇度量投影义务需重投影",
                     "（数据坐标系为WGS84经纬度）",
                     _facts(geometryTypes=["Point"], crs="EPSG:4326")),
    DataStateProfile("missing_required_field", "属性字段已知但无数值字段",
                     "（数据没有数值字段）",
                     _facts(geometryTypes=["Point"],
                            fields={"name": {"type": "string"}},
                            numericFields=[])),
    DataStateProfile("too_few_samples", "样本量低于方法学下限", "（样本只有3条）",
                     _facts(geometryTypes=["Point"], featureCount=3,
                            rowCount=3, numericSampleCount=3)),
    DataStateProfile("zero_variance", "数值场零方差（常量场）", "（数值字段全为常数）",
                     _facts(geometryTypes=["Point"], valueVariance=0.0)),
    DataStateProfile("large_data", "大数据量（近规模上限）", "（数据量约45万条）",
                     _facts(geometryTypes=["Point"], featureCount=450000,
                            rowCount=450000, numericSampleCount=450000)),
    DataStateProfile("empty_dataset", "空数据集", "（数据为空）",
                     _facts(geometryTypes=["Point"], featureCount=0,
                            rowCount=0, numericSampleCount=0)),
    DataStateProfile("high_null_ratio", "数值字段高空值率", "（数值字段八成为空值）",
                     _facts(geometryTypes=["Point"], fields={
                         "value": {"type": "number", "null_ratio": 0.8},
                         "population": {"type": "number"},
                         "name": {"type": "string"}})),
    DataStateProfile("temporal_insufficient", "时序观测不足", "（观测只有1期）",
                     _facts(geometryTypes=["Point"], temporalObservationCount=1)),
)

#: state_id → 索引（构建期校验 + 确定性查表）。
_STATE_BY_ID: Dict[str, DataStateProfile] = {s.state_id: s for s in DATA_STATE_PROFILES}

#: 数据语义状态（data_ 前缀 scenario_kind 的来源；几何态沿用族类别标签）。
_DATA_STATES = (
    "bad_crs", "missing_required_field", "too_few_samples", "zero_variance",
    "large_data", "empty_dataset", "high_null_ratio", "temporal_insufficient",
)
_GEOMETRY_STATES = ("nominal_point", "nominal_polygon", "nominal_raster", "geometry_line")

#: 分片测试用：数据状态类 scenario_kind 前缀。
DATA_SCENARIO_PREFIX = "data_"

# ── 族 → goal-doc 类别 / 标签（场景分片与 scenario_kind 的来源）────────────

#: conformance 族在 goal-doc 类别体系中的落位：(类别, goal 标签)。
#: 类别 ∈ {basemap, science}；制图 / 数据 / agent 类别见下方新族表与状态轴。
_FAMILY_CATEGORY: Dict[str, Tuple[str, str]] = {
    # ── basemap / 基础 GIS ────────────────────────────────────────────
    "edu-facility-distribution": ("basemap", "point_distribution"),
    "healthcare-distribution": ("basemap", "point_distribution"),
    "poi-inventory": ("basemap", "point_distribution"),
    "admin-feature-audit": ("basemap", "spatial_join"),
    "density-visual": ("basemap", "density"),
    "density-quantitative": ("basemap", "density"),
    "density-grid": ("basemap", "density"),
    "rate-normalized": ("basemap", "choropleth"),
    "voronoi-coverage": ("basemap", "accessibility"),
    "extent-delimitation": ("basemap", "extent"),
    "shortest-path": ("basemap", "route"),
    "closest-facility": ("basemap", "nearest"),
    "od-flow": ("basemap", "od"),
    "service-area": ("basemap", "accessibility"),
    "coverage-gap": ("basemap", "accessibility"),
    "exposure-buffer-screen": ("basemap", "buffer"),
    "greening-rate": ("basemap", "choropleth"),
    "transit-coverage": ("basemap", "accessibility"),
    "monitoring-coverage": ("basemap", "point_distribution"),
    "hazard-inventory": ("basemap", "inventory"),
    "clinic-coverage": ("basemap", "accessibility"),
    "road-centrality": ("basemap", "route"),
    "od-matrix": ("basemap", "od"),
    "categorical-composition": ("basemap", "choropleth"),
    # ── science / 专业分析 ────────────────────────────────────────────
    "global-autocorrelation": ("science", "autocorrelation"),
    "local-autocorrelation": ("science", "autocorrelation"),
    "hotspot-significance": ("science", "hotspot"),
    "spatial-regression": ("science", "regression"),
    "kriging-explicit": ("science", "interpolation"),
    "interpolation-generic": ("science", "interpolation"),
    "interpolation-uncertainty": ("science", "interpolation"),
    "slope-analysis": ("science", "terrain"),
    "hillshade-product": ("science", "terrain"),
    "viewshed-analysis": ("science", "visibility"),
    "contour-product": ("science", "terrain"),
    "watershed-delineation": ("science", "terrain"),
    "stream-extraction": ("science", "terrain"),
    "flood-extent-screen": ("science", "terrain"),
    "twi-index": ("science", "terrain"),
    "ndvi-monitor": ("science", "raster_index"),
    "landcover-map": ("science", "raster_index"),
    "cultivated-land": ("science", "land_inventory"),
    "bitemporal-optical-change": ("science", "change_detection"),
    "landcover-change": ("science", "change_detection"),
    "urban-expansion": ("science", "change_detection"),
    "change-comparison": ("science", "change_detection"),
    "sar-overview": ("science", "sar"),
    "sar-speckle": ("science", "sar"),
    "insar-deformation": ("science", "sar"),
    "linear-trend": ("science", "temporal"),
    "changepoint-detection": ("science", "temporal"),
    "interannual-comparison": ("science", "temporal"),
    "equity-generic": ("science", "equity"),
    "site-selection-generic": ("science", "site_selection"),
    "suitability-generic": ("science", "suitability"),
    "risk-generic": ("science", "risk"),
    "fire-risk": ("science", "risk"),
    "population-exposure": ("science", "risk"),
    "warehouse-site": ("science", "site_selection"),
}

#: 离线族：nominal 下 resolved 工具全部 network=False（校准事实）。
#: 契约按数据状态逐态声明（重路由态可能引入网络工具，如实收窄）。
_OFFLINE_FAMILY_STATES: Dict[str, Tuple[str, ...]] = {
    "changepoint-detection": (
        "nominal_point", "nominal_polygon", "geometry_line", "bad_crs",
        "missing_required_field", "too_few_samples", "zero_variance",
        "large_data", "empty_dataset", "high_null_ratio",
        "temporal_insufficient",
    ),
}


@dataclass(frozen=True)
class QualityFamily:
    """goal-doc 新增族（conformance 表未覆盖的制图 / agent 类别）。

    - cartography 族：task/recipe/facet/导出契约为审定事实（校准自 frozen
      planner），参与 plan tier 全量断言；
    - agent 族（spec_only=True）：plan tier 只能诚实锁定语义身份，执行期 /
      取消期语义由后续 chaos / cancellation wave 消费（见模块 docstring）。
    """

    family_id: str
    category: str                     # cartography | agent
    label: str                        # goal-doc 标签（chart / legend / cancel …）
    description: str
    phrases: Tuple[str, ...]          # [0] 为参考查询，其余为扩展短语
    expected_task: str = ""
    # spec 族短语的诚实落点可能跨任务族（如模糊请求 → distribution_overview
    # 或 simple_view）；非空时按 set 成员判定，覆盖单值断言。
    expected_tasks: Tuple[str, ...] = ()
    expected_recipe: str = ""
    expected_capabilities: Tuple[str, ...] = ()
    expected_product_facets: Tuple[str, ...] = ()
    expected_export_formats: Tuple[str, ...] = ()
    spec_only: bool = False


#: 制图族（thematic / chart / legend+colorbar / export；visibility 由
#: viewshed-analysis 族的 "visibility" 标签承载）。
CARTOGRAPHY_FAMILIES: Tuple[QualityFamily, ...] = (
    QualityFamily(
        "thematic-choropleth", "cartography", "thematic", "专题制图（行政专题图）",
        ("各区绿化率专题地图", "各区收入水平专题图"),
        expected_task="distribution_overview",
        expected_recipe="poi_distribution_overview",
        expected_capabilities=("poi_query", "admin_boundary_query"),
    ),
    QualityFamily(
        "chart-admin", "cartography", "chart", "行政区统计柱状图",
        ("各区小学数量做柱状图对比", "各区医院数量画柱状图"),
        expected_task="administrative_statistic",
        expected_recipe="administrative_choropleth",
        expected_capabilities=("poi_query", "admin_boundary_query", "admin_aggregation"),
        expected_product_facets=("chart",),
        expected_export_formats=("png", "csv"),
    ),
    QualityFamily(
        "chart-categorical", "cartography", "chart", "类别构成柱状图",
        ("设施类别构成柱状图", "POI类型占比柱状图"),
        expected_task="categorical_distribution",
        expected_recipe="categorical_distribution",
        expected_capabilities=("poi_query", "category_breakdown"),
        expected_product_facets=("chart",),
    ),
    QualityFamily(
        "legend-thematic", "cartography", "legend", "带图例的专题图",
        ("带图例的各区人口专题图", "带图例的各区绿地专题图"),
        expected_task="distribution_overview",
        expected_recipe="poi_distribution_overview",
        expected_capabilities=("poi_query", "admin_boundary_query"),
        expected_product_facets=("legend",),
    ),
    QualityFamily(
        "colorbar-thematic", "cartography", "colorbar", "带色带的密度专题图",
        ("各区人口密度专题图带图例和色带", "各区公园分布密度专题图带色带"),
        expected_task="distribution_overview",
        expected_recipe="poi_distribution_overview",
        expected_capabilities=("poi_query", "admin_boundary_query"),
        expected_product_facets=("legend",),
    ),
    QualityFamily(
        "export-png", "cartography", "export", "导出 PNG 出图",
        ("把各区绿化率地图导出为png", "把各区公园分布图导出为png"),
        expected_task="distribution_overview",
        expected_recipe="poi_distribution_overview",
        expected_capabilities=("poi_query", "admin_boundary_query"),
        expected_export_formats=("png",),
    ),
    QualityFamily(
        "export-pdf", "cartography", "export", "导出 PDF 出图",
        ("各区绿化率地图导出为pdf", "各区公园分布地图导出为pdf"),
        expected_task="distribution_overview",
        expected_recipe="poi_distribution_overview",
        expected_capabilities=("poi_query", "admin_boundary_query"),
        expected_export_formats=("pdf",),
    ),
    QualityFamily(
        "export-csv", "cartography", "export", "统计表导出 CSV",
        ("各区学校数量统计导出csv", "各区医院数量统计导出csv"),
        expected_task="administrative_statistic",
        expected_recipe="administrative_choropleth",
        expected_capabilities=("poi_query", "admin_boundary_query", "admin_aggregation"),
        expected_export_formats=("csv",),
    ),
)

#: agent 行为族（spec-labeled；见模块 docstring「spec-labeled 场景」）。
AGENT_FAMILIES: Tuple[QualityFamily, ...] = (
    QualityFamily(
        "agent-ambiguous", "agent", "ambiguous", "模糊请求",
        ("帮我分析一下这份数据", "帮我看看这些数据能做什么分析"),
        expected_tasks=("distribution_overview", "simple_view"),
        expected_recipe="poi_distribution_overview", spec_only=True,
    ),
    QualityFamily(
        "agent-followup", "agent", "followup", "追问扩展（范围上卷）",
        ("那把分析范围扩大到全省呢", "那再看看重庆呢"),
        expected_task="distribution_overview",
        expected_recipe="poi_distribution_overview", spec_only=True,
    ),
    QualityFamily(
        "agent-correction", "agent", "correction", "目标纠正",
        ("不对，我要的是坡度图，不是高程图", "说错了，重新做坡度分析"),
        expected_task="terrain_analysis",
        expected_recipe="slope_analysis_workflow", spec_only=True,
    ),
    QualityFamily(
        "agent-partial-data", "agent", "partial_data", "部分数据先行",
        ("只有部分区域的数据，先做能做的分析", "数据只覆盖两个区，先分析这部分"),
        expected_task="distribution_overview",
        expected_recipe="poi_distribution_overview", spec_only=True,
    ),
    QualityFamily(
        "agent-cancel", "agent", "cancel", "取消任务",
        ("取消当前的分析任务", "不用分析了，取消吧"),
        expected_task="distribution_overview",
        expected_recipe="poi_distribution_overview", spec_only=True,
    ),
    QualityFamily(
        "agent-retry", "agent", "retry", "重试请求",
        ("再试一次刚才的插值分析", "重新跑一遍刚才的克里金插值"),
        expected_task="raster_distribution",
        expected_recipe="raster_distribution", spec_only=True,
    ),
    QualityFamily(
        "agent-tool-failure", "agent", "tool_failure", "工具失败后的替代路径",
        ("刚才的查询工具失败了，换个方式查", "这个工具用不了，用本地数据再试"),
        expected_task="distribution_overview",
        expected_recipe="poi_distribution_overview", spec_only=True,
    ),
    QualityFamily(
        "agent-no-progress", "agent", "no_progress", "无进展追问",
        ("分析怎么还没出结果", "进度到哪一步了"),
        expected_task="distribution_overview",
        expected_recipe="poi_distribution_overview", spec_only=True,
    ),
    QualityFamily(
        "agent-provider-failure", "agent", "provider_failure", "数据服务不可用降级",
        ("数据服务暂时不可用，先用本地数据分析", "在线数据拿不到，先离线分析"),
        expected_task="distribution_overview",
        expected_recipe="poi_distribution_overview", spec_only=True,
    ),
)

# ── DSL 预算 / 工具类别契约（nominal 校准值；生成器冻结）───────────────────

#: family_id → (期望工具类别, 期望导出格式, 上下文 schema 预算字节)。
#: 标定于提交时的 frozen ToolRegistry（descriptor.output_semantic_type /
#: schema_size）；registry 契约变化 → 语料变红，需显式重标。
_TOOL_CONTRACTS: Dict[str, Tuple[Tuple[str, ...], Tuple[str, ...], int]] = {
    'admin-feature-audit': (('geojson_fc', 'stats'), ('csv', 'pdf', 'png'), 7168),
    'bitemporal-optical-change': (('list', 'ref', 'stats'), ('pdf', 'png'), 4096),
    'categorical-composition': (('geojson_fc', 'stats'), (), 4096),
    'change-comparison': (('list', 'ref', 'stats'), ('pdf', 'png'), 4096),
    'changepoint-detection': (('stats',), (), 3072),
    'clinic-coverage': (('geojson_fc', 'stats'), (), 4096),
    'closest-facility': (('geojson_fc', 'stats', 'table'), (), 7168),
    'contour-product': (('geojson_fc', 'list', 'stats'), (), 4096),
    'coverage-gap': (('geojson_fc', 'stats'), (), 4096),
    'cultivated-land': (('geojson_fc', 'stats'), ('pdf', 'png'), 8192),
    'density-grid': (('geojson_fc', 'stats'), ('png',), 6144),
    'density-quantitative': (('geojson_fc', 'stats'), ('csv', 'pdf', 'png'), 7168),
    'density-visual': (('geojson_fc', 'stats'), ('pdf', 'png'), 8192),
    'edu-facility-distribution': (('geojson_fc', 'stats'), ('csv', 'pdf', 'png'), 8192),
    'equity-generic': (('geojson_fc', 'stats'), (), 7168),
    'exposure-buffer-screen': (('geojson_fc',), (), 8192),
    'extent-delimitation': (('geojson_fc', 'stats'), ('pdf', 'png'), 8192),
    'fire-risk': (('geojson_fc',), (), 8192),
    'flood-extent-screen': (('geojson_fc', 'list', 'ref', 'stats'), (), 8192),
    'global-autocorrelation': (('geojson_fc', 'stats'), (), 5120),
    'greening-rate': (('geojson_fc', 'stats'), ('csv', 'pdf', 'png'), 7168),
    'hazard-inventory': (('geojson_fc',), (), 8192),
    'healthcare-distribution': (('geojson_fc', 'stats'), ('pdf', 'png'), 8192),
    'hillshade-product': (('list', 'stats'), (), 4096),
    'hotspot-significance': (('geojson_fc', 'stats'), (), 7168),
    'insar-deformation': (('list', 'stats'), (), 4096),
    'interannual-comparison': (('geojson_fc', 'stats', 'table'), (), 5120),
    'interpolation-generic': (('geojson_fc', 'list', 'stats'), ('pdf', 'png'), 3072),
    'interpolation-uncertainty': (('geojson_fc', 'list', 'stats'), ('pdf', 'png'), 3072),
    'kriging-explicit': (('geojson_fc', 'list', 'stats'), ('pdf', 'png'), 5120),
    'landcover-change': (('list', 'ref', 'stats'), ('pdf', 'png'), 4096),
    'landcover-map': (('list', 'stats'), ('pdf', 'png'), 3072),
    'linear-trend': (('geojson_fc', 'stats'), (), 5120),
    'local-autocorrelation': (('geojson_fc', 'stats'), (), 5120),
    'monitoring-coverage': (('geojson_fc', 'stats'), ('pdf', 'png'), 8192),
    'ndvi-monitor': (('list', 'stats'), ('pdf', 'png'), 3072),
    'od-flow': (('geojson_fc', 'stats', 'table'), (), 5120),
    'od-matrix': (('geojson_fc', 'stats', 'table'), (), 5120),
    'poi-inventory': (('geojson_fc', 'stats'), ('pdf', 'png'), 8192),
    'population-exposure': (('geojson_fc',), (), 8192),
    'rate-normalized': (('geojson_fc', 'stats'), ('csv', 'pdf', 'png'), 7168),
    'risk-generic': (('geojson_fc',), (), 8192),
    'road-centrality': (('geojson_fc', 'stats', 'table'), (), 7168),
    'sar-overview': (('list', 'stats'), (), 3072),
    'sar-speckle': (('list', 'stats'), (), 3072),
    'service-area': (('geojson_fc', 'stats'), (), 4096),
    'shortest-path': (('geojson_fc', 'stats', 'text'), (), 6144),
    'site-selection-generic': (('geojson_fc', 'stats'), (), 9216),
    'slope-analysis': (('geojson_fc', 'list', 'stats'), (), 7168),
    'spatial-regression': (('geojson_fc',), (), 6144),
    'stream-extraction': (('list', 'stats'), (), 4096),
    'suitability-generic': (('geojson_fc', 'list', 'ref', 'stats'), (), 7168),
    'transit-coverage': (('geojson_fc', 'stats'), (), 4096),
    'twi-index': (('geojson_fc', 'list', 'stats'), (), 7168),
    'urban-expansion': (('list', 'ref', 'stats'), ('pdf', 'png'), 4096),
    'viewshed-analysis': (('list', 'stats'), (), 5120),
    'voronoi-coverage': (('geojson_fc', 'stats'), (), 4096),
    'warehouse-site': (('geojson_fc', 'stats'), (), 9216),
    'watershed-delineation': (('geojson_fc', 'list', 'stats'), (), 6144),
}

# ── 数据状态契约冻结表（第二个审定工件；生成器填充）────────────────────────

#: (family_id, state_id) → (fallback_tier, {role: state})。
#: 按提交时的 frozen compiler 标定；不得照抄运行时输出更新（见 docstring）。
_STATE_CONTRACTS: Dict[Tuple[str, str], Tuple[str, Dict[str, str]]] = {
    ('edu-facility-distribution', 'nominal_point'): ('preferred', {}),  # 小学的分布情况
    ('edu-facility-distribution', 'nominal_polygon'): ('degraded', {}),  # 小学的分布情况（数据为面要素）
    ('edu-facility-distribution', 'nominal_raster'): ('preferred', {}),  # 小学的分布情况（数据为像元矩阵）
    ('edu-facility-distribution', 'geometry_line'): ('degraded', {}),  # 小学的分布情况（数据为线要素）
    ('edu-facility-distribution', 'bad_crs'): ('preferred', {}),  # 小学的分布情况（数据坐标系为WGS84经纬度）
    ('edu-facility-distribution', 'missing_required_field'): ('preferred', {}),  # 小学的分布情况（数据没有数值字段）
    ('edu-facility-distribution', 'too_few_samples'): ('preferred', {}),  # 小学的分布情况（样本只有3条）
    ('edu-facility-distribution', 'zero_variance'): ('preferred', {}),  # 小学的分布情况（数值字段全为常数）
    ('edu-facility-distribution', 'large_data'): ('preferred', {}),  # 小学的分布情况（数据量约45万条）
    ('edu-facility-distribution', 'empty_dataset'): ('preferred', {}),  # 小学的分布情况（数据为空）
    ('edu-facility-distribution', 'high_null_ratio'): ('preferred', {}),  # 小学的分布情况（数值字段八成为空值）
    ('edu-facility-distribution', 'temporal_insufficient'): ('preferred', {}),  # 小学的分布情况（观测只有1期）
    ('healthcare-distribution', 'nominal_point'): ('preferred', {}),  # 医院的分布情况
    ('healthcare-distribution', 'nominal_polygon'): ('degraded', {}),  # 医院的分布情况（数据为面要素）
    ('healthcare-distribution', 'nominal_raster'): ('preferred', {}),  # 医院的分布情况（数据为像元矩阵）
    ('healthcare-distribution', 'geometry_line'): ('degraded', {}),  # 医院的分布情况（数据为线要素）
    ('healthcare-distribution', 'bad_crs'): ('preferred', {}),  # 医院的分布情况（数据坐标系为WGS84经纬度）
    ('healthcare-distribution', 'missing_required_field'): ('preferred', {}),  # 医院的分布情况（数据没有数值字段）
    ('healthcare-distribution', 'too_few_samples'): ('preferred', {}),  # 医院的分布情况（样本只有3条）
    ('healthcare-distribution', 'zero_variance'): ('preferred', {}),  # 医院的分布情况（数值字段全为常数）
    ('healthcare-distribution', 'large_data'): ('preferred', {}),  # 医院的分布情况（数据量约45万条）
    ('healthcare-distribution', 'empty_dataset'): ('preferred', {}),  # 医院的分布情况（数据为空）
    ('healthcare-distribution', 'high_null_ratio'): ('preferred', {}),  # 医院的分布情况（数值字段八成为空值）
    ('healthcare-distribution', 'temporal_insufficient'): ('preferred', {}),  # 医院的分布情况（观测只有1期）
    ('poi-inventory', 'nominal_point'): ('preferred', {}),  # 咖啡馆要素清单
    ('poi-inventory', 'nominal_polygon'): ('degraded', {}),  # 咖啡馆要素清单（数据为面要素）
    ('poi-inventory', 'nominal_raster'): ('preferred', {}),  # 咖啡馆要素清单（数据为像元矩阵）
    ('poi-inventory', 'geometry_line'): ('degraded', {}),  # 咖啡馆要素清单（数据为线要素）
    ('poi-inventory', 'bad_crs'): ('preferred', {}),  # 咖啡馆要素清单（数据坐标系为WGS84经纬度）
    ('poi-inventory', 'missing_required_field'): ('preferred', {}),  # 咖啡馆要素清单（数据没有数值字段）
    ('poi-inventory', 'too_few_samples'): ('preferred', {}),  # 咖啡馆要素清单（样本只有3条）
    ('poi-inventory', 'zero_variance'): ('preferred', {}),  # 咖啡馆要素清单（数值字段全为常数）
    ('poi-inventory', 'large_data'): ('preferred', {}),  # 咖啡馆要素清单（数据量约45万条）
    ('poi-inventory', 'empty_dataset'): ('preferred', {}),  # 咖啡馆要素清单（数据为空）
    ('poi-inventory', 'high_null_ratio'): ('preferred', {}),  # 咖啡馆要素清单（数值字段八成为空值）
    ('poi-inventory', 'temporal_insufficient'): ('preferred', {}),  # 咖啡馆要素清单（观测只有1期）
    ('admin-feature-audit', 'nominal_point'): ('preferred', {}),  # 小学各街道数量清查
    ('admin-feature-audit', 'nominal_polygon'): ('degraded', {}),  # 小学各街道数量清查（数据为面要素）
    ('admin-feature-audit', 'nominal_raster'): ('preferred', {}),  # 小学各街道数量清查（数据为像元矩阵）
    ('admin-feature-audit', 'geometry_line'): ('degraded', {}),  # 小学各街道数量清查（数据为线要素）
    ('admin-feature-audit', 'bad_crs'): ('preferred', {}),  # 小学各街道数量清查（数据坐标系为WGS84经纬度）
    ('admin-feature-audit', 'missing_required_field'): ('preferred', {}),  # 小学各街道数量清查（数据没有数值字段）
    ('admin-feature-audit', 'too_few_samples'): ('preferred', {}),  # 小学各街道数量清查（样本只有3条）
    ('admin-feature-audit', 'zero_variance'): ('preferred', {}),  # 小学各街道数量清查（数值字段全为常数）
    ('admin-feature-audit', 'large_data'): ('preferred', {}),  # 小学各街道数量清查（数据量约45万条）
    ('admin-feature-audit', 'empty_dataset'): ('preferred', {}),  # 小学各街道数量清查（数据为空）
    ('admin-feature-audit', 'high_null_ratio'): ('preferred', {}),  # 小学各街道数量清查（数值字段八成为空值）
    ('admin-feature-audit', 'temporal_insufficient'): ('preferred', {}),  # 小学各街道数量清查（观测只有1期）
    ('density-visual', 'nominal_point'): ('preferred', {}),  # 餐饮店的疏密态势
    ('density-visual', 'nominal_polygon'): ('degraded', {}),  # 餐饮店的疏密态势（数据为面要素）
    ('density-visual', 'nominal_raster'): ('preferred', {}),  # 餐饮店的疏密态势（数据为像元矩阵）
    ('density-visual', 'geometry_line'): ('degraded', {}),  # 餐饮店的疏密态势（数据为线要素）
    ('density-visual', 'bad_crs'): ('preferred', {}),  # 餐饮店的疏密态势（数据坐标系为WGS84经纬度）
    ('density-visual', 'missing_required_field'): ('preferred', {}),  # 餐饮店的疏密态势（数据没有数值字段）
    ('density-visual', 'too_few_samples'): ('preferred', {}),  # 餐饮店的疏密态势（样本只有3条）
    ('density-visual', 'zero_variance'): ('preferred', {}),  # 餐饮店的疏密态势（数值字段全为常数）
    ('density-visual', 'large_data'): ('preferred', {}),  # 餐饮店的疏密态势（数据量约45万条）
    ('density-visual', 'empty_dataset'): ('preferred', {}),  # 餐饮店的疏密态势（数据为空）
    ('density-visual', 'high_null_ratio'): ('preferred', {}),  # 餐饮店的疏密态势（数值字段八成为空值）
    ('density-visual', 'temporal_insufficient'): ('preferred', {}),  # 餐饮店的疏密态势（观测只有1期）
    ('density-quantitative', 'nominal_point'): ('preferred', {}),  # 每平方公里人口密度
    ('density-quantitative', 'nominal_polygon'): ('degraded', {}),  # 每平方公里人口密度（数据为面要素）
    ('density-quantitative', 'nominal_raster'): ('preferred', {}),  # 每平方公里人口密度（数据为像元矩阵）
    ('density-quantitative', 'geometry_line'): ('degraded', {}),  # 每平方公里人口密度（数据为线要素）
    ('density-quantitative', 'bad_crs'): ('preferred', {}),  # 每平方公里人口密度（数据坐标系为WGS84经纬度）
    ('density-quantitative', 'missing_required_field'): ('preferred', {}),  # 每平方公里人口密度（数据没有数值字段）
    ('density-quantitative', 'too_few_samples'): ('preferred', {}),  # 每平方公里人口密度（样本只有3条）
    ('density-quantitative', 'zero_variance'): ('preferred', {}),  # 每平方公里人口密度（数值字段全为常数）
    ('density-quantitative', 'large_data'): ('preferred', {}),  # 每平方公里人口密度（数据量约45万条）
    ('density-quantitative', 'empty_dataset'): ('preferred', {}),  # 每平方公里人口密度（数据为空）
    ('density-quantitative', 'high_null_ratio'): ('preferred', {}),  # 每平方公里人口密度（数值字段八成为空值）
    ('density-quantitative', 'temporal_insufficient'): ('preferred', {}),  # 每平方公里人口密度（观测只有1期）
    ('density-grid', 'nominal_point'): ('preferred', {}),  # 写字楼格网聚合
    ('density-grid', 'nominal_polygon'): ('degraded', {}),  # 写字楼格网聚合（数据为面要素）
    ('density-grid', 'nominal_raster'): ('preferred', {}),  # 写字楼格网聚合（数据为像元矩阵）
    ('density-grid', 'geometry_line'): ('degraded', {}),  # 写字楼格网聚合（数据为线要素）
    ('density-grid', 'bad_crs'): ('preferred', {}),  # 写字楼格网聚合（数据坐标系为WGS84经纬度）
    ('density-grid', 'missing_required_field'): ('preferred', {}),  # 写字楼格网聚合（数据没有数值字段）
    ('density-grid', 'too_few_samples'): ('preferred', {}),  # 写字楼格网聚合（样本只有3条）
    ('density-grid', 'zero_variance'): ('preferred', {}),  # 写字楼格网聚合（数值字段全为常数）
    ('density-grid', 'large_data'): ('preferred', {}),  # 写字楼格网聚合（数据量约45万条）
    ('density-grid', 'empty_dataset'): ('preferred', {}),  # 写字楼格网聚合（数据为空）
    ('density-grid', 'high_null_ratio'): ('preferred', {}),  # 写字楼格网聚合（数值字段八成为空值）
    ('density-grid', 'temporal_insufficient'): ('preferred', {}),  # 写字楼格网聚合（观测只有1期）
    ('global-autocorrelation', 'nominal_point'): ('degraded', {'subject': 'eligible', 'boundary': 'degraded', 'measure': 'degraded'}),  # 房价的空间自相关分析
    ('global-autocorrelation', 'nominal_polygon'): ('degraded', {'subject': 'transform_required', 'boundary': 'eligible', 'measure': 'eligible'}),  # 房价的空间自相关分析（数据为面要素）
    ('global-autocorrelation', 'nominal_raster'): ('preferred', {'subject': 'eligible', 'boundary': 'eligible', 'measure': 'eligible'}),  # 房价的空间自相关分析（数据为像元矩阵）
    ('global-autocorrelation', 'geometry_line'): ('minimal', {'subject': 'degraded', 'boundary': 'degraded', 'measure': 'degraded'}),  # 房价的空间自相关分析（数据为线要素）
    ('global-autocorrelation', 'bad_crs'): ('degraded', {'subject': 'eligible', 'boundary': 'degraded', 'measure': 'degraded'}),  # 房价的空间自相关分析（数据坐标系为WGS84经纬度）
    ('global-autocorrelation', 'missing_required_field'): ('blocked', {'subject': 'eligible', 'boundary': 'degraded', 'measure': 'degraded'}),  # 房价的空间自相关分析（数据没有数值字段）
    ('global-autocorrelation', 'too_few_samples'): ('degraded', {'subject': 'eligible', 'boundary': 'degraded', 'measure': 'degraded'}),  # 房价的空间自相关分析（样本只有3条）
    ('global-autocorrelation', 'zero_variance'): ('degraded', {'subject': 'eligible', 'boundary': 'degraded', 'measure': 'degraded'}),  # 房价的空间自相关分析（数值字段全为常数）
    ('global-autocorrelation', 'large_data'): ('degraded', {'subject': 'eligible', 'boundary': 'degraded', 'measure': 'degraded'}),  # 房价的空间自相关分析（数据量约45万条）
    ('global-autocorrelation', 'empty_dataset'): ('blocked', {'subject': 'blocked', 'boundary': 'blocked', 'measure': 'blocked'}),  # 房价的空间自相关分析（数据为空）
    ('global-autocorrelation', 'high_null_ratio'): ('degraded', {'subject': 'transform_required', 'boundary': 'degraded', 'measure': 'degraded'}),  # 房价的空间自相关分析（数值字段八成为空值）
    ('global-autocorrelation', 'temporal_insufficient'): ('degraded', {'subject': 'eligible', 'boundary': 'degraded', 'measure': 'degraded'}),  # 房价的空间自相关分析（观测只有1期）
    ('local-autocorrelation', 'nominal_point'): ('degraded', {'subject': 'eligible', 'boundary': 'degraded', 'measure': 'degraded'}),  # 房价局部自相关分析
    ('local-autocorrelation', 'nominal_polygon'): ('degraded', {'subject': 'transform_required', 'boundary': 'eligible', 'measure': 'eligible'}),  # 房价局部自相关分析（数据为面要素）
    ('local-autocorrelation', 'nominal_raster'): ('preferred', {'subject': 'eligible', 'boundary': 'eligible', 'measure': 'eligible'}),  # 房价局部自相关分析（数据为像元矩阵）
    ('local-autocorrelation', 'geometry_line'): ('minimal', {'subject': 'degraded', 'boundary': 'degraded', 'measure': 'degraded'}),  # 房价局部自相关分析（数据为线要素）
    ('local-autocorrelation', 'bad_crs'): ('degraded', {'subject': 'eligible', 'boundary': 'degraded', 'measure': 'degraded'}),  # 房价局部自相关分析（数据坐标系为WGS84经纬度）
    ('local-autocorrelation', 'missing_required_field'): ('blocked', {'subject': 'eligible', 'boundary': 'degraded', 'measure': 'degraded'}),  # 房价局部自相关分析（数据没有数值字段）
    ('local-autocorrelation', 'too_few_samples'): ('degraded', {'subject': 'eligible', 'boundary': 'degraded', 'measure': 'degraded'}),  # 房价局部自相关分析（样本只有3条）
    ('local-autocorrelation', 'zero_variance'): ('degraded', {'subject': 'eligible', 'boundary': 'degraded', 'measure': 'degraded'}),  # 房价局部自相关分析（数值字段全为常数）
    ('local-autocorrelation', 'large_data'): ('degraded', {'subject': 'eligible', 'boundary': 'degraded', 'measure': 'degraded'}),  # 房价局部自相关分析（数据量约45万条）
    ('local-autocorrelation', 'empty_dataset'): ('blocked', {'subject': 'blocked', 'boundary': 'blocked', 'measure': 'blocked'}),  # 房价局部自相关分析（数据为空）
    ('local-autocorrelation', 'high_null_ratio'): ('degraded', {'subject': 'transform_required', 'boundary': 'degraded', 'measure': 'degraded'}),  # 房价局部自相关分析（数值字段八成为空值）
    ('local-autocorrelation', 'temporal_insufficient'): ('degraded', {'subject': 'eligible', 'boundary': 'degraded', 'measure': 'degraded'}),  # 房价局部自相关分析（观测只有1期）
    ('hotspot-significance', 'nominal_point'): ('preferred', {}),  # 各区案件量的显著性热点
    ('hotspot-significance', 'nominal_polygon'): ('degraded', {}),  # 各区案件量的显著性热点（数据为面要素）
    ('hotspot-significance', 'nominal_raster'): ('preferred', {}),  # 各区案件量的显著性热点（数据为像元矩阵）
    ('hotspot-significance', 'geometry_line'): ('degraded', {}),  # 各区案件量的显著性热点（数据为线要素）
    ('hotspot-significance', 'bad_crs'): ('preferred', {}),  # 各区案件量的显著性热点（数据坐标系为WGS84经纬度）
    ('hotspot-significance', 'missing_required_field'): ('preferred', {}),  # 各区案件量的显著性热点（数据没有数值字段）
    ('hotspot-significance', 'too_few_samples'): ('preferred', {}),  # 各区案件量的显著性热点（样本只有3条）
    ('hotspot-significance', 'zero_variance'): ('preferred', {}),  # 各区案件量的显著性热点（数值字段全为常数）
    ('hotspot-significance', 'large_data'): ('preferred', {}),  # 各区案件量的显著性热点（数据量约45万条）
    ('hotspot-significance', 'empty_dataset'): ('degraded', {}),  # 各区案件量的显著性热点（数据为空）
    ('hotspot-significance', 'high_null_ratio'): ('preferred', {}),  # 各区案件量的显著性热点（数值字段八成为空值）
    ('hotspot-significance', 'temporal_insufficient'): ('preferred', {}),  # 各区案件量的显著性热点（观测只有1期）
    ('rate-normalized', 'nominal_point'): ('preferred', {}),  # 各区商店占比统计
    ('rate-normalized', 'nominal_polygon'): ('degraded', {}),  # 各区商店占比统计（数据为面要素）
    ('rate-normalized', 'nominal_raster'): ('preferred', {}),  # 各区商店占比统计（数据为像元矩阵）
    ('rate-normalized', 'geometry_line'): ('degraded', {}),  # 各区商店占比统计（数据为线要素）
    ('rate-normalized', 'bad_crs'): ('preferred', {}),  # 各区商店占比统计（数据坐标系为WGS84经纬度）
    ('rate-normalized', 'missing_required_field'): ('preferred', {}),  # 各区商店占比统计（数据没有数值字段）
    ('rate-normalized', 'too_few_samples'): ('preferred', {}),  # 各区商店占比统计（样本只有3条）
    ('rate-normalized', 'zero_variance'): ('preferred', {}),  # 各区商店占比统计（数值字段全为常数）
    ('rate-normalized', 'large_data'): ('preferred', {}),  # 各区商店占比统计（数据量约45万条）
    ('rate-normalized', 'empty_dataset'): ('preferred', {}),  # 各区商店占比统计（数据为空）
    ('rate-normalized', 'high_null_ratio'): ('preferred', {}),  # 各区商店占比统计（数值字段八成为空值）
    ('rate-normalized', 'temporal_insufficient'): ('preferred', {}),  # 各区商店占比统计（观测只有1期）
    ('voronoi-coverage', 'nominal_point'): ('preferred', {}),  # 诊所服务域划分
    ('voronoi-coverage', 'nominal_polygon'): ('degraded', {}),  # 诊所服务域划分（数据为面要素）
    ('voronoi-coverage', 'nominal_raster'): ('preferred', {}),  # 诊所服务域划分（数据为像元矩阵）
    ('voronoi-coverage', 'geometry_line'): ('degraded', {}),  # 诊所服务域划分（数据为线要素）
    ('voronoi-coverage', 'bad_crs'): ('preferred', {}),  # 诊所服务域划分（数据坐标系为WGS84经纬度）
    ('voronoi-coverage', 'missing_required_field'): ('preferred', {}),  # 诊所服务域划分（数据没有数值字段）
    ('voronoi-coverage', 'too_few_samples'): ('preferred', {}),  # 诊所服务域划分（样本只有3条）
    ('voronoi-coverage', 'zero_variance'): ('preferred', {}),  # 诊所服务域划分（数值字段全为常数）
    ('voronoi-coverage', 'large_data'): ('preferred', {}),  # 诊所服务域划分（数据量约45万条）
    ('voronoi-coverage', 'empty_dataset'): ('preferred', {}),  # 诊所服务域划分（数据为空）
    ('voronoi-coverage', 'high_null_ratio'): ('preferred', {}),  # 诊所服务域划分（数值字段八成为空值）
    ('voronoi-coverage', 'temporal_insufficient'): ('preferred', {}),  # 诊所服务域划分（观测只有1期）
    ('extent-delimitation', 'nominal_point'): ('preferred', {}),  # 共享单车分布范围
    ('extent-delimitation', 'nominal_polygon'): ('degraded', {}),  # 共享单车分布范围（数据为面要素）
    ('extent-delimitation', 'nominal_raster'): ('preferred', {}),  # 共享单车分布范围（数据为像元矩阵）
    ('extent-delimitation', 'geometry_line'): ('degraded', {}),  # 共享单车分布范围（数据为线要素）
    ('extent-delimitation', 'bad_crs'): ('preferred', {}),  # 共享单车分布范围（数据坐标系为WGS84经纬度）
    ('extent-delimitation', 'missing_required_field'): ('preferred', {}),  # 共享单车分布范围（数据没有数值字段）
    ('extent-delimitation', 'too_few_samples'): ('preferred', {}),  # 共享单车分布范围（样本只有3条）
    ('extent-delimitation', 'zero_variance'): ('preferred', {}),  # 共享单车分布范围（数值字段全为常数）
    ('extent-delimitation', 'large_data'): ('preferred', {}),  # 共享单车分布范围（数据量约45万条）
    ('extent-delimitation', 'empty_dataset'): ('preferred', {}),  # 共享单车分布范围（数据为空）
    ('extent-delimitation', 'high_null_ratio'): ('preferred', {}),  # 共享单车分布范围（数值字段八成为空值）
    ('extent-delimitation', 'temporal_insufficient'): ('preferred', {}),  # 共享单车分布范围（观测只有1期）
    ('kriging-explicit', 'nominal_point'): ('preferred', {}),  # 克里金插值分析气温
    ('kriging-explicit', 'nominal_polygon'): ('degraded', {}),  # 克里金插值分析气温（数据为面要素）
    ('kriging-explicit', 'nominal_raster'): ('preferred', {}),  # 克里金插值分析气温（数据为像元矩阵）
    ('kriging-explicit', 'geometry_line'): ('degraded', {}),  # 克里金插值分析气温（数据为线要素）
    ('kriging-explicit', 'bad_crs'): ('preferred', {}),  # 克里金插值分析气温（数据坐标系为WGS84经纬度）
    ('kriging-explicit', 'missing_required_field'): ('degraded', {}),  # 克里金插值分析气温（数据没有数值字段）
    ('kriging-explicit', 'too_few_samples'): ('preferred', {}),  # 克里金插值分析气温（样本只有3条）
    ('kriging-explicit', 'zero_variance'): ('preferred', {}),  # 克里金插值分析气温（数值字段全为常数）
    ('kriging-explicit', 'large_data'): ('preferred', {}),  # 克里金插值分析气温（数据量约45万条）
    ('kriging-explicit', 'empty_dataset'): ('degraded', {}),  # 克里金插值分析气温（数据为空）
    ('kriging-explicit', 'high_null_ratio'): ('preferred', {}),  # 克里金插值分析气温（数值字段八成为空值）
    ('kriging-explicit', 'temporal_insufficient'): ('preferred', {}),  # 克里金插值分析气温（观测只有1期）
    ('interpolation-generic', 'nominal_point'): ('preferred', {}),  # 气温插值成面
    ('interpolation-generic', 'nominal_polygon'): ('degraded', {}),  # 气温插值成面（数据为面要素）
    ('interpolation-generic', 'nominal_raster'): ('preferred', {}),  # 气温插值成面（数据为像元矩阵）
    ('interpolation-generic', 'geometry_line'): ('degraded', {}),  # 气温插值成面（数据为线要素）
    ('interpolation-generic', 'bad_crs'): ('preferred', {}),  # 气温插值成面（数据坐标系为WGS84经纬度）
    ('interpolation-generic', 'missing_required_field'): ('degraded', {}),  # 气温插值成面（数据没有数值字段）
    ('interpolation-generic', 'too_few_samples'): ('preferred', {}),  # 气温插值成面（样本只有3条）
    ('interpolation-generic', 'zero_variance'): ('preferred', {}),  # 气温插值成面（数值字段全为常数）
    ('interpolation-generic', 'large_data'): ('preferred', {}),  # 气温插值成面（数据量约45万条）
    ('interpolation-generic', 'empty_dataset'): ('degraded', {}),  # 气温插值成面（数据为空）
    ('interpolation-generic', 'high_null_ratio'): ('preferred', {}),  # 气温插值成面（数值字段八成为空值）
    ('interpolation-generic', 'temporal_insufficient'): ('preferred', {}),  # 气温插值成面（观测只有1期）
    ('slope-analysis', 'nominal_point'): ('minimal', {'elevation': 'degraded'}),  # 坡度分析
    ('slope-analysis', 'nominal_polygon'): ('minimal', {'elevation': 'degraded'}),  # 坡度分析（数据为面要素）
    ('slope-analysis', 'nominal_raster'): ('preferred', {'elevation': 'eligible'}),  # 坡度分析（数据为像元矩阵）
    ('slope-analysis', 'geometry_line'): ('minimal', {'elevation': 'degraded'}),  # 坡度分析（数据为线要素）
    ('slope-analysis', 'bad_crs'): ('minimal', {'elevation': 'degraded'}),  # 坡度分析（数据坐标系为WGS84经纬度）
    ('slope-analysis', 'missing_required_field'): ('minimal', {'elevation': 'degraded'}),  # 坡度分析（数据没有数值字段）
    ('slope-analysis', 'too_few_samples'): ('minimal', {'elevation': 'degraded'}),  # 坡度分析（样本只有3条）
    ('slope-analysis', 'zero_variance'): ('minimal', {'elevation': 'degraded'}),  # 坡度分析（数值字段全为常数）
    ('slope-analysis', 'large_data'): ('minimal', {'elevation': 'degraded'}),  # 坡度分析（数据量约45万条）
    ('slope-analysis', 'empty_dataset'): ('blocked', {'elevation': 'blocked'}),  # 坡度分析（数据为空）
    ('slope-analysis', 'high_null_ratio'): ('minimal', {'elevation': 'degraded'}),  # 坡度分析（数值字段八成为空值）
    ('slope-analysis', 'temporal_insufficient'): ('minimal', {'elevation': 'degraded'}),  # 坡度分析（观测只有1期）
    ('hillshade-product', 'nominal_point'): ('minimal', {'elevation': 'degraded'}),  # 山体阴影渲染
    ('hillshade-product', 'nominal_polygon'): ('minimal', {'elevation': 'degraded'}),  # 山体阴影渲染（数据为面要素）
    ('hillshade-product', 'nominal_raster'): ('preferred', {'elevation': 'eligible'}),  # 山体阴影渲染（数据为像元矩阵）
    ('hillshade-product', 'geometry_line'): ('minimal', {'elevation': 'degraded'}),  # 山体阴影渲染（数据为线要素）
    ('hillshade-product', 'bad_crs'): ('minimal', {'elevation': 'degraded'}),  # 山体阴影渲染（数据坐标系为WGS84经纬度）
    ('hillshade-product', 'missing_required_field'): ('minimal', {'elevation': 'degraded'}),  # 山体阴影渲染（数据没有数值字段）
    ('hillshade-product', 'too_few_samples'): ('minimal', {'elevation': 'degraded'}),  # 山体阴影渲染（样本只有3条）
    ('hillshade-product', 'zero_variance'): ('minimal', {'elevation': 'degraded'}),  # 山体阴影渲染（数值字段全为常数）
    ('hillshade-product', 'large_data'): ('minimal', {'elevation': 'degraded'}),  # 山体阴影渲染（数据量约45万条）
    ('hillshade-product', 'empty_dataset'): ('blocked', {'elevation': 'blocked'}),  # 山体阴影渲染（数据为空）
    ('hillshade-product', 'high_null_ratio'): ('minimal', {'elevation': 'degraded'}),  # 山体阴影渲染（数值字段八成为空值）
    ('hillshade-product', 'temporal_insufficient'): ('minimal', {'elevation': 'degraded'}),  # 山体阴影渲染（观测只有1期）
    ('viewshed-analysis', 'nominal_point'): ('degraded', {'elevation': 'degraded', 'reference': 'eligible'}),  # 瞭望塔视域分析
    ('viewshed-analysis', 'nominal_polygon'): ('degraded', {'elevation': 'degraded', 'reference': 'transform_required'}),  # 瞭望塔视域分析（数据为面要素）
    ('viewshed-analysis', 'nominal_raster'): ('preferred', {'elevation': 'eligible', 'reference': 'eligible'}),  # 瞭望塔视域分析（数据为像元矩阵）
    ('viewshed-analysis', 'geometry_line'): ('minimal', {'elevation': 'degraded', 'reference': 'degraded'}),  # 瞭望塔视域分析（数据为线要素）
    ('viewshed-analysis', 'bad_crs'): ('degraded', {'elevation': 'degraded', 'reference': 'transform_required'}),  # 瞭望塔视域分析（数据坐标系为WGS84经纬度）
    ('viewshed-analysis', 'missing_required_field'): ('degraded', {'elevation': 'degraded', 'reference': 'eligible'}),  # 瞭望塔视域分析（数据没有数值字段）
    ('viewshed-analysis', 'too_few_samples'): ('degraded', {'elevation': 'degraded', 'reference': 'eligible'}),  # 瞭望塔视域分析（样本只有3条）
    ('viewshed-analysis', 'zero_variance'): ('degraded', {'elevation': 'degraded', 'reference': 'eligible'}),  # 瞭望塔视域分析（数值字段全为常数）
    ('viewshed-analysis', 'large_data'): ('degraded', {'elevation': 'degraded', 'reference': 'eligible'}),  # 瞭望塔视域分析（数据量约45万条）
    ('viewshed-analysis', 'empty_dataset'): ('blocked', {'elevation': 'blocked', 'reference': 'blocked'}),  # 瞭望塔视域分析（数据为空）
    ('viewshed-analysis', 'high_null_ratio'): ('degraded', {'elevation': 'degraded', 'reference': 'eligible'}),  # 瞭望塔视域分析（数值字段八成为空值）
    ('viewshed-analysis', 'temporal_insufficient'): ('degraded', {'elevation': 'degraded', 'reference': 'eligible'}),  # 瞭望塔视域分析（观测只有1期）
    ('contour-product', 'nominal_point'): ('minimal', {'elevation': 'degraded'}),  # 等高线图
    ('contour-product', 'nominal_polygon'): ('minimal', {'elevation': 'degraded'}),  # 等高线图（数据为面要素）
    ('contour-product', 'nominal_raster'): ('preferred', {'elevation': 'eligible'}),  # 等高线图（数据为像元矩阵）
    ('contour-product', 'geometry_line'): ('minimal', {'elevation': 'degraded'}),  # 等高线图（数据为线要素）
    ('contour-product', 'bad_crs'): ('minimal', {'elevation': 'degraded'}),  # 等高线图（数据坐标系为WGS84经纬度）
    ('contour-product', 'missing_required_field'): ('minimal', {'elevation': 'degraded'}),  # 等高线图（数据没有数值字段）
    ('contour-product', 'too_few_samples'): ('minimal', {'elevation': 'degraded'}),  # 等高线图（样本只有3条）
    ('contour-product', 'zero_variance'): ('minimal', {'elevation': 'degraded'}),  # 等高线图（数值字段全为常数）
    ('contour-product', 'large_data'): ('minimal', {'elevation': 'degraded'}),  # 等高线图（数据量约45万条）
    ('contour-product', 'empty_dataset'): ('blocked', {'elevation': 'blocked'}),  # 等高线图（数据为空）
    ('contour-product', 'high_null_ratio'): ('minimal', {'elevation': 'degraded'}),  # 等高线图（数值字段八成为空值）
    ('contour-product', 'temporal_insufficient'): ('minimal', {'elevation': 'degraded'}),  # 等高线图（观测只有1期）
    ('watershed-delineation', 'nominal_point'): ('minimal', {'elevation': 'degraded'}),  # 流域划分
    ('watershed-delineation', 'nominal_polygon'): ('minimal', {'elevation': 'degraded'}),  # 流域划分（数据为面要素）
    ('watershed-delineation', 'nominal_raster'): ('preferred', {'elevation': 'eligible'}),  # 流域划分（数据为像元矩阵）
    ('watershed-delineation', 'geometry_line'): ('minimal', {'elevation': 'degraded'}),  # 流域划分（数据为线要素）
    ('watershed-delineation', 'bad_crs'): ('minimal', {'elevation': 'degraded'}),  # 流域划分（数据坐标系为WGS84经纬度）
    ('watershed-delineation', 'missing_required_field'): ('minimal', {'elevation': 'degraded'}),  # 流域划分（数据没有数值字段）
    ('watershed-delineation', 'too_few_samples'): ('minimal', {'elevation': 'degraded'}),  # 流域划分（样本只有3条）
    ('watershed-delineation', 'zero_variance'): ('minimal', {'elevation': 'degraded'}),  # 流域划分（数值字段全为常数）
    ('watershed-delineation', 'large_data'): ('minimal', {'elevation': 'degraded'}),  # 流域划分（数据量约45万条）
    ('watershed-delineation', 'empty_dataset'): ('blocked', {'elevation': 'blocked'}),  # 流域划分（数据为空）
    ('watershed-delineation', 'high_null_ratio'): ('minimal', {'elevation': 'degraded'}),  # 流域划分（数值字段八成为空值）
    ('watershed-delineation', 'temporal_insufficient'): ('minimal', {'elevation': 'degraded'}),  # 流域划分（观测只有1期）
    ('stream-extraction', 'nominal_point'): ('minimal', {'elevation': 'degraded'}),  # 河网提取
    ('stream-extraction', 'nominal_polygon'): ('minimal', {'elevation': 'degraded'}),  # 河网提取（数据为面要素）
    ('stream-extraction', 'nominal_raster'): ('preferred', {'elevation': 'eligible'}),  # 河网提取（数据为像元矩阵）
    ('stream-extraction', 'geometry_line'): ('minimal', {'elevation': 'degraded'}),  # 河网提取（数据为线要素）
    ('stream-extraction', 'bad_crs'): ('minimal', {'elevation': 'degraded'}),  # 河网提取（数据坐标系为WGS84经纬度）
    ('stream-extraction', 'missing_required_field'): ('minimal', {'elevation': 'degraded'}),  # 河网提取（数据没有数值字段）
    ('stream-extraction', 'too_few_samples'): ('minimal', {'elevation': 'degraded'}),  # 河网提取（样本只有3条）
    ('stream-extraction', 'zero_variance'): ('minimal', {'elevation': 'degraded'}),  # 河网提取（数值字段全为常数）
    ('stream-extraction', 'large_data'): ('minimal', {'elevation': 'degraded'}),  # 河网提取（数据量约45万条）
    ('stream-extraction', 'empty_dataset'): ('blocked', {'elevation': 'blocked'}),  # 河网提取（数据为空）
    ('stream-extraction', 'high_null_ratio'): ('minimal', {'elevation': 'degraded'}),  # 河网提取（数值字段八成为空值）
    ('stream-extraction', 'temporal_insufficient'): ('minimal', {'elevation': 'degraded'}),  # 河网提取（观测只有1期）
    ('flood-extent-screen', 'nominal_point'): ('minimal', {'elevation': 'degraded'}),  # 洪水淹没范围初筛
    ('flood-extent-screen', 'nominal_polygon'): ('minimal', {'elevation': 'degraded'}),  # 洪水淹没范围初筛（数据为面要素）
    ('flood-extent-screen', 'nominal_raster'): ('preferred', {'elevation': 'eligible'}),  # 洪水淹没范围初筛（数据为像元矩阵）
    ('flood-extent-screen', 'geometry_line'): ('minimal', {'elevation': 'degraded'}),  # 洪水淹没范围初筛（数据为线要素）
    ('flood-extent-screen', 'bad_crs'): ('minimal', {'elevation': 'degraded'}),  # 洪水淹没范围初筛（数据坐标系为WGS84经纬度）
    ('flood-extent-screen', 'missing_required_field'): ('minimal', {'elevation': 'degraded'}),  # 洪水淹没范围初筛（数据没有数值字段）
    ('flood-extent-screen', 'too_few_samples'): ('minimal', {'elevation': 'degraded'}),  # 洪水淹没范围初筛（样本只有3条）
    ('flood-extent-screen', 'zero_variance'): ('minimal', {'elevation': 'degraded'}),  # 洪水淹没范围初筛（数值字段全为常数）
    ('flood-extent-screen', 'large_data'): ('minimal', {'elevation': 'degraded'}),  # 洪水淹没范围初筛（数据量约45万条）
    ('flood-extent-screen', 'empty_dataset'): ('blocked', {'elevation': 'blocked'}),  # 洪水淹没范围初筛（数据为空）
    ('flood-extent-screen', 'high_null_ratio'): ('minimal', {'elevation': 'degraded'}),  # 洪水淹没范围初筛（数值字段八成为空值）
    ('flood-extent-screen', 'temporal_insufficient'): ('minimal', {'elevation': 'degraded'}),  # 洪水淹没范围初筛（观测只有1期）
    ('ndvi-monitor', 'nominal_point'): ('degraded', {}),  # NDVI 植被指数计算
    ('ndvi-monitor', 'nominal_polygon'): ('degraded', {}),  # NDVI 植被指数计算（数据为面要素）
    ('ndvi-monitor', 'nominal_raster'): ('preferred', {}),  # NDVI 植被指数计算（数据为像元矩阵）
    ('ndvi-monitor', 'geometry_line'): ('degraded', {}),  # NDVI 植被指数计算（数据为线要素）
    ('ndvi-monitor', 'bad_crs'): ('degraded', {}),  # NDVI 植被指数计算（数据坐标系为WGS84经纬度）
    ('ndvi-monitor', 'missing_required_field'): ('degraded', {}),  # NDVI 植被指数计算（数据没有数值字段）
    ('ndvi-monitor', 'too_few_samples'): ('degraded', {}),  # NDVI 植被指数计算（样本只有3条）
    ('ndvi-monitor', 'zero_variance'): ('degraded', {}),  # NDVI 植被指数计算（数值字段全为常数）
    ('ndvi-monitor', 'large_data'): ('degraded', {}),  # NDVI 植被指数计算（数据量约45万条）
    ('ndvi-monitor', 'empty_dataset'): ('degraded', {}),  # NDVI 植被指数计算（数据为空）
    ('ndvi-monitor', 'high_null_ratio'): ('degraded', {}),  # NDVI 植被指数计算（数值字段八成为空值）
    ('ndvi-monitor', 'temporal_insufficient'): ('degraded', {}),  # NDVI 植被指数计算（观测只有1期）
    ('landcover-map', 'nominal_point'): ('preferred', {}),  # 土地利用影像分类图
    ('landcover-map', 'nominal_polygon'): ('degraded', {}),  # 土地利用影像分类图（数据为面要素）
    ('landcover-map', 'nominal_raster'): ('preferred', {}),  # 土地利用影像分类图（数据为像元矩阵）
    ('landcover-map', 'geometry_line'): ('degraded', {}),  # 土地利用影像分类图（数据为线要素）
    ('landcover-map', 'bad_crs'): ('degraded', {}),  # 土地利用影像分类图（数据坐标系为WGS84经纬度）
    ('landcover-map', 'missing_required_field'): ('degraded', {}),  # 土地利用影像分类图（数据没有数值字段）
    ('landcover-map', 'too_few_samples'): ('preferred', {}),  # 土地利用影像分类图（样本只有3条）
    ('landcover-map', 'zero_variance'): ('preferred', {}),  # 土地利用影像分类图（数值字段全为常数）
    ('landcover-map', 'large_data'): ('preferred', {}),  # 土地利用影像分类图（数据量约45万条）
    ('landcover-map', 'empty_dataset'): ('degraded', {}),  # 土地利用影像分类图（数据为空）
    ('landcover-map', 'high_null_ratio'): ('degraded', {}),  # 土地利用影像分类图（数值字段八成为空值）
    ('landcover-map', 'temporal_insufficient'): ('preferred', {}),  # 土地利用影像分类图（观测只有1期）
    ('bitemporal-optical-change', 'nominal_point'): ('degraded', {}),  # 两期影像变化检测
    ('bitemporal-optical-change', 'nominal_polygon'): ('degraded', {}),  # 两期影像变化检测（数据为面要素）
    ('bitemporal-optical-change', 'nominal_raster'): ('preferred', {}),  # 两期影像变化检测（数据为像元矩阵）
    ('bitemporal-optical-change', 'geometry_line'): ('degraded', {}),  # 两期影像变化检测（数据为线要素）
    ('bitemporal-optical-change', 'bad_crs'): ('degraded', {}),  # 两期影像变化检测（数据坐标系为WGS84经纬度）
    ('bitemporal-optical-change', 'missing_required_field'): ('degraded', {}),  # 两期影像变化检测（数据没有数值字段）
    ('bitemporal-optical-change', 'too_few_samples'): ('degraded', {}),  # 两期影像变化检测（样本只有3条）
    ('bitemporal-optical-change', 'zero_variance'): ('degraded', {}),  # 两期影像变化检测（数值字段全为常数）
    ('bitemporal-optical-change', 'large_data'): ('degraded', {}),  # 两期影像变化检测（数据量约45万条）
    ('bitemporal-optical-change', 'empty_dataset'): ('degraded', {}),  # 两期影像变化检测（数据为空）
    ('bitemporal-optical-change', 'high_null_ratio'): ('degraded', {}),  # 两期影像变化检测（数值字段八成为空值）
    ('bitemporal-optical-change', 'temporal_insufficient'): ('degraded', {}),  # 两期影像变化检测（观测只有1期）
    ('sar-overview', 'nominal_point'): ('minimal', {'subject': 'degraded'}),  # SAR 影像后向散射概览
    ('sar-overview', 'nominal_polygon'): ('minimal', {'subject': 'degraded'}),  # SAR 影像后向散射概览（数据为面要素）
    ('sar-overview', 'nominal_raster'): ('preferred', {'subject': 'eligible'}),  # SAR 影像后向散射概览（数据为像元矩阵）
    ('sar-overview', 'geometry_line'): ('minimal', {'subject': 'degraded'}),  # SAR 影像后向散射概览（数据为线要素）
    ('sar-overview', 'bad_crs'): ('minimal', {'subject': 'degraded'}),  # SAR 影像后向散射概览（数据坐标系为WGS84经纬度）
    ('sar-overview', 'missing_required_field'): ('minimal', {'subject': 'degraded'}),  # SAR 影像后向散射概览（数据没有数值字段）
    ('sar-overview', 'too_few_samples'): ('minimal', {'subject': 'degraded'}),  # SAR 影像后向散射概览（样本只有3条）
    ('sar-overview', 'zero_variance'): ('minimal', {'subject': 'degraded'}),  # SAR 影像后向散射概览（数值字段全为常数）
    ('sar-overview', 'large_data'): ('minimal', {'subject': 'degraded'}),  # SAR 影像后向散射概览（数据量约45万条）
    ('sar-overview', 'empty_dataset'): ('blocked', {'subject': 'blocked'}),  # SAR 影像后向散射概览（数据为空）
    ('sar-overview', 'high_null_ratio'): ('minimal', {'subject': 'degraded'}),  # SAR 影像后向散射概览（数值字段八成为空值）
    ('sar-overview', 'temporal_insufficient'): ('minimal', {'subject': 'degraded'}),  # SAR 影像后向散射概览（观测只有1期）
    ('insar-deformation', 'nominal_point'): ('minimal', {'subject': 'degraded'}),  # InSAR 地表形变筛查
    ('insar-deformation', 'nominal_polygon'): ('minimal', {'subject': 'degraded'}),  # InSAR 地表形变筛查（数据为面要素）
    ('insar-deformation', 'nominal_raster'): ('degraded', {'subject': 'eligible'}),  # InSAR 地表形变筛查（数据为像元矩阵）
    ('insar-deformation', 'geometry_line'): ('minimal', {'subject': 'degraded'}),  # InSAR 地表形变筛查（数据为线要素）
    ('insar-deformation', 'bad_crs'): ('minimal', {'subject': 'degraded'}),  # InSAR 地表形变筛查（数据坐标系为WGS84经纬度）
    ('insar-deformation', 'missing_required_field'): ('minimal', {'subject': 'degraded'}),  # InSAR 地表形变筛查（数据没有数值字段）
    ('insar-deformation', 'too_few_samples'): ('minimal', {'subject': 'degraded'}),  # InSAR 地表形变筛查（样本只有3条）
    ('insar-deformation', 'zero_variance'): ('minimal', {'subject': 'degraded'}),  # InSAR 地表形变筛查（数值字段全为常数）
    ('insar-deformation', 'large_data'): ('minimal', {'subject': 'degraded'}),  # InSAR 地表形变筛查（数据量约45万条）
    ('insar-deformation', 'empty_dataset'): ('blocked', {'subject': 'blocked'}),  # InSAR 地表形变筛查（数据为空）
    ('insar-deformation', 'high_null_ratio'): ('minimal', {'subject': 'degraded'}),  # InSAR 地表形变筛查（数值字段八成为空值）
    ('insar-deformation', 'temporal_insufficient'): ('blocked', {'subject': 'degraded'}),  # InSAR 地表形变筛查（观测只有1期）
    ('linear-trend', 'nominal_point'): ('preferred', {'subject': 'eligible', 'target_time': 'eligible'}),  # 站点观测变化趋势
    ('linear-trend', 'nominal_polygon'): ('degraded', {'subject': 'transform_required', 'target_time': 'eligible'}),  # 站点观测变化趋势（数据为面要素）
    ('linear-trend', 'nominal_raster'): ('preferred', {'subject': 'eligible', 'target_time': 'eligible'}),  # 站点观测变化趋势（数据为像元矩阵）
    ('linear-trend', 'geometry_line'): ('degraded', {'subject': 'degraded', 'target_time': 'eligible'}),  # 站点观测变化趋势（数据为线要素）
    ('linear-trend', 'bad_crs'): ('preferred', {'subject': 'eligible', 'target_time': 'eligible'}),  # 站点观测变化趋势（数据坐标系为WGS84经纬度）
    ('linear-trend', 'missing_required_field'): ('preferred', {'subject': 'eligible', 'target_time': 'eligible'}),  # 站点观测变化趋势（数据没有数值字段）
    ('linear-trend', 'too_few_samples'): ('preferred', {'subject': 'eligible', 'target_time': 'eligible'}),  # 站点观测变化趋势（样本只有3条）
    ('linear-trend', 'zero_variance'): ('preferred', {'subject': 'eligible', 'target_time': 'eligible'}),  # 站点观测变化趋势（数值字段全为常数）
    ('linear-trend', 'large_data'): ('preferred', {'subject': 'eligible', 'target_time': 'eligible'}),  # 站点观测变化趋势（数据量约45万条）
    ('linear-trend', 'empty_dataset'): ('blocked', {'subject': 'blocked', 'target_time': 'blocked'}),  # 站点观测变化趋势（数据为空）
    ('linear-trend', 'high_null_ratio'): ('preferred', {'subject': 'transform_required', 'target_time': 'eligible'}),  # 站点观测变化趋势（数值字段八成为空值）
    ('linear-trend', 'temporal_insufficient'): ('blocked', {'subject': 'eligible', 'target_time': 'eligible'}),  # 站点观测变化趋势（观测只有1期）
    ('changepoint-detection', 'nominal_point'): ('preferred', {'subject': 'eligible', 'target_time': 'eligible'}),  # 用水量突变点检测
    ('changepoint-detection', 'nominal_polygon'): ('preferred', {'subject': 'transform_required', 'target_time': 'eligible'}),  # 用水量突变点检测（数据为面要素）
    ('changepoint-detection', 'nominal_raster'): ('preferred', {'subject': 'eligible', 'target_time': 'eligible'}),  # 用水量突变点检测（数据为像元矩阵）
    ('changepoint-detection', 'geometry_line'): ('degraded', {'subject': 'degraded', 'target_time': 'eligible'}),  # 用水量突变点检测（数据为线要素）
    ('changepoint-detection', 'bad_crs'): ('preferred', {'subject': 'eligible', 'target_time': 'eligible'}),  # 用水量突变点检测（数据坐标系为WGS84经纬度）
    ('changepoint-detection', 'missing_required_field'): ('preferred', {'subject': 'eligible', 'target_time': 'eligible'}),  # 用水量突变点检测（数据没有数值字段）
    ('changepoint-detection', 'too_few_samples'): ('preferred', {'subject': 'eligible', 'target_time': 'eligible'}),  # 用水量突变点检测（样本只有3条）
    ('changepoint-detection', 'zero_variance'): ('preferred', {'subject': 'eligible', 'target_time': 'eligible'}),  # 用水量突变点检测（数值字段全为常数）
    ('changepoint-detection', 'large_data'): ('preferred', {'subject': 'eligible', 'target_time': 'eligible'}),  # 用水量突变点检测（数据量约45万条）
    ('changepoint-detection', 'empty_dataset'): ('blocked', {'subject': 'blocked', 'target_time': 'blocked'}),  # 用水量突变点检测（数据为空）
    ('changepoint-detection', 'high_null_ratio'): ('preferred', {'subject': 'transform_required', 'target_time': 'eligible'}),  # 用水量突变点检测（数值字段八成为空值）
    ('changepoint-detection', 'temporal_insufficient'): ('preferred', {'subject': 'eligible', 'target_time': 'eligible'}),  # 用水量突变点检测（观测只有1期）
    ('landcover-change', 'nominal_point'): ('degraded', {}),  # 土地利用变化台账
    ('landcover-change', 'nominal_polygon'): ('degraded', {}),  # 土地利用变化台账（数据为面要素）
    ('landcover-change', 'nominal_raster'): ('preferred', {}),  # 土地利用变化台账（数据为像元矩阵）
    ('landcover-change', 'geometry_line'): ('degraded', {}),  # 土地利用变化台账（数据为线要素）
    ('landcover-change', 'bad_crs'): ('degraded', {}),  # 土地利用变化台账（数据坐标系为WGS84经纬度）
    ('landcover-change', 'missing_required_field'): ('degraded', {}),  # 土地利用变化台账（数据没有数值字段）
    ('landcover-change', 'too_few_samples'): ('degraded', {}),  # 土地利用变化台账（样本只有3条）
    ('landcover-change', 'zero_variance'): ('degraded', {}),  # 土地利用变化台账（数值字段全为常数）
    ('landcover-change', 'large_data'): ('degraded', {}),  # 土地利用变化台账（数据量约45万条）
    ('landcover-change', 'empty_dataset'): ('degraded', {}),  # 土地利用变化台账（数据为空）
    ('landcover-change', 'high_null_ratio'): ('degraded', {}),  # 土地利用变化台账（数值字段八成为空值）
    ('landcover-change', 'temporal_insufficient'): ('degraded', {}),  # 土地利用变化台账（观测只有1期）
    ('urban-expansion', 'nominal_point'): ('degraded', {}),  # 城市扩张监测
    ('urban-expansion', 'nominal_polygon'): ('degraded', {}),  # 城市扩张监测（数据为面要素）
    ('urban-expansion', 'nominal_raster'): ('preferred', {}),  # 城市扩张监测（数据为像元矩阵）
    ('urban-expansion', 'geometry_line'): ('degraded', {}),  # 城市扩张监测（数据为线要素）
    ('urban-expansion', 'bad_crs'): ('degraded', {}),  # 城市扩张监测（数据坐标系为WGS84经纬度）
    ('urban-expansion', 'missing_required_field'): ('degraded', {}),  # 城市扩张监测（数据没有数值字段）
    ('urban-expansion', 'too_few_samples'): ('degraded', {}),  # 城市扩张监测（样本只有3条）
    ('urban-expansion', 'zero_variance'): ('degraded', {}),  # 城市扩张监测（数值字段全为常数）
    ('urban-expansion', 'large_data'): ('degraded', {}),  # 城市扩张监测（数据量约45万条）
    ('urban-expansion', 'empty_dataset'): ('degraded', {}),  # 城市扩张监测（数据为空）
    ('urban-expansion', 'high_null_ratio'): ('degraded', {}),  # 城市扩张监测（数值字段八成为空值）
    ('urban-expansion', 'temporal_insufficient'): ('degraded', {}),  # 城市扩张监测（观测只有1期）
    ('shortest-path', 'nominal_point'): ('degraded', {'subject': 'eligible', 'network': 'degraded'}),  # 从春熙路到天府广场的最短路径
    ('shortest-path', 'nominal_polygon'): ('degraded', {'subject': 'transform_required', 'network': 'degraded'}),  # 从春熙路到天府广场的最短路径（数据为面要素）
    ('shortest-path', 'nominal_raster'): ('preferred', {'subject': 'eligible', 'network': 'eligible'}),  # 从春熙路到天府广场的最短路径（数据为像元矩阵）
    ('shortest-path', 'geometry_line'): ('degraded', {'subject': 'degraded', 'network': 'eligible'}),  # 从春熙路到天府广场的最短路径（数据为线要素）
    ('shortest-path', 'bad_crs'): ('degraded', {'subject': 'eligible', 'network': 'degraded'}),  # 从春熙路到天府广场的最短路径（数据坐标系为WGS84经纬度）
    ('shortest-path', 'missing_required_field'): ('degraded', {'subject': 'eligible', 'network': 'degraded'}),  # 从春熙路到天府广场的最短路径（数据没有数值字段）
    ('shortest-path', 'too_few_samples'): ('degraded', {'subject': 'eligible', 'network': 'degraded'}),  # 从春熙路到天府广场的最短路径（样本只有3条）
    ('shortest-path', 'zero_variance'): ('degraded', {'subject': 'eligible', 'network': 'degraded'}),  # 从春熙路到天府广场的最短路径（数值字段全为常数）
    ('shortest-path', 'large_data'): ('degraded', {'subject': 'eligible', 'network': 'degraded'}),  # 从春熙路到天府广场的最短路径（数据量约45万条）
    ('shortest-path', 'empty_dataset'): ('blocked', {'subject': 'blocked', 'network': 'blocked'}),  # 从春熙路到天府广场的最短路径（数据为空）
    ('shortest-path', 'high_null_ratio'): ('degraded', {'subject': 'transform_required', 'network': 'degraded'}),  # 从春熙路到天府广场的最短路径（数值字段八成为空值）
    ('shortest-path', 'temporal_insufficient'): ('degraded', {'subject': 'eligible', 'network': 'degraded'}),  # 从春熙路到天府广场的最短路径（观测只有1期）
    ('closest-facility', 'nominal_point'): ('degraded', {'subject': 'eligible', 'network': 'degraded'}),  # 离小区最近的医院
    ('closest-facility', 'nominal_polygon'): ('degraded', {'subject': 'transform_required', 'network': 'degraded'}),  # 离小区最近的医院（数据为面要素）
    ('closest-facility', 'nominal_raster'): ('preferred', {'subject': 'eligible', 'network': 'eligible'}),  # 离小区最近的医院（数据为像元矩阵）
    ('closest-facility', 'geometry_line'): ('degraded', {'subject': 'degraded', 'network': 'eligible'}),  # 离小区最近的医院（数据为线要素）
    ('closest-facility', 'bad_crs'): ('degraded', {'subject': 'eligible', 'network': 'degraded'}),  # 离小区最近的医院（数据坐标系为WGS84经纬度）
    ('closest-facility', 'missing_required_field'): ('degraded', {'subject': 'eligible', 'network': 'degraded'}),  # 离小区最近的医院（数据没有数值字段）
    ('closest-facility', 'too_few_samples'): ('degraded', {'subject': 'eligible', 'network': 'degraded'}),  # 离小区最近的医院（样本只有3条）
    ('closest-facility', 'zero_variance'): ('degraded', {'subject': 'eligible', 'network': 'degraded'}),  # 离小区最近的医院（数值字段全为常数）
    ('closest-facility', 'large_data'): ('degraded', {'subject': 'eligible', 'network': 'degraded'}),  # 离小区最近的医院（数据量约45万条）
    ('closest-facility', 'empty_dataset'): ('blocked', {'subject': 'blocked', 'network': 'blocked'}),  # 离小区最近的医院（数据为空）
    ('closest-facility', 'high_null_ratio'): ('degraded', {'subject': 'transform_required', 'network': 'degraded'}),  # 离小区最近的医院（数值字段八成为空值）
    ('closest-facility', 'temporal_insufficient'): ('degraded', {'subject': 'eligible', 'network': 'degraded'}),  # 离小区最近的医院（观测只有1期）
    ('od-flow', 'nominal_point'): ('preferred', {}),  # 通勤流分析
    ('od-flow', 'nominal_polygon'): ('degraded', {}),  # 通勤流分析（数据为面要素）
    ('od-flow', 'nominal_raster'): ('preferred', {}),  # 通勤流分析（数据为像元矩阵）
    ('od-flow', 'geometry_line'): ('degraded', {}),  # 通勤流分析（数据为线要素）
    ('od-flow', 'bad_crs'): ('preferred', {}),  # 通勤流分析（数据坐标系为WGS84经纬度）
    ('od-flow', 'missing_required_field'): ('preferred', {}),  # 通勤流分析（数据没有数值字段）
    ('od-flow', 'too_few_samples'): ('preferred', {}),  # 通勤流分析（样本只有3条）
    ('od-flow', 'zero_variance'): ('preferred', {}),  # 通勤流分析（数值字段全为常数）
    ('od-flow', 'large_data'): ('preferred', {}),  # 通勤流分析（数据量约45万条）
    ('od-flow', 'empty_dataset'): ('preferred', {}),  # 通勤流分析（数据为空）
    ('od-flow', 'high_null_ratio'): ('preferred', {}),  # 通勤流分析（数值字段八成为空值）
    ('od-flow', 'temporal_insufficient'): ('preferred', {}),  # 通勤流分析（观测只有1期）
    ('service-area', 'nominal_point'): ('preferred', {}),  # 15分钟步行圈
    ('service-area', 'nominal_polygon'): ('degraded', {}),  # 15分钟步行圈（数据为面要素）
    ('service-area', 'nominal_raster'): ('preferred', {}),  # 15分钟步行圈（数据为像元矩阵）
    ('service-area', 'geometry_line'): ('degraded', {}),  # 15分钟步行圈（数据为线要素）
    ('service-area', 'bad_crs'): ('preferred', {}),  # 15分钟步行圈（数据坐标系为WGS84经纬度）
    ('service-area', 'missing_required_field'): ('preferred', {}),  # 15分钟步行圈（数据没有数值字段）
    ('service-area', 'too_few_samples'): ('preferred', {}),  # 15分钟步行圈（样本只有3条）
    ('service-area', 'zero_variance'): ('preferred', {}),  # 15分钟步行圈（数值字段全为常数）
    ('service-area', 'large_data'): ('preferred', {}),  # 15分钟步行圈（数据量约45万条）
    ('service-area', 'empty_dataset'): ('preferred', {}),  # 15分钟步行圈（数据为空）
    ('service-area', 'high_null_ratio'): ('preferred', {}),  # 15分钟步行圈（数值字段八成为空值）
    ('service-area', 'temporal_insufficient'): ('preferred', {}),  # 15分钟步行圈（观测只有1期）
    ('coverage-gap', 'nominal_point'): ('preferred', {}),  # 养老设施覆盖盲区
    ('coverage-gap', 'nominal_polygon'): ('degraded', {}),  # 养老设施覆盖盲区（数据为面要素）
    ('coverage-gap', 'nominal_raster'): ('preferred', {}),  # 养老设施覆盖盲区（数据为像元矩阵）
    ('coverage-gap', 'geometry_line'): ('degraded', {}),  # 养老设施覆盖盲区（数据为线要素）
    ('coverage-gap', 'bad_crs'): ('preferred', {}),  # 养老设施覆盖盲区（数据坐标系为WGS84经纬度）
    ('coverage-gap', 'missing_required_field'): ('preferred', {}),  # 养老设施覆盖盲区（数据没有数值字段）
    ('coverage-gap', 'too_few_samples'): ('preferred', {}),  # 养老设施覆盖盲区（样本只有3条）
    ('coverage-gap', 'zero_variance'): ('preferred', {}),  # 养老设施覆盖盲区（数值字段全为常数）
    ('coverage-gap', 'large_data'): ('preferred', {}),  # 养老设施覆盖盲区（数据量约45万条）
    ('coverage-gap', 'empty_dataset'): ('preferred', {}),  # 养老设施覆盖盲区（数据为空）
    ('coverage-gap', 'high_null_ratio'): ('preferred', {}),  # 养老设施覆盖盲区（数值字段八成为空值）
    ('coverage-gap', 'temporal_insufficient'): ('preferred', {}),  # 养老设施覆盖盲区（观测只有1期）
    ('equity-generic', 'nominal_point'): ('preferred', {}),  # 各区小学数量是否均衡
    ('equity-generic', 'nominal_polygon'): ('degraded', {}),  # 各区小学数量是否均衡（数据为面要素）
    ('equity-generic', 'nominal_raster'): ('preferred', {}),  # 各区小学数量是否均衡（数据为像元矩阵）
    ('equity-generic', 'geometry_line'): ('degraded', {}),  # 各区小学数量是否均衡（数据为线要素）
    ('equity-generic', 'bad_crs'): ('preferred', {}),  # 各区小学数量是否均衡（数据坐标系为WGS84经纬度）
    ('equity-generic', 'missing_required_field'): ('preferred', {}),  # 各区小学数量是否均衡（数据没有数值字段）
    ('equity-generic', 'too_few_samples'): ('preferred', {}),  # 各区小学数量是否均衡（样本只有3条）
    ('equity-generic', 'zero_variance'): ('preferred', {}),  # 各区小学数量是否均衡（数值字段全为常数）
    ('equity-generic', 'large_data'): ('preferred', {}),  # 各区小学数量是否均衡（数据量约45万条）
    ('equity-generic', 'empty_dataset'): ('preferred', {}),  # 各区小学数量是否均衡（数据为空）
    ('equity-generic', 'high_null_ratio'): ('preferred', {}),  # 各区小学数量是否均衡（数值字段八成为空值）
    ('equity-generic', 'temporal_insufficient'): ('preferred', {}),  # 各区小学数量是否均衡（观测只有1期）
    ('site-selection-generic', 'nominal_point'): ('preferred', {}),  # 新学校选址
    ('site-selection-generic', 'nominal_polygon'): ('preferred', {}),  # 新学校选址（数据为面要素）
    ('site-selection-generic', 'nominal_raster'): ('preferred', {}),  # 新学校选址（数据为像元矩阵）
    ('site-selection-generic', 'geometry_line'): ('degraded', {}),  # 新学校选址（数据为线要素）
    ('site-selection-generic', 'bad_crs'): ('preferred', {}),  # 新学校选址（数据坐标系为WGS84经纬度）
    ('site-selection-generic', 'missing_required_field'): ('preferred', {}),  # 新学校选址（数据没有数值字段）
    ('site-selection-generic', 'too_few_samples'): ('preferred', {}),  # 新学校选址（样本只有3条）
    ('site-selection-generic', 'zero_variance'): ('preferred', {}),  # 新学校选址（数值字段全为常数）
    ('site-selection-generic', 'large_data'): ('preferred', {}),  # 新学校选址（数据量约45万条）
    ('site-selection-generic', 'empty_dataset'): ('preferred', {}),  # 新学校选址（数据为空）
    ('site-selection-generic', 'high_null_ratio'): ('preferred', {}),  # 新学校选址（数值字段八成为空值）
    ('site-selection-generic', 'temporal_insufficient'): ('preferred', {}),  # 新学校选址（观测只有1期）
    ('suitability-generic', 'nominal_point'): ('preferred', {}),  # 适建区评价
    ('suitability-generic', 'nominal_polygon'): ('preferred', {}),  # 适建区评价（数据为面要素）
    ('suitability-generic', 'nominal_raster'): ('preferred', {}),  # 适建区评价（数据为像元矩阵）
    ('suitability-generic', 'geometry_line'): ('preferred', {}),  # 适建区评价（数据为线要素）
    ('suitability-generic', 'bad_crs'): ('preferred', {}),  # 适建区评价（数据坐标系为WGS84经纬度）
    ('suitability-generic', 'missing_required_field'): ('preferred', {}),  # 适建区评价（数据没有数值字段）
    ('suitability-generic', 'too_few_samples'): ('preferred', {}),  # 适建区评价（样本只有3条）
    ('suitability-generic', 'zero_variance'): ('preferred', {}),  # 适建区评价（数值字段全为常数）
    ('suitability-generic', 'large_data'): ('preferred', {}),  # 适建区评价（数据量约45万条）
    ('suitability-generic', 'empty_dataset'): ('preferred', {}),  # 适建区评价（数据为空）
    ('suitability-generic', 'high_null_ratio'): ('preferred', {}),  # 适建区评价（数值字段八成为空值）
    ('suitability-generic', 'temporal_insufficient'): ('preferred', {}),  # 适建区评价（观测只有1期）
    ('exposure-buffer-screen', 'nominal_point'): ('preferred', {}),  # 加油站卫生防护距离筛查
    ('exposure-buffer-screen', 'nominal_polygon'): ('degraded', {}),  # 加油站卫生防护距离筛查（数据为面要素）
    ('exposure-buffer-screen', 'nominal_raster'): ('preferred', {}),  # 加油站卫生防护距离筛查（数据为像元矩阵）
    ('exposure-buffer-screen', 'geometry_line'): ('degraded', {}),  # 加油站卫生防护距离筛查（数据为线要素）
    ('exposure-buffer-screen', 'bad_crs'): ('preferred', {}),  # 加油站卫生防护距离筛查（数据坐标系为WGS84经纬度）
    ('exposure-buffer-screen', 'missing_required_field'): ('preferred', {}),  # 加油站卫生防护距离筛查（数据没有数值字段）
    ('exposure-buffer-screen', 'too_few_samples'): ('preferred', {}),  # 加油站卫生防护距离筛查（样本只有3条）
    ('exposure-buffer-screen', 'zero_variance'): ('preferred', {}),  # 加油站卫生防护距离筛查（数值字段全为常数）
    ('exposure-buffer-screen', 'large_data'): ('preferred', {}),  # 加油站卫生防护距离筛查（数据量约45万条）
    ('exposure-buffer-screen', 'empty_dataset'): ('preferred', {}),  # 加油站卫生防护距离筛查（数据为空）
    ('exposure-buffer-screen', 'high_null_ratio'): ('preferred', {}),  # 加油站卫生防护距离筛查（数值字段八成为空值）
    ('exposure-buffer-screen', 'temporal_insufficient'): ('preferred', {}),  # 加油站卫生防护距离筛查（观测只有1期）
    ('greening-rate', 'nominal_point'): ('preferred', {}),  # 各区绿化率统计
    ('greening-rate', 'nominal_polygon'): ('degraded', {}),  # 各区绿化率统计（数据为面要素）
    ('greening-rate', 'nominal_raster'): ('preferred', {}),  # 各区绿化率统计（数据为像元矩阵）
    ('greening-rate', 'geometry_line'): ('degraded', {}),  # 各区绿化率统计（数据为线要素）
    ('greening-rate', 'bad_crs'): ('preferred', {}),  # 各区绿化率统计（数据坐标系为WGS84经纬度）
    ('greening-rate', 'missing_required_field'): ('preferred', {}),  # 各区绿化率统计（数据没有数值字段）
    ('greening-rate', 'too_few_samples'): ('preferred', {}),  # 各区绿化率统计（样本只有3条）
    ('greening-rate', 'zero_variance'): ('preferred', {}),  # 各区绿化率统计（数值字段全为常数）
    ('greening-rate', 'large_data'): ('preferred', {}),  # 各区绿化率统计（数据量约45万条）
    ('greening-rate', 'empty_dataset'): ('preferred', {}),  # 各区绿化率统计（数据为空）
    ('greening-rate', 'high_null_ratio'): ('preferred', {}),  # 各区绿化率统计（数值字段八成为空值）
    ('greening-rate', 'temporal_insufficient'): ('preferred', {}),  # 各区绿化率统计（观测只有1期）
    ('transit-coverage', 'nominal_point'): ('preferred', {}),  # 公交站点服务区分析
    ('transit-coverage', 'nominal_polygon'): ('degraded', {}),  # 公交站点服务区分析（数据为面要素）
    ('transit-coverage', 'nominal_raster'): ('preferred', {}),  # 公交站点服务区分析（数据为像元矩阵）
    ('transit-coverage', 'geometry_line'): ('degraded', {}),  # 公交站点服务区分析（数据为线要素）
    ('transit-coverage', 'bad_crs'): ('preferred', {}),  # 公交站点服务区分析（数据坐标系为WGS84经纬度）
    ('transit-coverage', 'missing_required_field'): ('preferred', {}),  # 公交站点服务区分析（数据没有数值字段）
    ('transit-coverage', 'too_few_samples'): ('preferred', {}),  # 公交站点服务区分析（样本只有3条）
    ('transit-coverage', 'zero_variance'): ('preferred', {}),  # 公交站点服务区分析（数值字段全为常数）
    ('transit-coverage', 'large_data'): ('preferred', {}),  # 公交站点服务区分析（数据量约45万条）
    ('transit-coverage', 'empty_dataset'): ('preferred', {}),  # 公交站点服务区分析（数据为空）
    ('transit-coverage', 'high_null_ratio'): ('preferred', {}),  # 公交站点服务区分析（数值字段八成为空值）
    ('transit-coverage', 'temporal_insufficient'): ('preferred', {}),  # 公交站点服务区分析（观测只有1期）
    ('cultivated-land', 'nominal_point'): ('preferred', {}),  # 耕地分布情况
    ('cultivated-land', 'nominal_polygon'): ('degraded', {}),  # 耕地分布情况（数据为面要素）
    ('cultivated-land', 'nominal_raster'): ('preferred', {}),  # 耕地分布情况（数据为像元矩阵）
    ('cultivated-land', 'geometry_line'): ('degraded', {}),  # 耕地分布情况（数据为线要素）
    ('cultivated-land', 'bad_crs'): ('preferred', {}),  # 耕地分布情况（数据坐标系为WGS84经纬度）
    ('cultivated-land', 'missing_required_field'): ('preferred', {}),  # 耕地分布情况（数据没有数值字段）
    ('cultivated-land', 'too_few_samples'): ('preferred', {}),  # 耕地分布情况（样本只有3条）
    ('cultivated-land', 'zero_variance'): ('preferred', {}),  # 耕地分布情况（数值字段全为常数）
    ('cultivated-land', 'large_data'): ('preferred', {}),  # 耕地分布情况（数据量约45万条）
    ('cultivated-land', 'empty_dataset'): ('preferred', {}),  # 耕地分布情况（数据为空）
    ('cultivated-land', 'high_null_ratio'): ('preferred', {}),  # 耕地分布情况（数值字段八成为空值）
    ('cultivated-land', 'temporal_insufficient'): ('preferred', {}),  # 耕地分布情况（观测只有1期）
    ('monitoring-coverage', 'nominal_point'): ('preferred', {}),  # 空气质量监测站分布
    ('monitoring-coverage', 'nominal_polygon'): ('degraded', {}),  # 空气质量监测站分布（数据为面要素）
    ('monitoring-coverage', 'nominal_raster'): ('preferred', {}),  # 空气质量监测站分布（数据为像元矩阵）
    ('monitoring-coverage', 'geometry_line'): ('degraded', {}),  # 空气质量监测站分布（数据为线要素）
    ('monitoring-coverage', 'bad_crs'): ('preferred', {}),  # 空气质量监测站分布（数据坐标系为WGS84经纬度）
    ('monitoring-coverage', 'missing_required_field'): ('preferred', {}),  # 空气质量监测站分布（数据没有数值字段）
    ('monitoring-coverage', 'too_few_samples'): ('preferred', {}),  # 空气质量监测站分布（样本只有3条）
    ('monitoring-coverage', 'zero_variance'): ('preferred', {}),  # 空气质量监测站分布（数值字段全为常数）
    ('monitoring-coverage', 'large_data'): ('preferred', {}),  # 空气质量监测站分布（数据量约45万条）
    ('monitoring-coverage', 'empty_dataset'): ('preferred', {}),  # 空气质量监测站分布（数据为空）
    ('monitoring-coverage', 'high_null_ratio'): ('preferred', {}),  # 空气质量监测站分布（数值字段八成为空值）
    ('monitoring-coverage', 'temporal_insufficient'): ('preferred', {}),  # 空气质量监测站分布（观测只有1期）
    ('hazard-inventory', 'nominal_point'): ('preferred', {}),  # 地质灾害隐患点分布
    ('hazard-inventory', 'nominal_polygon'): ('degraded', {}),  # 地质灾害隐患点分布（数据为面要素）
    ('hazard-inventory', 'nominal_raster'): ('preferred', {}),  # 地质灾害隐患点分布（数据为像元矩阵）
    ('hazard-inventory', 'geometry_line'): ('degraded', {}),  # 地质灾害隐患点分布（数据为线要素）
    ('hazard-inventory', 'bad_crs'): ('preferred', {}),  # 地质灾害隐患点分布（数据坐标系为WGS84经纬度）
    ('hazard-inventory', 'missing_required_field'): ('preferred', {}),  # 地质灾害隐患点分布（数据没有数值字段）
    ('hazard-inventory', 'too_few_samples'): ('preferred', {}),  # 地质灾害隐患点分布（样本只有3条）
    ('hazard-inventory', 'zero_variance'): ('preferred', {}),  # 地质灾害隐患点分布（数值字段全为常数）
    ('hazard-inventory', 'large_data'): ('preferred', {}),  # 地质灾害隐患点分布（数据量约45万条）
    ('hazard-inventory', 'empty_dataset'): ('preferred', {}),  # 地质灾害隐患点分布（数据为空）
    ('hazard-inventory', 'high_null_ratio'): ('preferred', {}),  # 地质灾害隐患点分布（数值字段八成为空值）
    ('hazard-inventory', 'temporal_insufficient'): ('preferred', {}),  # 地质灾害隐患点分布（观测只有1期）
    ('clinic-coverage', 'nominal_point'): ('preferred', {}),  # 社区卫生服务中心服务区分析
    ('clinic-coverage', 'nominal_polygon'): ('degraded', {}),  # 社区卫生服务中心服务区分析（数据为面要素）
    ('clinic-coverage', 'nominal_raster'): ('preferred', {}),  # 社区卫生服务中心服务区分析（数据为像元矩阵）
    ('clinic-coverage', 'geometry_line'): ('degraded', {}),  # 社区卫生服务中心服务区分析（数据为线要素）
    ('clinic-coverage', 'bad_crs'): ('preferred', {}),  # 社区卫生服务中心服务区分析（数据坐标系为WGS84经纬度）
    ('clinic-coverage', 'missing_required_field'): ('preferred', {}),  # 社区卫生服务中心服务区分析（数据没有数值字段）
    ('clinic-coverage', 'too_few_samples'): ('preferred', {}),  # 社区卫生服务中心服务区分析（样本只有3条）
    ('clinic-coverage', 'zero_variance'): ('preferred', {}),  # 社区卫生服务中心服务区分析（数值字段全为常数）
    ('clinic-coverage', 'large_data'): ('preferred', {}),  # 社区卫生服务中心服务区分析（数据量约45万条）
    ('clinic-coverage', 'empty_dataset'): ('preferred', {}),  # 社区卫生服务中心服务区分析（数据为空）
    ('clinic-coverage', 'high_null_ratio'): ('preferred', {}),  # 社区卫生服务中心服务区分析（数值字段八成为空值）
    ('clinic-coverage', 'temporal_insufficient'): ('preferred', {}),  # 社区卫生服务中心服务区分析（观测只有1期）
    ('risk-generic', 'nominal_point'): ('preferred', {}),  # 城市内涝风险区域
    ('risk-generic', 'nominal_polygon'): ('preferred', {}),  # 城市内涝风险区域（数据为面要素）
    ('risk-generic', 'nominal_raster'): ('preferred', {}),  # 城市内涝风险区域（数据为像元矩阵）
    ('risk-generic', 'geometry_line'): ('degraded', {}),  # 城市内涝风险区域（数据为线要素）
    ('risk-generic', 'bad_crs'): ('preferred', {}),  # 城市内涝风险区域（数据坐标系为WGS84经纬度）
    ('risk-generic', 'missing_required_field'): ('preferred', {}),  # 城市内涝风险区域（数据没有数值字段）
    ('risk-generic', 'too_few_samples'): ('preferred', {}),  # 城市内涝风险区域（样本只有3条）
    ('risk-generic', 'zero_variance'): ('preferred', {}),  # 城市内涝风险区域（数值字段全为常数）
    ('risk-generic', 'large_data'): ('preferred', {}),  # 城市内涝风险区域（数据量约45万条）
    ('risk-generic', 'empty_dataset'): ('preferred', {}),  # 城市内涝风险区域（数据为空）
    ('risk-generic', 'high_null_ratio'): ('preferred', {}),  # 城市内涝风险区域（数值字段八成为空值）
    ('risk-generic', 'temporal_insufficient'): ('preferred', {}),  # 城市内涝风险区域（观测只有1期）
    ('road-centrality', 'nominal_point'): ('degraded', {'subject': 'eligible', 'network': 'degraded'}),  # 路网中心性分析
    ('road-centrality', 'nominal_polygon'): ('degraded', {'subject': 'transform_required', 'network': 'degraded'}),  # 路网中心性分析（数据为面要素）
    ('road-centrality', 'nominal_raster'): ('preferred', {'subject': 'eligible', 'network': 'eligible'}),  # 路网中心性分析（数据为像元矩阵）
    ('road-centrality', 'geometry_line'): ('degraded', {'subject': 'degraded', 'network': 'eligible'}),  # 路网中心性分析（数据为线要素）
    ('road-centrality', 'bad_crs'): ('degraded', {'subject': 'eligible', 'network': 'degraded'}),  # 路网中心性分析（数据坐标系为WGS84经纬度）
    ('road-centrality', 'missing_required_field'): ('degraded', {'subject': 'eligible', 'network': 'degraded'}),  # 路网中心性分析（数据没有数值字段）
    ('road-centrality', 'too_few_samples'): ('degraded', {'subject': 'eligible', 'network': 'degraded'}),  # 路网中心性分析（样本只有3条）
    ('road-centrality', 'zero_variance'): ('degraded', {'subject': 'eligible', 'network': 'degraded'}),  # 路网中心性分析（数值字段全为常数）
    ('road-centrality', 'large_data'): ('degraded', {'subject': 'eligible', 'network': 'degraded'}),  # 路网中心性分析（数据量约45万条）
    ('road-centrality', 'empty_dataset'): ('blocked', {'subject': 'blocked', 'network': 'blocked'}),  # 路网中心性分析（数据为空）
    ('road-centrality', 'high_null_ratio'): ('degraded', {'subject': 'transform_required', 'network': 'degraded'}),  # 路网中心性分析（数值字段八成为空值）
    ('road-centrality', 'temporal_insufficient'): ('degraded', {'subject': 'eligible', 'network': 'degraded'}),  # 路网中心性分析（观测只有1期）
    ('fire-risk', 'nominal_point'): ('preferred', {}),  # 城市火灾风险热点
    ('fire-risk', 'nominal_polygon'): ('degraded', {}),  # 城市火灾风险热点（数据为面要素）
    ('fire-risk', 'nominal_raster'): ('preferred', {}),  # 城市火灾风险热点（数据为像元矩阵）
    ('fire-risk', 'geometry_line'): ('degraded', {}),  # 城市火灾风险热点（数据为线要素）
    ('fire-risk', 'bad_crs'): ('preferred', {}),  # 城市火灾风险热点（数据坐标系为WGS84经纬度）
    ('fire-risk', 'missing_required_field'): ('preferred', {}),  # 城市火灾风险热点（数据没有数值字段）
    ('fire-risk', 'too_few_samples'): ('preferred', {}),  # 城市火灾风险热点（样本只有3条）
    ('fire-risk', 'zero_variance'): ('preferred', {}),  # 城市火灾风险热点（数值字段全为常数）
    ('fire-risk', 'large_data'): ('preferred', {}),  # 城市火灾风险热点（数据量约45万条）
    ('fire-risk', 'empty_dataset'): ('preferred', {}),  # 城市火灾风险热点（数据为空）
    ('fire-risk', 'high_null_ratio'): ('preferred', {}),  # 城市火灾风险热点（数值字段八成为空值）
    ('fire-risk', 'temporal_insufficient'): ('preferred', {}),  # 城市火灾风险热点（观测只有1期）
    ('population-exposure', 'nominal_point'): ('preferred', {}),  # 人口暴露估算
    ('population-exposure', 'nominal_polygon'): ('degraded', {}),  # 人口暴露估算（数据为面要素）
    ('population-exposure', 'nominal_raster'): ('preferred', {}),  # 人口暴露估算（数据为像元矩阵）
    ('population-exposure', 'geometry_line'): ('degraded', {}),  # 人口暴露估算（数据为线要素）
    ('population-exposure', 'bad_crs'): ('preferred', {}),  # 人口暴露估算（数据坐标系为WGS84经纬度）
    ('population-exposure', 'missing_required_field'): ('preferred', {}),  # 人口暴露估算（数据没有数值字段）
    ('population-exposure', 'too_few_samples'): ('preferred', {}),  # 人口暴露估算（样本只有3条）
    ('population-exposure', 'zero_variance'): ('preferred', {}),  # 人口暴露估算（数值字段全为常数）
    ('population-exposure', 'large_data'): ('preferred', {}),  # 人口暴露估算（数据量约45万条）
    ('population-exposure', 'empty_dataset'): ('preferred', {}),  # 人口暴露估算（数据为空）
    ('population-exposure', 'high_null_ratio'): ('preferred', {}),  # 人口暴露估算（数值字段八成为空值）
    ('population-exposure', 'temporal_insufficient'): ('preferred', {}),  # 人口暴露估算（观测只有1期）
    ('warehouse-site', 'nominal_point'): ('preferred', {}),  # 仓库选址评价
    ('warehouse-site', 'nominal_polygon'): ('preferred', {}),  # 仓库选址评价（数据为面要素）
    ('warehouse-site', 'nominal_raster'): ('preferred', {}),  # 仓库选址评价（数据为像元矩阵）
    ('warehouse-site', 'geometry_line'): ('degraded', {}),  # 仓库选址评价（数据为线要素）
    ('warehouse-site', 'bad_crs'): ('preferred', {}),  # 仓库选址评价（数据坐标系为WGS84经纬度）
    ('warehouse-site', 'missing_required_field'): ('preferred', {}),  # 仓库选址评价（数据没有数值字段）
    ('warehouse-site', 'too_few_samples'): ('preferred', {}),  # 仓库选址评价（样本只有3条）
    ('warehouse-site', 'zero_variance'): ('preferred', {}),  # 仓库选址评价（数值字段全为常数）
    ('warehouse-site', 'large_data'): ('preferred', {}),  # 仓库选址评价（数据量约45万条）
    ('warehouse-site', 'empty_dataset'): ('preferred', {}),  # 仓库选址评价（数据为空）
    ('warehouse-site', 'high_null_ratio'): ('preferred', {}),  # 仓库选址评价（数值字段八成为空值）
    ('warehouse-site', 'temporal_insufficient'): ('preferred', {}),  # 仓库选址评价（观测只有1期）
    ('od-matrix', 'nominal_point'): ('preferred', {}),  # 医院之间od矩阵
    ('od-matrix', 'nominal_polygon'): ('degraded', {}),  # 医院之间od矩阵（数据为面要素）
    ('od-matrix', 'nominal_raster'): ('preferred', {}),  # 医院之间od矩阵（数据为像元矩阵）
    ('od-matrix', 'geometry_line'): ('degraded', {}),  # 医院之间od矩阵（数据为线要素）
    ('od-matrix', 'bad_crs'): ('preferred', {}),  # 医院之间od矩阵（数据坐标系为WGS84经纬度）
    ('od-matrix', 'missing_required_field'): ('preferred', {}),  # 医院之间od矩阵（数据没有数值字段）
    ('od-matrix', 'too_few_samples'): ('preferred', {}),  # 医院之间od矩阵（样本只有3条）
    ('od-matrix', 'zero_variance'): ('preferred', {}),  # 医院之间od矩阵（数值字段全为常数）
    ('od-matrix', 'large_data'): ('preferred', {}),  # 医院之间od矩阵（数据量约45万条）
    ('od-matrix', 'empty_dataset'): ('preferred', {}),  # 医院之间od矩阵（数据为空）
    ('od-matrix', 'high_null_ratio'): ('preferred', {}),  # 医院之间od矩阵（数值字段八成为空值）
    ('od-matrix', 'temporal_insufficient'): ('preferred', {}),  # 医院之间od矩阵（观测只有1期）
    ('categorical-composition', 'nominal_point'): ('preferred', {}),  # 各设施类别占比
    ('categorical-composition', 'nominal_polygon'): ('degraded', {}),  # 各设施类别占比（数据为面要素）
    ('categorical-composition', 'nominal_raster'): ('preferred', {}),  # 各设施类别占比（数据为像元矩阵）
    ('categorical-composition', 'geometry_line'): ('degraded', {}),  # 各设施类别占比（数据为线要素）
    ('categorical-composition', 'bad_crs'): ('preferred', {}),  # 各设施类别占比（数据坐标系为WGS84经纬度）
    ('categorical-composition', 'missing_required_field'): ('preferred', {}),  # 各设施类别占比（数据没有数值字段）
    ('categorical-composition', 'too_few_samples'): ('preferred', {}),  # 各设施类别占比（样本只有3条）
    ('categorical-composition', 'zero_variance'): ('preferred', {}),  # 各设施类别占比（数值字段全为常数）
    ('categorical-composition', 'large_data'): ('preferred', {}),  # 各设施类别占比（数据量约45万条）
    ('categorical-composition', 'empty_dataset'): ('preferred', {}),  # 各设施类别占比（数据为空）
    ('categorical-composition', 'high_null_ratio'): ('preferred', {}),  # 各设施类别占比（数值字段八成为空值）
    ('categorical-composition', 'temporal_insufficient'): ('preferred', {}),  # 各设施类别占比（观测只有1期）
    ('change-comparison', 'nominal_point'): ('degraded', {}),  # 两期影像卷帘对比
    ('change-comparison', 'nominal_polygon'): ('degraded', {}),  # 两期影像卷帘对比（数据为面要素）
    ('change-comparison', 'nominal_raster'): ('preferred', {}),  # 两期影像卷帘对比（数据为像元矩阵）
    ('change-comparison', 'geometry_line'): ('degraded', {}),  # 两期影像卷帘对比（数据为线要素）
    ('change-comparison', 'bad_crs'): ('degraded', {}),  # 两期影像卷帘对比（数据坐标系为WGS84经纬度）
    ('change-comparison', 'missing_required_field'): ('degraded', {}),  # 两期影像卷帘对比（数据没有数值字段）
    ('change-comparison', 'too_few_samples'): ('degraded', {}),  # 两期影像卷帘对比（样本只有3条）
    ('change-comparison', 'zero_variance'): ('degraded', {}),  # 两期影像卷帘对比（数值字段全为常数）
    ('change-comparison', 'large_data'): ('degraded', {}),  # 两期影像卷帘对比（数据量约45万条）
    ('change-comparison', 'empty_dataset'): ('degraded', {}),  # 两期影像卷帘对比（数据为空）
    ('change-comparison', 'high_null_ratio'): ('degraded', {}),  # 两期影像卷帘对比（数值字段八成为空值）
    ('change-comparison', 'temporal_insufficient'): ('degraded', {}),  # 两期影像卷帘对比（观测只有1期）
    ('sar-speckle', 'nominal_point'): ('minimal', {'subject': 'degraded'}),  # sar斑点滤波
    ('sar-speckle', 'nominal_polygon'): ('minimal', {'subject': 'degraded'}),  # sar斑点滤波（数据为面要素）
    ('sar-speckle', 'nominal_raster'): ('preferred', {'subject': 'eligible'}),  # sar斑点滤波（数据为像元矩阵）
    ('sar-speckle', 'geometry_line'): ('minimal', {'subject': 'degraded'}),  # sar斑点滤波（数据为线要素）
    ('sar-speckle', 'bad_crs'): ('minimal', {'subject': 'degraded'}),  # sar斑点滤波（数据坐标系为WGS84经纬度）
    ('sar-speckle', 'missing_required_field'): ('minimal', {'subject': 'degraded'}),  # sar斑点滤波（数据没有数值字段）
    ('sar-speckle', 'too_few_samples'): ('minimal', {'subject': 'degraded'}),  # sar斑点滤波（样本只有3条）
    ('sar-speckle', 'zero_variance'): ('minimal', {'subject': 'degraded'}),  # sar斑点滤波（数值字段全为常数）
    ('sar-speckle', 'large_data'): ('minimal', {'subject': 'degraded'}),  # sar斑点滤波（数据量约45万条）
    ('sar-speckle', 'empty_dataset'): ('blocked', {'subject': 'blocked'}),  # sar斑点滤波（数据为空）
    ('sar-speckle', 'high_null_ratio'): ('minimal', {'subject': 'degraded'}),  # sar斑点滤波（数值字段八成为空值）
    ('sar-speckle', 'temporal_insufficient'): ('minimal', {'subject': 'degraded'}),  # sar斑点滤波（观测只有1期）
    ('interannual-comparison', 'nominal_point'): ('preferred', {'subject': 'eligible', 'baseline': 'eligible', 'comparison_time': 'eligible'}),  # 年际对比分析
    ('interannual-comparison', 'nominal_polygon'): ('degraded', {'subject': 'transform_required', 'baseline': 'eligible', 'comparison_time': 'eligible'}),  # 年际对比分析（数据为面要素）
    ('interannual-comparison', 'nominal_raster'): ('preferred', {'subject': 'eligible', 'baseline': 'eligible', 'comparison_time': 'eligible'}),  # 年际对比分析（数据为像元矩阵）
    ('interannual-comparison', 'geometry_line'): ('degraded', {'subject': 'degraded', 'baseline': 'eligible', 'comparison_time': 'eligible'}),  # 年际对比分析（数据为线要素）
    ('interannual-comparison', 'bad_crs'): ('preferred', {'subject': 'eligible', 'baseline': 'eligible', 'comparison_time': 'eligible'}),  # 年际对比分析（数据坐标系为WGS84经纬度）
    ('interannual-comparison', 'missing_required_field'): ('preferred', {'subject': 'eligible', 'baseline': 'eligible', 'comparison_time': 'eligible'}),  # 年际对比分析（数据没有数值字段）
    ('interannual-comparison', 'too_few_samples'): ('preferred', {'subject': 'eligible', 'baseline': 'eligible', 'comparison_time': 'eligible'}),  # 年际对比分析（样本只有3条）
    ('interannual-comparison', 'zero_variance'): ('preferred', {'subject': 'eligible', 'baseline': 'eligible', 'comparison_time': 'eligible'}),  # 年际对比分析（数值字段全为常数）
    ('interannual-comparison', 'large_data'): ('preferred', {'subject': 'eligible', 'baseline': 'eligible', 'comparison_time': 'eligible'}),  # 年际对比分析（数据量约45万条）
    ('interannual-comparison', 'empty_dataset'): ('blocked', {'subject': 'blocked', 'baseline': 'blocked', 'comparison_time': 'blocked'}),  # 年际对比分析（数据为空）
    ('interannual-comparison', 'high_null_ratio'): ('preferred', {'subject': 'transform_required', 'baseline': 'eligible', 'comparison_time': 'eligible'}),  # 年际对比分析（数值字段八成为空值）
    ('interannual-comparison', 'temporal_insufficient'): ('preferred', {'subject': 'eligible', 'baseline': 'eligible', 'comparison_time': 'eligible'}),  # 年际对比分析（观测只有1期）
    ('twi-index', 'nominal_point'): ('minimal', {'elevation': 'degraded'}),  # twi指数计算
    ('twi-index', 'nominal_polygon'): ('minimal', {'elevation': 'degraded'}),  # twi指数计算（数据为面要素）
    ('twi-index', 'nominal_raster'): ('preferred', {'elevation': 'eligible'}),  # twi指数计算（数据为像元矩阵）
    ('twi-index', 'geometry_line'): ('minimal', {'elevation': 'degraded'}),  # twi指数计算（数据为线要素）
    ('twi-index', 'bad_crs'): ('minimal', {'elevation': 'degraded'}),  # twi指数计算（数据坐标系为WGS84经纬度）
    ('twi-index', 'missing_required_field'): ('minimal', {'elevation': 'degraded'}),  # twi指数计算（数据没有数值字段）
    ('twi-index', 'too_few_samples'): ('minimal', {'elevation': 'degraded'}),  # twi指数计算（样本只有3条）
    ('twi-index', 'zero_variance'): ('minimal', {'elevation': 'degraded'}),  # twi指数计算（数值字段全为常数）
    ('twi-index', 'large_data'): ('minimal', {'elevation': 'degraded'}),  # twi指数计算（数据量约45万条）
    ('twi-index', 'empty_dataset'): ('blocked', {'elevation': 'blocked'}),  # twi指数计算（数据为空）
    ('twi-index', 'high_null_ratio'): ('minimal', {'elevation': 'degraded'}),  # twi指数计算（数值字段八成为空值）
    ('twi-index', 'temporal_insufficient'): ('minimal', {'elevation': 'degraded'}),  # twi指数计算（观测只有1期）
    ('spatial-regression', 'nominal_point'): ('degraded', {'subject': 'eligible', 'boundary': 'degraded', 'measure': 'degraded'}),  # 地理加权回归gwr
    ('spatial-regression', 'nominal_polygon'): ('degraded', {'subject': 'transform_required', 'boundary': 'eligible', 'measure': 'eligible'}),  # 地理加权回归gwr（数据为面要素）
    ('spatial-regression', 'nominal_raster'): ('preferred', {'subject': 'eligible', 'boundary': 'eligible', 'measure': 'eligible'}),  # 地理加权回归gwr（数据为像元矩阵）
    ('spatial-regression', 'geometry_line'): ('minimal', {'subject': 'degraded', 'boundary': 'degraded', 'measure': 'degraded'}),  # 地理加权回归gwr（数据为线要素）
    ('spatial-regression', 'bad_crs'): ('degraded', {'subject': 'eligible', 'boundary': 'degraded', 'measure': 'degraded'}),  # 地理加权回归gwr（数据坐标系为WGS84经纬度）
    ('spatial-regression', 'missing_required_field'): ('blocked', {'subject': 'eligible', 'boundary': 'degraded', 'measure': 'degraded'}),  # 地理加权回归gwr（数据没有数值字段）
    ('spatial-regression', 'too_few_samples'): ('degraded', {'subject': 'eligible', 'boundary': 'degraded', 'measure': 'degraded'}),  # 地理加权回归gwr（样本只有3条）
    ('spatial-regression', 'zero_variance'): ('degraded', {'subject': 'eligible', 'boundary': 'degraded', 'measure': 'degraded'}),  # 地理加权回归gwr（数值字段全为常数）
    ('spatial-regression', 'large_data'): ('degraded', {'subject': 'eligible', 'boundary': 'degraded', 'measure': 'degraded'}),  # 地理加权回归gwr（数据量约45万条）
    ('spatial-regression', 'empty_dataset'): ('blocked', {'subject': 'blocked', 'boundary': 'blocked', 'measure': 'blocked'}),  # 地理加权回归gwr（数据为空）
    ('spatial-regression', 'high_null_ratio'): ('degraded', {'subject': 'transform_required', 'boundary': 'degraded', 'measure': 'degraded'}),  # 地理加权回归gwr（数值字段八成为空值）
    ('spatial-regression', 'temporal_insufficient'): ('degraded', {'subject': 'eligible', 'boundary': 'degraded', 'measure': 'degraded'}),  # 地理加权回归gwr（观测只有1期）
    ('interpolation-uncertainty', 'nominal_point'): ('preferred', {}),  # 插值预测不确定性
    ('interpolation-uncertainty', 'nominal_polygon'): ('degraded', {}),  # 插值预测不确定性（数据为面要素）
    ('interpolation-uncertainty', 'nominal_raster'): ('preferred', {}),  # 插值预测不确定性（数据为像元矩阵）
    ('interpolation-uncertainty', 'geometry_line'): ('degraded', {}),  # 插值预测不确定性（数据为线要素）
    ('interpolation-uncertainty', 'bad_crs'): ('preferred', {}),  # 插值预测不确定性（数据坐标系为WGS84经纬度）
    ('interpolation-uncertainty', 'missing_required_field'): ('degraded', {}),  # 插值预测不确定性（数据没有数值字段）
    ('interpolation-uncertainty', 'too_few_samples'): ('preferred', {}),  # 插值预测不确定性（样本只有3条）
    ('interpolation-uncertainty', 'zero_variance'): ('preferred', {}),  # 插值预测不确定性（数值字段全为常数）
    ('interpolation-uncertainty', 'large_data'): ('preferred', {}),  # 插值预测不确定性（数据量约45万条）
    ('interpolation-uncertainty', 'empty_dataset'): ('degraded', {}),  # 插值预测不确定性（数据为空）
    ('interpolation-uncertainty', 'high_null_ratio'): ('preferred', {}),  # 插值预测不确定性（数值字段八成为空值）
    ('interpolation-uncertainty', 'temporal_insufficient'): ('preferred', {}),  # 插值预测不确定性（观测只有1期）
}

# ── 确定性表述扩展（语义保持包装，conformance 已审定其语义中立性）───────────

#: 扩展句式（UTTERANCE_VARIANTS 前 4）与扩展 scope（无 / 单城）。
_EXPANSION_UTTERANCES = UTTERANCE_VARIANTS[:4]
_EXPANSION_SCOPES = SCOPE_VARIANTS[:2]
#: 每个参考场景的扩展数（×8 → 语料总量 ≥ 5000）。
_EXPANSIONS_PER_REFERENCE = 8


def _zh_wrap(phrase: str, scope_text: str, zh_tpl: str) -> str:
    return " ".join(zh_tpl.replace("{scope}", scope_text).replace("{q}", phrase).split())


def _reference_query(family_phrase: str, state: DataStateProfile) -> str:
    """参考查询 = 族短语 + 数据状态声明后缀（(family, state) 查询唯一）。"""
    return f"{family_phrase}{state.query_suffix}"


def _expansion_queries(family_phrase: str, state: DataStateProfile) -> List[str]:
    """确定性表述扩展：句式 × scope × 备选短语，附加同一状态声明后缀。"""
    queries: List[str] = []
    for i in range(_EXPANSIONS_PER_REFERENCE):
        zh_tpl, _, _ = _EXPANSION_UTTERANCES[i % len(_EXPANSION_UTTERANCES)]
        scope_text = _EXPANSION_SCOPES[i // len(_EXPANSION_UTTERANCES)][0]
        queries.append(_zh_wrap(family_phrase, scope_text, zh_tpl) + state.query_suffix)
    # 同族同态下查询唯一（句式模板互异保证）
    assert len(set(queries)) == len(queries), "expansion queries must be unique"
    return queries


def _scenario_kind_for(category: str, label: str, state_id: str) -> str:
    """scenario_kind：几何态沿用族类别，数据态统一 ``data_<state>``。"""
    if state_id in _DATA_STATES:
        return f"{DATA_SCENARIO_PREFIX}{state_id}"
    return f"{category}_{label}"


def _conformance_reference_case(
    family: ConformanceFamily, state: DataStateProfile,
) -> GISBenchmarkCase:
    """一个 (conformance 族, 数据状态) 参考场景（全契约）。"""
    category, label = _FAMILY_CATEGORY[family.family_id]
    tier, quals = _STATE_CONTRACTS[(family.family_id, state.state_id)]
    tool_classes, exports, budget = _TOOL_CONTRACTS[family.family_id]
    query = _reference_query(family.phrases_zh[0], state)
    is_nominal = state.state_id == "nominal_point"
    offline = state.state_id in _OFFLINE_FAMILY_STATES.get(family.family_id, ())
    trace = ["plan.task", "plan.recipe_id", "plan.resolved_tools"]
    if state.state_id in _DATA_STATES:
        trace += ["qualification.states", "fallback.tier"]
    return GISBenchmarkCase(
        id=f"QS-{family.family_id}-{state.state_id}",
        name=f"{family.family_id} :: {state.label}",
        group=f"quality-{category}",  # type: ignore[arg-type]
        query=query,
        description=f"[{family.domain}/{category}:{label}] {family.description} —— {state.label}",
        plan_only=True,
        scenario_kind=_scenario_kind_for(category, label, state.state_id),
        # 语义身份（复用审定族表；全部表述共享）
        expected_task=family.expected_task,
        expected_tasks=[t for t in (family.expected_task, *family.alternative_tasks) if t],
        expected_recipes=[r for r in (family.expected_recipe, *family.alternative_recipes) if r],
        expected_recipe=family.expected_recipe,
        expected_capabilities=list(family.expected_capabilities),
        expected_warning_codes=list(family.expected_warning_codes),
        forbidden_warning_codes=list(family.forbidden_warning_codes),
        expected_ontology_task=family.expected_ontology_task,
        # 数据状态契约（冻结表）
        qualification_profile=dict(state.facts),
        expected_fallback_tier=tier,
        expected_qualification=dict(quals),
        # DSL 预算 / 工具类别契约（nominal 校准；离线族按态附加）
        expected_tool_classes=list(tool_classes) if is_nominal else [],
        expected_export_formats=list(exports) if is_nominal else [],
        max_context_schema_bytes=budget if is_nominal else None,
        forbid_network_tools=offline,
        trace_requirements=trace,
        check_determinism=False,
    )


def _conformance_expansion_case(
    family: ConformanceFamily, state: DataStateProfile, seq: int, query: str,
) -> GISBenchmarkCase:
    """参考场景的表述扩展（语义身份契约；资格契约仅在参考场景深断言）。"""
    category, label = _FAMILY_CATEGORY[family.family_id]
    return GISBenchmarkCase(
        id=f"QS-{family.family_id}-{state.state_id}-e{seq}",
        name=f"{family.family_id} :: {state.state_id} 扩展{seq}",
        group=f"quality-{category}",  # type: ignore[arg-type]
        query=query,
        description=f"[{family.domain}/{category}:{label}] 表述扩展（语义身份）",
        plan_only=True,
        scenario_kind=_scenario_kind_for(category, label, state.state_id),
        expected_task=family.expected_task,
        expected_tasks=[t for t in (family.expected_task, *family.alternative_tasks) if t],
        expected_recipes=[r for r in (family.expected_recipe, *family.alternative_recipes) if r],
        expected_recipe=family.expected_recipe,
        expected_capabilities=list(family.expected_capabilities),
        expected_warning_codes=list(family.expected_warning_codes),
        forbidden_warning_codes=list(family.forbidden_warning_codes),
        expected_ontology_task=family.expected_ontology_task,
        forbid_network_tools=(
            state.state_id in _OFFLINE_FAMILY_STATES.get(family.family_id, ())
        ),
    )


def _quality_family_cases(family: QualityFamily) -> List[GISBenchmarkCase]:
    """制图 / agent 新族：参考场景 + 短语扩展（制图族带 facet/导出契约）。"""
    cases: List[GISBenchmarkCase] = []
    kind = f"{family.category}_{family.label}"
    for pi, phrase in enumerate(family.phrases):
        cases.append(GISBenchmarkCase(
            id=f"QS-{family.family_id}-{pi}",
            name=f"{family.family_id} :: {family.label}",
            group=f"quality-{family.category}",  # type: ignore[arg-type]
            query=phrase,
            description=f"[{family.category}:{family.label}] {family.description}",
            plan_only=True,
            scenario_kind=kind,
            expected_task=family.expected_task or None,
            expected_tasks=[t for t in family.expected_tasks if t],
            expected_recipe=family.expected_recipe or None,
            expected_capabilities=list(family.expected_capabilities),
            expected_product_facets=list(family.expected_product_facets),
            expected_export_formats=list(family.expected_export_formats),
            trace_requirements=["plan.task", "plan.recipe_id", "plan.resolved_tools"],
        ))
    return cases


def build_quality_scenario_corpus() -> List[GISBenchmarkCase]:
    """确定性生成质量场景语料（id 排序；零随机、零 LLM、零 I/O）。

    结构：
    - 参考场景：59 个 conformance 族 × 12 数据状态（契约冻结表）+ 制图族 +
      agent spec 族；
    - 扩展场景：conformance 参考的确定性表述扩展（语义身份契约）。
    """
    cases: List[GISBenchmarkCase] = []
    seen_ref_queries: set = set()
    for family in CONFORMANCE_FAMILIES:
        if family.family_id not in _FAMILY_CATEGORY:  # pragma: no cover - 审定表遗漏哨兵
            raise AssertionError(f"family missing goal-doc category: {family.family_id}")
        for state in DATA_STATE_PROFILES:
            cases.append(_conformance_reference_case(family, state))
            ref_query = _reference_query(family.phrases_zh[0], state)
            # 反垃圾护栏：参考查询按 (family, state) 唯一（声明后缀消歧）
            assert ref_query not in seen_ref_queries, \
                f"duplicate reference query: {ref_query}"
            seen_ref_queries.add(ref_query)
            for seq, query in enumerate(
                    _expansion_queries(family.phrases_zh[0], state)):
                cases.append(_conformance_expansion_case(family, state, seq, query))
    for family in (*CARTOGRAPHY_FAMILIES, *AGENT_FAMILIES):
        cases.extend(_quality_family_cases(family))
    cases.sort(key=lambda c: c.id)
    # 反垃圾护栏（构造即校验，fail fast）
    ids = [c.id for c in cases]
    assert len(ids) == len(set(ids)), "quality corpus has duplicate ids"
    assert len(DATA_STATE_PROFILES) <= 64, "per-family profile variants must stay <= 64"
    return cases


def quality_corpus_reference_count() -> int:
    """参考场景数 = (conformance 族 × 数据状态) + 新族参考场景。"""
    return len(CONFORMANCE_FAMILIES) * len(DATA_STATE_PROFILES) + len(
        CARTOGRAPHY_FAMILIES) + len(AGENT_FAMILIES)


__all__ = [
    "AGENT_FAMILIES",
    "CARTOGRAPHY_FAMILIES",
    "DATA_STATE_PROFILES",
    "build_quality_scenario_corpus",
    "quality_corpus_reference_count",
]
