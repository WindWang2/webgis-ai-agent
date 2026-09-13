"""Typed GIS intent model + deterministic resolver.

「分布」不再等价于单个工具调用：本模块把自然语言 GIS 请求解析为
typed / validated / serializable 的 :class:`MapRequestIntent`，供
Recipe 选择、产品规划与 Harness evidence 消费。

设计约束：

- deterministic —— 同一输入永远同一输出（:func:`resolve_map_request_intent`
  是纯函数；LLM 双轨只在显式的 :func:`resolve_intent_adaptive` 入口发生）；
- 非 prompt-only —— 双语规则快路径（特异性分级裁决）可单测、可回放；
- LLM 可补充 —— agent 的语义理解通过 :func:`merge_intent_hints` /
  :func:`resolve_intent_adaptive` 合并，合并显式、有记录、可审计；
- 证据可外泄 —— 每次解析产出 ``intent_evidence``（槽位/规则/本体对齐/
  置信度分量/降级原因），低置信场景由 :mod:`.clarification` 反问而非
  静默兜底（所有 fallback 携带 ``FallbackDecision``）。

规则表、双语词表、实体解析、置信度模型与 observability 的实现见
:mod:`app.services.gis_harness.intent_semantic`（ADR-0150）；
澄清状态机见 :mod:`app.services.gis_harness.clarification`。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Literal, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.services.gis_harness import intent_semantic as semantic
from app.services.gis_harness.clarification import (
    ClarificationPolicy,
    ClarificationRequest,
    FallbackDecision,
    make_fallback_decision,
)

logger = logging.getLogger(__name__)

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
    "vegetation_index",          # 「NDVI/植被指数」→ 光谱指数计算（ADR-0092）
    "mobility_flow",             # 「通勤流/出行流/OD」→ 流动分析（ADR-0092）
    # ── Semantic V2（ADR-0098）：决策族产品语义一等公民，不再落分布兜底 ──
    "spatial_equity",            # 「公平/均衡/是否合理」→ 分母归一化公平评价
    "site_selection",            # 「选址/最优位置」→ 多准则候选评价（MCDA）
    "suitability_assessment",    # 「适宜性/适建区」→ 因子标准化加权适宜面
    "risk_exposure",             # 「风险/暴露/危险源」→ 影响区×受体暴露评价
    # ── Workflow V2（Goal C / ADR-0101）：专业领域任务族（纯加法）──────
    "terrain_analysis",          # 「坡度/坡向/山体阴影/视域」→ 地形衍生产品
    "watershed_analysis",        # 「流域/汇水/水文」→ DEM 水文产品
    "spatial_autocorrelation",   # 「莫兰/空间自相关」→ global/local 统计产品
    "temporal_trend",            # 「趋势/逐年/时序」→ 多期趋势产品（非两期对比）
    "sar_analysis",              # 「SAR/InSAR/形变」→ 雷达产品族
    "network_route",             # 「最短路径/最近设施/路径规划」→ 网络路径产品
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
    # ── Semantic V2（ADR-0098）：决策族分析意图 ──────────────────────
    "equity_assessment",         # 公平性评价（分母归一化）
    "mcda_evaluation",           # 多准则决策评价（WSM/TOPSIS）
    "overlay_weighted",          # 因子标准化加权叠加（适宜性）
    "exposure_assessment",       # 影响区×受体暴露评价
    # ── Workflow V2（Goal C / ADR-0101）：专业领域分析意图（纯加法）────
    "terrain_derivatives",       # 地形衍生（坡度/坡向/山体阴影/视域）
    "hydrology_analysis",        # 水文分析（填洼/流向/汇流累积/流域划分）
    "autocorrelation_analysis",  # 空间自相关（global/local Moran/Geary）
    "trend_analysis",            # 时序趋势（多期斜率/显著性）
    "sar_interpretation",        # SAR 语义解译（极化/定标义务）
    "route_analysis",            # 网络路径（最短路径/最近设施）
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
    "isoline_contour",           # 等值线/等高线（Workflow V2：地形衍生产品族）
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
    """GIS 制图请求的结构化意图契约（typed / serializable / deterministic）。

    AC-01（ADR-0150）新增字段（只加不改，02/03/04 线兼容）：
    ``lang`` / ``slots`` / ``ontology_link`` / ``intent_evidence`` /
    ``degraded_reason`` / ``fallback_decision`` / ``clarification``。
    """
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
    # ── AC-01（ADR-0150）additive 字段 ─────────────────────────────────
    lang: str = "zh"                     # 查询语言（zh/en，槽位抽取语言面）
    slots: Optional[Dict[str, Any]] = None       # IntentSlots 序列化
    ontology_link: Optional[Dict[str, Any]] = None  # gis_ontology 对齐
    intent_evidence: Optional[Dict[str, Any]] = None  # P6 证据包
    degraded_reason: str = ""            # 降级原因（空 = 无降级）
    fallback_decision: Optional[Dict[str, Any]] = None  # 显式兜底（零静默）
    clarification: Optional[Dict[str, Any]] = None  # 澄清请求（P4）


# ─── 兼容再导出（tools.py 从 intent 导入这两个符号） ─────────────────────


def _match_subject(query: str) -> SubjectIntent:
    subject, _surface = semantic.match_subject(query)
    return SubjectIntent(type=subject.type, category=subject.category) \
        if subject.type != "unknown" else SubjectIntent()


def _match_scope(query: str) -> ScopeIntent:
    scope_result, _trace = semantic.match_scope(query)
    if not scope_result.name:
        return ScopeIntent()
    return ScopeIntent(name=scope_result.name,
                       level=scope_result.level)  # type: ignore[arg-type]


def _entity_geometry(subject: Any, task: str) -> GeometryExpectation:
    subject_type = subject if isinstance(subject, str) \
        else getattr(subject, "type", "unknown")
    return semantic.entity_geometry(subject_type, task)  # type: ignore[return-value]


def _v1_served_tasks_cached() -> set:
    """V1 seed 服务的任务族（模块级缓存；registry 重建时自动失效）。

    供本体任务升级的目标族守卫使用。惰性 import 避免 intent ↔ recipes
    模块环；registry 不可用时返回空集合 = 升级被完全抑制（保守缺省）。
    """
    global _V1_SERVED_TASKS_CACHE, _V1_SERVED_TASKS_REG_GEN
    from app.services.gis_harness import recipes as _recipes_mod

    registry = _recipes_mod.get_recipe_registry()
    gen = registry.content_fingerprint()
    if _V1_SERVED_TASKS_REG_GEN != gen or _V1_SERVED_TASKS_CACHE is None:
        _V1_SERVED_TASKS_CACHE = registry.v1_served_tasks
        _V1_SERVED_TASKS_REG_GEN = gen
    return _V1_SERVED_TASKS_CACHE


_V1_SERVED_TASKS_CACHE: Optional[set] = None
_V1_SERVED_TASKS_REG_GEN: str = ""


def _matched_once(matched: List[str], entry: str) -> None:
    """审计去重追加：本体升级重放会以同一 query 二次调用
    ``_base_intents_for``，相同信号不得在 matched_rules 重复记账
    （保持首次出现位，audit 面一条信号只记一行）。"""
    if entry not in matched:
        matched.append(entry)


def _base_intents_for(task: str, query: str, matched: List[str],
                      report_product: bool,
                      apply_export_output: bool = False) -> Tuple:
    """任务 → 派生意图 + 显式形态/图表/报告信号。

    ``matched`` 追加顺序与 legacy 一致：output:chart → cartography:* →
    report_product → export_requested（去重追加，见 :func:`_matched_once`）。
    ``apply_export_output`` 仅在本体升级重放时为真（legacy R5：升级重算后
    重放报告/导出信号）。
    """
    derived = semantic.derived_intents_for(task)
    analysis = list(derived.analysis)
    cartography = list(derived.cartography)
    output_intents = list(derived.output)
    measure, group_by = derived.measure, derived.group_by

    if semantic._CHART_WORD_RE.search(query) and "chart" not in output_intents:
        output_intents = list(dict.fromkeys(output_intents + ["chart"]))
        _matched_once(matched, "output:chart")

    signal, analysis, cartography = semantic.apply_form_signals(
        query, analysis, cartography)
    if signal:
        _matched_once(matched, f"cartography:{signal}")

    if semantic._MEASURE_COUNT_RE.search(query) and not measure:
        measure = "count"

    if report_product:
        _matched_once(matched, "report_product")
        output_intents = list(dict.fromkeys(output_intents + ["export", "summary"]))
    if semantic._EXPORT_RE.search(query):
        _matched_once(matched, "export_requested")
        if apply_export_output:
            output_intents = list(dict.fromkeys(output_intents + ["export"]))
    return analysis, cartography, output_intents, measure, group_by


def resolve_map_request_intent(
    query: str,
    *,
    session_consistency: float = 0.5,
    precomputed_slots: Optional[semantic.IntentSlots] = None,
    entity_service: Any = None,
    record_metrics: bool = True,
) -> MapRequestIntent:
    """确定性解析自然语言 GIS 请求为 typed intent。

    规则快路径（特异性分级裁决）+ 双语槽位 + 证据加权置信度 + 证据包。
    本函数是**纯函数**（不调用 LLM、不产生网络 I/O）；LLM 双轨与澄清
    见 :func:`resolve_intent_adaptive`。``record_metrics=False`` 供
    adaptive 入口复用（由入口统一记录最终 outcome，避免双重计数）。
    每次命中的规则、范围、主体与降级原因都记录进 ``matched_rules`` /
    ``intent_evidence``（可审计）。
    """
    query = (query or "").strip()
    assumptions: List[str] = []
    matched: List[str] = []
    lang = semantic.detect_language(query)

    task: TaskType = "distribution_overview"
    decision = semantic.decide_task(query)
    if decision.fallback:
        matched.append("fallback_distribution_default")
        assumptions.append("未命中任务规则，按通用分布概览兜底")
    else:
        task = decision.task  # type: ignore[assignment]
        matched.extend(decision.matched_rules)

    slots = precomputed_slots or semantic.extract_slots(query)
    if slots.degraded_reason:
        semantic.record_degraded(slots.degraded_reason)

    scope_result, scope_trace = semantic.match_scope(
        query, entity_service=entity_service)
    scope = ScopeIntent(
        name=scope_result.name,
        level=scope_result.level,  # type: ignore[arg-type]
    )
    if scope.name:
        matched.append(f"scope:{scope.level}:{scope.name}")
    else:
        assumptions.append("未识别地理范围，按全局/当前视口处理")
    scope_degraded = scope_trace.get("degraded_reason") or ""

    subject_like, _surface = semantic.match_subject(query)
    subject = SubjectIntent(type=subject_like.type,  # type: ignore[arg-type]
                            category=subject_like.category)
    if subject.type != "unknown":
        matched.append(f"subject:{subject.type}:{subject.category}")
    else:
        assumptions.append("未识别分析主体类型")

    report_product = bool(semantic._REPORT_RE.search(query))

    analysis_intents, cartography_intents, output_intents, measure, group_by = (
        _base_intents_for(task, query, matched, report_product))

    if semantic._DENSITY_WORD_RE.search(query) \
            and task not in ("analytical_density",):
        assumptions.append("请求含「密度」但非定量表述，按视觉密度处理")

    export_intents = ["png", "pdf"] if report_product else []

    # V3（GIS Task Ontology）：保守本体任务升级（源任务为通用族 + 命中
    # 本体专业关键词 + 目标族无 V1 seed 保护）。升级记录可审计（legacy 语义）。
    escalation: Optional[Dict[str, Any]] = None
    if task in ("distribution_overview", "simple_view"):
        try:
            from app.services.gis_harness.gis_ontology import escalation_target

            family, onto_task = escalation_target(
                type("_EscalationProbe", (), {
                    "task": task, "query": query,
                })(),
                v1_served_tasks=_v1_served_tasks_cached(),
            )
            if family and onto_task:
                task = family  # type: ignore[assignment]
                # 升级成功即"获救"：移除 fallback 头标记（AC-01——下游以
                # matched_rules[0] 判定确定性合成；本体升级后的任务不再是
                # 兜底），审计痕迹由 escalation 标记与 assumptions 承载。
                if matched and matched[0] == "fallback_distribution_default":
                    matched.pop(0)
                matched.append(f"ontology_escalation:{onto_task}->{family}")
                assumptions.append(
                    f"query 命中本体任务 {onto_task} 的专业关键词："
                    f"任务族升级为 {family}")
                escalation = {"from": onto_task, "to": family}
                analysis_intents, cartography_intents, output_intents, \
                    measure, group_by = _base_intents_for(
                        task, query, matched, report_product,
                        apply_export_output=True)
        except Exception:  # noqa: BLE001 — 升级失败保守回退，但必须留痕可观测
            logger.warning(
                "ontology task escalation failed (kept task=%s) query=%r",
                task, query[:80], exc_info=True,
            )

    confidence, components = semantic.compute_confidence(
        decision, slots, scope_known=bool(scope.name),
        session_consistency=session_consistency)
    if task == "simple_view":
        # legacy 护栏：轻量查看不装作高置信（test_intent Case H 锁定 ≤0.75）
        confidence = min(confidence, 0.7)

    degraded_reason = scope_degraded or slots.degraded_reason
    fallback_decision: Optional[Dict[str, Any]] = None
    if decision.fallback and escalation is None:
        fb = make_fallback_decision(
            from_task="", to_task="distribution_overview",
            reason_code="task_rule_miss",
            evidence={
                "query_head": query[:48],
                "task_candidates": [
                    {"rule": rid, "task": t, "specificity": spec, "span": span}
                    for rid, t, spec, span in decision.candidates[:4]
                ],
            },
        )
        fallback_decision = fb.model_dump()
        assumptions.append(fb.note())

    ontology_link = semantic.ontology_link_for(task)

    intent = MapRequestIntent(
        query=query,
        scope=scope,
        subject=subject,
        entity_type=subject.type,
        geometry_expectation=_entity_geometry(subject.type, task),
        task=task,
        measure=measure,
        group_by=group_by,
        analysis_intents=analysis_intents,
        cartography_intents=cartography_intents,
        output_intents=output_intents,
        export_intents=export_intents,
        report_product=report_product,
        confidence=confidence,
        assumptions=assumptions,
        matched_rules=matched,
        lang=lang,
        slots=slots.model_dump(),
        ontology_link=ontology_link,
        degraded_reason=degraded_reason,
        fallback_decision=fallback_decision,
        intent_evidence={
            "lang": lang,
            "slots": slots.model_dump(),
            "llm_used": False,
            "llm_requested": False,
            "degraded_reason": degraded_reason,
            "confidence_components": components,
            "confidence_model": "evidence_weighted_v1",
            "scope_source": scope_trace.get("source", "none"),
            "task_candidates": [
                {"rule": rid, "task": t, "specificity": spec, "span": span}
                for rid, t, spec, span in decision.candidates[:4]
            ],
            "ontology_escalation": escalation,
        },
    )
    if record_metrics:
        semantic.record_resolve(
            lang, "rule_fallback" if decision.fallback else "rule")
    return intent


# LLM hint 可覆盖的字段（显式白名单；task 覆盖必须给出理由并记录）。
_HINT_OVERRIDABLE = {
    "task", "measure", "group_by", "time", "comparison",
    "geometry_expectation", "entity_type",
}


# hint 不可降级的确定性任务（语义护栏，与工具描述对齐）：
# - analytical_density：定量密度不可降级为视觉热力（原有护栏）；
# - administrative_statistic：『各区数量』首选行政聚合+choropleth 而非热力图
#   （#780：此前只有 density 方向受保护，statistic 可被单个 hint 静默降级
#   为热力产品族）；
# - 决策族（ADR-0098）：公平/选址/适宜性/风险是不可降级的评价语义 ——
#   降级为视觉分布会把「评价问题」伪装成「看一眼」，正是 Semantic V2
#   要消灭的静默兜底。
# - Workflow V2（Goal C）：专业分析族是确定性规则推导的科学结论，LLM hint
#   只能纠偏更弱的判定，不得降级为视觉/概览任务。
_HINT_PROTECTED_TASKS = (
    "analytical_density",
    "administrative_statistic",
    "spatial_equity",
    "site_selection",
    "suitability_assessment",
    "risk_exposure",
    "terrain_analysis",
    "watershed_analysis",
    "spatial_autocorrelation",
    "temporal_trend",
    "sar_analysis",
    "network_route",
)


def merge_intent_hints(
    base: MapRequestIntent,
    hints: Optional[Dict[str, Any]],
) -> MapRequestIntent:
    """把 agent（LLM）的语义提示合并进确定性 intent。

    LLM 负责语义理解（scope/subject/任务纠偏），确定性规则负责护栏：
    受保护任务不允许被 hint 降级为视觉任务。任务 hint 生效后派生意图
    （analysis/cartography/measure/group_by）按新任务重算（#780）。所有
    覆盖显式记录进 ``hint_applied``；单个非法 hint 值只拒绝该键，不炸掉
    整个调用（#780）。
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
                    # task 与 cartography_intents/measure 各说各话
                    # （legacy #780/#834：重算用纯派生集，不掺图表/报告信号）。
                    derived = semantic.derived_intents_for(value)
                    analysis = list(derived.analysis)
                    carto = list(derived.cartography)
                    _signal, analysis, carto = semantic.apply_form_signals(
                        merged.query, analysis, carto)
                    merged.analysis_intents = analysis
                    merged.cartography_intents = carto
                    merged.measure = derived.measure
                    merged.group_by = derived.group_by
                    merged.output_intents = list(derived.output)
                    merged.geometry_expectation = _entity_geometry(
                        merged.subject, value)
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


