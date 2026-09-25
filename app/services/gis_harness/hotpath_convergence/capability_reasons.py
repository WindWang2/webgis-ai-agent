"""Canonical capability reason codes（F06, ADR-0215 候选）.

dispatch bind 的拒绝/允许证据需要 **稳定、不泄密** 的结构化 reason codes。
qualification_v8 的 ``QualificationReason.check`` 已是稳定词表，但 evidence
与 denial decision record 需要一个 canonical 投影：check → reason code 的
确定性映射 + 有界投影，杜绝自由文本进证据面。

形状与 ``decision_record.reason_code``（check/observed/expected/hint）一致
—— 本模块只做「qualification 结论 → canonical codes」的纯投影，不建第二
裁决体系。
"""
from __future__ import annotations

from typing import Any, Dict, List

from app.services.gis_harness.qualification_v8 import (
    QualificationReason,
    QualificationResult,
)

#: qualification check → canonical reason code（封闭映射；新 check 落地时
#: 在此登记 —— 未登记 check 投影为 ``unmapped:<check>``，绝不静默丢失）。
REASON_CODES: Dict[str, str] = {
    "offline_network_required": "offline_network_required",
    "confirm_required": "confirm_required",
    "auth_tier": "auth_tier_insufficient",
    "auth_tier_insufficient": "auth_tier_insufficient",
    "budget": "budget_exceeded",
    "budget_exceeded": "budget_exceeded",
    "credentials": "credentials_missing",
    "credentials_missing": "credentials_missing",
    "required_permission": "permission_not_granted",
    "permission_not_granted": "permission_not_granted",
    "dependency": "dependency_unavailable",
    "dependency_unavailable": "dependency_unavailable",
    "latency": "latency_constraint",
    "latency_constraint": "latency_constraint",
    "owner_scope": "owner_scope_mismatch",
    "owner_scope_mismatch": "owner_scope_mismatch",
    "min_features": "min_features",
    "quality_gate": "quality_gate_blocked",
    "quality_gate_blocked": "quality_gate_blocked",
    "raster_bands": "raster_bands",
    "raster_bands_unknown": "raster_bands_unknown",
    "resolution": "resolution",
    "resolution_unknown": "resolution_unknown",
    "temporal_inputs": "temporal_inputs",
    "gpu": "gpu_unavailable",
    "data_volume": "data_volume",
    "geometry": "geometry_mismatch",
    "crs": "crs_mismatch",
    "field": "field_missing",
}

#: 证据/决策面的 codes 上限（与 decision_record._MAX_REASON_CODES 对齐）。
MAX_REASON_CODES = 6

_STR_MAX = 96


def canonical_reason_code(check: str) -> str:
    """check → canonical code（未登记 check → ``unmapped:<check>``）。"""
    key = str(check or "").strip()[:48]
    if not key:
        return "unspecified"
    return REASON_CODES.get(key, f"unmapped:{key[:40]}")


def _bound(value: Any, limit: int = _STR_MAX) -> str:
    return str(value if value is not None else "")[:limit]


def reason_codes_from_qualification(
    qualification: Any,
    *,
    limit: int = MAX_REASON_CODES,
) -> List[Dict[str, str]]:
    """QualificationResult/dict → canonical reason codes（确定性、有界）。

    接受 QualificationResult（.reasons）或已投影 dict（reasons 列表）。
    条目形状与 ``decision_record.reason_code`` 一致；sorted 保证同输入同序。
    """
    if qualification is None:
        return []
    reasons: List[Any] = []
    if isinstance(qualification, QualificationResult):
        reasons = list(qualification.reasons or [])
    elif isinstance(qualification, dict):
        raw = qualification.get("reasons") or []
        if isinstance(raw, list):
            reasons = [r for r in raw if isinstance(r, dict)]
    else:
        reasons = list(getattr(qualification, "reasons", None) or [])

    entries: List[Dict[str, str]] = []
    for reason in reasons:
        if isinstance(reason, QualificationReason):
            entries.append({
                "check": canonical_reason_code(reason.check),
                "observed": _bound(reason.observed),
                "expected": _bound(reason.expected),
                "hint": _bound(reason.hint) if reason.hint else "",
            })
        elif isinstance(reason, dict):
            entries.append({
                "check": canonical_reason_code(str(reason.get("check", ""))),
                "observed": _bound(reason.get("observed")),
                "expected": _bound(reason.get("expected")),
                "hint": _bound(reason.get("hint")) or "",
            })
        else:
            entries.append({
                "check": canonical_reason_code(str(reason)),
                "observed": "", "expected": "", "hint": "",
            })
    entries.sort(key=lambda e: (e["check"], e["observed"], e["expected"]))
    return [{k: v for k, v in e.items() if v} for e in entries[:max(1, limit)]]


__all__ = [
    "REASON_CODES",
    "MAX_REASON_CODES",
    "canonical_reason_code",
    "reason_codes_from_qualification",
]
