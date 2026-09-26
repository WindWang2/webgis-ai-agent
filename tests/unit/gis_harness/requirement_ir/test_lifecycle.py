"""F02 lifecycle 状态机单测。

覆盖：文档/条目两级迁移表、非法迁移 fail-closed、accept 门禁
（open blocking 歧义阻断）、supersede 终态、effective lifecycle 建议。
"""
from __future__ import annotations

import pytest

from app.services.gis_harness.intent import resolve_map_request_intent
from app.services.gis_harness.requirement_ir import (
    Ambiguity,
    LifecycleError,
    RequirementDocument,
    build_document,
    can_accept,
    supersede,
)
from app.services.gis_harness.requirement_ir.lifecycle import (
    DOCUMENT_TRANSITIONS,
    ITEM_TRANSITIONS,
    effective_document_lifecycle,
    transition_document,
    transition_item,
)


def _doc() -> RequirementDocument:
    return build_document("统计成都各区小学数量",
                          resolve_map_request_intent("统计成都各区小学数量"))


class TestDocumentTransitions:
    def test_draft_to_accepted(self):
        doc = _doc()
        assert transition_document(doc.lifecycle, "accepted") == "accepted"

    def test_draft_to_clarifying(self):
        assert transition_document("draft", "clarifying") == "clarifying"

    def test_illegal_superseded_escape(self):
        with pytest.raises(LifecycleError):
            transition_document("superseded", "draft")

    def test_accepted_reopens_by_user_edit(self):
        assert transition_document("accepted", "draft") == "draft"

    def test_table_is_total(self):
        for state in ("draft", "clarifying", "accepted", "superseded"):
            assert state in DOCUMENT_TRANSITIONS


class TestItemTransitions:
    def test_proposed_flow(self):
        assert transition_item("proposed", "clarified") == "clarified"
        assert transition_item("clarified", "accepted") == "accepted"

    def test_accepted_is_stable(self):
        with pytest.raises(LifecycleError):
            transition_item("accepted", "proposed")

    def test_rejected_terminal(self):
        with pytest.raises(LifecycleError):
            transition_item("rejected", "accepted")

    def test_table_is_total(self):
        for state in ("proposed", "clarified", "accepted", "superseded", "rejected"):
            assert state in ITEM_TRANSITIONS


class TestAcceptGate:
    def test_open_blocking_ambiguity_blocks_accept(self):
        doc = _doc()
        intent = doc.intent.model_copy(update={
            "ambiguities": [Ambiguity(
                code="measure_missing_for_statistics", path="measures",
                context_key="k1", blocking=True, state="open")]})
        blocked = doc.model_copy(update={"intent": intent})
        ok, codes = can_accept(blocked)
        assert not ok and "measure_missing_for_statistics" in codes

    def test_waived_ambiguity_allows_accept(self):
        doc = _doc()
        intent = doc.intent.model_copy(update={
            "ambiguities": [Ambiguity(
                code="measure_missing_for_statistics", path="measures",
                context_key="k1", blocking=True, state="waived")]})
        ok, _ = can_accept(doc.model_copy(update={"intent": intent}))
        assert ok

    def test_effective_lifecycle_suggests_clarifying(self):
        doc = _doc()
        intent = doc.intent.model_copy(update={
            "ambiguities": [Ambiguity(
                code="aoi_unresolved", path="aoi.name",
                context_key="k2", blocking=True, state="open")]})
        assert effective_document_lifecycle(
            doc.model_copy(update={"intent": intent})) == "clarifying"


class TestSupersede:
    def test_supersede_marks_terminal(self):
        doc = _doc()
        old = supersede(doc, "req-successor")
        assert old.lifecycle == "superseded"
        assert old.superseded_by == "req-successor"
        assert doc.lifecycle == "draft"   # 入参不被修改（纯函数）

    def test_supersede_idempotent(self):
        doc = _doc()
        once = supersede(doc, "s1")
        assert supersede(once, "s2") is once
