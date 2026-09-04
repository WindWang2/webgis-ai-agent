"""Deterministic case matrix（VNext §15 — Evaluation Harness V2）.

不生成无意义的关键词变体：矩阵按**语义族 × 表述风格 × 语言**组织 —— 每个
族覆盖口语/正式/任务导向/英文四种真实说法，断言 task/recipe/核心能力/
禁止过度分析/方法论警告。数据条件类（缺分母、稀疏点）走方法论与资格
断言（plan-tier 诚实面）；交互语义（user-wins / expired-no-remount）
保留在 golden 执行档（G8/G9）。

矩阵与 golden cases（G1–G33）合并后 ≥300 案（Phase 2 目标）。
"""
from __future__ import annotations

from typing import List

from app.evaluation.case import GISBenchmarkCase

# ─── 语义族定义 ────────────────────────────────────────────────────────
# (family, task, recipe, expected_caps, forbidden_algorithms, warnings)
# 表述按 (query, extra_assert_dict) 列出；extra 可覆盖 recipe/caps/警告。

_F = dict(
    distribution=(
        "distribution_overview", "poi_distribution_overview",
        ["poi_query"], ["interpolation."], []),
    simple_view=(
        "simple_view", "poi_distribution_overview",
        ["poi_query"], ["spatial.kde", "stats."], []),
    admin_stat=(
        "administrative_statistic", "administrative_choropleth",
        ["poi_query", "admin_aggregation"], [], []),
    density=(
        "analytical_density", "administrative_choropleth",
        ["poi_query", "admin_aggregation"], [], []),
    concentration=(
        "concentration_analysis", "point_density",
        ["poi_query", "kde_density"], ["interpolation."], []),
    categorical=(
        "categorical_distribution", "categorical_distribution",
        ["poi_query", "category_breakdown"], [], []),
    proximity=(
        "proximity_analysis", "proximity_analysis",
        ["poi_query", "proximity_buffer"], [], []),
    accessibility=(
        "accessibility_analysis", "accessibility_analysis",
        ["service_area"], [], []),
    raster=(
        "raster_distribution", "raster_distribution",
        ["raster_source"], ["spatial.kde"], []),
    change=(
        "change_detection", "raster_distribution",
        ["raster_source"], [], []),
    vegetation=(
        "vegetation_index", "raster_distribution",
        ["raster_source"], [], []),
    flow=(
        "mobility_flow", "od_flow_overview",
        ["od_matrix"], [], []),
    equity=(
        "spatial_equity", "spatial_equity",
        ["poi_query", "admin_aggregation"], ["interpolation."],
        ["spatial_equity"]),
    site_selection=(
        "site_selection", "site_selection",
        ["proximity_buffer", "mcda_evaluation"], ["interpolation."],
        ["site_selection"]),
    suitability=(
        "suitability_assessment", "suitability_assessment",
        ["raster_reclassify", "mcda_evaluation"], [],
        ["suitability"]),
    risk=(
        "risk_exposure", "risk_exposure",
        ["proximity_buffer", "spatial_join"], ["interpolation."],
        ["risk_exposure"]),
)

# 语义族 → 评估分组（closed Literal 词表）。
_FAMILY_GROUP = {
    "distribution": "poi", "simple_view": "poi", "admin_stat": "poi",
    "density": "poi", "concentration": "poi", "categorical": "poi",
    "proximity": "network", "accessibility": "network", "raster": "raster",
    "change": "raster", "vegetation": "raster", "flow": "od",
    "equity": "decision", "site_selection": "decision",
    "suitability": "decision", "risk": "decision",
}


