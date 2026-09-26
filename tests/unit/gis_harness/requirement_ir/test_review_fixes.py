"""Review 修复回归（review-gate P1/P2 盲区补测）。

覆盖：journal 折叠后的回放不变式（P1-1）、超替 user 字段值携带与
幽灵 provenance 防护（P1-2）、澄清答案回写寻址字段（P1-3）、locks
运行时上限。全部由 review 独立发现的实现行为反推，非 green-by-construction。
"""
from __future__ import annotations

import asyncio

import pytest

from app.services.gis_harness.requirement_ir import service
from app.services.gis_harness.requirement_ir.contracts import MAX_LOCKS
from app.services.gis_harness.requirement_ir.patch import (
    PatchValueInvalid,
    apply_patch,
)
from app.services.gis_harness.requirement_ir.contracts import PatchRecord
from app.services.gis_harness.requirement_ir.digest import document_digest


class MemStore:
    def __init__(self):
        self.data = {}

    async def get_map_state(self, sid):
        return self.data.get(sid, {})

    async def set_map_state(self, sid, key, value, seq=None):
        self.data.setdefault(sid, {})[key] = value
        return True


def run(coro):
    return asyncio.run(coro)


class TestJournalFoldingReplay:
    """P1-1：折叠后 replay(genesis, journal) 必须仍等于现网文档。"""

    def _patches(self, count, start_turn=2):
        return [
            service.TurnPatch(
                op="set", path="representation.palette",
                value="blue" if i % 2 == 0 else "red",
                turn=start_turn + i // 10, actor="user", op_id=f"fold-{i}")
            for i in range(count)
        ]

    def test_fold_keeps_replay_invariant(self):
        store = MemStore()
        run(service.ensure_document(
            store, "s1", "统计成都各区小学数量", turn=1))
        result = run(service.apply_user_patches(
            store, "s1", self._patches(205)))
        assert result["applied"] == 205
        state = run(service.load_state(store, "s1"))
        doc = service._doc_from(state.document)
        # journal 已折叠，现网 journal 低于上限
        assert len(doc.patches) < 205
        assert state.folded_patch_count > 0
        assert state.genesis["revision"] > 1   # genesis 已前滚
        # 回放不变式：折叠后依然成立（修复前此处抛 PatchStale）
        rebuilt = service.replay_document(state)
        assert document_digest(rebuilt) == document_digest(doc)

    def test_folded_op_id_is_idempotent(self):
        store = MemStore()
        run(service.ensure_document(
            store, "s1", "统计成都各区小学数量", turn=1))
        run(service.apply_user_patches(store, "s1", self._patches(205)))
        state = run(service.load_state(store, "s1"))
        doc = service._doc_from(state.document)
        revision_before = doc.revision
        # 重放一个已折叠出 journal 的旧 patch：幂等跳过，revision 不动
        old_patch = service.TurnPatch(
            op="set", path="representation.palette", value="blue",
            turn=2, actor="user", op_id="fold-0")
        result = run(service.apply_user_patches(store, "s1", [old_patch]))
        assert result["applied"] == 0
        doc_after = service._doc_from(
            run(service.load_state(store, "s1")).document)
        assert doc_after.revision == revision_before


class TestSupersedeCarriesUserValues:
    """P1-2：超替必须连值带 provenance 携带；幽灵 user-wins 禁止。"""

    def test_new_utterance_wins_over_carried_field(self):
        store = MemStore()
        run(service.ensure_document(
            store, "s1", "成都的小学分布情况，做一张图", turn=1))
        # 用户显式改了范围（无锁）
        run(service.apply_user_patches(store, "s1", [
            service.TurnPatch(op="set", path="aoi.name", value="天府新区",
                              turn=2, actor="user", op_id="u1"),
        ]))
        # 新任务点名北京 → 最新用户表达优先
        result = run(service.ensure_document(
            store, "s1", "统计北京各区医院数量", turn=3))
        assert result["superseded"] is True
        state = run(service.load_state(store, "s1"))
        doc = service._doc_from(state.document)
        assert doc.intent.aoi.name == "北京"
        # 幽灵 provenance 禁止：新值不是用户旧选择，不得带 user-wins 护栏
        assert not doc.intent.field_provenance.get("aoi.name", object()).is_user() \
            if "aoi.name" in doc.intent.field_provenance else True
        # agent 可修正该字段（修复前被幽灵 user-wins 冻结）
        from app.services.gis_harness.requirement_ir.contracts import PatchRecord
        doc2, applied = apply_patch(doc, PatchRecord(
            op_id="agent-fix", turn=4, actor="agent", op="set",
            path="aoi.name", value="上海"))
        assert applied

    def test_unexpressed_user_value_carries_with_value(self):
        store = MemStore()
        run(service.ensure_document(
            store, "s1", "成都的小学分布情况，做一张图", turn=1))
        run(service.apply_user_patches(store, "s1", [
            service.TurnPatch(op="set", path="time.range_start", value="2020",
                              turn=2, actor="user", op_id="u1"),
        ]))
        # 新任务完全不含时间词面 → 旧用户值连 provenance 一起携带
        run(service.ensure_document(
            store, "s1", "统计重庆各区医院数量", turn=3))
        state = run(service.load_state(store, "s1"))
        doc = service._doc_from(state.document)
        assert doc.intent.time.range_start == "2020"
        assert doc.intent.field_provenance["time.range_start"].is_user()


class TestClarificationWriteBack:
    """P1-3：澄清答案必须回写寻址字段。"""

    def _doc_with_aoi_gap(self):
        from app.services.gis_harness.intent import resolve_map_request_intent
        from app.services.gis_harness.requirement_ir import (
            build_document, plan_clarifications, record_ambiguities,
        )
        doc = build_document(
            "统计各片区的数据", resolve_map_request_intent("统计各片区的数据"),
            turn=1)
        if doc.intent.measures:
            doc = doc.model_copy(update={"intent": doc.intent.model_copy(
                update={"measures": []})})
        doc = record_ambiguities(doc, plan_clarifications(doc), 1)
        questions = [a for a in doc.intent.ambiguities
                     if a.state == "open" and a.blocking]
        assert questions, "应产生 open blocking 歧义"
        aoi_gap = next((a for a in questions if a.code == "aoi_unresolved"), None)
        assert aoi_gap is not None, "应产生 aoi_unresolved（范围缺失）"
        return doc, aoi_gap

    def test_answer_writes_addressed_field(self):
        doc, target = self._doc_with_aoi_gap()
        assert target.code == "aoi_unresolved"
        doc, applied = apply_patch(doc, PatchRecord(
            op_id="ans1", turn=2, actor="user", op="answer_ambiguity",
            value={"context_key": target.context_key, "answer": "青羊区"}))
        assert applied
        assert doc.intent.aoi.name == "青羊区"
        assert doc.intent.field_provenance["aoi.name"].is_user()
        assert doc.intent.core.scope.name == "青羊区"   # core 同步
        answered = next(a for a in doc.intent.ambiguities
                        if a.context_key == target.context_key)
        assert answered.state == "answered"

    def test_invalid_answer_leaves_state_untouched(self):
        doc, target = self._doc_with_aoi_gap()
        snapshot = doc.model_dump()
        with pytest.raises(PatchValueInvalid):
            apply_patch(doc, PatchRecord(
                op_id="ans2", turn=2, actor="user", op="answer_ambiguity",
                value={"context_key": target.context_key, "answer": ""}))
        assert doc.model_dump() == snapshot   # fail-closed，无部分状态


class TestLocksRuntimeCap:
    def test_add_lock_bounded(self):
        from app.services.gis_harness.intent import resolve_map_request_intent
        from app.services.gis_harness.requirement_ir import build_document
        doc = build_document(
            "统计成都各区小学数量",
            resolve_map_request_intent("统计成都各区小学数量"), turn=1)
        for i in range(MAX_LOCKS):
            doc, applied = apply_patch(doc, PatchRecord(
                op_id=f"lock-{i}", turn=2, actor="user", op="add_lock",
                value={"scope": "other", "value": f"item-{i}"}))
            assert applied
        with pytest.raises(PatchValueInvalid):
            apply_patch(doc, PatchRecord(
                op_id="lock-over", turn=2, actor="user", op="add_lock",
                value={"scope": "other", "value": "overflow"}))
