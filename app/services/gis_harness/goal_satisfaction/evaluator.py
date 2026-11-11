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
from .contracts import row_evidence_id
from .contracts import RequirementVerdict as ReqVerdict
from app.services.gis_harness.completion.contracts import (  # noqa: E402
    VERDICT_BLOCKED_BY_DATA,
    VERDICT_BLOCKED_BY_METHOD,
    VERDICT_NEEDS_REPAIR,
    VERDICT_READY,
    VERDICT_READY_WITH_WARNINGS,
)
from .evidence import build_evidence_registry, data_family_blockers
from .requirements import classify_capability
from .semantics import (
    conflicted_artifact_names,
    caps_for_receipt_row,
)
from .receipts import bound_receipt_rows

logger = logging.getLogger(__name__)

# ── 失败/缺失规则 id（机器可读；语料与测试锁定）──────────────────────────
R_PRODUCT_ABSENT = "product_verdict_absent"
R_FINAL_MAP_UNVERIFIED = "final_map_unverified"
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
# ── G28（P1/P2/P3 加性规则；receipt 权威回执语义闸 + frame 切面）────────
R_RECEIPT_PARTIAL = "receipt_population_partial"
R_RECEIPT_DENSITY_DENOM = "receipt_density_without_denominator"
R_RECEIPT_RATE_TIMEBASE = "receipt_rate_without_timebase"
R_RECEIPT_UNIT = "receipt_unit_undeclared"
R_RECEIPT_CRS = "receipt_crs_unverified"
R_RECEIPT_ARTIFACT = "receipt_artifact_mismatch"
R_RECEIPT_SNAPSHOT = "receipt_observation_snapshot"
R_RECEIPT_SCOPE = "receipt_scope_subset"
R_RECEIPT_FAILED = "receipt_failed"
R_RECEIPT_CANCELLED = "receipt_cancelled"
R_ARTIFACT_UNVERIFIED = "artifact_unverified"
R_DELIVERY_RECEIPT_ABSENT = "delivery_receipt_absent"
R_FRAME_INCOMPLETE = "frame_product_incomplete"
R_FRAME_SNAPSHOT_ONLY = "frame_product_snapshot_only"

#: 证据类型前缀 → 交付路径标记（A16：数据交付与地图出版回执不混背）。
_DELIVERY_DETAIL_PREFIX = "delivery:"
_MAP_EXPORT_DETAIL_PREFIX = "map_export:"

#: 语义 cap 码 → 失败规则 id（semantics.py 词表对齐）。
_CAP_RULE_MAP = {
    "population_partial": R_RECEIPT_PARTIAL,
    "density_without_denominator": R_RECEIPT_DENSITY_DENOM,
    "rate_without_timebase": R_RECEIPT_RATE_TIMEBASE,
    "unit_undeclared": R_RECEIPT_UNIT,
    "crs_unverified": R_RECEIPT_CRS,
    "artifact_checksum_mismatch": R_RECEIPT_ARTIFACT,
    "observation_snapshot_not_live": R_RECEIPT_SNAPSHOT,
    "scope_subset": R_RECEIPT_SCOPE,
    "receipt_failed": R_RECEIPT_FAILED,
    "receipt_cancelled": R_RECEIPT_CANCELLED,
}

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