# 每族的真实表述变体（口语 / 正式 / 任务导向 / 混合修饰 / 英文 ×2）。
_QUERIES = {
        "distribution": [
        "成都咖啡馆的分布情况", "分析一下成都小学的空间分布格局",
        "成都市的超市都分布在哪些地方", "看看成都加油站的散布态势",
        "成都博物馆都在哪儿散布着", "给我一份成都药店的分布概览",
        "了解一下成都加油站的疏密情况",
        "Show the distribution of schools in Chengdu",
        "What does the spatial distribution of hospitals look like in Chengdu",
        "where are the libraries scattered in Chengdu",
        "overview of cafe locations in Chengdu",
    ],
    "simple_view": [
        "给我看看成都的咖啡馆", "帮我显示一下成都的地铁站",
        "在地图上展示成都的公园", "瞄一眼成都的药店",
        "show me the cafes in Chengdu", "Display Chengdu metro stations",
    ],
        "admin_stat": [
        "成都各区的学校数量", "统计一下成都每个区有多少家医院",
        "按区县汇总成都的便利店数量并排名", "成都各区图书馆数量统计表",
        "成都哪个区体育馆最多", "各区加油站数量对比一览",
        "成都下辖各区小学有几所",
        "count schools by district in Chengdu",
        "number of hospitals by county in Chengdu",
        "Statistics by district for parks in Chengdu",
        "how many schools in each district of Chengdu",
    ],
    "density": [
        "成都每平方公里的人口密度", "成都各区单位面积的学校密度",
        "密度（每平方公里）分布图", "成都人口密度按每平方公里计算",
        "population density per square kilometer in Chengdu",
    ],
        "concentration": [
        "成都哪里最热闹最集中", "哪些区域餐饮最密集扎堆",
        "成都的热点区域在哪里", "找出成都聚集效应最强的片区",
        "成都夜宵聚集区在哪儿", "哪个片区写字楼最密集",
        "Where are the hottest gathering areas in Chengdu",
        "most crowded dining areas in Chengdu",
    ],
        "categorical": [
        "各类绿地占比构成", "成都餐饮按类别的分布结构",
        "分析成都POI的类型构成", "各类别教育设施数量占比",
        "成都商业业态分类分布", "按种类统计休闲设施",
        "category breakdown of restaurants in Chengdu",
        "composition of land use types",
    ],
        "proximity": [
        "地铁站周边500米内的便利店", "学校1公里范围内的教育培训机构",
        "医院附近的药店分布", "成都天府广场周边2公里的餐饮",
        "体育中心3km内的酒店", "公园绿地周边800米的住宅",
        "convenience stores within 500 meters of metro stations",
        "pharmacies within 1 km of hospitals",
    ],
        "accessibility": [
        "成都三甲医院的15分钟可达性", "地铁站800米服务区覆盖",
        "社区卫生服务中心的等时圈分析", "15分钟步行可达范围",
        "医疗设施服务覆盖盲区识别", "10分钟车程可达圈分析",
        "公交站500米覆盖范围评估", "步行15分钟生活圈分析",
        "walking accessibility of parks in Chengdu",
        "isochrone analysis for hospitals",
    ],
        "raster": [
        "成都DEM地形分布", "成都的遥感影像图",
        "展示成都的气温分布栅格", "成都高程数据可视化",
        "成都降水量分布栅格图", "成都会建成区不透水面分布",
        "Show the DEM terrain around Chengdu",
        "satellite imagery of Chengdu",
    ],
    "change": [
        "对比两期影像的变化", "成都2015与2025年土地利用变迁分析",
        "这两年的城市扩张变化检测", "历年对比成都植被覆盖变化",
        "land cover change between two periods",
    ],
    "vegetation": [
        "计算NDVI植被指数", "成都植被覆盖度NDVI分析",
        "用遥感影像算一下NDWI水体指数", "chengdu ndvi analysis",
    ],
        "flow": [
        "分析通勤OD流向", "成都职住通勤流可视化",
        "各区间出行流量OD矩阵", "早高峰客流流向分析",
        "跨区通勤的主要流向走廊", "游客在景区间的流动分析",
        "commuting flow analysis between districts",
        "origin destination matrix of trips",
    ],
        "equity": [
        "各区医疗资源是否均衡", "分析成都市小学分布是否公平",
        "成都各区教育资源公平性评价", "养老服务设施分布合理吗",
        "成都公园绿地分配是否均衡", "各区间体育设施人均差距分析",
        "社区卫生服务配置是否合理",
        "Is the distribution of schools in Chengdu equitable",
        "教育资源不足的区域在哪里",
        "healthcare equity across districts",
        "are libraries fairly distributed across Chengdu",
    ],
        "site_selection": [
        "为成都新分校选址推荐最优位置", "新医院选址分析",
        "成都新建地铁站布点建议", "物流仓库最佳位置选址",
        "新商业综合体候选位置评估", "成都第二机场选址建议",
        "充电站的选址推荐",
        "Where should we build the new hospital in Chengdu",
        "best location for a new fire station",
        "best site for a new logistics hub",
        "choose a site for the new campus library",
    ],
        "suitability": [
        "成都适建区评价", "农业用地适宜性评价",
        "城市建设用地适宜性分析", "生态红线外区域的开发适宜性",
        "浅丘地区耕作适宜性评价", "成都周边露营地适宜性分析",
        "land suitability assessment for construction",
        "suitable areas for urban development",
    ],
        "risk": [
        "分析成都洪水风险区域", "化工厂周边安全风险分析",
        "地质灾害隐患区域评估", "洪水淹没风险分析",
        "成都内涝风险区划", "危化品运输风险暴露分析",
        "山区滑坡灾害风险评估",
        "flood risk assessment for Chengdu",
        "hazard exposure around industrial parks",
    ],
}


