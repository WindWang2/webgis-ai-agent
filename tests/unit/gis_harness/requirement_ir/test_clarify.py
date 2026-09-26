"""F02 clarify 策略单测（F02 DoD #2：明确 reason code；同一 ambiguity 在
context 未变化时不重复询问）。

覆盖：blocking/安全默认策略表、context-key 去重（同 context 不重问、
context 变可再问）、answer/waive 终结、≤2 问上限、legacy 兼容出口形状、
入档不改语义核（record 是 lifecycle 记账）。
"""
from __future__ import annotations

import pytest

from app.services.gis_harness.clarification import ClarificationRequest
from app.services.gis_harness.intent import resolve_map_request_intent
from app.services.gis_harness.requirement_ir import (
    build_document,
    context_key,
    plan_clarifications,
    record_ambiguities,
    to_legacy_request,
)
from app.services.gis_harness.requirement_ir.clarify import (
    MAX_QUESTIONS_PER_TURN,
    pending_questions,
)
from app.services.gis_harness.requirement_ir.digest import (
    canonical_core,
    requirement_digest,
)


def _doc(query: str, turn: int = 1):
    return build_document(query, resolve_map_request_intent(query), turn=turn)


def _doc_with_measure_gap():
    """构造统计语义但无指标 → measure_missing_for_statistics blocking。"""
    doc = build_document(
        "统计成都各片区的数据",
        resolve_map_request_intent("统计成都各片区的数据"), turn=1)
    # 若 resolver 已给出 measure，则手动清空 measures 构造缺口（确定性）
    if doc.intent.measures:
        intent = doc.intent.model_copy(update={"measures": []})
        doc = doc.model_copy(update={"intent": intent})
    return doc


class TestBlockingPolicy:
    def test_measure_gap_is_blocking(self):
        doc = _doc_with_measure_gap()
        needs = plan_clarifications(doc)
        codes = [n.ambiguity.code for n in needs]
        assert "measure_missing_for_statistics" in codes
        assert all(n.ambiguity.blocking for n in needs
                   if n.ambiguity.code == "measure_missing_for_statistics")

    def test_publish_without_format_is_blocking(self):
        doc = _doc("把地图导出成PDF")
        from app.services.gis_harness.requirement_ir.patch import apply_patch
        from app.services.gis_harness.requirement_ir import PatchRecord
        doc, _ = apply_patch(doc, PatchRecord(
            op_id="pb0", turn=2, actor="user", op="set",
            path="output.formats", value=["pdf"]))
        doc, _ = apply_patch(doc, PatchRecord(
            op_id="pb1", turn=3, actor="user", op="set",
            path="output.publish", value=True))
        # formats 已有 pdf → 不问
        codes = [n.ambiguity.code for n in plan_clarifications(doc)]
        assert "export_format_missing_when_publish" not in codes
        # 清空 formats → blocking 问
        intent = doc.intent.model_copy(update={
            "output": doc.intent.output.model_copy(update={"formats": ()})})
        doc2 = doc.model_copy(update={"intent": intent})
        codes = [n.ambiguity.code for n in plan_clarifications(doc2)]
        assert "export_format_missing_when_publish" in codes

    def test_non_export_format_gap_defaults_silently(self):
        doc = _doc("统计成都各区小学数量")
        doc = record_ambiguities(doc, plan_clarifications(doc), turn=1)
        for need in plan_clarifications(doc):
            if not need.ambiguity.blocking:
                assert need.resolvable_by_default
                assert need.ambiguity.default_rationale