def _frame_gate(map_product: Optional[Dict[str, Any]]):
    """frame_product 终验块 → 闸函数（None = 无闸；闭包写 out 的部分应用）。

    只消费 G15 ``verify_frame_product`` 产出的有界块（status ∈
    complete/incomplete）；required 帧 verified < required → PARTIAL +
    逐帧缺口码；required 全 verified 但存在 snapshot 观察的可选帧 →
    PARTIAL + snapshot_only 披露（A14：快照不冒充 live）。
    """
    fp = _dict(map_product).get("frame_product")
    if not isinstance(fp, dict) or fp.get("status") not in (
            "complete", "incomplete"):
        return None
    summary = _dict(fp.get("summary"))
    required = int(summary.get("required") or 0)
    verified = int(summary.get("verified") or 0)
    snapshot_only = int(summary.get("snapshot_only") or 0)
    # G28 复审加固（fail-closed 一致性重查）：frames 行在场时以**行**为准
    # 重算 required/verified/snapshot_only，与 summary 取更严一侧 —— 伪造
    # summary（complete/全 verified/required=0）不得越过逐行事实（评审
    # F3a/F3d 方向）。生产块由 verify_frame_product 单遍同源产出，一致时
    # 数值不变（零漂移）。
    frame_rows = [r for r in (fp.get("frames") or [])[:12]
                  if isinstance(r, dict)]
    if frame_rows:
        rows_required = sum(1 for r in frame_rows if r.get("required"))
        rows_verified = sum(
            1 for r in frame_rows
            if r.get("binding") == "bound"
            and r.get("observation") in ("live_observed", "export_verified"))
        rows_snapshot = sum(
            1 for r in frame_rows
            if r.get("observation") == "snapshot")
        required = max(required, rows_required)
        verified = min(verified, rows_verified)
        snapshot_only = max(snapshot_only, rows_snapshot)
    if required <= 0:
        return None
    if verified < required:
        problems = [str(p)[:48] for p in (fp.get("problems") or [])[:4]]

        def _gate(out):
            out.verdict = ReqState.PARTIAL
            out.failed_rule = R_FRAME_INCOMPLETE
            out.missing.extend(problems)
            out.uncertainty.append(f"frames verified {verified}/{required}")
            out.next_action = "reobserve"
            return out

        return _gate
    if snapshot_only > 0:

        def _gate_snapshot(out):
            out.verdict = ReqState.PARTIAL
            out.failed_rule = R_FRAME_SNAPSHOT_ONLY
            out.uncertainty.append(
                f"snapshot_only frames={snapshot_only} (not live)")
            out.next_action = "reobserve"
            return out

        return _gate_snapshot
    return None


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
    if verdict_token.startswith((VERDICT_READY, VERDICT_READY_WITH_WARNINGS)):
        if final_status not in ("verified", "verified_with_degradation"):
            out.missing.append(f"final_map:{final_status or 'unknown'}")
            out.verdict = ReqState.NOT_EVALUATED
            out.failed_rule = R_FINAL_MAP_UNVERIFIED
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
        # unknown / failed / empty-on-degraded is not a seen map. Do not
        # fulfill a map requirement from a tool receipt alone.
        if render_status not in ("verified", "not_applicable"):
            if render_status or final_status != "verified":
                out.verdict = ReqState.NOT_EVALUATED
                out.failed_rule = R_FINAL_MAP_UNVERIFIED
                out.missing.append(f"render:{render_status or 'absent'}")
                out.next_action = "reobserve"
                return out
        # G28 P3（A13/A14/A05）：多画幅产品逐帧闸 —— 产品块上 frame_product
        # 终验（G15 verify_frame_product 只消费）incomplete → 整图 partial；
        # 一幅成功绝不汇总为产品完成；旧 revision 观察 → stale 按未观察计。
        frame_gate = _frame_gate(map_product)
        if frame_gate is not None:
            return frame_gate(out)
        out.verdict = ReqState.FULFILLED
        return out
    if verdict_token == VERDICT_NEEDS_REPAIR:
        out.verdict = ReqState.PARTIAL
        out.failed_rule = R_MAP_NEEDS_REPAIR
        out.next_action = "repair_cartography"
        return out
    if verdict_token == VERDICT_BLOCKED_BY_DATA:
        out.verdict = ReqState.BLOCKED
        out.failed_rule = R_MAP_BLOCKED_BY_DATA
        out.next_action = "resolve_data"
        return out
    if verdict_token == VERDICT_BLOCKED_BY_METHOD:
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
    receipts: Optional[Dict[str, Any]] = None,
) -> ReqVerdict:
    """analysis / comparison / statistics / chart 的行证据裁决。

    fail-closed：背书 fulfilled 的证据必须 ∈ PASS_CAPABLE_CLASSES
    （deterministic/structural）；visual/assisted 在证不算数（G3 红线）。
    """
    out = ReqVerdict(requirement_id=requirement.id, kind=requirement.kind)

    if requirement.id == "requirements:truncated":
        out.missing.append("requirements_truncated")
        return out

    def _backing(ev_id: str) -> bool:
        return any(
            e.id == ev_id and e.status == EvidenceStatus.PRESENT
            and e.evidence_class in PASS_CAPABLE_CLASSES
            for e in evidence)

    if requirement.kind == RequirementKind.ANALYSIS:
        row_ev_id = row_evidence_id(requirement.capability)
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
        present = [row_evidence_id(c) for c in caps if _backing(row_evidence_id(c))]
        out.evidence_ids = present[:MAX_MISSING]
        if not present:
            out.missing.append(R_NO_COMPARISON_ARTIFACT)
            return out
    elif requirement.kind == RequirementKind.STATISTICS:
        caps = _rows_by_classification(chapter, "statistics")
        present = [row_evidence_id(c) for c in caps if _backing(row_evidence_id(c))]
        out.evidence_ids = present[:MAX_MISSING]
        if not present:
            out.missing.append(R_NO_STATISTICS)
            return out
    else:  # CHART
        chart_rows = _rows_by_classification(chapter, "statistics")
        row_present = [row_evidence_id(c) for c in chart_rows if _backing(row_evidence_id(c))]
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

    # ── G28 P1/P2：绑定回执的语义闸（背书在证后才降级 —— 闸只降级不升级；
    #    回执缺席/缺字段 = 无码 unknown，兼容面零漂移，A25）。─────────────
    if receipts is not None:
        cap_out = _receipt_caps_verdict(requirement, chapter, receipts, out)
        if cap_out is not None:
            return cap_out

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


