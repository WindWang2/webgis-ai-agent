"""Evidence Registry（ADR-0183 G2）—— 既有真相源 → 有界证据投影。

红线：**只读投影，零持久化、零计算重复**。每条证据绑定一个既有事实源
（章节行 / map_product 块 / workflow 契约 / 观察状态 / 导出回执 /
user-wins 披露），带 ``source / revision / confidence / evidence_class``；
随时可从同输入重建（与 SpatialGoalGraph / analysis_graph 同不变式）。

分级语义（fail-closed，evaluator 强制）：

- ``deterministic``：行状态、契约状态、产品裁决、回执 —— 可背书 PASS；
- ``structural``：spec 级在场性（组件/图层槽位）—— 可背书 PASS；
- ``visual``：L5 visual judge —— 只降级，不得单独背书；
- ``assisted``：LLM 语义辅助 —— 只补注，不得把缺证据判 PASS。

产品侧证据一次读全：``map_product`` 块在场时从块上取 verdict / findings /
task_complete（与 completion 管线同源，不重算颜色/布局/标注 —— G7）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .contracts import (
    MAX_EVIDENCE,
    EvidenceClass,
    EvidenceKind,
    EvidenceStatus,
    GoalEvidence,
)

#: 数据族阻断码（与 completion/contracts._DATA_BLOCK_CODES 同词表；
#: 平铺避免跨包私有 import —— parity 由测试锁定）。
_DATA_BLOCK_CODES = frozenset({
    "artifact_missing", "artifact_expired", "empty_result",
    "execution_blocked", "source_missing", "render_source_missing",
})

_READY_VERDICTS = ("READY", "READY_WITH_WARNINGS")


def _dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _rows(value: Any) -> List[Dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [r for r in value if isinstance(r, dict)]


def _row_status_evidence(
    rows: List[Dict[str, Any]],
    *,
    source: str,
    prefix: str,
) -> List[GoalEvidence]:
    """章节能力行 → 工具回执证据（deterministic；status 同名直映）。"""
    out: List[GoalEvidence] = []
    for row in rows[:32]:
        cap = str(row.get("capability") or "").strip()
        if not cap:
            continue
        status = str(row.get("status") or "pending").lower()
        evidence_status = {
            "complete": EvidenceStatus.PRESENT,
            "done": EvidenceStatus.PRESENT,
            "available": EvidenceStatus.PRESENT,
            "failed": EvidenceStatus.FAILED,
            "unavailable": EvidenceStatus.ABSENT,
            "voided": EvidenceStatus.ABSENT,
        }.get(status, EvidenceStatus.ABSENT)  # pending/skipped/未知 → absent
        out.append(GoalEvidence(
            id=f"{prefix}:{cap}"[:64],
            kind=EvidenceKind.TOOL_RECEIPT,
            evidence_class=EvidenceClass.DETERMINISTIC,
            status=evidence_status,
            source=source,
            revision=str(row.get("bound_ref") or "")[:64],
            confidence=1.0,
            detail=str(row.get("resolved_algorithm") or "")[:64],
        ))
    return out


def _cartography_review_evidence(
    cartographic_review: Optional[Dict[str, Any]],
) -> List[GoalEvidence]:
    """`_cartographic_review`（review_cartography 确定性 checks）→ 证据。

    只消费既有检查行（G7：不重算颜色/布局/标注）；blocking fail → FAILED。
    L5 visual judge 证据在 finalize 面不持久化（recon 附录 A）——缺席即
    诚实缺席，本投影绝不虚构 visual 证据行。
    """
    review = _dict(cartographic_review)
    checks = _rows(review.get("checks"))
    if not checks and "checks" not in review:
        return []
    blocking = sorted({
        str(c.get("rule")) for c in checks
        if c.get("rule") and c.get("status") == "fail"
    })
    return [GoalEvidence(
        id="cartography_review", kind=EvidenceKind.CARTOGRAPHY,
        evidence_class=EvidenceClass.DETERMINISTIC,
        status=EvidenceStatus.FAILED if blocking else EvidenceStatus.PRESENT,
        source="map_state:_cartographic_review:checks",
        revision=str(review.get("final_fingerprint") or "")[:64],
        detail=",".join(blocking[:4])[:160],
    )]


def _map_product_evidence(
    map_product: Optional[Dict[str, Any]],
) -> List[GoalEvidence]:
    """map_product 块 → 产品/质量/观察证据（只消费，不重算 —— G7）。"""
    block = _dict(map_product)
    if not block:
        return []
    out: List[GoalEvidence] = []
    verdict = block.get("product_verdict")
    verdict_token = str(
        verdict.get("verdict") if isinstance(verdict, dict) else verdict or ""
    )
    revision = str(block.get("checked_revision") or "")
    out.append(GoalEvidence(
        id="product_verdict", kind=EvidenceKind.MAP_PRODUCT,
        evidence_class=EvidenceClass.DETERMINISTIC,
        status=EvidenceStatus.PRESENT if verdict_token else EvidenceStatus.ABSENT,
        source="map_product:product_verdict", revision=revision,
        detail=verdict_token[:48],
    ))
    final_status = str(block.get("final_map_status") or "")
    if final_status:
        out.append(GoalEvidence(
            id="final_map", kind=EvidenceKind.SPATIAL_VALIDATION,
            evidence_class=EvidenceClass.DETERMINISTIC,
            status=(
                EvidenceStatus.STALE
                if final_status == "failed"
                else EvidenceStatus.PRESENT
            ),
            source="map_product:final_map_status", revision=revision,
            detail=final_status[:32],
        ))
    render_status = str(block.get("render_status") or "")
    if render_status and render_status not in ("unknown",):
        out.append(GoalEvidence(
            id="render_observation", kind=EvidenceKind.RENDER_OBSERVATION,
            evidence_class=EvidenceClass.DETERMINISTIC,
            status=(
                EvidenceStatus.PRESENT if render_status == "verified"
                else EvidenceStatus.STALE if render_status == "stale"
                else EvidenceStatus.ABSENT
            ),
            source="map_product:render_status", revision=revision,
            detail=render_status[:32],
        ))
    # V11 cartographic review 摘要（derive_product_verdict 挂的 additive
    # 键 ``cartography`` —— 真实终验块上携带；只消费，不重算 —— G7）。
    cartography = _dict(block.get("cartography"))
    if cartography and ("blocking_rules" in cartography
                        or "checks" in cartography
                        or "status" in cartography):
        blocking = [str(r) for r in (cartography.get("blocking_rules") or [])]
        out.append(GoalEvidence(
            id="cartography_review", kind=EvidenceKind.CARTOGRAPHY,
            evidence_class=EvidenceClass.DETERMINISTIC,
            status=(
                EvidenceStatus.FAILED if blocking else EvidenceStatus.PRESENT
            ),
            source="map_product:cartography", revision=revision,
            detail=",".join(blocking[:4])[:160],
        ))
    # user-wins 披露（intent_acceptance 的 disclosures 投影）。
    disclosures = [
        str(d) for d in (_dict(block.get("intent_acceptance"))
                         .get("disclosures") or [])
    ]
    for i, d in enumerate(disclosures[:4]):
        out.append(GoalEvidence(
            id=f"user_override:{i}", kind=EvidenceKind.USER_OVERRIDE,
            evidence_class=EvidenceClass.DETERMINISTIC,
            status=EvidenceStatus.PRESENT,
            source="map_product:intent_acceptance:disclosures",
            revision=revision, confidence=1.0, detail=d[:160],
        ))
    return out


def _export_receipt_evidence(
    chapter: Dict[str, Any],
    checked_revision: str,
) -> List[GoalEvidence]:
    """导出回执 → 证据（契约键 ``chapter["export_receipts"]``：
    [{format, revision?, created_at?}]，additive；缺席 = 无回执 = absent）。

    revision 在场且 ≠ 当前 checked_revision → stale（旧代回执不得背书
    当前导出需求 —— G6 反作弊锚）。
    """
    out: List[GoalEvidence] = []
    for i, rcpt in enumerate(_rows(chapter.get("export_receipts"))[:8]):
        fmt = str(rcpt.get("format") or "").strip().lower()
        if not fmt:
            continue
        receipt_rev = str(rcpt.get("revision") or "")
        status = EvidenceStatus.PRESENT
        if receipt_rev and checked_revision and receipt_rev != checked_revision:
            status = EvidenceStatus.STALE
        out.append(GoalEvidence(
            id=f"export_receipt:{fmt}"[:64],
            kind=EvidenceKind.EXPORT_RECEIPT,
            evidence_class=EvidenceClass.DETERMINISTIC,
            status=status,
            source="chapter:export_receipts",
            revision=receipt_rev[:64],
            detail=f"format={fmt}",
        ))
    return out


def build_evidence_registry(
    chapter: Optional[Dict[str, Any]],
    map_product: Optional[Dict[str, Any]] = None,
    cartographic_review: Optional[Dict[str, Any]] = None,
) -> List[GoalEvidence]:
    """章节 + 产品块 + 制图评审 → 有界证据清单（纯函数；同输入同清单）。"""
    if not isinstance(chapter, dict) or not chapter:
        return []
    block = _dict(map_product)
    checked_revision = str(block.get("checked_revision") or "")
    evidence: List[GoalEvidence] = []
    evidence.extend(_row_status_evidence(
        _rows(chapter.get("analysis_steps")),
        source="chapter:analysis_steps", prefix="row",
    ))
    evidence.extend(_row_status_evidence(
        _rows(chapter.get("data_requirements")),
        source="chapter:data_requirements", prefix="data",
    ))
    evidence.extend(_map_product_evidence(map_product))
    evidence.extend(_cartography_review_evidence(cartographic_review))
    evidence.extend(_export_receipt_evidence(chapter, checked_revision))
    # workflow 契约角色资格（bound/blocked/external → 数据充分性证据）。
    contract = _dict(chapter.get("workflow_contract"))
    for role in _rows(contract.get("roles"))[:8]:
        role_name = str(role.get("role") or "")[:32]
        status = str(role.get("status") or "")
        if not role_name or not status:
            continue
        evidence.append(GoalEvidence(
            id=f"qualify:{role_name}"[:64],
            kind=EvidenceKind.DATA_QUALIFICATION,
            evidence_class=EvidenceClass.DETERMINISTIC,
            status=(
                EvidenceStatus.PRESENT if status == "bound"
                else EvidenceStatus.ABSENT
            ),
            source="chapter:workflow_contract:roles",
            detail=f"role={role_name} status={status}"[:160],
        ))
    return evidence[:MAX_EVIDENCE]


def data_family_blockers(
    chapter: Dict[str, Any],
    map_product: Optional[Dict[str, Any]] = None,
) -> List[str]:
    """数据充分性阻断码合集（workflow 契约 + 产品裁决，去重有界）。

    产品裁决 token 本身是数据族证据：``BLOCKED_BY_DATA`` 即便 reasons
    缺席（合成块/旧块）也构成阻断 —— 数据欠账不得被裁决词表外的事实
    稀释（G6 反作弊锚）。
    """
    codes: List[str] = []
    contract = _dict(_dict(chapter).get("workflow_contract"))
    codes.extend(str(b) for b in (contract.get("data_blockers") or []))
    verdict = _dict(map_product).get("product_verdict")
    if isinstance(verdict, dict):
        for reason in (verdict.get("reasons") or []):
            code = str(reason)
            if code in _DATA_BLOCK_CODES:
                codes.append(code)
        if str(verdict.get("verdict") or "") == "BLOCKED_BY_DATA":
            codes.append("blocked_by_data")
    else:
        if str(verdict or "") == "BLOCKED_BY_DATA":
            codes.append("blocked_by_data")
    return list(dict.fromkeys(codes))[:6]


__all__ = [
    "build_evidence_registry",
    "data_family_blockers",
]
