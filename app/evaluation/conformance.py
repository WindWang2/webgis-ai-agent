"""Conformance Corpus（Goal C / C9 + C10）—— 大规模离线语义一致性案例库。

设计契约（规格 §14）：

- 案例由「人工审定的语义期望表（family 表）× 确定性输入扩展」生成 ——
  family 表是本模块中被审定的工件：每族声明 task / recipe / 核心能力 /
  警告码契约；扩展只改变**表述**（语言 × scope × 句式），不改变语义身份。
  语义不变量即被测物：同一族的所有表述必须解析到同一 task/recipe/义务。
- 全部离线、确定、零 LLM；plan tier 复用 GISBenchmarkRunner；
  workflow 契约 tier 复用 workflow_compiler（C5）与 verdict V2（C7）。
- 与 306 案例（golden + matrix）的关系：那里锁定通用产品族；本库把覆盖
  推到专业工作流、多语言多 scope 表述与 anti-claim 负例。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple

from app.evaluation.case import GISBenchmarkCase

# ── 确定性输入扩展维度 ────────────────────────────────────────────────────

#: scope 注入词（前缀）。空串 = 无 scope 表述。多城 scope 用「和」连接，
#: 不携带任务语义（不会触发 change/proximity 等规则）。
#: V3 扩容 5→12：纯城市/省级地名，逐一核实不改变语义身份（task/recipe）。
SCOPE_VARIANTS: Tuple[Tuple[str, str], ...] = (
    ("", "city-unspecified"),
    ("成都市", "city-chengdu"),
    ("成都武侯区", "district-wuhou"),
    ("四川省", "province-sichuan"),
    ("成都和重庆", "multi-city"),
    ("重庆市", "city-chongqing"),
    ("北京市", "city-beijing"),
    ("上海市", "city-shanghai"),
    ("广州市", "city-guangzhou"),
    ("杭州市", "city-hangzhou"),
    ("深圳市", "city-shenzhen"),
    ("四川省成都市", "province-city-chengdu"),
)

#: 句式包装（语言 × 语态）。zh 模板占位 {scope}/{q}；en 模板占位
#: {scope_en}/{q}。包装词均不携带任务语义（核实：display/疑问动词在
#: 规则序中弱于专业规则；报告词仅触发 report_product 附加信号）。
#: V3 扩容 4→9：新增语态逐一核实不改变语义身份。
UTTERANCE_VARIANTS: Tuple[Tuple[str, str, str, str], ...] = (
    ("{scope}{q}", "{q} in {scope_en}", "direct"),
    ("帮我看看{scope}{q}", "please show {q} in {scope_en}", "colloquial-show"),
    ("{scope}{q}吗", "what about {q} in {scope_en}", "interrogative"),
    ("用于报告：{scope}{q}", "report: {q} in {scope_en}", "report"),
    ("请分析{scope}{q}", "analyze {q} in {scope_en}", "analyze"),
    ("{scope}{q}的情况", "the situation of {q} in {scope_en}", "situation"),
    ("我想了解{scope}{q}", "i want to know about {q} in {scope_en}", "want-know"),
    ("{scope}{q}如何", "how about {q} in {scope_en}", "how-about"),
    ("帮忙做一下{scope}{q}", "help with {q} in {scope_en}", "help-do"),
)

#: scope 的英文展开（en 句式用）。
_SCOPE_EN = {
    "city-unspecified": "", "city-chengdu": "Chengdu",
    "district-wuhou": "Wuhou District", "province-sichuan": "Sichuan",
    "multi-city": "Chengdu and Chongqing",
    "city-chongqing": "Chongqing", "city-beijing": "Beijing",
    "city-shanghai": "Shanghai", "city-guangzhou": "Guangzhou",
    "city-hangzhou": "Hangzhou", "city-shenzhen": "Shenzhen",
    "province-city-chengdu": "Chengdu, Sichuan",
}


@dataclass(frozen=True)
class ConformanceFamily:
    """一个人工审定的语义族（期望表的审定工件）。

    语义身份断言（每族一份，全部表述共享）：
    - expected_task：确定性规则族（exact）；
    - expected_recipe：该表述下的产品族（exact）；
    - expected_capabilities：必须全部 resolve 的核心能力子集；
    - expected_warning_codes / forbidden_warning_codes：稳定警告码契约。
    """
    family_id: str
    domain: str
    description: str
    phrases_zh: Tuple[str, ...]           # 含专业触发词的种子短语
    phrases_en: Tuple[str, ...] = ()
    expected_task: Optional[str] = None
    expected_recipe: Optional[str] = None
    expected_capabilities: Tuple[str, ...] = ()
    # 口语展示句式诚实落到相邻产品族时的备选 recipe（与 expected_recipe
    # 并集判定；仅用于声明 alternative_tasks 的族）。
    alternative_recipes: Tuple[str, ...] = ()
    # 口语展示句式的诚实落点（与 expected_task 并集判定；recipe 锁不变）。
    # 置于位置参数之后，保持 (id, domain, desc, zh, en, task, recipe, caps)
    # 的既有位置参数布局；需要时以关键字传参。
    alternative_tasks: Tuple[str, ...] = ()
    expected_warning_codes: Tuple[str, ...] = ()
    forbidden_warning_codes: Tuple[str, ...] = ()
    # V3（Goal §十二）：本体任务匹配契约 —— intent 的本体 top-1 必须命中
    # 声明任务（GIS task ontology 映射的语义回归锁；空 = 不检查）。
    expected_ontology_task: str = ""
    group: str = "conformance"


# ── 人工审定的语义族表（C2 recipe packs 的 NL 路由契约面）────────────────

CONFORMANCE_FAMILIES: Tuple[ConformanceFamily, ...] = (
    # ── distribution / inventory ──────────────────────────────────────
    ConformanceFamily(
        "edu-facility-distribution", "distribution", "教育设施分布概览",
        ("小学的分布情况", "学校分布", "中小学分布情况"),
        ("school distribution",),
        "distribution_overview", "poi_distribution_overview",
        ("poi_query",), group="conformance-distribution",
    ),
    ConformanceFamily(
        "healthcare-distribution", "distribution", "医疗设施分布概览",
        ("医院的分布情况", "医院分布", "诊所分布"),
        ("hospital distribution",),
        "distribution_overview", "poi_distribution_overview",
        ("poi_query",), group="conformance-distribution",
    ),
    ConformanceFamily(
        "poi-inventory", "distribution", "POI 要素清单",
        ("咖啡馆要素清单", "咖啡馆有哪些", "健身房本底调查"),
        ("cafe inventory",),
        "distribution_overview", "poi_distribution_overview",
        ("poi_query",), alternative_tasks=("simple_view",),
        group="conformance-distribution",
    ),
    ConformanceFamily(
        "admin-feature-audit", "distribution", "行政区划要素清查",
        ("小学各街道数量清查", "公园按街道统计", "便利店各乡镇数量"),
        (),
        "administrative_statistic", "administrative_choropleth",
        ("poi_query", "admin_boundary_query"), group="conformance-distribution",
    ),
    # ── density / hotspot ─────────────────────────────────────────────
    ConformanceFamily(
        "density-visual", "density", "视觉密度（非显著性）",
        ("餐饮店的疏密态势", "商铺密集程度", "超市哪里密"),
        (),
        "distribution_overview", "poi_distribution_overview",
        ("poi_query",), alternative_tasks=("simple_view",),
        forbidden_warning_codes=("HOTSPOT_SCREENING_NOT_SIGNIFICANCE",),
        group="conformance-density",
    ),
    ConformanceFamily(
        "density-quantitative", "density", "定量密度（每平方公里）",
        ("每平方公里人口密度", "单位面积岗位密度"),
        ("population density per square kilometer",),
        "analytical_density", "administrative_choropleth",
        ("admin_aggregation",), group="conformance-density",
    ),
    ConformanceFamily(
        "density-grid", "density", "格网聚合密度",
        ("写字楼格网聚合", "餐馆六边形格网分析", "门店网格密度分布"),
        (),
        "distribution_overview", "grid_density_aggregate",
        ("poi_query",), alternative_tasks=("simple_view", "analytical_density"),
        alternative_recipes=("poi_distribution_overview",),
        group="conformance-density",
    ),
    # ── statistics / autocorrelation（新任务族）────────────────────────
    ConformanceFamily(
        "global-autocorrelation", "statistics", "全局空间自相关",
        ("房价的空间自相关分析", "各区房价莫兰指数", "房价 global moran 检验"),
        (),
        "spatial_autocorrelation", "global_moran_autocorrelation",
        ("global_morans_i",),
        group="conformance-statistics",
    ),
    ConformanceFamily(
        "local-autocorrelation", "statistics", "局部自相关聚类（LISA）",
        ("房价局部自相关分析", "房价 lisa 聚集图"),
        (),
        "spatial_autocorrelation", "local_moran_lisa",
        ("local_morans_i",),
        group="conformance-statistics",
    ),
    ConformanceFamily(
        "hotspot-significance", "statistics", "Gi* 显著性热点",
        ("各区案件量的显著性热点", "案件量热点检验", "报警量 getis 热点分析"),
        (),
        "concentration_analysis", "point_density",
        ("poi_query",),
        group="conformance-statistics",
    ),
    ConformanceFamily(
        "rate-normalized", "statistics", "比率归一化专题",
        ("各区商店占比统计", "各区地均产出统计", "各区人口占比统计"),
        (),
        "administrative_statistic", "administrative_choropleth",
        ("admin_aggregation",),
        group="conformance-statistics",
    ),
    # ── point pattern ─────────────────────────────────────────────────
    ConformanceFamily(
        "voronoi-coverage", "point_pattern", "Voronoi 服务域",
        ("诊所服务域划分", "门店泰森多边形", "网点 voronoi 覆盖"),
        (),
        "accessibility_analysis", "accessibility_analysis",
        ("service_area",),
        group="conformance-point-pattern",
    ),
    ConformanceFamily(
        "extent-delimitation", "point_pattern", "分布范围圈定",
        ("共享单车分布范围", "单车活动范围圈定", "运维点覆盖圈"),
        (),
        "distribution_overview", "poi_distribution_overview",
        ("poi_query",), alternative_tasks=("simple_view",),
        group="conformance-point-pattern",
    ),
    # ── interpolation（旧任务族内契约经显式 recipe 断言，见 contract 库）──
    ConformanceFamily(
        "kriging-explicit", "interpolation", "克里金插值（专业词路由）",
        ("克里金插值分析气温", "气温 kriging 表面", "克里金插值降水"),
        (),
        "raster_distribution", "raster_distribution",
        ("spatial_interpolation",),
        group="conformance-interpolation",
    ),
    ConformanceFamily(
        "interpolation-generic", "interpolation", "插值面（通用路由）",
        ("气温插值成面", "降水插值", "土壤湿度插值表面"),
        ("interpolate temperature",),
        "raster_distribution", "raster_distribution",
        ("spatial_interpolation",),
        group="conformance-interpolation",
    ),
    # ── terrain（新任务族）─────────────────────────────────────────────
    ConformanceFamily(
        "slope-analysis", "terrain", "坡度分析",
        ("坡度分析", "区域坡度分级", "steep slope 坡度图"),
        ("slope analysis",),
        "terrain_analysis", "slope_analysis_workflow",
        ("terrain_slope",),
        expected_warning_codes=("TERRAIN_METRIC_CRS_REQUIRED",),
        group="conformance-terrain",
    ),
    ConformanceFamily(
        "hillshade-product", "terrain", "山体阴影渲染",
        ("山体阴影渲染", "地形晕渲图", "hillshade 渲染"),
        ("hillshade",),
        "terrain_analysis", "hillshade_cartography",
        ("terrain_hillshade",),
        group="conformance-terrain",
    ),
    ConformanceFamily(
        "viewshed-analysis", "terrain", "视域分析",
        ("瞭望塔视域分析", "信号塔通视范围", "观景台可视域"),
        ("viewshed analysis",),
        "terrain_analysis", "viewshed_analysis_workflow",
        ("terrain_viewshed",),
        group="conformance-terrain",
    ),
    ConformanceFamily(
        "contour-product", "terrain", "等高线产品",
        ("等高线图", "区域等值线地形图", "contour 地形产品"),
        ("contour map",),
        "terrain_analysis", "contour_map_product",
        ("terrain_contours",),
        group="conformance-terrain",
    ),
    # ── hydrology（新任务族）───────────────────────────────────────────
    ConformanceFamily(
        "watershed-delineation", "hydrology", "流域划分",
        ("流域划分", "汇水区圈定", "分水岭流域提取"),
        ("watershed delineation",),
        "watershed_analysis", "watershed_delineation_workflow",
        ("terrain_hydrology",),
        group="conformance-hydrology",
    ),
    ConformanceFamily(
        "stream-extraction", "hydrology", "河网提取",
        ("河网提取", "水系提取分析", "河道提取"),
        ("stream extraction",),
        "watershed_analysis", "stream_network_extraction",
        ("terrain_hydrology",),
        group="conformance-hydrology",
    ),
    ConformanceFamily(
        "flood-extent-screen", "hydrology", "淹没范围初筛",
        ("洪水淹没范围初筛", "水位推演淹没"),
        ("flood extent",),
        "watershed_analysis", "flood_extent_screening",
        ("terrain_hydrology",),
        group="conformance-hydrology",
    ),
    # ── remote sensing ────────────────────────────────────────────────
    ConformanceFamily(
        "ndvi-monitor", "remote_sensing", "NDVI 植被监测",
        ("NDVI 植被指数计算", "植被覆盖度制图", "绿度指数分析"),
        ("ndvi mapping",),
        "vegetation_index", "raster_distribution",
        ("ndvi",),
        group="conformance-remote-sensing",
    ),
    ConformanceFamily(
        "landcover-map", "remote_sensing", "地表覆盖分类图",
        ("土地利用影像分类图", "地表覆盖影像制图", "地类影像解译"),
        ("land cover imagery map",),
        "raster_distribution", "raster_distribution",
        ("raster_source",), alternative_tasks=("simple_view",),
        group="conformance-remote-sensing",
    ),
    ConformanceFamily(
        "bitemporal-optical-change", "remote_sensing", "双时相变化检测",
        ("两期影像变化检测", "影像前后对比", "两期遥感变化图斑"),
        ("image change detection",),
        "change_detection", "raster_distribution",
        ("raster_change_detection",),
        group="conformance-remote-sensing",
    ),
    # ── SAR（新任务族）─────────────────────────────────────────────────
    ConformanceFamily(
        "sar-overview", "sar", "SAR 后向散射概览",
        ("SAR 影像后向散射概览", "雷达影像解译", "合成孔径雷达影像分析"),
        (),
        "sar_analysis", "sar_backscatter_overview",
        ("sar_analysis",),
        group="conformance-sar",
    ),
    ConformanceFamily(
        "insar-deformation", "sar", "InSAR 形变筛查",
        ("InSAR 地表形变筛查", "地面沉降 InSAR 监测", "差分干涉形变测量"),
        (),
        "sar_analysis", "insar_deformation_screening",
        ("sar_analysis",),
        group="conformance-sar",
    ),
    # ── temporal（新任务族）────────────────────────────────────────────
    ConformanceFamily(
        "linear-trend", "temporal", "线性趋势",
        ("站点观测变化趋势", "用水量逐年趋势分析", "客流逐年趋势分析"),
        ("trend analysis",),
        "temporal_trend", "linear_trend_analysis",
        ("temporal_trend",),
        group="conformance-temporal",
    ),
    ConformanceFamily(
        "changepoint-detection", "temporal", "突变点检测",
        ("用水量突变点检测", "销量拐点分析", "订单量变化节点检测"),
        (),
        "temporal_trend", "changepoint_detection_workflow",
        ("temporal_change_point",),
        group="conformance-temporal",
    ),
    # ── change detection（旧任务族）────────────────────────────────────
    ConformanceFamily(
        "landcover-change", "change_detection", "地类变化台账",
        ("土地利用变化台账", "耕地变化分析", "林地两期对比分析"),
        (),
        "change_detection", "raster_distribution",
        ("raster_change_detection",),
        group="conformance-change",
    ),
    ConformanceFamily(
        "urban-expansion", "change_detection", "城市扩张监测",
        ("城市扩张监测", "建成区扩张分析", "城镇扩展监测"),
        ("urban expansion",),
        "change_detection", "raster_distribution",
        ("raster_change_detection",),
        group="conformance-change",
    ),
    # ── network（新任务族 network_route + 旧 mobility）──────────────────
    ConformanceFamily(
        "shortest-path", "network", "最短路径",
        ("从春熙路到天府广场的最短路径", "小区到地铁站的路径规划", "配送路径规划"),
        ("shortest path between two points",),
        "network_route", "shortest_path_routing",
        ("shortest_path",),
        group="conformance-network",
    ),
    ConformanceFamily(
        "closest-facility", "network", "最近设施",
        ("离小区最近的医院", "就近分配最近的消防站", "closest facility 就近分配"),
        ("closest facility",),
        "network_route", "closest_facility_assignment",
        ("closest_facility",),
        group="conformance-network",
    ),
    ConformanceFamily(
        "od-flow", "network", "OD 流动",
        ("通勤流分析", "OD 矩阵分析", "客流流动走廊"),
        ("commuting flows",),
        "mobility_flow", "od_flow_overview",
        ("od_matrix",),
        group="conformance-network",
    ),
    # ── accessibility ─────────────────────────────────────────────────
    ConformanceFamily(
        "service-area", "accessibility", "服务区/等时圈",
        ("15分钟步行圈", "医院服务区", "地铁站等时圈"),
        ("service area",),
        "accessibility_analysis", "accessibility_analysis",
        ("service_area",),
        group="conformance-accessibility",
    ),
    ConformanceFamily(
        "coverage-gap", "accessibility", "覆盖盲区",
        ("养老设施覆盖盲区", "服务空白区识别", "健身设施覆盖缺口"),
        (),
        "accessibility_analysis", "accessibility_analysis",
        ("service_area",),
        group="conformance-accessibility",
    ),
    # ── equity ────────────────────────────────────────────────────────
    ConformanceFamily(
        "equity-generic", "equity", "公平性（通用表述 → V1 seed）",
        ("各区小学数量是否均衡", "医疗资源分布是否合理", "公园分布公不公平"),
        ("is healthcare access equitable",),
        "spatial_equity", "spatial_equity",
        ("poi_query",),
        expected_warning_codes=("EQUITY_MISSING_DENOMINATOR",),
        group="conformance-equity",
    ),
    # ── site selection / suitability / risk（决策族）───────────────────
    ConformanceFamily(
        "site-selection-generic", "site_selection", "选址（通用表述）",
        ("新学校选址", "仓库选址分析", "新的配送站最优位置"),
        (),
        "site_selection", "site_selection",
        ("mcda_evaluation",),
        expected_warning_codes=("SITE_SELECTION_CRITERIA_UNDECLARED",),
        group="conformance-decision",
    ),
    ConformanceFamily(
        "suitability-generic", "suitability", "适宜性（通用表述）",
        ("适建区评价", "开发适宜性分析", "建设适宜程度评价"),
        ("suitability assessment",),
        "suitability_assessment", "suitability_assessment",
        ("mcda_evaluation",),
        expected_warning_codes=("SUITABILITY_WEIGHT_PROVENANCE",),
        group="conformance-decision",
    ),
    ConformanceFamily(
        "exposure-buffer-screen", "exposure", "危险源缓冲受体筛查",
        ("加油站卫生防护距离筛查", "污染源周边敏感目标暴露筛查", "化工园区卫生防护距离风险评估"),
        ("buffer exposure screening",),
        "risk_exposure", "hazard_buffer_receptor_screen",
        (), alternative_recipes=("risk_exposure",),
        group="conformance-decision",
    ),
    ConformanceFamily(
        "greening-rate", "urban", "绿化率统计",
        ("各区绿化率统计", "各区绿地率排名", "城区各区绿地率统计"),
        (),
        "administrative_statistic", "administrative_choropleth",
        ("admin_aggregation",),
        group="conformance-decision",
    ),
    ConformanceFamily(
        "transit-coverage", "transport", "公交服务覆盖",
        ("公交站点服务区分析", "轨道站点15分钟等时圈", "地铁站可达性分析"),
        (),
        "accessibility_analysis", "accessibility_analysis",
        ("service_area",),
        group="conformance-accessibility",
    ),
    ConformanceFamily(
        "cultivated-land", "natural_resources", "耕地分布清查",
        ("耕地分布情况", "基本农田分布"),
        (),
        "raster_distribution", "raster_distribution",
        (),
        alternative_tasks=("simple_view", "distribution_overview"),
        alternative_recipes=("poi_distribution_overview",),
        group="conformance-remote-sensing",
    ),
    ConformanceFamily(
        "monitoring-coverage", "environment", "监测站点覆盖",
        ("空气质量监测站分布", "监测站点覆盖情况", "水质监测点分布"),
        (),
        "distribution_overview", "poi_distribution_overview",
        ("poi_query",), alternative_tasks=("simple_view",),
        group="conformance-distribution",
    ),
    ConformanceFamily(
        "hazard-inventory", "disaster", "地灾隐患点清单",
        ("地质灾害隐患点分布", "地质灾害隐患点风险清单", "地灾隐患风险点排查"),
        (),
        "risk_exposure", "geological_hazard_inventory",
        ("poi_query",), alternative_tasks=("distribution_overview",),
        alternative_recipes=("risk_exposure",),
        group="conformance-decision",
    ),
    ConformanceFamily(
        "clinic-coverage", "public_health", "基层医疗覆盖",
        ("社区卫生服务中心服务区分析", "诊所可达性分析", "基层医疗机构15分钟服务圈"),
        (),
        "accessibility_analysis", "accessibility_analysis",
        ("service_area",),
        group="conformance-accessibility",
    ),
    ConformanceFamily(
        "risk-generic", "risk", "风险（通用表述）",
        ("城市内涝风险区域", "地质灾害风险分析", "hazard 暴露评价"),
        ("flood risk area",),
        "risk_exposure", "risk_exposure",
        (),
        expected_warning_codes=("RISK_RECEPTORS_UNCONFIRMED",),
        group="conformance-decision",
    ),
    # ── V3 扩容族（本体任务升级 / 新语义维度锁定，期望为经验核验）──────
    ConformanceFamily(
        "road-centrality", "network", "路网中心性（本体升级族）",
        ("路网中心性分析", "道路介数中心性计算"),
        (),
        "network_route", "closest_facility_assignment",
        (),
        group="conformance-network",
    ),
    ConformanceFamily(
        "fire-risk", "risk", "火灾风险评价",
        ("城市火灾风险热点", "火灾风险评价"),
        (),
        "risk_exposure", "risk_exposure",
        (),
        expected_warning_codes=("RISK_RECEPTORS_UNCONFIRMED",),
        expected_ontology_task="decision.risk_exposure",
        group="conformance-decision",
    ),
    ConformanceFamily(
        "population-exposure", "exposure", "人口暴露估算",
        ("人口暴露估算", "洪泛区暴露人口"),
        (),
        "risk_exposure", "risk_exposure",
        (),
        group="conformance-decision",
    ),
    ConformanceFamily(
        "warehouse-site", "site_selection", "仓储物流选址",
        ("仓库选址评价", "物流园区选址"),
        (),
        "site_selection", "site_selection",
        (),
        expected_ontology_task="decision.site_selection",
        group="conformance-decision",
    ),
    ConformanceFamily(
        "od-matrix", "network", "OD 矩阵与流线",
        ("医院之间od矩阵", "通勤流线图"),
        (),
        "mobility_flow", "od_flow_overview",
        ("od_matrix",),
        expected_ontology_task="network.od_analysis",
        group="conformance-network",
    ),
    ConformanceFamily(
        "categorical-composition", "statistics", "分类构成统计",
        ("各设施类别占比", "设施类别构成"),
        (),
        "categorical_distribution", "categorical_distribution",
        ("category_breakdown",),
        expected_ontology_task="distribution.category_breakdown",
        group="conformance-statistics",
    ),
    ConformanceFamily(
        "change-comparison", "change_detection", "两期对比（卷帘/前后）",
        ("两期影像卷帘对比", "变化前后对比图"),
        (),
        "change_detection", "raster_distribution",
        (),
        group="conformance-change",
    ),
    ConformanceFamily(
        "sar-speckle", "sar", "SAR 斑点滤波",
        ("sar斑点滤波", "sar影像去噪"),
        (),
        "sar_analysis", "sar_backscatter_overview",
        ("sar_speckle_filtering",),
        group="conformance-sar",
    ),
    ConformanceFamily(
        "interannual-comparison", "temporal", "年际对比",
        ("年际对比分析", "多年年际对比"),
        (),
        "temporal_trend", "interannual_comparison_workflow",
        ("temporal_aggregate",),
        group="conformance-temporal",
    ),
    ConformanceFamily(
        "twi-index", "hydrology", "地形湿度指数（本体升级族）",
        ("twi指数计算",),
        (),
        "terrain_analysis", "slope_analysis_workflow",
        (),
        group="conformance-hydrology",
    ),
    ConformanceFamily(
        "spatial-regression", "statistics", "空间回归（本体升级族）",
        ("地理加权回归gwr", "空间回归分析"),
        (),
        "spatial_autocorrelation", "getis_ord_hotspot_significance",
        (),
        expected_ontology_task="spatial_statistics.spatial_regression",
        group="conformance-statistics",
    ),
    ConformanceFamily(
        "interpolation-uncertainty", "interpolation", "插值不确定性面",
        ("插值预测不确定性", "克里金方差面", "插值误差评估"),
        (),
        "raster_distribution", "raster_distribution",
        (),
        group="conformance-interpolation",
    ),
)


def _expand_family(
    family: ConformanceFamily,
) -> List[GISBenchmarkCase]:
    """family × scope × 句式 → 确定性案例集（id 稳定可复现）。"""
    cases: List[GISBenchmarkCase] = []
    scope_text, scope_tag = SCOPE_VARIANTS[0]
    phrases: List[Tuple[str, str]] = [
        (p, "zh") for p in family.phrases_zh
    ] + [(p, "en") for p in family.phrases_en]
    n = 0
    for pi, (phrase, lang) in enumerate(phrases):
        for scope_text, scope_tag in SCOPE_VARIANTS:
            for zh_tpl, en_tpl, utter_tag in UTTERANCE_VARIANTS:
                scope_en = _SCOPE_EN[scope_tag]
                if lang == "en":
                    query = en_tpl.replace("{q}", phrase)
                    if scope_en:
                        query = query.replace("{scope_en}", scope_en)
                    else:
                        query = query.replace(" in {scope_en}", "").replace(
                            "{scope_en}", "")
                    query = " ".join(query.split())
                    if query and query[0].isalpha() and not query[0].isupper():
                        query = query[0].upper() + query[1:]
                else:
                    query = zh_tpl.replace("{scope}", scope_text).replace("{q}", phrase)
                    query = " ".join(query.split())
                n += 1
                cases.append(GISBenchmarkCase(
                    id=f"CF-{family.family_id}-{lang}{pi}-{scope_tag}-{utter_tag}",
                    name=f"{family.family_id} :: {query[:40]}",
                    group=family.group,  # type: ignore[arg-type]
                    query=query,
                    description=f"[{family.domain}] {family.description}",
                    plan_only=True,
                    expected_task=family.expected_task,
                    expected_tasks=[t for t in (family.expected_task, *family.alternative_tasks) if t],
                    expected_recipes=[r for r in (family.expected_recipe, *family.alternative_recipes) if r],
                    expected_capabilities=list(family.expected_capabilities),
                    expected_recipe=family.expected_recipe,
                    expected_warning_codes=list(family.expected_warning_codes),
                    forbidden_warning_codes=list(family.forbidden_warning_codes),
                    expected_ontology_task=family.expected_ontology_task,
                ))
    return cases


def build_conformance_corpus(
    *,
    domains: Optional[List[str]] = None,
    family_ids: Optional[List[str]] = None,
) -> List[GISBenchmarkCase]:
    """确定性生成一致性语料（id 排序，无随机性）。

    ``domains`` / ``family_ids`` 过滤子集（分片测试用）。"""
    families = CONFORMANCE_FAMILIES
    if domains:
        families = tuple(f for f in families if f.domain in domains)
    if family_ids:
        families = tuple(f for f in families if f.family_id in family_ids)
    cases: List[GISBenchmarkCase] = []
    for family in families:
        cases.extend(_expand_family(family))
    cases.sort(key=lambda c: c.id)
    # 去重断言（构造即校验：id 冲突是语料定义错误，fail fast）
    ids = [c.id for c in cases]
    assert len(ids) == len(set(ids)), "conformance corpus has duplicate ids"
    return cases