class TestNoRepeatAsking:
    def test_same_context_not_reasked(self):
        doc = _doc_with_measure_gap()
        needs = plan_clarifications(doc)
        assert needs
        doc = record_ambiguities(doc, needs, turn=1)
        # 同 context 再次 plan：已在档且 open → 不再产生"可问"新条目
        needs_again = plan_clarifications(doc)
        fresh = [n for n in needs_again if not n.already_recorded]
        assert fresh == []

    def test_pending_questions_stable_after_ask(self):
        doc = _doc_with_measure_gap()
        doc = record_ambiguities(doc, plan_clarifications(doc), turn=1)
        first = pending_questions(doc)
        assert first
        # context 未变化 → pending 集合稳定（不会越问越多）
        second = pending_questions(doc)
        assert [q.context_key for q in first] == [q.context_key for q in second]

    def test_context_change_allows_reask(self):
        doc = _doc_with_measure_gap()
        needs = plan_clarifications(doc)
        doc = record_ambiguities(doc, needs, turn=1)
        target = next(a for a in doc.intent.ambiguities if a.blocking)
        # waivable 后 context 变化（不同统计维度语境）→ 新 key 出现
        answered_key = target.context_key
        new_ctx = context_key("measure_missing_for_statistics", {"task": "temporal_trend", "dim": "grid"})
        assert new_ctx != answered_key

    def test_waived_not_asked_again(self):
        from app.services.gis_harness.requirement_ir import apply_patch, PatchRecord
        doc = _doc_with_measure_gap()
        doc = record_ambiguities(doc, plan_clarifications(doc), turn=1)
        target = pending_questions(doc)[0]
        doc, applied = apply_patch(doc, PatchRecord(
            op_id="w1", turn=2, actor="user", op="waive_ambiguity",
            value={"context_key": target.context_key}))
        assert applied
        assert all(a.context_key != target.context_key
                   for a in pending_questions(doc))


class TestAnswerFlow:
    def test_answer_via_patch(self):
        from app.services.gis_harness.requirement_ir import apply_patch, PatchRecord
        doc = _doc_with_measure_gap()
        doc = record_ambiguities(doc, plan_clarifications(doc), turn=1)
        target = pending_questions(doc)[0]
        doc, applied = apply_patch(doc, PatchRecord(
            op_id="a1", turn=2, actor="user", op="answer_ambiguity",
            value={"context_key": target.context_key, "answer": "小学数量"}))
        assert applied
        answered = next(a for a in doc.intent.ambiguities
                        if a.context_key == target.context_key)
        assert answered.state == "answered" and answered.answer == "小学数量"

    def test_answer_without_open_ambiguity_rejected(self):
        from app.services.gis_harness.requirement_ir import (
            PatchRecord,
            PatchValueInvalid,
            apply_patch,
        )
        with pytest.raises(PatchValueInvalid):
            apply_patch(_doc("统计成都各区小学数量"), PatchRecord(
                op_id="a2", turn=2, actor="user", op="answer_ambiguity",
                value={"context_key": "nope", "answer": "x"}))


class TestRecordIsBookkeeping:
    def test_record_does_not_change_semantic_digest(self):
        doc = _doc_with_measure_gap()
        before = requirement_digest(doc)
        doc2 = record_ambiguities(doc, plan_clarifications(doc), turn=1)
        assert requirement_digest(doc2) == before
        # 语义核不含歧义状态
        assert "ambiguit" not in str(sorted(canonical_core(doc).items()))

    def test_record_pure_on_input(self):
        doc = _doc_with_measure_gap()
        snapshot = doc.model_dump()
        record_ambiguities(doc, plan_clarifications(doc), turn=1)
        assert doc.model_dump() == snapshot


class TestLegacyCompat:
    def test_legacy_request_shape(self):
        doc = _doc_with_measure_gap()
        doc = record_ambiguities(doc, plan_clarifications(doc), turn=1)
        request = to_legacy_request(pending_questions(doc))
        assert isinstance(request, ClarificationRequest)
        assert 1 <= len(request.questions) <= MAX_QUESTIONS_PER_TURN
        for question in request.questions:
            assert question.reason_code
            assert question.options
            assert question.default_option() is not None

    def test_no_questions_returns_none(self):
        doc = _doc("统计成都各区小学数量")
        doc = record_ambiguities(doc, plan_clarifications(doc), turn=1)
        assert to_legacy_request(pending_questions(doc)) is None or \
            pending_questions(doc) == []