def _family_cases(family: str, start_no: int) -> List[GISBenchmarkCase]:
    task, recipe, caps, forbidden, warnings = _F[family]
    cases: List[GISBenchmarkCase] = []
    for i, query in enumerate(_QUERIES[family], start=1):
        cases.append(GISBenchmarkCase(
            id=f"M{start_no + i - 1:03d}",
            name=f"{family} 矩阵#{i}",
            group=_FAMILY_GROUP[family],
            query=query,
            expected_task=task,
            expected_recipe=recipe,
            expected_capabilities=list(caps),
            expected_methodology_warnings=list(warnings),
            forbidden_algorithms=list(forbidden),
            max_tool_calls=10,
        ))
    return cases


# ─── 负例 / 无噪声档（方法论不误报）────────────────────────────────────

_NEGATIVE = [
    # 纯统计不带 equity 噪声
    ("N001", "生成成都各区小学数量柱状图", "administrative_statistic",
     ["spatial_equity"]),
    ("N002", "成都各区人口总数排名", "administrative_statistic",
     ["spatial_equity", "risk_exposure"]),
    ("N003", "成都咖啡馆分布情况", "distribution_overview",
     ["spatial_equity", "risk_exposure", "site_selection", "suitability"]),
    ("N004", "给我看看成都的咖啡馆", "simple_view",
     ["spatial_equity", "risk_exposure"]),
    ("N005", "成都各类型餐厅数量统计", None,
     ["risk_exposure", "site_selection"]),
    ("N006", "成都小学密度专题图", None,
     ["risk_exposure"]),
    ("N007", "成都各区GDP排名对比", None,
     ["temporal_change"]),
    ("N008", "成都人口分布热力图", None,
     ["temporal_change", "spatial_equity"]),
    ("N009", "地铁站周边500米便利店", None,
     ["risk_exposure"]),
    ("N010", "成都DEM地形图", None,
     ["temporal_change"]),
    ("N011", "Show schools in Chengdu", "simple_view",
     ["spatial_equity"]),
    ("N012", "school count per district", "administrative_statistic",
     ["spatial_equity"]),
    ("N013", "Chengdu cafe distribution overview", "distribution_overview",
     ["risk_exposure", "spatial_equity"]),
    ("N014", "NDVI of Chengdu", None, ["temporal_change"]),
    ("N015", "commuting flows in Chengdu", None, ["spatial_equity"]),
]


def _negative_cases() -> List[GISBenchmarkCase]:
    return [
        GISBenchmarkCase(
            id=cid, name=f"负例 无噪声#{cid}", group="negative",
            query=query,
            expected_task=task,
            forbidden_methodology_warnings=list(forbidden),
            max_tool_calls=8,
        )
        for cid, query, task, forbidden in _NEGATIVE
    ]


# ─── 形态信号 / 输出意图档 ─────────────────────────────────────────────