# ── G28 P1/P2：绑定回执语义闸 ────────────────────────────────────────────

_RECEIPT_FAMILIES = ("compute", "import", "edit", "delivery")

#: cap 码 → 可重试动作（有界词；repair_actions.py 消费同一词表）。
_CAP_NEXT_ACTION = {
    "population_partial": "rerun_complete",
    "density_without_denominator": "requalify",
    "rate_without_timebase": "requalify",
    "unit_undeclared": "requalify",
    "crs_unverified": "requalify",
    "artifact_checksum_mismatch": "rerun_complete",
    "observation_snapshot_not_live": "reobserve",
    "scope_subset": "resolve_data",
    "receipt_failed": "retry_step",
    "receipt_cancelled": "retry_step",
}


def _bound_refs_for(requirement: GoalRequirement,
                    chapter: Dict[str, Any]) -> List[str]:
    """需求 → 相关行 bound_ref 清单（ANALYSIS 单行；statistics/comparison/
    chart 族按分类行全量，去重有界）。"""
    refs: List[str] = []
    rows: List[Dict[str, Any]] = []
    if requirement.kind == RequirementKind.ANALYSIS:
        row = _row_for(requirement, chapter)
        if row is not None:
            rows.append(row)
    else:
        classification = {
            RequirementKind.COMPARISON: "comparison",
            RequirementKind.STATISTICS: "statistics",
        }.get(requirement.kind)
        if classification is not None:
            caps = _rows_by_classification(chapter, classification)
            for row in _rows(chapter.get("analysis_steps"))[:32]:
                if str(row.get("capability") or "") in caps:
                    rows.append(row)
    for row in rows[:8]:
        ref = str(row.get("bound_ref") or "").strip()
        if ref and ref not in refs:
            refs.append(ref)
    return refs[:8]


