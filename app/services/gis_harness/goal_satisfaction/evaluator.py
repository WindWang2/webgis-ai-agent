"""Deterministic Goal Satisfaction Evaluator（ADR-0183 G3/G4/G5/G7/G8）。

单一纯函数评估点：``evaluate_goal_satisfaction(chapter, ...) -> report``。

红线（全部由测试钉死，见 tests/unit/goal_satisfaction/**）：

- **deterministic first（G3）**：需求 fulfilled 必须有 ≥1 条
  deterministic/structural 级在证证据；visual/assisted 只降级、只补注，
  **永远不能把缺证据的任务判 PASS**；
- **multi-goal（G4）**：每个需求独立裁决（fulfilled/partial/blocked/
  not_evaluated/failed），全局裁决由逐项 ledger 聚合 —— 一个子目标成功
  绝不产生全局 pass；
- **stop/replan 信号（G5）**：输出 ``signal`` 供 Harness 收口/续行消费；
  信号词与既有 continuation 路由同族，不新开循环；
- **quality integration（G7）**：只消费 product_verdict / final_map /
  cartographic_review / render observation 等既有裁决，不重算颜色、布局、
  标注；
- **explainability（G8）**：requirement → evidence → verdict 链、缺失面、
  失败规则、建议动作、不确定性，全部有界可序列化。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from .contracts import (
    MAX_MISSING,
    PASS_CAPABLE_CLASSES,
    EvidenceKind,
    EvidenceStatus,
    GoalContract,
    GoalEvidence,
    GoalRequirement,
    GoalSatisfactionReport,
    GoalVerdict,
    HarnessSignal,
    RequirementKind,
)
from .contracts import RequirementState as ReqState
from .contracts import RequirementVerdict as ReqVerdict
from .evidence import build_evidence_registry, data_family_blockers
from .requirements import classify_capability

logger = logging.getLogger(__name__)

# ── 失败/缺失规则 id（机器可读；语料与测试锁定）──────────────────────────
R_PRODUCT_ABSENT = "product_verdict_absent"
R_MAP_NEEDS_REPAIR = "map_needs_repair"
R_MAP_BLOCKED_BY_DATA = "map_blocked_by_data"
R_MAP_BLOCKED_BY_METHOD = "map_blocked_by_method"
R_MAP_STALE_RENDER = "map_render_stale"
R_CARTO_BLOCKING = "cartography_blocking"
R_ROW_PENDING = "row_status_pending"
R_ROW_FAILED = "row_status_failed"
R_ROW_BLOCKED = "row_status_unavailable"
R_DATA_INSUFFICIENT = "data_insufficient"
R_FALLBACK_NON_COMPARABLE = "fallback_non_comparable"
R_FALLBACK_PROXY = "fallback_proxy_result"
R_SCOPE_MISMATCH = "scope_mismatch"
R_FILTER_MISMATCH = "filter_mismatch"
R_EXPORT_ABSENT = "export_receipt_absent"
R_EXPORT_STALE = "export_receipt_stale"
R_FORBIDDEN_MET = "forbidden_outcome_present"
R_NO_COMPARISON_ARTIFACT = "comparison_artifact_missing"
R_NO_STATISTICS = "statistics_artifact_missing"
R_NO_CHART = "chart_artifact_missing"

#: proxy 族降级（结论不可表述为原生分析 → partial + 披露）。
_PROXY_CLASSES = frozenset({"proxy", "approximation", "degraded"})
#: 不可比降级（G6 锚：fallback source non-comparable 不得 PASS）。
_NON_COMPARABLE_CLASSES = frozenset({"not_allowed"})


def _dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _rows(value: Any) -> List[Dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [r for r in value if isinstance(r, dict)]


def _verdict_token(map_product: Optional[Dict[str, Any]]) -> str:
    verdict = _dict(map_product).get("product_verdict")
    return str(
        verdict.get("verdict") if isinstance(verdict, dict) else verdict or ""
    )


def _finding_codes(map_product: Optional[Dict[str, Any]]) -> List[str]:
    return list(dict.fromkeys(
        str(f.get("code") or "") for f in _rows(_dict(map_product).get("issues"))
        if f.get("code")
    ))[:12]


def _rows_by_classification(
    chapter: Dict[str, Any], classification: str
) -> List[str]:
    """章节分析行 → 语义分类命中的 capability id 列表（稳定序）。"""
    out: List[str] = []
    for row in _rows(chapter.get("analysis_steps"))[:32]:
        cap = str(row.get("capability") or "").strip()
        if not cap or cap in out:
            continue
        if classify_capability(cap, str(row.get("purpose") or "")) == classification:
            out.append(cap)
    return out


def _row_param(row: Dict[str, Any], keys: Tuple[str, ...]) -> str:
    """行 params 的首个非空标量（确定性读取；无则空串）。"""
    params = _dict(row.get("params"))
    for key in keys:
        value = params.get(key)
        if isinstance(value, (str, int, float)) and str(value).strip():
            return str(value).strip()
    return ""


def _scope_of_row(row: Dict[str, Any]) -> str:
    return _row_param(row, ("scope", "region", "city", "district", "adm", "area"))


def _group_of_row(row: Dict[str, Any]) -> str:
    return _row_param(row, ("group_by", "by", "category_field", "district_field"))


def _contains(a: str, b: str) -> bool:
    """行政名包含匹配（「成都市」vs「成都」级宽松；双向）。"""
    return bool(a) and bool(b) and (a in b or b in a)


def resolve_goal_contract(
    chapter: Optional[Dict[str, Any]],
    user_goal: str = "",
) -> Optional[GoalContract]:
    """契约解析：显式 ``chapter["goal_contract"]``（校验后有界收窄）优先，
    否则结构化事实确定性派生。"""
    if not isinstance(chapter, dict) or not chapter:
        return None
    explicit = chapter.get("goal_contract")
    if isinstance(explicit, dict):
        try:
            return GoalContract.model_validate(explicit)
        except Exception:  # noqa: BLE001 — 非法显式契约 → 回退派生（不假用）
            logger.warning("[GoalSatisfaction] invalid explicit goal_contract; "
                           "falling back to derived contract")
    from .requirements import derive_goal_contract

    return derive_goal_contract(chapter, user_goal=user_goal)


# ── 单需求评估 ───────────────────────────────────────────────────────────

def _eval_map_requirement(
    requirement: GoalRequirement,
    evidence: List[GoalEvidence],
    chapter: Dict[str, Any],
    map_product: Optional[Dict[str, Any]],
) -> ReqVerdict:
    verdict_token = _verdict_token(map_product)
    final_status = str(_dict(map_product).get("final_map_status") or "")
    render_status = str(_dict(map_product).get("render_status") or "")
    out = ReqVerdict(
        requirement_id=requirement.id, kind=requirement.kind,
        evidence_ids=[e.id for e in evidence
                      if e.kind in (EvidenceKind.MAP_PRODUCT,
                                    EvidenceKind.SPATIAL_VALIDATION,
                                    EvidenceKind.RENDER_OBSERVATION,
                                    EvidenceKind.CARTOGRAPHY)]
        [:MAX_MISSING],
    )
    user_hidden = [
        e.detail for e in evidence if e.kind == EvidenceKind.USER_OVERRIDE
    ]
    out.uncertainty.extend(user_hidden)

    if not verdict_token:
        out.missing.append(R_PRODUCT_ABSENT)
        return out
    if verdict_token.startswith("READY"):
        if final_status not in ("verified", "verified_with_degradation"):
            out.missing.append(f"final_map:{final_status or 'unknown'}")
            out.verdict = ReqState.NOT_EVALUATED
            out.failed_rule = R_PRODUCT_ABSENT
            return out
        carto_failed = any(
            e.kind == EvidenceKind.CARTOGRAPHY and e.status == EvidenceStatus.FAILED
            for e in evidence)
        if carto_failed:
            out.verdict = ReqState.PARTIAL
            out.failed_rule = R_CARTO_BLOCKING
            out.next_action = "repair_cartography"
            return out
        if render_status == "stale":
            out.verdict = ReqState.PARTIAL
            out.failed_rule = R_MAP_STALE_RENDER
            out.next_action = "reobserve"
            return out
        out.verdict = ReqState.FULFILLED
        return out
    if verdict_token == "NEEDS_REPAIR":
        out.verdict = ReqState.PARTIAL
        out.failed_rule = R_MAP_NEEDS_REPAIR
        out.next_action = "repair_cartography"
        return out
    if verdict_token == "BLOCKED_BY_DATA":
        out.verdict = ReqState.BLOCKED
        out.failed_rule = R_MAP_BLOCKED_BY_DATA
        out.next_action = "resolve_data"
        return out
    if verdict_token == "BLOCKED_BY_METHOD":
        out.verdict = ReqState.FAILED
        out.failed_rule = R_MAP_BLOCKED_BY_METHOD
        out.next_action = "replan"
        return out
    out.missing.append(f"verdict:{verdict_token}")
    return out


def _eval_row_requirement(
    requirement: GoalRequirement,
    evidence: List[GoalEvidence],
    chapter: Dict[str, Any],
    map_product: Optional[Dict[str, Any]],
) -> ReqVerdict:
    """analysis / comparison / statistics / chart 的行证据裁决。

    fail-closed：背书 fulfilled 的证据必须 ∈ PASS_CAPABLE_CLASSES
    （deterministic/structural）；visual/assisted 在证不算数（G3 红线）。
    """
    out = ReqVerdict(requirement_id=requirement.id, kind=requirement.kind)

    def _backing(ev_id: str) -> bool:
        return any(
            e.id == ev_id and e.status == EvidenceStatus.PRESENT
            and e.evidence_class in PASS_CAPABLE_CLASSES
            for e in evidence)

    if requirement.kind == RequirementKind.ANALYSIS:
        row_ev_id = f"row:{requirement.capability}"
        row_ev = next((e for e in evidence if e.id == row_ev_id), None)
        out.evidence_ids = [row_ev_id]
        if row_ev is None or row_ev.status == EvidenceStatus.ABSENT:
            out.missing.append(R_ROW_PENDING)
            return out
        if row_ev.status == EvidenceStatus.FAILED:
            out.verdict = ReqState.FAILED
            out.failed_rule = R_ROW_FAILED
            out.next_action = "retry_step"
            return out
        if row_ev.evidence_class not in PASS_CAPABLE_CLASSES:
            out.missing.append(f"evidence_class:{row_ev.evidence_class.value}")
            return out
    elif requirement.kind == RequirementKind.COMPARISON:
        caps = _rows_by_classification(chapter, "comparison")
        present = [f"row:{c}" for c in caps if _backing(f"row:{c}")]
        out.evidence_ids = present[:MAX_MISSING]
        if not present:
            out.missing.append(R_NO_COMPARISON_ARTIFACT)
            return out
    elif requirement.kind == RequirementKind.STATISTICS:
        caps = _rows_by_classification(chapter, "statistics")
        present = [f"row:{c}" for c in caps if _backing(f"row:{c}")]
        out.evidence_ids = present[:MAX_MISSING]
        if not present:
            out.missing.append(R_NO_STATISTICS)
            return out
    else:  # CHART
        chart_rows = _rows_by_classification(chapter, "statistics")
        row_present = [f"row:{c}" for c in chart_rows if _backing(f"row:{c}")]
        codes = _finding_codes(map_product)
        component_ok = (
            any("chart" in str(s).lower()
                for s in _rows2(chapter.get("required_components")))
            and "component_missing" not in codes
            and "chart_data_missing" not in codes
        )
        out.evidence_ids = (row_present[:MAX_MISSING]
                            + (["component:chart"] if component_ok else []))
        if not row_present and not component_ok:
            out.missing.append(R_NO_CHART)
            return out

    # ── 在证后的降级规则（顺序即优先级）──────────────────────────────
    blockers = data_family_blockers(chapter, map_product)
    if blockers:
        out.verdict = ReqState.PARTIAL
        out.failed_rule = R_DATA_INSUFFICIENT
        out.missing.append(f"data:{blockers[0]}")
        out.next_action = "resolve_data"
        return out
    fallback_class, fallback_note = _worst_fallback(chapter)
    if fallback_class in _NON_COMPARABLE_CLASSES:
        out.verdict = ReqState.PARTIAL
        out.failed_rule = R_FALLBACK_NON_COMPARABLE
        out.uncertainty.append(fallback_note)
        out.next_action = "replan_method"
        return out
    # scope / filter 一致性（G6：图 PASS 但 scope/过滤不同的反锚）。
    row = _row_for(requirement, chapter)
    if row is not None:
        if requirement.scope_name and not _contains(
                requirement.scope_name, _scope_of_row(row)) and _scope_of_row(row):
            out.verdict = ReqState.PARTIAL
            out.failed_rule = R_SCOPE_MISMATCH
            out.next_action = "correct_scope"
            return out
        if requirement.group_by and _group_of_row(row) and \
                requirement.group_by != _group_of_row(row):
            out.verdict = ReqState.PARTIAL
            out.failed_rule = R_FILTER_MISMATCH
            out.next_action = "correct_filter"
            return out
    out.verdict = ReqState.FULFILLED
    if fallback_class in _PROXY_CLASSES:
        out.verdict = ReqState.PARTIAL
        out.failed_rule = R_FALLBACK_PROXY
        out.uncertainty.append(fallback_note)
    return out


def _rows2(value: Any) -> List[str]:
    """required_components 槽位名清单（str 直读，非 dict 行）。"""
    if not isinstance(value, list):
        return []
    return [str(v) for v in value if isinstance(v, (str, dict)) and
            (isinstance(v, str) or v.get("type") or v.get("slot"))]


def _row_for(
    requirement: GoalRequirement, chapter: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    if requirement.kind != RequirementKind.ANALYSIS:
        return None
    for row in _rows(chapter.get("analysis_steps"))[:32]:
        if str(row.get("capability") or "") == requirement.capability:
            return row
    return None


def _worst_fallback(chapter: Dict[str, Any]) -> Tuple[str, str]:
    """章节回退 → 最差 downgrade_class（not_allowed > proxy 族 > equivalent）。"""
    worst_class, note = "equivalent", ""
    for fb in _rows(chapter.get("fallbacks"))[:8]:
        evidencefb = _dict(fb.get("evidence"))
        cls = str(fb.get("downgrade_class")
                  or evidencefb.get("downgrade_class") or "equivalent")
        if cls in _NON_COMPARABLE_CLASSES:
            return cls, str(fb.get("reason_code") or cls)[:120]
        if cls in _PROXY_CLASSES and worst_class == "equivalent":
            worst_class, note = cls, str(fb.get("reason_code") or cls)[:120]
    return worst_class, note


def _eval_export_requirement(
    requirement: GoalRequirement,
    evidence: List[GoalEvidence],
) -> ReqVerdict:
    out = ReqVerdict(requirement_id=requirement.id, kind=requirement.kind)
    receipt_id = f"export_receipt:{requirement.export_format}"
    receipt = next((e for e in evidence if e.id == receipt_id), None)
    out.evidence_ids = [receipt_id]
    if receipt is None or receipt.status == EvidenceStatus.ABSENT:
        out.missing.append(R_EXPORT_ABSENT)
        out.next_action = "export_product"
        return out
    if receipt.status == EvidenceStatus.STALE:
        out.verdict = ReqState.PARTIAL
        out.failed_rule = R_EXPORT_STALE
        out.uncertainty.append(
            f"export revision {receipt.revision[:24]} != current")
        out.next_action = "reexport"
        return out
    out.verdict = ReqState.FULFILLED
    return out


def _eval_requirement(
    requirement: GoalRequirement,
    evidence: List[GoalEvidence],
    chapter: Dict[str, Any],
    map_product: Optional[Dict[str, Any]],
) -> ReqVerdict:
    if requirement.polarity == "must_not":
        out = ReqVerdict(requirement_id=requirement.id,
                         kind=requirement.kind)
        # 禁止结局的「在证」= 与需求目标绑定的 deterministic 证据在场
        # （requirement.capability 为空 = 目标未声明 → 不凭任意证据判违反，
        # 落 not_evaluated —— 禁止面绝不靠猜测触发）。
        target = str(requirement.capability or "").strip().lower()
        violated = [
            e for e in evidence
            if e.status == EvidenceStatus.PRESENT
            and e.evidence_class in PASS_CAPABLE_CLASSES
            and (not target or target in e.id.lower()
                 or target in e.detail.lower())
        ]
        if target and violated:
            out.verdict = ReqState.FAILED
            out.failed_rule = R_FORBIDDEN_MET
            out.evidence_ids = [e.id for e in violated[:MAX_MISSING]]
        elif not target:
            out.verdict = ReqState.NOT_EVALUATED
            out.missing.append("must_not_target_undeclared")
        else:
            out.verdict = ReqState.FULFILLED  # 目标声明且无在证 = 禁止未被违反
        return out
    if requirement.kind == RequirementKind.MAP:
        return _eval_map_requirement(
            requirement, evidence, chapter, map_product)
    if requirement.kind == RequirementKind.EXPORT:
        return _eval_export_requirement(requirement, evidence)
    return _eval_row_requirement(
        requirement, evidence, chapter, map_product)


# ── 全局聚合 + 信号 ──────────────────────────────────────────────────────

_SIGNAL_PRIORITY = {
    HarnessSignal.COMPLETE: 0,
    HarnessSignal.BLOCKED_BY_DATA: 1,
    HarnessSignal.REPLAN: 2,
    HarnessSignal.REPAIR_CARTOGRAPHY: 3,
    HarnessSignal.REQUEST_CLARIFICATION: 4,
    HarnessSignal.CONTINUE: 5,
}


def _aggregate(
    verdicts: List[ReqVerdict],
    contract: GoalContract,
) -> GoalVerdict:
    by_id = {v.requirement_id: v for v in verdicts}
    # 1) 禁止结局最先裁决：任一 must_not 被违反 → 全局 failed（不允许
    #    「其他都做完了」掩盖显式禁令 —— G6 反作弊锚）。
    for req in contract.requirements:
        if req.polarity == "must_not" and req.required:
            v = by_id.get(req.id)
            if v is not None and v.verdict == ReqState.FAILED:
                return GoalVerdict.FAILED
    required = [
        v for v in verdicts
        if v.requirement_id in set(contract.required_ids())
    ]
    if not required:
        # 无 required 需求 = 无可验证契约面（诚实三态，绝不默认 PASS）。
        return GoalVerdict.NOT_EVALUATED
    states = [v.verdict for v in required]
    if any(s == ReqState.FAILED for s in states):
        return GoalVerdict.FAILED
    fulfilled = sum(1 for s in states if s == ReqState.FULFILLED)
    threshold = max(0.0, min(1.0, float(contract.success_threshold)))
    # 满足 = fulfilled 分数 ≥ 阈值 ∧ 无 BLOCKED（缺证据不计入分子 ——
    # not_evaluated 永远不算 fulfilled；默认阈值 1.0 = 全部 fulfilled）。
    if fulfilled / len(required) >= threshold and not any(
            s == ReqState.BLOCKED for s in states):
        return GoalVerdict.SATISFIED
    if any(s == ReqState.BLOCKED for s in states):
        return (GoalVerdict.PARTIAL if fulfilled else GoalVerdict.BLOCKED)
    if fulfilled or any(s == ReqState.PARTIAL for s in states):
        return GoalVerdict.PARTIAL
    return GoalVerdict.NOT_EVALUATED


def _derive_signal(
    verdict: GoalVerdict,
    verdicts: List[ReqVerdict],
    contract: GoalContract,
    chapter: Dict[str, Any],
    map_product: Optional[Dict[str, Any]],
) -> HarnessSignal:
    if verdict == GoalVerdict.SATISFIED:
        return HarnessSignal.COMPLETE
    intent = _dict(chapter.get("intent"))
    clarification = _dict(intent.get("clarification"))
    has_open_question = bool(
        str(clarification.get("question") or clarification.get("query") or "")
    ) and str(clarification.get("status") or "") != "resolved"
    map_blocked_by_data = any(
        v.failed_rule == R_MAP_BLOCKED_BY_DATA for v in verdicts)
    if (data_family_blockers(chapter, map_product) or map_blocked_by_data) \
            and verdict in (
                GoalVerdict.BLOCKED, GoalVerdict.PARTIAL, GoalVerdict.FAILED):
        return HarnessSignal.BLOCKED_BY_DATA
    method_blockers = [
        str(b) for b in
        (_dict(chapter.get("workflow_contract")).get("method_blockers") or [])
    ]
    # 方法阻断/不可比回退 → 重规划（partial 也算：方法产不出可比结果，
    # 「带病 partial」不是可续行态 —— G6 不可比回退锚）。
    method_capped = any(
        v.failed_rule in (R_FALLBACK_NON_COMPARABLE, R_MAP_BLOCKED_BY_METHOD)
        for v in verdicts)
    if (method_blockers or method_capped) and verdict in (
            GoalVerdict.BLOCKED, GoalVerdict.FAILED, GoalVerdict.PARTIAL):
        return HarnessSignal.REPLAN
    map_partial_needs_repair = any(
        v.kind == RequirementKind.MAP
        and v.failed_rule in (R_MAP_NEEDS_REPAIR, R_CARTO_BLOCKING)
        for v in verdicts)
    if map_partial_needs_repair:
        return HarnessSignal.REPAIR_CARTOGRAPHY
    if has_open_question and verdict in (
            GoalVerdict.NOT_EVALUATED, GoalVerdict.BLOCKED):
        return HarnessSignal.REQUEST_CLARIFICATION
    return HarnessSignal.CONTINUE


def evaluate_goal_satisfaction(
    chapter: Optional[Dict[str, Any]],
    *,
    user_goal: str = "",
    map_product: Optional[Dict[str, Any]] = None,
    cartographic_review: Optional[Dict[str, Any]] = None,
) -> Optional[GoalSatisfactionReport]:
    """评估入口（纯函数、零 IO、fail-closed）。

    返回 None = 章节/契约面不存在（调用方保持既有行为零漂移）；
    返回 report.verdict == not_evaluated = 契约在但证据不足（诚实三态）。
    """
    contract = resolve_goal_contract(chapter, user_goal=user_goal)
    if contract is None:
        return None
    chapter = chapter if isinstance(chapter, dict) else {}
    evidence = build_evidence_registry(
        chapter, map_product, cartographic_review)
    verdicts = [
        _eval_requirement(r, evidence, chapter, map_product)
        for r in contract.requirements
    ]
    verdict = _aggregate(verdicts, contract)
    signal = _derive_signal(
        verdict, verdicts, contract, chapter, map_product)

    counts: Dict[str, int] = {}
    for v in verdicts:
        counts[v.verdict.value] = counts.get(v.verdict.value, 0) + 1
    missing_summary = [
        m for v in verdicts if v.verdict != ReqState.FULFILLED
        for m in ([f"{v.requirement_id}:{code}" for code in v.missing])
    ][:MAX_MISSING]
    uncertainties = [
        u for v in verdicts for u in v.uncertainty if u][:4]
    from .requirements import contract_fingerprint

    return GoalSatisfactionReport(
        goal_id=contract.goal_id,
        contract_fingerprint=contract_fingerprint(contract),
        verdict=verdict,
        signal=signal,
        requirements=verdicts,
        counts=counts,
        missing_summary=missing_summary,
        uncertainties=uncertainties,
        summary_line=_summary_line(verdict, signal, counts, missing_summary),
    )


def _summary_line(
    verdict: GoalVerdict,
    signal: HarnessSignal,
    counts: Dict[str, int],
    missing_summary: List[str],
) -> str:
    """单行有界投影（LLM/SSE 面；不暴露内部敏感 trace）。"""
    parts = [f"goal={verdict.value}", f"signal={signal.value}"]
    if counts:
        parts.append(" ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    if missing_summary:
        parts.append("missing:" + ",".join(
            m.split(":")[-1] for m in missing_summary[:3]))
    return "[GIS Goal] " + " ".join(parts)[:220]


__all__ = [
    "evaluate_goal_satisfaction",
    "resolve_goal_contract",
]