_FORM = [
    ("F001", "成都小学按1公里格网统计分布", "distribution_overview",
     "grid_density_aggregate", []),
    ("F002", "成都人口六边形蜂窝聚合图", "distribution_overview",
     "grid_density_aggregate", []),
    ("F003", "用气泡图展示各区人口", "distribution_overview",
     "proportional_symbol_map", []),
    ("F004", "各区GDP用比例符号地图", "distribution_overview",
     "proportional_symbol_map", []),
    ("F005", "成都小学按类别分布并给出占比图", "categorical_distribution",
     None, ["chart"]),
    ("F006", "成都各区小学数量柱状图", "administrative_statistic",
     None, ["chart"]),
    ("F007", "成都人口构成的饼图", "categorical_distribution",
     None, ["chart"]),
    ("F008", "给我一张用于报告的成都学校分布图", "distribution_overview",
     None, []),
    ("F009", "做一份成都绿地分布的汇报插图", None, None, []),
    ("F010", "成都学校分布图导出为PDF", "distribution_overview",
     None, []),
]


def _form_cases() -> List[GISBenchmarkCase]:
    cases = [
        GISBenchmarkCase(
            id=cid, name=f"形态信号 {cid}", group="form",
            query=query,
            expected_task=task,
            expected_recipe=recipe,
            expected_product_facets=list(facets or []),
            max_tool_calls=10,
        )
        for cid, query, task, recipe, facets in _FORM
    ]
    # 格网族的核心能力断言（grid_binning 必须进计划）
    for case in cases:
        if case.id in ("F001", "F002"):
            case.expected_capabilities = ["grid_binning"]
    return cases


# ─── 跨城 scope 稳健性（同一语义换城市不换族）──────────────────────────

_SCOPE_CITIES = ["北京", "上海", "杭州", "武汉", "西安"]
_SCOPE_FAMILY_QUERIES = [
    ("{c}小学分布情况", "distribution"),
    ("{c}各区医院数量", "admin_stat"),
    ("{c}咖啡馆哪里最集中", "concentration"),
    ("{c}新建消防站选址", "site_selection"),
    ("{c}各区公园分布是否均衡", "equity"),
    ("{c}内涝风险区域分析", "risk"),
    ("{c}各类型餐饮占比构成", "categorical"),
    ("{c}地铁站点周边500米便利店", "proximity"),
    ("{c}建设适宜性评价", "suitability"),
    ("{c}每平方公里的人口密度", "density"),
]


def _scope_cases() -> List[GISBenchmarkCase]:
    cases: List[GISBenchmarkCase] = []
    no = 0
    for tpl, family in _SCOPE_FAMILY_QUERIES:
        task, recipe, caps, forbidden, warnings = _F[family]
        for city in _SCOPE_CITIES:
            no += 1
            cases.append(GISBenchmarkCase(
                id=f"S{no:03d}",
                name=f"scope {city}·{family}",
                group="scope",
                query=tpl.format(c=city),
                expected_task=task,
                expected_recipe=recipe,
                expected_capabilities=list(caps),
                expected_methodology_warnings=list(warnings),
                forbidden_algorithms=list(forbidden),
                max_tool_calls=10,
            ))
    return cases


# ─── 决策族义务披露深挖（权重/受体/分母边界）───────────────────────────

_DECISION_DISCLOSURE = [
    ("D001", "为成都新分校选址推荐最优位置", "site_selection",
     ["site_selection"], None),
    ("D002", "在成都选一个最优位置建新医院，要求交通便利", "site_selection",
     ["site_selection"], None),
    ("D003", "成都新的体育中心选址评估", "site_selection",
     ["site_selection"], None),
    ("D004", "候选地块的选址评价分析", "site_selection",
     ["site_selection"], None),
    ("D005", "成都适建区评价", "suitability_assessment",
     ["suitability"], None),
    ("D006", "农业适宜性评价（都江堰周边）", "suitability_assessment",
     ["suitability"], None),
    ("D007", "山坡地建设适宜性分析", "suitability_assessment",
     ["suitability"], None),
    ("D008", "分析成都洪水风险区域", "risk_exposure",
     ["risk_exposure"], None),
    ("D009", "危化品仓储周边暴露分析", "risk_exposure",
     ["risk_exposure"], None),
    ("D010", "地震灾害易发区风险评估", "risk_exposure",
     ["risk_exposure"], None),
    ("D011", "成都各区小学教育资源公平性", "spatial_equity",
     ["spatial_equity"], None),
    ("D012", "社区卫生服务分布是否均衡", "spatial_equity",
     ["spatial_equity"], None),
    ("D013", "成都养老设施配置公平性分析", "spatial_equity",
     ["spatial_equity"], None),
    ("D014", "best site for a new logistics hub", "site_selection",
     ["site_selection"], None),
    ("D015", "flood exposure near the river", "risk_exposure",
     ["risk_exposure"], None),
    ("D016", "urban development suitability mapping", "suitability_assessment",
     ["suitability"], None),
    ("D017", "healthcare equity across districts", "spatial_equity",
     ["spatial_equity"], None),
]