# ─── 自适应入口（LLM 双轨 + 澄清；规则路径的严格超集） ───────────────────


def _task_type_valid(value: str) -> bool:
    import typing

    return value in typing.get_args(TaskType)


def resolve_intent_adaptive(
    query: str,
    *,
    use_llm: bool = True,
    asked_slots: Optional[set] = None,
    session_consistency: float = 0.5,
    policy: Optional[ClarificationPolicy] = None,
    entity_service: Any = None,
) -> Tuple[MapRequestIntent, Optional[ClarificationRequest]]:
    """自适应解析：规则快路径 ⊕ LLM 结构化槽位 ⊕ 澄清策略（P1/P4）。

    - LLM 不可用/失败 → 规则结果 + ``degraded_reason``，**永不抛错**；
    - 规则 fallback 且 LLM 给出合法 ``task_candidate`` → 采信 LLM 任务
      （经 :func:`merge_intent_hints` 审计通道）；
    - 低置信/关键槽位缺失/规则-语义冲突 → ``ClarificationRequest``
      （≤2 问，带默认推荐），序列化进 ``intent.clarification``。
    """
    query = (query or "").strip()
    lang = semantic.detect_language(query)
    rule_slots = semantic.extract_slots(query)
    llm_used = False
    slot_conflict = False
    if use_llm:
        llm_slots = semantic.extract_slots_with_llm(query)
        if llm_slots.degraded_reason:
            semantic.record_degraded(llm_slots.degraded_reason)
        llm_used = llm_slots.degraded_reason == ""
        slots, conflicts = semantic.merge_slots(rule_slots, llm_slots)
        slot_conflict = bool(conflicts)
    else:
        slots = rule_slots
        if not slots.degraded_reason:
            slots.degraded_reason = "llm_not_requested"

    intent = resolve_map_request_intent(
        query, session_consistency=session_consistency,
        precomputed_slots=slots, entity_service=entity_service,
        record_metrics=False)

    # 证据优先级：规则 fallback 时才允许 LLM 任务建议接管（decisions 口径）
    if llm_used and slots.task_candidate \
            and _task_type_valid(slots.task_candidate) \
            and intent.matched_rules \
            and intent.matched_rules[0] == "fallback_distribution_default" \
            and slots.task_candidate != intent.task:
        intent = merge_intent_hints(intent, {"task": slots.task_candidate})

    # 澄清（不静默 fallback）
    policy = policy or ClarificationPolicy(
        confidence_floor=_clarify_floor())
    task_candidates = [
        c.get("task") for c in (intent.intent_evidence or {}).get(
            "task_candidates", [])
        if c.get("task") not in ("distribution_overview",)
    ]
    request = policy.evaluate(
        intent, task_candidates=task_candidates,
        slot_conflict=slot_conflict, asked_slots=asked_slots or set())
    if request is not None:
        intent.clarification = request.model_dump()
        for question in request.questions:
            semantic.record_clarification(question.slot)

    evidence = dict(intent.intent_evidence or {})
    evidence["llm_used"] = llm_used
    evidence["llm_requested"] = bool(use_llm)
    evidence["slot_conflicts"] = conflicts if use_llm else []
    evidence["clarification"] = intent.clarification
    intent.intent_evidence = evidence

    outcome = "semantic"
    if intent.matched_rules and \
            intent.matched_rules[0] == "fallback_distribution_default":
        outcome = "semantic_fallback"
    semantic.record_resolve(lang, outcome)
    return intent, request


def _clarify_floor() -> float:
    try:
        from app.core.config import settings

        return settings.INTENT_CLARIFY_CONFIDENCE_FLOOR
    except Exception:  # noqa: BLE001 — 配置缺失用保守默认
        return 0.55


__all__ = [
    "MapRequestIntent",
    "ScopeIntent",
    "SubjectIntent",
    "TaskType",
    "resolve_map_request_intent",
    "resolve_intent_adaptive",
    "merge_intent_hints",
    "FallbackDecision",
    "ClarificationPolicy",
]
