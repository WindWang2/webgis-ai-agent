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

from pydantic import BaseModel, Field

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
     re.compile(r"(可达性|等时圈|服务区|覆盖范围|通勤时间|车程[^，。?？]*内|步行[^，。?？]*分钟内)", re.I),
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
    ("distribution_generic",
     re.compile(r"(分布|散布|散落|态势|格局|疏密)", re.I),
     "distribution_overview"),
]

_EXPORT_RE = re.compile(r"(导出|下载|出图|存成|保存为|export)", re.I)
_REPORT_RE = re.compile(r"(用于|做|做一份|生成|制作)[^，。?？]*(报告|汇报|论文|汇报材料|简报|插图|印刷|打印)|"
                        r"(报告|论文|简报)[^，。?？]*(用|插图|配图)", re.I)
_DENSITY_WORD_RE = re.compile(r"密度", re.I)
_MEASURE_COUNT_RE = re.compile(r"(数量|多少|几|个数|计数)", re.I)


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
    # 3) 区县（排除「各区/每个区」分组表述 —— 那是 group_by 不是 scope）
    m = _DISTRICT_RE.search(query)
    if m and not _GROUPBY_RE.search(query[:m.start() + 2]):
        return ScopeIntent(name=m.group(1), level="district")
    return ScopeIntent()


def _match_subject(query: str) -> SubjectIntent:
    lowered = query.lower()
    for token in _POINT_SUBJECTS:
        if token in lowered:
            return SubjectIntent(type="poi", category=token)
    for token in _RASTER_SUBJECTS:
        if token in lowered:
            return SubjectIntent(type="raster", category=token)
    for token in _POLYGON_SUBJECTS:
        if token in lowered:
            return SubjectIntent(type="boundary", category=token)
    for token in _LINE_SUBJECTS:
        if token in lowered:
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


def merge_intent_hints(
    base: MapRequestIntent,
    hints: Optional[Dict[str, Any]],
) -> MapRequestIntent:
    """把 agent（LLM）的语义提示合并进确定性 intent。

    LLM 负责语义理解（scope/subject/任务纠偏），确定性规则负责护栏：
    ``analytical_density`` 任务不允许被 hint 降级为非定量任务 —— 视觉
    热力不是定量证据。所有覆盖显式记录进 ``hint_applied``。
    """
    merged = base.model_copy(deep=True)
    if not isinstance(hints, dict):
        return merged
    for key, value in hints.items():
        if key not in _HINT_OVERRIDABLE or value in (None, "", []):
            continue
        if key == "task":
            if base.task == "analytical_density" and value != "analytical_density":
                merged.hint_applied.append(
                    f"task:{value} rejected — analytical_density 不可降级为视觉任务"
                )
                continue
            if value != base.task:
                merged.task = value
                merged.hint_applied.append(f"task:{base.task}->{value}")
        elif getattr(merged, key) != value:
            setattr(merged, key, value)
            merged.hint_applied.append(f"{key}->{value}")
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
