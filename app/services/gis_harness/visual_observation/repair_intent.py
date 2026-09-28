"""VisualRepairIntent codec（C13）：finding → 修复意图的 typed 编解码。

**为什么不在 intent_codec.body_to_intent 里**（F15 既定决策的延续）：
body codec 是 HTTP mutation 直提通道的单一映射源；把 heal intent 加进去
等于在 approval 结构门槛之外开第二个 apply 入口。本 codec 服务的是
plan / apply / 决策账本 / 复验之间的**持久化与传输**面 ——
``intent_to_dict`` / ``intent_from_dict`` round-trip 机器锁定，
``intent_fingerprint`` 是账本幂等键（同一意图重复入账可识别）。

有界：defects 载荷是 healer ``dataclass_dict`` 的固定 8 键形状（上游已
clip），ids/labels 各 ≤12/≤8 条。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, Optional, Tuple

SCHEMA_VERSION = "v1"
INTENT_KIND = "apply_visual_heal_patch"

_MAX_FINDING_IDS = 12
_MAX_OP_LABELS = 8
_DEFECT_KEYS = frozenset({
    "category", "severity", "layer_ids", "occluder_layer_id",
    "dimension", "evidence", "min_contrast_ratio", "suggested_operation",
})


@dataclass(frozen=True)
class VisualRepairIntent:
    """一条可执行的视觉修复意图（approval 门槛在 apply 面，不在本对象）。"""

    proposal_id: str = ""
    defect_fingerprint: str = ""
    base_revision: int = 0
    approval_class: str = ""          # auto_safe | needs_approval
    origin: str = ""                  # user | system
    finding_ids: Tuple[str, ...] = ()
    defects: Tuple[Dict[str, Any], ...] = field(default_factory=tuple)
    op_labels: Tuple[str, ...] = ()
    schema_version: str = SCHEMA_VERSION
    intent_kind: str = INTENT_KIND


def _clip(value: Any, limit: int) -> str:
    return str(value or "")[:limit]


def _norm_defects(defects: Iterable[Any]) -> Tuple[Dict[str, Any], ...]:
    from app.services.mapspec.visual_healer import dataclass_dict

    out = []
    for d in tuple(defects)[:_MAX_FINDING_IDS]:
        try:
            payload = dataclass_dict(d) if not isinstance(d, dict) else d
        except Exception:  # noqa: BLE001 — 形状漂移条目诚实跳过
            continue
        if isinstance(payload, dict) and "category" in payload:
            out.append({
                k: payload.get(k) for k in _DEFECT_KEYS
            })
    return tuple(out)


def compile_intent(
    *,
    proposal_id: str,
    base_revision: int,
    approval_class: str,
    origin: str,
    defects: Iterable[Any],
    finding_ids: Iterable[str] = (),
    op_labels: Iterable[str] = (),
    defect_fingerprint: str = "",
) -> VisualRepairIntent:
    """plan/自动通道的统一编译点（defects 接受对象或已编码 dict）。"""
    return VisualRepairIntent(
        proposal_id=_clip(proposal_id, 96),
        defect_fingerprint=_clip(defect_fingerprint, 96),
        base_revision=int(base_revision or 0),
        approval_class=_clip(approval_class, 24),
        origin=_clip(origin, 12),
        finding_ids=tuple(_clip(x, 96) for x in tuple(finding_ids)[:_MAX_FINDING_IDS]),
        defects=_norm_defects(defects),
        op_labels=tuple(_clip(x, 40) for x in tuple(op_labels)[:_MAX_OP_LABELS]),
    )


def intent_fingerprint(intent: VisualRepairIntent) -> str:
    """稳定指纹（canonical JSON；账本幂等键 + round-trip 不变量）。"""
    payload = json.dumps(
        intent_to_dict(intent), ensure_ascii=False, sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"vrint-sha256:{hashlib.sha256(payload).hexdigest()}"


def intent_to_dict(intent: VisualRepairIntent) -> Dict[str, Any]:
    return {
        "schema_version": intent.schema_version,
        "intent_kind": intent.intent_kind,
        "proposal_id": intent.proposal_id,
        "defect_fingerprint": intent.defect_fingerprint,
        "base_revision": int(intent.base_revision),
        "approval_class": intent.approval_class,
        "origin": intent.origin,
        "finding_ids": list(intent.finding_ids),
        "defects": [dict(d) for d in intent.defects],
        "op_labels": list(intent.op_labels),
    }


def intent_from_dict(raw: Any) -> Optional[VisualRepairIntent]:
    """dict → 意图（形状漂移诚实 None —— 不猜、不部分还原）。"""
    if not isinstance(raw, dict):
        return None
    if str(raw.get("intent_kind") or "") != INTENT_KIND:
        return None
    defects = raw.get("defects")
    if defects is None:
        defects = ()
    if not isinstance(defects, (list, tuple)):
        return None
    norm = []
    for d in tuple(defects)[:_MAX_FINDING_IDS]:
        if not isinstance(d, dict) or "category" not in d:
            return None
        norm.append({k: d.get(k) for k in _DEFECT_KEYS})
    try:
        base_revision = int(raw.get("base_revision") or 0)
    except (TypeError, ValueError):
        return None
    return VisualRepairIntent(
        proposal_id=_clip(raw.get("proposal_id"), 96),
        defect_fingerprint=_clip(raw.get("defect_fingerprint"), 96),
        base_revision=base_revision,
        approval_class=_clip(raw.get("approval_class"), 24),
        origin=_clip(raw.get("origin"), 12),
        finding_ids=tuple(
            _clip(x, 96) for x in tuple(raw.get("finding_ids") or ())[:_MAX_FINDING_IDS]),
        defects=tuple(norm),
        op_labels=tuple(
            _clip(x, 40) for x in tuple(raw.get("op_labels") or ())[:_MAX_OP_LABELS]),
        schema_version=_clip(raw.get("schema_version"), 8) or SCHEMA_VERSION,
        intent_kind=INTENT_KIND,
    )


__all__ = [
    "SCHEMA_VERSION",
    "INTENT_KIND",
    "VisualRepairIntent",
    "compile_intent",
    "intent_fingerprint",
    "intent_to_dict",
    "intent_from_dict",
]