def _receipt_caps_verdict(
    requirement: GoalRequirement,
    chapter: Dict[str, Any],
    receipts: Dict[str, Any],
    out: ReqVerdict,
) -> Optional[ReqVerdict]:
    """绑定回执 cap 码 → 降级裁决（None = 无绑定/无码，零漂移）。

    hard cap（receipt_failed/cancelled）→ FAILED（工具行 done 但权威回执
    失败 = 事实矛盾，不得 PASS）；其余 cap → PARTIAL + 规则 + 动作。
    """
    refs = _bound_refs_for(requirement, chapter)
    if not refs:
        return None
    caps: List[str] = []
    for ref in refs:
        for family in _RECEIPT_FAMILIES:
            for row in bound_receipt_rows(receipts, family, ref)[:2]:
                caps.extend(caps_for_receipt_row(family, row))
    caps = list(dict.fromkeys(caps))[:6]
    if not caps:
        return None
    hard = [c for c in caps if c in ("receipt_failed", "receipt_cancelled")]
    if hard:
        out.verdict = ReqState.FAILED
        out.failed_rule = _CAP_RULE_MAP[hard[0]]
        out.missing.append(f"receipt:{hard[0]}")
        out.next_action = _CAP_NEXT_ACTION.get(hard[0], "retry_step")
        return out
    rule = _CAP_RULE_MAP.get(caps[0])
    if rule is None:
        return None
    out.verdict = ReqState.PARTIAL
    out.failed_rule = rule
    out.missing.append(f"receipt:{caps[0]}")
    out.next_action = _CAP_NEXT_ACTION.get(caps[0], "reobserve")
    out.uncertainty.append(f"receipt cap: {caps[0]}")
    return out


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
    receipts: Optional[Dict[str, Any]] = None,
) -> ReqVerdict:
    out = ReqVerdict(requirement_id=requirement.id, kind=requirement.kind)
    receipt_id = f"export_receipt:{requirement.export_format}"
    receipt = next((e for e in evidence if e.id == receipt_id), None)
    if receipt is None and requirement.delivery_expected and receipts:
        # 交付需求也接受 delivery:<fmt> 命名回执（适配器产出两种 id 形态）。
        receipt = next(
            (e for e in evidence
             if e.id.startswith("export_receipt:delivery:")
             and e.detail.startswith(_DELIVERY_DETAIL_PREFIX)
             and f"fmt={requirement.export_format}" in e.detail), None)
        if receipt is not None:
            receipt_id = receipt.id
    out.evidence_ids = [receipt_id]
    if receipt is None or receipt.status == EvidenceStatus.ABSENT:
        out.missing.append(R_EXPORT_ABSENT)
        out.next_action = ("run_delivery" if requirement.delivery_expected
                           else "export_product")
        return out
    if receipt.status == EvidenceStatus.FAILED:
        out.verdict = ReqState.FAILED
        out.failed_rule = R_RECEIPT_FAILED
        out.next_action = "retry_step"
        return out
    if receipt.status == EvidenceStatus.STALE:
        out.verdict = ReqState.PARTIAL
        out.failed_rule = R_EXPORT_STALE
        out.uncertainty.append(
            f"export revision {receipt.revision[:24]} != current")
        out.next_action = "reexport"
        return out
    # ── G28 A16：交付路径绑定 —— 数据交付需求不被地图出版回执背书，
    #    反之亦然（detail 前缀 = 生产写入者标记）。
    detail = receipt.detail or ""
    is_delivery = detail.startswith(_DELIVERY_DETAIL_PREFIX)
    if requirement.delivery_expected and not is_delivery:
        out.missing.append(R_DELIVERY_RECEIPT_ABSENT)
        out.next_action = "run_delivery"
        return out
    if not requirement.delivery_expected and is_delivery:
        out.missing.append(R_EXPORT_ABSENT)
        out.next_action = "export_product"
        return out
    if is_delivery and receipts is not None:
        cap_out = _delivery_caps_verdict(requirement, receipts, out,
                                         evidence=receipt)
        if cap_out is not None:
            return cap_out
    out.verdict = ReqState.FULFILLED
    return out