def _decision_cases() -> List[GISBenchmarkCase]:
    cases = []
    for cid, query, task, expected_warns, _ in _DECISION_DISCLOSURE:
        task_def = _F[{
            "site_selection": "site_selection",
            "suitability_assessment": "suitability",
            "risk_exposure": "risk",
            "spatial_equity": "equity",
        }[task]]
        cases.append(GISBenchmarkCase(
            id=cid, name=f"决策披露 {cid}", group="decision",
            query=query,
            expected_task=task,
            expected_recipe=task_def[1],
            expected_capabilities=list(task_def[2]),
            expected_methodology_warnings=list(expected_warns),
            forbidden_algorithms=list(task_def[3]),
            max_tool_calls=10,
        ))
    return cases


#: 个案任务覆盖（按 query 键控 —— id 随矩阵扩展开移）：展示动词 + 栅格
#: 主体 = simple_view 轻量栅格视图（#781 栅格族 recipe 随主体）。
_TASK_OVERRIDES = {"Show the DEM terrain around Chengdu": "simple_view"}


# ─── 复合意图 / 混合表述档（VNext §15 Phase 2）─────────────────────────
# 多意图查询锁定「最强语义胜出」的裁决：展示动词+分布词=分布；统计+图表=
# 行政统计+chart facet；决策词压过一切形式词。

