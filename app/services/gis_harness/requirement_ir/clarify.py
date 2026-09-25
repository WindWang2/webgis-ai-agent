"""Requirement clarification 统一策略（F02：只在影响正确性或不可逆时提问）。

三层结构：

1. **typed reason codes**（不与 ``clarification.py`` 旧 6 code 重名；
   兼容出口 :func:`to_legacy_request` 产 出旧 ``ClarificationRequest`` 形状）；
2. **策略表**：每个 code 声明 ``blocking``（影响正确性/不可逆 → 允许提问）
   与安全默认（非 blocking 或用户不答时必须携带 rationale，绝不静默）；
3. **context-key 去重**：``context_key = hash(code + 相关字段规范值)``。
   同 key 已问未答 → 不再问（context 未变化）；context 变 → 新 key 可再问；
   answered/waived 终结（F02 DoD #2）。

本模块纯函数零 IO；歧义入档（:func:`record_ambiguities`）是 lifecycle
记账（不动 revision/语义 journal），语义修订走 patch 协议。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict

from app.services.gis_harness.clarification import (
    ClarificationOption,
    ClarificationQuestion,
    ClarificationRequest,
)
from app.services.gis_harness.requirement_ir.contracts import (
    MAX_AMBIGUITIES,
    Ambiguity,
    AmbiguityOption,
    RequirementDocument,
)
from app.services.gis_harness.requirement_ir.lifecycle import (
    effective_document_lifecycle,
)
from app.services.gis_harness.workflow_instance import canonical_fingerprint

# 每回合最多提问数（对齐 ClarificationRequest ≤2 questions）
MAX_QUESTIONS_PER_TURN = 2

# ── typed reason codes（策略表） ──────────────────────────────────────
# code → (blocking, question_zh, question_en, default_value, default_rationale)
_CODE_POLICY: Dict[str, Dict[str, Any]] = {
    # 正确性面：统计缺指标 → 结果口径不成立
    "measure_missing_for_statistics": {
        "blocking": True,
        "question_zh": "要统计的指标是什么？（如数量/密度/占比）",
        "question_en": "Which measure should be aggregated (count/density/share)?",
        "default": "",
        "default_rationale": "",
    },
    # 正确性面：字段解析平级多候选（field_resolver needs_clarification 回流）
    "measure_multiple_candidates": {
        "blocking": True,
        "question_zh": "该指标对应多个同名字段，请指定使用哪个",
        "question_en": "Multiple candidate fields match; pick one",
        "default": "",
        "default_rationale": "",
    },
    # 正确性面：归一化口径缺分母（人均/单位面积 无人口/面积口径 → 数字失义）
    "denominator_missing_for_normalized_statistic": {
        "blocking": True,
        "question_zh": "归一化口径需要分母（如常住人口/面积），用哪个？",
        "question_en": "Normalization needs a denominator (population/area)",
        "default": "",
        "default_rationale": "",
    },
    # 正确性面：时序缺时间窗（趋势窗口不明 → 结论失义）
    "time_range_missing_for_series": {
        "blocking": True,
        "question_zh": "时序分析的时间范围是？（如 2019-2024）",
        "question_en": "What time range for the series (e.g. 2019-2024)?",
        "default": "",
        "default_rationale": "",
    },
    # 正确性面：空间关系缺目标（proximity 无目标 → 无从计算）
    "spatial_target_missing": {
        "blocking": True,
        "question_zh": "空间关系的目标是什么？（如 地铁站点/某区域）",
        "question_en": "Target of the spatial relation (e.g. metro stations)?",
        "default": "",
        "default_rationale": "",
    },
    # 正确性面：行政统计缺范围（不猜地理）
    "aoi_unresolved": {
        "blocking": True,
        "question_zh": "分析范围是哪个城市/区域？",
        "question_en": "Which city/area should be analyzed?",
        "default": "",
        "default_rationale": "",
    },
    # 不可逆面：对外发布缺格式
    "export_format_missing_when_publish": {
        "blocking": True,
        "question_zh": "发布导出用什么格式？（png/pdf/svg）",
        "question_en": "Export format for publication (png/pdf/svg)?",
        "default": "pdf",
        "default_rationale": "publish 场景默认矢量 pdf（出版常用，可缩放）；用户不答时经显式 waive 应用并披露",
    },
    # 非 blocking：普通导出缺格式 → 安全默认，不问
    "export_format_defaulted": {
        "blocking": False,
        "question_zh": "",
        "question_en": "",
        "default": "png",
        "default_rationale": "非发布导出默认 png（屏幕渲染通用）；不影响正确性，不打断用户",
    },
    # 非 blocking：调色板未表达 → grammar 权威默认，不问
    "palette_defaulted": {
        "blocking": False,
        "question_zh": "",
        "question_en": "",
        "default": "",
        "default_rationale": "未表达时由 cartographic grammar 依 purpose/字段证据求解，属权威默认",
    },
}


class ClarificationNeed(BaseModel):
    """一次 plan 的产物（纯描述；不入档）。"""

    model_config = ConfigDict(extra="forbid")

    ambiguity: Ambiguity
    already_recorded: bool = False    # 同 key 已在档（open 未答 → 不重复问）
    resolvable_by_default: bool = False


def context_key(code: str, ctx: Dict[str, Any]) -> str:
    """去重锚：code + 相关字段规范值（context 变 → key 变 → 可再问）。"""
    return canonical_fingerprint({"code": code, "ctx": ctx})[:16]


def _ambiguity(code: str, path: str, ctx: Dict[str, Any],
               options: Optional[List[AmbiguityOption]] = None) -> Ambiguity:
    policy = _CODE_POLICY[code]
    return Ambiguity(
        code=code,
        path=path,
        context_key=context_key(code, ctx),
        blocking=bool(policy["blocking"]),
        question_zh=policy["question_zh"],
        question_en=policy["question_en"],
        options=options or [],
        default_value=str(policy.get("default", "")),
        default_rationale=str(policy.get("default_rationale", "")),
    )


def _detect(spec) -> List[Ambiguity]:
    """确定性检测当前 spec 的歧义（只检测，不入档）。"""
    found: List[Ambiguity] = []
    needs_statistics = (
        spec.statistics.dimension != "none"
        or spec.task.task_type in ("administrative_statistic", "temporal_trend",
                                   "categorical_distribution",
                                   "concentration_analysis", "spatial_equity",
                                   "analytical_density")
        or any(a in ("administrative_summary", "administrative_aggregation",
                     "trend_analysis", "category_breakdown")
               for a in spec.core.analysis_intents)
    )
    # 1) 统计缺指标
    if needs_statistics and not spec.measures:
        found.append(_ambiguity(
            "measure_missing_for_statistics", "measures",
            {"task": spec.task.task_type, "dim": spec.statistics.dimension}))
    # 2) 归一化缺分母
    if spec.statistics.normalization in ("per_area", "per_capita", "custom") \
            and not spec.statistics.denominator:
        found.append(_ambiguity(
            "denominator_missing_for_normalized_statistic", "statistics.denominator",
            {"norm": spec.statistics.normalization}))
    # 3) 时序缺时间窗
    if spec.time.series and not (spec.time.range_start and spec.time.range_end):
        found.append(_ambiguity(
            "time_range_missing_for_series", "time.range_start",
            {"granularity": spec.time.granularity, "start": spec.time.range_start}))
    # 4) 空间关系缺目标
    if spec.spatial_relation.kind not in ("none", "") and not spec.spatial_relation.target:
        found.append(_ambiguity(
            "spatial_target_missing", "spatial_relation.target",
            {"kind": spec.spatial_relation.kind}))
    # 5) 统计语义缺范围（不猜地理；『这片区域』等指示词同样视为未解析）
    if needs_statistics and not spec.aoi.name:
        found.append(_ambiguity(
            "aoi_unresolved", "aoi.name",
            {"dim": spec.statistics.dimension, "task": spec.task.task_type}))
    # 6) 发布导出缺格式（不可逆面才问）
    if spec.output.publish and not spec.output.formats:
        found.append(_ambiguity(
            "export_format_missing_when_publish", "output.formats",
            {"publish": True, "formats": list(spec.output.formats)}))
    # 7) 非发布导出缺格式 → 安全默认（不问，入档披露）
    elif not spec.output.publish and spec.output.report is False \
            and spec.task.kind == "export" and not spec.output.formats:
        found.append(_ambiguity(
            "export_format_defaulted", "output.formats",
            {"kind": "export", "formats": list(spec.output.formats)}))
    return found


def plan_clarifications(doc: RequirementDocument) -> List[ClarificationNeed]:
    """当前文档的澄清计划（纯函数；不做入档）。

    - 只返回 blocking 且未终结的需求作为"可问项"（≤2/turn）；
    - 非 blocking 安全默认一并入档披露（resolvable_by_default=True）；
    - 同 key 已在档且状态非 open → 不再出现；open 未答 → 标记
      already_recorded（context 未变化，不重复询问）。
    """
    spec = doc.intent
    by_key = {a.context_key: a for a in spec.ambiguities}
    needs: List[ClarificationNeed] = []
    askable: List[ClarificationNeed] = []
    for ambiguity in _detect(spec):
        existing = by_key.get(ambiguity.context_key)
        if existing is not None and existing.state != "open":
            continue    # answered/waived：同 context 终结
        need = ClarificationNeed(
            ambiguity=ambiguity,
            already_recorded=existing is not None,
            resolvable_by_default=bool(ambiguity.default_value),
        )
        if ambiguity.blocking:
            askable.append(need)
        else:
            needs.append(need)
    needs.extend(askable[:MAX_QUESTIONS_PER_TURN])
    return needs


def pending_questions(doc: RequirementDocument) -> List[Ambiguity]:
    """当前可问且允许问的 open blocking 歧义（≤2；入档后用它驱动提问）。"""
    questions = [
        a for a in doc.intent.ambiguities
        if a.state == "open" and a.blocking
    ]
    return questions[:MAX_QUESTIONS_PER_TURN]


def record_ambiguities(
    doc: RequirementDocument,
    needs: List[ClarificationNeed],
    turn: int,
) -> RequirementDocument:
    """把 plan 出的新歧义入档（lifecycle 记账：不动 revision/语义 journal）。

    已在档（already_recorded）只补 asked_turn；文档 lifecycle 依
    effective 建议推进（有 open blocking → clarifying）。
    """
    existing = {a.context_key: a for a in doc.intent.ambiguities}
    merged: List[Ambiguity] = list(doc.intent.ambiguities)
    for need in needs:
        ambiguity = need.ambiguity
        current = existing.get(ambiguity.context_key)
        if current is None:
            if len(merged) >= MAX_AMBIGUITIES:
                break
            merged.append(ambiguity.model_copy(update={"asked_turn": turn}))
        elif current.asked_turn is None:
            updated = current.model_copy(update={"asked_turn": turn})
            merged[merged.index(current)] = updated
    intent = doc.intent.model_copy(update={"ambiguities": merged})
    doc = doc.model_copy(update={"intent": intent})
    target = effective_document_lifecycle(doc)
    if target != doc.lifecycle:
        from app.services.gis_harness.requirement_ir.lifecycle import transition_document
        doc = doc.model_copy(update={"lifecycle": transition_document(doc.lifecycle, target)})
    return doc


def to_legacy_request(questions: List[Ambiguity]) -> Optional[ClarificationRequest]:
    """兼容出口：IR 歧义 → 旧 ``ClarificationRequest``（无可用问题 → None）。"""
    if not questions:
        return None
    legacy_slots = {"aoi_unresolved": "area"}
    q_list: List[ClarificationQuestion] = []
    for ambiguity in questions:
        options = [
            ClarificationOption(
                value=option.value, label_zh=option.label_zh,
                label_en=option.label_en, is_default=option.is_default)
            for option in ambiguity.options
        ] or [
            ClarificationOption(
                value=ambiguity.default_value or "default",
                label_zh="使用默认", label_en="use default", is_default=True),
        ]
        q_list.append(ClarificationQuestion(
            slot=legacy_slots.get(ambiguity.code, ambiguity.path or ambiguity.code),
            prompt_zh=ambiguity.question_zh or ambiguity.code,
            prompt_en=ambiguity.question_en or ambiguity.code,
            options=options,
            reason_code=ambiguity.code,
        ))
    return ClarificationRequest(
        questions=q_list[:2],
        reason_codes=[q.reason_code for q in q_list[:2]],
    )
