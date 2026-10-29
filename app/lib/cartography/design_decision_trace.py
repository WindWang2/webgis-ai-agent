"""DesignDecisionTrace —— 制图设计决策链的一等可解释工件（M2）。

把「用户目的 → 数据资格 → 候选（为何选/为何弃）→ 选定表达 → 分类裁决 →
组合模板 → MapSpec 指纹」序列化为有界 dict，随 ``webgis_map_product``
结果载荷下发（Agent 可解释、前端可展示、QA 可反查）。

设计纪律：
- 纯组装，无决策逻辑——候选与裁决全部来自 ``design_candidates``（其又
  复用 composition_selection / model_library / choose_classification 的
  既有权威）；
- 确定性：不含时间戳/随机数（golden 可锁；调用方需要时刻自行记录）；
- 有界：candidates ≤4、每理由 ≤120 字符（与 MapDesignCandidate.
  to_bounded_dict 同纪律）；
- 诚实缺席：字段缺失写 "absent:<原因>"，不编造。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.lib.cartography.design_candidates import (
    DesignCandidateSet,
    DesignTaskContext,
)

DESIGN_DECISION_TRACE_VERSION = 1
_MAX_FACTS = 8
_MAX_RECEIPTS = 6


def _data_facts(ctx: DesignTaskContext) -> List[str]:
    """数据资格事实行（≤8，人类可读；供 Agent 解释与前端展示）。"""
    facts: List[str] = []
    facts.append(f"几何 {ctx.geometry_kind}；在场产物 "
                 + (", ".join(ctx.artifact_types[:4]) if ctx.artifact_types
                    else "（无显式声明）"))
    if ctx.point_count_basis != "unknown":
        facts.append(f"点要素计数 {ctx.point_count}（依据 {ctx.point_count_basis}）")
    else:
        facts.append("点要素计数未知（证据缺席，过密/热力资格规则不裁决）")
    if ctx.denominator.available:
        facts.append(f"分母在场：{ctx.denominator.field or '?'}"
                     f"（{ctx.denominator.unit or '单位未声明'}，"
                     f"来源 {ctx.denominator.source}）")
    else:
        facts.append("分母缺席——率/每万人语义不可计算，原始计数表达须披露")
    if ctx.temporal_periods >= 2:
        facts.append(f"时相证据：{ctx.temporal_periods} 期（change 族候选可达）")
    if ctx.distribution is not None:
        facts.append(
            f"分布统计 n={ctx.distribution.n}, min={ctx.distribution.min:.4g}, "
            f"max={ctx.distribution.max:.4g}, mean={ctx.distribution.mean or 0:.4g}, "
            f"median={ctx.distribution.median or 0:.4g}")
    if ctx.user_requested_models:
        facts.append("用户显式表达要求：" + ", ".join(ctx.user_requested_models[:4]))
    return facts[:_MAX_FACTS]


def build_design_decision_trace(
    ctx: DesignTaskContext,
    candidate_set: DesignCandidateSet,
    *,
    intent_summary: Optional[Dict[str, Any]] = None,
    mapspec_fingerprint: str = "",
    primary_bound_model: str = "",
) -> Dict[str, Any]:
    """组装决策 trace（纯函数；无时间戳，确定性）。

    intent_summary: {task, scope, subject, geometry_expectation} 有界投影。
    primary_bound_model: planner 实际绑定的主表达（对齐说明用）。
    """
    selected = next(
        (c for c in candidate_set.candidates
         if c.candidate_id == candidate_set.selected_candidate_id), None)
    receipts: List[str] = []
    for c in candidate_set.candidates:
        for code in c.condition_codes:
            receipts.append(f"{c.map_model_id}:{code}")
    return {
        "version": DESIGN_DECISION_TRACE_VERSION,
        "intent": {
            k: str(v)[:64] for k, v in (intent_summary or {}).items()
        } if intent_summary else {},
        "dataFacts": _data_facts(ctx)[:_MAX_FACTS],
        # 候选只留紧凑引用（模型/适宜性）——全量投影在结果载荷的
        # design_candidates 键（同一工具结果内不重复携带，评审 R1 后
        # G4 大数据契约尺寸复核：重复全量会无谓多 ~2.5KB）。
        "candidates": [
            {"m": c.map_model_id, "s": c.suitability.value}
            for c in candidate_set.candidates],
        "selected": {
            "candidateId": (selected.candidate_id[:64]
                            if selected else "absent:no-eligible-candidate"),
            "mapModel": (selected.map_model_id[:48]
                         if selected else ""),
            "basis": candidate_set.selection_basis[:160],
            "plannerPrimaryMatch": bool(
                selected and primary_bound_model
                and selected.map_model_id == primary_bound_model),
        },
        "userRequestHandling": {
            k: [m[:48] for m in v[:6]]
            for k, v in candidate_set.user_request_handling.items()
        },
        "scientificReceipts": receipts[:_MAX_RECEIPTS],
        "mapspecFingerprint": mapspec_fingerprint[:64] or "absent:not-committed",
    }


__all__ = [
    "DESIGN_DECISION_TRACE_VERSION",
    "build_design_decision_trace",
]