_COMPOUND = [
    # (id, query, task, recipe, caps, warnings, facets)
    ("C001", "展示成都小学的分布热力图", "distribution_overview",
     None, ["poi_query"], [], []),
    ("C002", "在地图上看看成都的咖啡馆分布", "distribution_overview",
     None, ["poi_query"], [], []),
    ("C003", "帮我显示成都各区医院数量", "administrative_statistic",
     None, ["poi_query", "admin_aggregation"], [], []),
    ("C004", "成都各区小学数量柱状图", "administrative_statistic",
     None, ["poi_query", "admin_aggregation"], [], ["chart"]),
    ("C005", "统计成都各类型学校的占比并画饼图", "categorical_distribution",
     None, ["poi_query", "category_breakdown"], [], ["chart"]),
    ("C006", "看看哪里最密集", "concentration_analysis",
     None, ["poi_query"], [], []),
    ("C007", "展示地铁站500米内的药店", "proximity_analysis",
     None, ["poi_query", "proximity_buffer"], [], []),
    ("C008", "成都学校分布图导出png", "distribution_overview",
     None, ["poi_query"], [], []),
    # 裁决：各区+分布图（无统计词）→ 分布族（report_product 随行）；
    # 行政统计需要数量/统计词（与 #780 语义一致）。
    ("C009", "用于汇报的成都各区GDP分布图", "distribution_overview",
     None, ["poi_query"], [], []),
    ("C010", "分析成都小学分布是否公平并标注薄弱区域", "spatial_equity",
     "spatial_equity", ["poi_query", "admin_aggregation"],
     ["spatial_equity"], []),
    ("C011", "为新校区选址并评估各候选位置", "site_selection",
     "site_selection", ["proximity_buffer", "mcda_evaluation"],
     ["site_selection"], []),
    # 裁决：双语义查询按规则序（选址 > 风险）—— 选址主导，风险语义词
    # 由查询面另行披露；确定性优先。
    ("C012", "评估洪水风险并给出避难场所选址建议", "site_selection",
     "site_selection", ["proximity_buffer", "mcda_evaluation"],
     ["site_selection"], []),
    ("C013", "在适宜建设区内为新学校选址", "site_selection",
     "site_selection", ["proximity_buffer", "mcda_evaluation"],
     ["site_selection"], []),
    ("C014", "成都各区教育资源的空间公平分析报告", "spatial_equity",
     "spatial_equity", ["poi_query", "admin_aggregation"],
     ["spatial_equity"], []),
    ("C015", "哪里租房最热门", "concentration_analysis",
     None, ["poi_query"], [], []),
    ("C016", "成都哪个区的公园最少", "administrative_statistic",
     None, ["poi_query", "admin_aggregation"], [], []),
    ("C017", "显示成都的餐饮热力分布", "distribution_overview",
     None, ["poi_query"], [], []),
    ("C018", "成都人口分布与各区的密度对比", "distribution_overview",
     None, ["poi_query"], [], []),
    ("C019", "Identify underserved areas for new clinics", "spatial_equity",
     "spatial_equity", ["poi_query", "admin_aggregation"],
     ["spatial_equity"], []),
    # 裁决：count by district 显式聚合词压过泛分布表述（规则序）。
    ("C020", "show hospital distribution and count by district",
     "administrative_statistic", None, ["poi_query", "admin_aggregation"], [], []),
    ("C021", "schools within walking distance of metro stations",
     "proximity_analysis", None, ["poi_query", "proximity_buffer"], [], []),
    ("C022", "分析两期土地利用变化并出图", "change_detection",
     "raster_distribution", ["raster_source"], [], []),
    ("C023", "成都各区学校人均配置的均衡性", "spatial_equity",
     "spatial_equity", ["poi_query", "admin_aggregation"],
     ["spatial_equity"], []),
    ("C024", "用克里金插值成都气温并显示误差", "raster_distribution",
     None, ["raster_source", "spatial_interpolation"], [], []),
    ("C025", "成都商圈客流来源的OD分析", "mobility_flow",
     "od_flow_overview", ["od_matrix"], [], []),
    ("C026", "哪些地方适合建郊野公园", "site_selection",
     "site_selection", ["proximity_buffer", "mcda_evaluation"],
     ["site_selection"], []),
    ("C027", "评估成都各地块的开发风险", "risk_exposure",
     "risk_exposure", ["proximity_buffer", "spatial_join"],
     ["risk_exposure"], []),
    ("C028", "成都平原农业适宜性制图", "suitability_assessment",
     "suitability_assessment", ["raster_reclassify", "mcda_evaluation"],
     ["suitability"], []),
    ("C029", "各区图书馆数量排名统计一下", "administrative_statistic",
     None, ["poi_query", "admin_aggregation"], [], []),
    ("C030", "Find the best location for a new community center",
     "site_selection", "site_selection",
     ["proximity_buffer", "mcda_evaluation"], ["site_selection"], []),
]


# ─── 表述风格深潜档（Phase 2 收口：口语化/长句/被动/问句/混合中英）────

