"""Requirement lifecycle 状态机（F02 DoD #3：patch 可回放、可 diff、可归因）。

两级状态：

- 文档级 ``DocumentLifecycle``：draft → clarifying → accepted → superseded；
- 条目级 ``SlotState``：proposed → clarified → accepted；superseded/rejected
  终态（rejected 仅用户显式否决）。

全部纯函数；非法迁移抛 :class:`LifecycleError`（fail-closed，不静默改写）。
"""
from __future__ import annotations

from typing import Optional

from app.services.gis_harness.requirement_ir.contracts import (
    DocumentLifecycle,
    RequirementDocument,
    SlotState,
)

DOCUMENT_TRANSITIONS: dict[DocumentLifecycle, tuple[DocumentLifecycle, ...]] = {
    "draft": ("draft", "clarifying", "accepted", "superseded"),
    "clarifying": ("clarifying", "draft", "accepted", "superseded"),
    "accepted": ("accepted", "draft", "superseded"),   # patch 重新打开 → draft
    "superseded": (),                                   # 终态
}

ITEM_TRANSITIONS: dict[SlotState, tuple[SlotState, ...]] = {
    "proposed": ("proposed", "clarified", "accepted", "superseded", "rejected"),
    "clarified": ("clarified", "accepted", "superseded", "rejected"),
    "accepted": ("accepted", "superseded"),            # 已确认事实只能被超替，不被改写
    "superseded": (),
    "rejected": (),
}


class LifecycleError(ValueError):
    """非法 lifecycle 迁移（fail-closed）。"""


def transition_document(current: DocumentLifecycle, target: DocumentLifecycle) -> DocumentLifecycle:
    if target not in DOCUMENT_TRANSITIONS.get(current, ()):
        raise LifecycleError(
            f"document lifecycle {current!r} -> {target!r} 非法"
            f"（合法：{DOCUMENT_TRANSITIONS.get(current, ())}）")
    return target


def transition_item(current: SlotState, target: SlotState) -> SlotState:
    if target not in ITEM_TRANSITIONS.get(current, ()):
        raise LifecycleError(
            f"item state {current!r} -> {target!r} 非法"
            f"（合法：{ITEM_TRANSITIONS.get(current, ())}）")
    return target


def blocking_ambiguities(doc: RequirementDocument):
    """open 且 blocking 的歧义（accepted 门禁输入）。"""
    return [a for a in doc.intent.ambiguities if a.state == "open" and a.blocking]


def can_accept(doc: RequirementDocument) -> tuple[bool, str]:
    """accept 门禁：无 open blocking 歧义才可 accepted（正确性优先）。"""
    blockers = blocking_ambiguities(doc)
    if blockers:
        return (False, ",".join(sorted({a.code for a in blockers}))[:96])
    return (True, "")


def effective_document_lifecycle(doc: RequirementDocument) -> DocumentLifecycle:
    """建议 lifecycle：有 open blocking 歧义 → clarifying；否则保持现值。"""
    if doc.lifecycle in ("superseded",):
        return doc.lifecycle
    if blocking_ambiguities(doc):
        return "clarifying"
    return doc.lifecycle


def supersede(old: RequirementDocument, new_document_id: str) -> RequirementDocument:
    """文档超替：旧文档入 superseded 终态并记录后继（user locks 由 build 层携带）。"""
    if old.lifecycle == "superseded":
        return old
    return old.model_copy(update={
        "lifecycle": "superseded",
        "superseded_by": new_document_id[:64],
    })


def item_state_after_patch(current: SlotState) -> SlotState:
    """patch 后条目状态：accepted/clarified 是已确认事实，不被系统 patch 降级；
    仅 proposed 随澄清/接受流程推进（由 clarify/accept 流程显式迁移）。"""
    if current in ("accepted", "clarified", "superseded", "rejected"):
        return current
    return "proposed"


def resolve_document_lifecycle(
    doc: RequirementDocument,
    *,
    target: Optional[DocumentLifecycle] = None,
) -> DocumentLifecycle:
    """统一迁移入口：显式 target 优先；否则 effective 建议。非法迁移 fail-closed。"""
    effective = target or effective_document_lifecycle(doc)
    return transition_document(doc.lifecycle, effective)
