"""Typed GIS intent model + deterministic resolver.

「分布」不再等价于单个工具调用：本模块把自然语言 GIS 请求解析为
typed / validated / serializable 的 :class:`MapRequestIntent`，供
Recipe 选择、产品规划与 Harness evidence 消费。

设计约束：

- deterministic —— 同一输入永远同一输出（规则序 + 无随机性）；
- 非 prompt-only —— 纯代码规则，可单测、可回放；
- LLM 可补充 —— agent 的语义理解通过 :func:`merge_intent_hints` 合并，
  但合并是显式、有记录的（``hint_applied``），不静默覆盖确定性结论的
  关键判定（如 analytical_density vs visual overview）。
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError

# ─── 类型词汇表 ─────────────────────────────────────────────────────────

TaskType = Literal[
    "distribution_overview",     # 宽泛「分布情况」→ 视觉概览产品族
    "simple_view",               # 「给我看看」→ 轻量点图，不过度分析
    "administrative_statistic",  # 「各区…数量」→ 行政聚合 + 分级统计
    "analytical_density",        # 「每平方公里密度」→ 定量密度（非视觉热力）
    "concentration_analysis",    # 「哪里最集中」→ 热点/密度/聚集语义
    "categorical_distribution",  # 「各类别占比/分布」→ 分类专题
    "proximity_analysis",        # 「周边/范围内」→ 邻近分析
    "accessibility_analysis",    # 「可达性/等时圈」→ 网络可达
    "raster_distribution",       # 栅格/遥感面状分布
    "change_detection",          # 变化检测
]

GeometryExpectation = Literal["point", "line", "polygon", "raster", "unknown"]

EntityType = Literal["poi", "facility", "boundary", "region", "network", "raster", "unknown"]

AnalysisIntent = Literal[
    "spatial_distribution",
    "administrative_summary",
    "administrative_aggregation",
    "analytical_density",
    "kde_density",
    "hotspot",
    "category_breakdown",
    "proximity_buffer",
    "service_area",
    "grid_binning",              # H3/渔网格网聚合
    "profile",
    "none",
]

CartographyIntent = Literal[
    "density_overview",          # 视觉热力
    "point_overlay",             # 点叠加
    "administrative_choropleth", # 行政分级填色
    "categorical_thematic",      # 分类专题
    "simple_point_map",          # 轻量点图
    "proximity_overlay",         # 缓冲/邻近叠加
    "raster_surface",            # 栅格面
    "hotspot_overlay",           # 热点标注/等值面
    "aggregate_grid",            # H3/渔网格网聚合填色
    "proportional_symbol",       # 比例符号（气泡）图
]

OutputIntent = Literal[
    "map", "statistics", "summary", "chart", "table", "export", "geojson", "csv",
]

ExportFormat = Literal["png", "pdf", "svg", "csv", "geojson"]


class ScopeIntent(BaseModel):
    """地理范围（未解析时 name 为空）。"""
    name: str = ""
    level: Literal["country", "province", "city", "district", "unknown"] = "unknown"


class SubjectIntent(BaseModel):
    """分析主体。"""
    type: EntityType = "unknown"
    category: str = ""


class MapRequestIntent(BaseModel):
    """GIS 制图请求的结构化意图契约（typed / serializable / deterministic）。"""
    # hint 合并用 setattr 覆写 typed 字段——validate_assignment 保证 LLM hint
    # 的越词汇表值（如未知 task）在校验边界被拒，而非静默进入 typed intent。
    model_config = ConfigDict(validate_assignment=True)

    query: str = ""
    scope: ScopeIntent = Field(default_factory=ScopeIntent)
    subject: SubjectIntent = Field(default_factory=SubjectIntent)
    entity_type: EntityType = "unknown"
    geometry_expectation: GeometryExpectation = "unknown"
    task: TaskType = "distribution_overview"
    measure: str = ""                    # count / density / area / length / ratio …
    group_by: str = ""                   # district / category / grid …
    time: str = ""
    comparison: str = ""
    analysis_intents: List[AnalysisIntent] = []
    cartography_intents: List[CartographyIntent] = []
    output_intents: List[OutputIntent] = ["map"]
    export_intents: List[ExportFormat] = []
    report_product: bool = False         # 「用于报告」→ 版面化成果
    confidence: float = Field(0.0, ge=0.0, le=1.0)
    assumptions: List[str] = []
    matched_rules: List[str] = []        # resolver 命中规则（可审计、可进 evidence）
    hint_applied: List[str] = []         # LLM hint 合并记录


# ─── 确定性规则（顺序 = 特异性，先命中先停） ─────────────────────────────

# 常见城市（含无「市」后缀的口语称呼）——scope 识别的第一优先级
_KNOWN_CITIES = (
    "成都", "北京", "上海", "广州", "深圳", "杭州", "武汉", "西安", "重庆",
    "天津", "南京", "苏州", "长沙", "郑州", "青岛", "大连", "厦门", "昆明",
    "拉萨", "乌鲁木齐", "哈尔滨", "沈阳", "长春", "兰州", "西宁", "银川",
    "南宁", "海口", "贵阳", "南昌", "合肥", "福州", "济南", "太原", "石家庄",
    "呼和浩特", "成都市", "北京市",
)

_CITY_RE = re.compile(
    r"(?P<name>[\u4e00-\u9fa5]{2,8}?(?:省|自治区))?"
    r"(?P<city>[\u4e00-\u9fa5]{2,6}?市)"
)
_DISTRICT_RE = re.compile(r"([\u4e00-\u9fa5]{2,6}(?:区|县|旗))")
# 「各/每个 + 区县」等分组表述不是 scope，是 group_by 信号
_GROUPBY_RE = re.compile(r"(各|每个?|按)(?:个)?(?:区|县|市|街道|乡镇|镇|村)", re.I)

_POINT_SUBJECTS = (
    "小学", "中学", "大学", "学校", "医院", "诊所", "药店", "银行", "超市",
    "餐厅", "咖啡馆", "加油站", "充电站", "公园", "景点", "地铁站", "公交站",
    "poi", "设施", "站点", "楼宇", "酒店", "商场", "图书馆", "体育馆",
)
_POLYGON_SUBJECTS = (
    "边界", "行政区划", "行政区", "区划", "地块", "土地利用", "规划范围", "流域",
)
_LINE_SUBJECTS = ("道路", "路网", "河流", "水系", "轨道", "管线", "航线")
_RASTER_SUBJECTS = ("遥感", "影像", "dem", "高程", "地形", "植被指数", "ndvi", "气温", "降水", "栅格")

# 任务规则：按特异性排序（先命中先停）。每条 = (rule_id, 正则, task)
_TASK_RULES: List[tuple] = [
    ("analytical_density_per_area",
     re.compile(r"每(?:平方|平方千米|平方公里|km|公里)[^，。?？]*密度|"
                r"密度[（(]?每|单位面积[^，。?？]*密度|density\s+per", re.I),
     "analytical_density"),
    ("administrative_statistic",
     re.compile(r"(各|每个|按?分?)(?:个)?(?:区|县|市|街道|乡镇|镇|村|州|省)[^，。?？]*"
                r"(数量|多少|几|统计|计数|汇总|排名|最多|最少)|"
                r"(数量|统计|汇总)按?(?:行政)?(?:区|县|市|街道|划分)", re.I),
     "administrative_statistic"),
    ("concentration_hotspot",
     re.compile(r"(哪里|哪儿|何处|哪个地方|哪片)[^，。?？]*(最集中|最密|最热门|聚集|扎堆)|"
                r"(最集中|热点|高发区|聚集区|核心区在哪)", re.I),
     "concentration_analysis"),
    ("accessibility_service_area",
     # #779: 「服务覆盖盲区/缺口/欠覆盖」是可达性-覆盖语义（教育/设施规划
     # 的核心问法），不是分布概览 —— 盲区/缺口/未覆盖/覆盖空白/欠覆盖 与
     # 可达性/服务区/覆盖范围 同族。
     re.compile(r"(可达性|等时圈|服务区|覆盖范围|盲区|缺口|未覆盖|覆盖空白|欠覆盖|"
                r"通勤时间|车程[^，。?？]*内|步行[^，。?？]*分钟内)", re.I),
     "accessibility_analysis"),
    ("proximity_buffer",
     re.compile(r"(\d+\s*(?:m|米|km|公里|千米)[^，。?？]*(内|之内|范围内|周边|附近)|"
                r"(周边|附近|旁边|范围内)[^，。?？]*的)", re.I),
     "proximity_analysis"),
    ("change_detection",
     re.compile(r"(变化|变迁|对比[^，。?？]*(年|期)|历年对比|两期)", re.I),
     "change_detection"),
    ("categorical_breakdown",
     re.compile(r"(各类|各类型|分类别|按(?:类型|类别|种类)|类别分布|类型分布|占比|构成)", re.I),
     "categorical_distribution"),
    ("simple_view",
     re.compile(r"^(给我看|看看|显示|查看|瞄一眼|瞧瞧|show\s+me)", re.I),
     "simple_view"),
    # #781: 栅格主体（遥感/影像/DEM/NDVI/气温/降水…）在无更强任务规则命中
    # 时归入 raster_distribution —— 此前栅格查询落入 distribution_overview
    # 兜底，raster_distribution recipe 从确定性路径不可达。规则序保证
    # 变化检测等更强语义先命中。
    ("raster_subject_thematic",
     re.compile("(" + "|".join(_RASTER_SUBJECTS) + ")", re.I),
     "raster_distribution"),
    ("distribution_generic",
     re.compile(r"(分布|散布|散落|态势|格局|疏密)", re.I),
     "distribution_overview"),
]

_EXPORT_RE = re.compile(r"(导出|下载|出图|存成|保存为|export)", re.I)
_REPORT_RE = re.compile(r"(用于|做|做一份|生成|制作)[^，。?？]*(报告|汇报|论文|汇报材料|简报|插图|印刷|打印)|"
                        r"(报告|论文|简报)[^，。?？]*(用|插图|配图)", re.I)
_DENSITY_WORD_RE = re.compile(r"密度", re.I)
_MEASURE_COUNT_RE = re.compile(r"(数量|多少|几|个数|计数)", re.I)
# 显式制图形态信号（模型库 aggregate_grid / proportional_symbol 的入口词）
_GRID_AGG_RE = re.compile(r"(格网|网格|hexbin|六边形|蜂窝|h3)", re.I)
_BUBBLE_RE = re.compile(r"(气泡图|气泡|比例符号|按[^，。?？]{0,6}(大小|规模)(表示|展示)?|圆(的)?大小)", re.I)


def _match_scope(query: str) -> ScopeIntent:
    # 1) 显式「市」后缀
    m = _CITY_RE.search(query)
    if m and m.group("city"):
        return ScopeIntent(name=m.group("city"), level="city")
    # 2) 已知城市名（含无后缀口语，如「成都小学」）——取最长命中避免子串歧义
    hit = ""
    for city in _KNOWN_CITIES:
        if city in query and len(city) > len(hit):
            hit = city
    if hit:
        return ScopeIntent(name=hit, level="city")
    # 3) 区县（排除「各区/每个区」分组表述 —— 那是 group_by 不是 scope）。
    #    对捕获文本本身做分组词检查：非知名城市的「绵阳各区」会把分组后缀
    #    误捕为 district（绵阳区），检查须覆盖捕获串而非仅其前缀。
    m = _DISTRICT_RE.search(query)
    if m:
        captured = m.group(1)
        prefix = query[: m.start() + 2]
        if not _GROUPBY_RE.search(prefix) and not _GROUPBY_RE.search(captured):
            return ScopeIntent(name=captured, level="district")
    return ScopeIntent()


def _last_matched_token(lowered: str, tokens: tuple) -> Optional[str]:
    """#785: 取查询中**最靠后**命中的主体词（「…的X」的中心语）。

    此前按词汇表顺序取第一个命中词 —— 「找出距离学校500米以内的地铁站」
    会因为「学校」排在表前而把主体误判为学校。中文邻近问句的中心语
    （被分析的标的）几乎总是最后一个主体词。
    """
    best: Optional[str] = None
    best_pos = -1
    for token in tokens:
        pos = lowered.rfind(token)
        if pos >= 0 and pos > best_pos:
            best, best_pos = token, pos
    return best


def _match_subject(query: str) -> SubjectIntent:
    lowered = query.lower()
    token = _last_matched_token(lowered, _POINT_SUBJECTS)
    if token:
        return SubjectIntent(type="poi", category=token)
    token = _last_matched_token(lowered, _RASTER_SUBJECTS)
    if token:
        return SubjectIntent(type="raster", category=token)
    token = _last_matched_token(lowered, _POLYGON_SUBJECTS)
    if token:
        return SubjectIntent(type="boundary", category=token)
    token = _last_matched_token(lowered, _LINE_SUBJECTS)
    if token:
        return SubjectIntent(type="network", category=token)
    return SubjectIntent()


def _entity_geometry(subject: SubjectIntent, task: str) -> GeometryExpectation:
    if subject.type == "poi":
        return "point"
    if subject.type == "network":
        return "line"
    if subject.type == "boundary":
        return "polygon"
    if subject.type == "raster":
        return "raster"
    if task == "administrative_statistic":
        return "polygon"
    return "unknown"


def _task_specific_intents(task: str, query: str) -> tuple:
    """(analysis_intents, cartography_intents, output_intents, measure, group_by)"""
    if task == "distribution_overview":
        return (
            ["spatial_distribution", "administrative_summary", "profile"],
            ["density_overview", "point_overlay", "administrative_choropleth"],
            ["map", "statistics", "summary"],
            "count", "district",
        )
    if task == "simple_view":
        return (
            ["profile"],
            ["simple_point_map"],
            ["map", "summary"],
            "count", "",
        )
    if task == "administrative_statistic":
        return (
            ["administrative_aggregation", "administrative_summary", "profile"],
            ["administrative_choropleth", "point_overlay"],
            ["map", "statistics", "table", "summary"],
            "count", "district",
        )
    if task == "analytical_density":
        return (
            ["analytical_density", "administrative_aggregation", "profile"],
            ["administrative_choropleth"],
            ["map", "statistics", "table", "summary"],
            "density", "district",
        )
    if task == "concentration_analysis":
        return (
            ["kde_density", "hotspot", "administrative_summary"],
            ["density_overview", "hotspot_overlay", "point_overlay"],
            ["map", "statistics", "summary"],
            "density", "",
        )
    if task == "categorical_distribution":
        return (
            ["category_breakdown", "profile"],
            ["categorical_thematic", "point_overlay"],
            ["map", "statistics", "chart", "summary"],
            "count", "category",
        )
    if task == "proximity_analysis":
        return (
            ["proximity_buffer", "profile"],
            ["proximity_overlay", "point_overlay"],
            ["map", "statistics", "summary"],
            "count", "",
        )
    if task == "accessibility_analysis":
        return (
            ["service_area", "profile"],
            ["proximity_overlay", "point_overlay"],
            ["map", "statistics", "summary"],
            "area", "",
        )
    if task == "raster_distribution":
        return (
            ["profile"],
            ["raster_surface"],
            ["map", "statistics", "summary"],
            "area", "",
        )
    if task == "change_detection":
        return (
            ["profile"],
            ["raster_surface"],
            ["map", "statistics", "summary"],
            "area", "",
        )
    return (["profile"], ["point_overlay"], ["map"], "count", "")


def _apply_form_signals(
    query: str,
    analysis_intents: List[str],
    cartography_intents: List[str],
) -> tuple:
    """显式制图形态信号（格网/气泡）：加法注入 cartography/analysis intents。

    返回 (signal, analysis_intents, cartography_intents)；signal 为命中的
    形态信号 id（未命中为 ""）。#780: resolve 与 hint 合并后的派生意图
    重算共用同一份逻辑 —— 任务被 hint 纠偏后显式形态词不得丢失。
    """
    if _GRID_AGG_RE.search(query):
        cartography_intents = list(dict.fromkeys(
            [*cartography_intents, "aggregate_grid"]))
        if "grid_binning" not in analysis_intents:
            analysis_intents.append("grid_binning")
        return "aggregate_grid", analysis_intents, cartography_intents
    if _BUBBLE_RE.search(query):
        cartography_intents = list(dict.fromkeys(
            [*cartography_intents, "proportional_symbol"]))
        return "proportional_symbol", analysis_intents, cartography_intents
    return "", analysis_intents, cartography_intents


def resolve_map_request_intent(query: str) -> MapRequestIntent:
    """确定性解析自然语言 GIS 请求为 typed intent。

    规则按特异性排序、先命中先停；每次命中记录进 ``matched_rules``
    （可审计、可作 Harness evidence）。无命中时返回低置信度默认
    distribution_overview —— 「分布」是 GIS 请求的最大公约数兜底。
    """
    query = (query or "").strip()
    assumptions: List[str] = []
    matched: List[str] = []

    task: TaskType = "distribution_overview"
    for rule_id, pattern, rule_task in _TASK_RULES:
        if pattern.search(query):
            task = rule_task  # type: ignore[assignment]
            matched.append(rule_id)
            break
    else:
        matched.append("fallback_distribution_default")
        assumptions.append("未命中任务规则，按通用分布概览兜底")

    scope = _match_scope(query)
    if scope.name:
        matched.append(f"scope:{scope.level}:{scope.name}")
    else:
        assumptions.append("未识别地理范围，按全局/当前视口处理")

    subject = _match_subject(query)
    if subject.type != "unknown":
        matched.append(f"subject:{subject.type}:{subject.category}")
    else:
        assumptions.append("未识别分析主体类型")

    analysis_intents, cartography_intents, output_intents, measure, group_by = (
        _task_specific_intents(task, query)
    )

    # 密度词 + 非定量任务 → 保留视觉密度但标注假设（定量密度必须走
    # analytical_density 任务，规则序保证「每平方公里」优先命中）。
    if _DENSITY_WORD_RE.search(query) and task not in ("analytical_density",):
        assumptions.append("请求含「密度」但非定量表述，按视觉密度处理")

    # 显式制图形态信号：加法注入 cartography_intents，不改变任务判定
    # （任务规则仍是权威；形态词只决定同一任务内的表达选型）。
    signal, analysis_intents, cartography_intents = _apply_form_signals(
        query, analysis_intents, cartography_intents)
    if signal:
        matched.append(f"cartography:{signal}")

    if _MEASURE_COUNT_RE.search(query) and not measure:
        measure = "count"

    report_product = bool(_REPORT_RE.search(query))
    if report_product:
        matched.append("report_product")
        output_intents = list(dict.fromkeys(output_intents + ["export", "summary"]))
    if _EXPORT_RE.search(query):
        matched.append("export_requested")

    confidence = 0.5
    if subject.type != "unknown":
        confidence += 0.2
    if scope.name:
        confidence += 0.15
    if matched and matched[0] != "fallback_distribution_default":
        confidence += 0.15
    if task == "simple_view":
        confidence = min(confidence, 0.7)

    return MapRequestIntent(
        query=query,
        scope=scope,
        subject=subject,
        entity_type=subject.type,
        geometry_expectation=_entity_geometry(subject, task),
        task=task,
        measure=measure,
        group_by=group_by,
        analysis_intents=analysis_intents,
        cartography_intents=cartography_intents,
        output_intents=output_intents,
        export_intents=["png", "pdf"] if report_product else [],
        report_product=report_product,
        confidence=round(min(confidence, 1.0), 2),
        assumptions=assumptions,
        matched_rules=matched,
    )


# LLM hint 可覆盖的字段（显式白名单；task 覆盖必须给出理由并记录）。
_HINT_OVERRIDABLE = {
    "task", "measure", "group_by", "time", "comparison",
    "geometry_expectation", "entity_type",
}


# hint 不可降级的确定性任务（语义护栏，与工具描述对齐）：
# - analytical_density：定量密度不可降级为视觉热力（原有护栏）；
# - administrative_statistic：『各区数量』首选行政聚合+choropleth 而非热力图
#   （#780：此前只有 density 方向受保护，statistic 可被单个 hint 静默降级
#   为热力产品族）。
_HINT_PROTECTED_TASKS = ("analytical_density", "administrative_statistic")


def merge_intent_hints(
    base: MapRequestIntent,
    hints: Optional[Dict[str, Any]],
) -> MapRequestIntent:
    """把 agent（LLM）的语义提示合并进确定性 intent。

    LLM 负责语义理解（scope/subject/任务纠偏），确定性规则负责护栏：
    ``analytical_density`` / ``administrative_statistic`` 任务不允许被 hint
    降级为视觉任务 —— 视觉热力不是定量证据，热力也不是「各区数量」的
    首选表达。任务 hint 生效后派生意图（analysis/cartography/measure/
    group_by）按新任务重算（#780：不得留着旧任务的派生集污染 evidence）。
    所有覆盖显式记录进 ``hint_applied``；单个非法 hint 值只拒绝该键，
    不炸掉整个调用（#780）。
    """
    merged = base.model_copy(deep=True)
    if not isinstance(hints, dict):
        return merged
    for key, value in hints.items():
        if key not in _HINT_OVERRIDABLE or value in (None, "", []):
            continue
        try:
            if key == "task":
                if base.task in _HINT_PROTECTED_TASKS and value != base.task:
                    merged.hint_applied.append(
                        f"task:{value} rejected — {base.task} 不可降级为视觉任务"
                    )
                    continue
                if value != base.task:
                    merged.task = value
                    merged.hint_applied.append(f"task:{base.task}->{value}")
                    # 派生意图按新任务重算（保留显式形态信号），否则
                    # task 与 cartography_intents/measure 各说各话。
                    analysis, carto, _outputs, measure, group_by = (
                        _task_specific_intents(value, merged.query)
                    )
                    _signal, analysis, carto = _apply_form_signals(
                        merged.query, analysis, carto)
                    merged.analysis_intents = analysis
                    merged.cartography_intents = carto
                    merged.measure = measure
                    merged.group_by = group_by
            elif getattr(merged, key) != value:
                setattr(merged, key, value)
                merged.hint_applied.append(f"{key}->{value}")
        except ValidationError:
            # #780: validate_assignment 会把越词汇表的 hint 值变成异常 ——
            # 拒绝该键、保留确定性基线，继续处理其余 hint。
            merged.hint_applied.append(f"{key}:{value} rejected (invalid value)")
            continue
    if merged.hint_applied:
        merged.confidence = round(min(1.0, merged.confidence + 0.1), 2)
    return merged


__all__ = [
    "MapRequestIntent",
    "ScopeIntent",
    "SubjectIntent",
    "resolve_map_request_intent",
    "merge_intent_hints",
]