_STYLE_DEEP = [
    # (id, query, task, recipe, caps, warnings)
    ("P001", "咱看看成都有多少家星巴克呗", "simple_view",
     None, ["poi_query"], []),
    ("P002", "麻烦帮我把成都的地铁站放到地图上", "simple_view",
     None, ["poi_query"], []),
    ("P003", "成都市锦江区范围内的小学分布", "distribution_overview",
     None, ["poi_query"], []),
    ("P004", "能不能统计下成都每个区县分别有多少个菜市场", "administrative_statistic",
     None, ["poi_query", "admin_aggregation"], []),
    ("P005", "成都写字楼密度（每平方公里）情况", "analytical_density",
     None, ["poi_query", "admin_aggregation"], []),
    ("P006", "成都是哪儿夜市摊贩最扎堆", "concentration_analysis",
     None, ["poi_query"], []),
    ("P007", "按种类看看成都的公园绿地构成", "categorical_distribution",
     None, ["poi_query", "category_breakdown"], []),
    ("P008", "成都春熙路附近1公里以内有什么奶茶店", "proximity_analysis",
     None, ["poi_query", "proximity_buffer"], []),
    ("P009", "从市民中心出发15分钟车程能到哪", "accessibility_analysis",
     None, ["service_area"], []),
    ("P010", "成都pm2.5监测站的克里金插值表面", "raster_distribution",
     None, ["raster_source", "spatial_interpolation"], []),
    ("P011", "2019和2024年成都建成区扩张变化检测", "change_detection",
     "raster_distribution", ["raster_source"], []),
    ("P012", "基于哨兵影像计算成都NDVI", "vegetation_index",
     None, ["raster_source"], []),
    ("P013", "早晚高峰跨区通勤的OD联系强度", "mobility_flow",
     "od_flow_overview", ["od_matrix"], []),
    ("P014", "成都各区社区卫生服务的可及性是否均衡", "spatial_equity",
     "spatial_equity", ["poi_query", "admin_aggregation"], ["spatial_equity"]),
    ("P015", "想在学校密集区以外新开一家书店，位置怎么选", "site_selection",
     "site_selection", ["proximity_buffer", "mcda_evaluation"], ["site_selection"]),
    ("P016", "沿河谷地带的居住适宜性怎么样", "suitability_assessment",
     "suitability_assessment", ["raster_reclassify", "mcda_evaluation"], ["suitability"]),
    ("P017", "加油站周边的火灾风险扩散分析", "risk_exposure",
     "risk_exposure", ["proximity_buffer", "spatial_join"], ["risk_exposure"]),
    ("P018", "Map the schools in Chengdu for me please", "simple_view",
     None, ["poi_query"], []),
    ("P019", "I need a density map per square km for Chengdu cafes",
     "analytical_density", None, ["poi_query", "admin_aggregation"], []),
    ("P020", "which parts of the city are underserved by parks",
     "spatial_equity", "spatial_equity",
     ["poi_query", "admin_aggregation"], ["spatial_equity"]),
]


def _style_cases() -> List[GISBenchmarkCase]:
    return [
        GISBenchmarkCase(
            id=cid, name=f"表述深潜 {cid}", group="decision" if warns else "poi",
            query=query,
            expected_task=task,
            expected_recipe=recipe,
            expected_capabilities=list(caps),
            expected_methodology_warnings=list(warns),
            max_tool_calls=10,
        )
        for cid, query, task, recipe, caps, warns in _STYLE_DEEP
    ]


def _compound_cases() -> List[GISBenchmarkCase]:
    return [
        GISBenchmarkCase(
            id=cid, name=f"复合意图 {cid}", group="compound",
            query=query,
            expected_task=task,
            expected_recipe=recipe,
            expected_capabilities=list(caps),
            expected_methodology_warnings=list(warns),
            expected_product_facets=list(facets),
            max_tool_calls=10,
        )
        for cid, query, task, recipe, caps, warns, facets in _COMPOUND
    ]


def build_matrix_cases() -> List[GISBenchmarkCase]:
    """全矩阵（确定性顺序：族矩阵 → 负例 → 形态 → scope → 决策披露）。"""
    cases: List[GISBenchmarkCase] = []
    no = 1
    for family in (
        "distribution", "simple_view", "admin_stat", "density",
        "concentration", "categorical", "proximity", "accessibility",
        "raster", "change", "vegetation", "flow",
        "equity", "site_selection", "suitability", "risk",
    ):
        fam_cases = _family_cases(family, no)
        cases.extend(fam_cases)
        no += len(fam_cases)
    cases.extend(_negative_cases())
    cases.extend(_form_cases())
    cases.extend(_scope_cases())
    cases.extend(_decision_cases())
    cases.extend(_compound_cases())
    cases.extend(_style_cases())
    # 去重保险（同 id 二次注册是编程错误）
    ids = [c.id for c in cases]
    assert len(ids) == len(set(ids)), "duplicate matrix case ids"
    for case in cases:
        if case.query in _TASK_OVERRIDES:
            case.expected_task = _TASK_OVERRIDES[case.query]
    return cases


def get_expected_total(golden_count: int) -> int:
    return golden_count + len(build_matrix_cases())


__all__ = ["build_matrix_cases"]