def _delivery_caps_verdict(
    requirement: GoalRequirement,
    receipts: Dict[str, Any],
    out: ReqVerdict,
    evidence: Optional[GoalEvidence] = None,
) -> Optional[ReqVerdict]:
    """交付回执语义闸（scope/身份/取消；None = 无码）。

    ``evidence``（G28 复审加固）：已绑定的证据行 —— 行选择以证据行 detail
    内的 ``fmt=<token>`` **终结匹配**为准（评审 P1：子串匹配被
    ``csv-forged`` 类前缀伪造绕过 exact-format 行选择 → 语义闸静默空转；
    现在证据行与 bundle 行身份不一致 → fail-closed artifact_unverified）。
    """
    rows_all = [r for r in _dict(receipts.get("families")).get("delivery", [])
                if isinstance(r, dict)]
    bound_fmt = ""
    if evidence is not None:
        for token in str(evidence.detail or "").split():
            if token.startswith("fmt="):
                bound_fmt = token[4:].strip().lower()
                break
    fmt_token = bound_fmt or requirement.export_format
    rows = [r for r in rows_all
            if str(r.get("format") or "").strip().lower() == fmt_token]
    if not rows:
        # 证据行在场但 bundle 无同身份行 —— 证据↔回执身份不可对账，
        # fail-closed（绝不静默落回 fulfilled）。
        out.missing.append(R_ARTIFACT_UNVERIFIED)
        out.verdict = ReqState.PARTIAL
        out.failed_rule = R_ARTIFACT_UNVERIFIED
        out.next_action = "reexport"
        out.uncertainty.append(
            f"delivery receipt identity unmatched (fmt={fmt_token[:24]})")
        return out
    caps: List[str] = []
    for row in rows[:2]:
        caps.extend(caps_for_receipt_row("delivery", row))
    conflicted = conflicted_artifact_names(rows_all)
    for row in rows[:2]:
        if str(row.get("filename") or "").strip() in conflicted:
            caps.append("artifact_checksum_mismatch")
    caps = list(dict.fromkeys(caps))[:6]
    hard = [c for c in caps if c in ("receipt_failed", "receipt_cancelled")]
    if hard:
        out.verdict = ReqState.FAILED
        out.failed_rule = _CAP_RULE_MAP[hard[0]]
        out.missing.append(f"receipt:{hard[0]}")
        out.next_action = _CAP_NEXT_ACTION.get(hard[0], "retry_step")
        return out
    if caps:
        rule = _CAP_RULE_MAP.get(caps[0])
        if rule is not None:
            out.verdict = ReqState.PARTIAL
            out.failed_rule = rule
            out.missing.append(f"receipt:{caps[0]}")
            out.next_action = _CAP_NEXT_ACTION.get(caps[0], "resolve_data")
            out.uncertainty.append(f"receipt cap: {caps[0]}")
            return out
    # 身份可验证性：completed 但无 content_sha256 → 不判 fulfilled（诚实
    # unknown —— 「文件生成了」≠「产物身份已核实」，A27 工件面）。
    if not any(str(r.get("content_sha256") or "").strip() for r in rows[:2]):
        out.missing.append(R_ARTIFACT_UNVERIFIED)
        out.verdict = ReqState.PARTIAL
        out.failed_rule = R_ARTIFACT_UNVERIFIED
        out.next_action = "reexport"
        return out
    return None


def _eval_requirement(
    requirement: GoalRequirement,
    evidence: List[GoalEvidence],
    chapter: Dict[str, Any],
    map_product: Optional[Dict[str, Any]],
    receipts: Optional[Dict[str, Any]] = None,
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
        return _eval_export_requirement(requirement, evidence, receipts)
    return _eval_row_requirement(
        requirement, evidence, chapter, map_product, receipts)


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
    # 钳制下界：至少 1 条 required fulfilled（threshold=0 不得把全
    # not_evaluated 判成 satisfied —— review P2-3 fail-open 枪）。
    effective = max(threshold, 1.0 / len(required))
    # 满足 = fulfilled 分数 ≥ 阈值 ∧ 无 BLOCKED（缺证据不计入分子 ——
    # not_evaluated 永远不算 fulfilled；默认阈值 1.0 = 全部 fulfilled）。
    if fulfilled / len(required) >= effective and not any(
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
    receipts: Optional[Dict[str, Any]] = None,
) -> Optional[GoalSatisfactionReport]:
    """评估入口（纯函数、零 IO、fail-closed）。

    ``receipts``（G28 P1 加性，默认 None = 旧调用零漂移）：finalizer 锁外
    预取的权威回执 bundle —— 评估只消费调用快照（迟到结果不改当前裁决，
    A20：下一次终验触发才见新回执）。

    返回 None = 章节/契约面不存在（调用方保持既有行为零漂移）；
    返回 report.verdict == not_evaluated = 契约在但证据不足（诚实三态）。
    """
    contract = resolve_goal_contract(chapter, user_goal=user_goal)
    if contract is None:
        return None
    chapter = chapter if isinstance(chapter, dict) else {}
    evidence = build_evidence_registry(
        chapter, map_product, cartographic_review, receipts=receipts)
    verdicts = [
        _eval_requirement(r, evidence, chapter, map_product, receipts)
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
