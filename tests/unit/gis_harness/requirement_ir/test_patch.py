"""F02 patch 协议单测（F02 DoD #3：可 replay、可 diff、可归因）。

覆盖：白名单路径/CAS/幂等/user-wins 硬约束/锁保护/回放不变式/diff/
归因/journal 有界折叠。全部确定性。
"""
from __future__ import annotations

import pytest

from app.services.gis_harness.intent import resolve_map_request_intent
from app.services.gis_harness.requirement_ir import (
    PatchConflict,
    PatchRecord,
    PatchStale,
    PatchUnknownPath,
    PatchValueInvalid,
    RequirementDocument,
    apply_patch,
    attribute_changes,
    build_document,
    diff_documents,
    replay,
)


def _doc(query: str = "统计成都各区小学数量", turn: int = 1) -> RequirementDocument:
    return build_document(query, resolve_map_request_intent(query), turn=turn)


def _patch(op: str = "set", path: str = "", value=None, *, actor: str = "user",
           op_id: str = "p1", turn: int = 2, expected_revision: int | None = None,
           reason: str = "") -> PatchRecord:
    return PatchRecord(
        op_id=op_id, turn=turn, actor=actor, op=op,  # type: ignore[arg-type]
        path=path, value=value, reason=reason, expected_revision=expected_revision)


class TestSetPatch:
    def test_set_palette(self):
        doc = _doc()
        new, applied = apply_patch(doc, _patch(
            path="representation.palette", value="blue", op_id="p1"))
        assert applied and new.revision == doc.revision + 1
        assert new.intent.representation.palette == "blue"
        # core 无 palette 对应面；user patch 的 provenance 落字段级账
        assert new.intent.field_provenance["representation.palette"].is_user()

    def test_set_syncs_core_group_by(self):
        doc = _doc("成都的小学分布情况，做一张图")
        assert doc.intent.core.group_by == "district"   # resolver 默认
        new, _ = apply_patch(doc, _patch(
            path="statistics.group_by", value="grid", op_id="g1"))
        assert new.intent.statistics.group_by == "grid"
        assert new.intent.core.group_by == "grid"

    def test_set_syncs_core_normalization(self):
        """反证（review P2）：statistics.normalization 的 patch 必须同步
        core.comparison（derive_sections 由 core.comparison 派生 normalization）
        —— 不同步则 patch 过的口径在下次从 core 重派生时静默回退，
        intent_view 外泄陈旧 core。"""
        doc = _doc("统计成都各区小学数量")
        assert doc.intent.core.comparison == ""
        new, applied = apply_patch(doc, _patch(
            path="statistics.normalization", value="per_capita", op_id="n1"))
        assert applied
        assert new.intent.statistics.normalization == "per_capita"
        assert new.intent.core.comparison == "per_capita"
        # 重派生口径一致（intent_view 不再陈旧）
        from app.services.gis_harness.requirement_ir.projection import intent_view
        from app.services.gis_harness.requirement_ir.normalize import (
            normalize_normalization,
        )
        assert normalize_normalization(
            intent_view(new).comparison) == "per_capita"

    def test_remove_last_measure_clears_core_measure(self):
        """反证（review P2）：删空 measures 后 core.measure 必须跟着清 ——
        否则 intent_view 外泄陈旧度量、重派生时复活已删除的 measure。"""
        doc = _doc("统计成都各区小学的平均身高")
        assert doc.intent.measures and doc.intent.core.measure
        mid = doc.intent.measures[0].id
        new, applied = apply_patch(doc, _patch(
            op="remove_measure", value={"id": mid}, op_id="rm1"))
        assert applied and new.intent.measures == []
        assert new.intent.core.measure == ""

    def test_set_syncs_core_scope(self):
        doc = _doc()
        new, _ = apply_patch(doc, _patch(
            path="aoi.name", value="重庆", op_id="a1"))
        assert new.intent.aoi.name == "重庆"
        assert new.intent.core.scope.name == "重庆"

    def test_task_type_vocab_guard(self):
        doc = _doc()
        with pytest.raises(PatchValueInvalid):
            apply_patch(doc, _patch(
                path="task.task_type", value="not_a_task", op_id="t1"))

    def test_unknown_path_rejected(self):
        with pytest.raises(PatchUnknownPath):
            apply_patch(_doc(), _patch(path="freeform.text", value="x"))

    def test_format_whitelist(self):
        with pytest.raises(PatchValueInvalid):
            apply_patch(_doc(), _patch(path="output.formats", value=["docx"]))


class TestCasAndIdempotency:
    def test_stale_revision_rejected(self):
        doc = _doc()
        with pytest.raises(PatchStale):
            apply_patch(doc, _patch(
                path="representation.palette", value="blue",
                expected_revision=doc.revision + 5))

    def test_duplicate_op_id_noop(self):
        doc = _doc()
        patch = _patch(path="representation.palette", value="blue", op_id="same")
        doc2, applied1 = apply_patch(doc, patch)
        doc3, applied2 = apply_patch(doc2, patch)
        assert applied1 and not applied2
        assert doc3 == doc2


class TestUserWins:
    def test_agent_cannot_override_user_field(self):
        doc = _doc()
        doc, _ = apply_patch(doc, _patch(
            path="representation.palette", value="blue", op_id="u1", actor="user"))
        with pytest.raises(PatchConflict):
            apply_patch(doc, _patch(
                path="representation.palette", value="red", op_id="a1",
                actor="agent"))

    def test_user_can_change_own_field(self):
        doc = _doc()
        doc, _ = apply_patch(doc, _patch(
            path="representation.palette", value="blue", op_id="u1", actor="user"))
        doc, _ = apply_patch(doc, _patch(
            path="representation.palette", value="red", op_id="u2", actor="user"))
        assert doc.intent.representation.palette == "red"

    def test_lock_blocks_agent(self):
        doc = _doc()
        doc, _ = apply_patch(doc, _patch(
            op="add_lock", value={"scope": "palette", "value": "blue"},
            op_id="l1", actor="user"))
        with pytest.raises(PatchConflict):
            apply_patch(doc, _patch(
                path="representation.palette", value="red", op_id="a1",
                actor="agent"))
        # user 仍可改（显式解锁前用户 own 该面）
        doc2, applied = apply_patch(doc, _patch(
            path="representation.palette", value="red", op_id="u2", actor="user"))
        assert applied

    def test_remove_lock_is_user_only(self):
        doc = _doc()
        doc, _ = apply_patch(doc, _patch(
            op="add_lock", value={"scope": "palette", "value": "blue"},
            op_id="l1", actor="user"))
        with pytest.raises(PatchConflict):
            apply_patch(doc, _patch(
                op="remove_lock", value={"scope": "palette", "value": "blue"},
                op_id="r1", actor="agent"))
        doc, applied = apply_patch(doc, _patch(
            op="remove_lock", value={"scope": "palette", "value": "blue"},
            op_id="r2", actor="user"))
        assert applied and doc.intent.locks == []

    def test_add_lock_requires_user(self):
        with pytest.raises(PatchConflict):
            apply_patch(_doc(), _patch(
                op="add_lock", value={"scope": "palette", "value": "blue"},
                op_id="l1", actor="system"))


class TestMeasureOps:
    def test_add_and_remove_measure(self):
        doc = _doc()
        doc, applied = apply_patch(doc, _patch(
            op="add_measure", value={"statistic": "density", "phrase": "密度"},
            op_id="m1"))
        assert applied
        added = [m for m in doc.intent.measures if m.statistic == "density"]
        assert len(added) == 1
        doc, applied = apply_patch(doc, _patch(
            op="remove_measure", value={"id": added[0].id}, op_id="m2"))
        assert applied
        assert all(m.statistic != "density" for m in doc.intent.measures)

    def test_add_measure_long_phrase_not_rejected_by_container_cap(self):
        """2 段路径（measures.phrase / datasets）曾解析到**容器**字段，
        把容器计数上限（MAX_MEASURES=8 / MAX_DATASETS=16）当成单项长度上限
        —— measure 短语 >8 字、数据集名 >16 字即被 patch_value_invalid
        拒绝（schema 允许 128）。"""
        doc = _doc()
        long_phrase = "成都市每万名常住人口普通小学学位供给数"
        assert len(long_phrase) > 8
        doc, applied = apply_patch(doc, _patch(
            op="add_measure", value={"statistic": "density",
                                     "phrase": long_phrase},
            op_id="m-long"))
        assert applied
        added = next(m for m in doc.intent.measures if m.statistic == "density")
        assert added.phrase == long_phrase
        # 真越界（>MeasureSpec.phrase 的 128）仍拒绝
        with pytest.raises(PatchValueInvalid):
            apply_patch(doc, _patch(
                op="add_measure", value={"statistic": "density",
                                         "phrase": "x" * 200},
                op_id="m-too-long"))

    def test_set_datasets_long_name_not_rejected_by_container_cap(self):
        doc = _doc()
        name = "chengdu_primary_schools_full_precision_dataset"
        assert len(name) > 16
        doc, applied = apply_patch(doc, _patch(
            path="datasets", value=[name], op_id="d1"))
        assert applied
        assert name in doc.intent.datasets

    def test_remove_user_measure_requires_user(self):
        doc = _doc()
        doc, _ = apply_patch(doc, _patch(
            op="add_measure", value={"statistic": "density"}, op_id="m1",
            actor="user"))
        target = next(m for m in doc.intent.measures if m.statistic == "density")
        with pytest.raises(PatchConflict):
            apply_patch(doc, _patch(
                op="remove_measure", value={"id": target.id}, op_id="m2",
                actor="agent"))


class TestAmbiguityOps:
    def test_record_ambiguity_is_system_journal_bookkeeping(self):
        from app.services.gis_harness.requirement_ir.contracts import Ambiguity
        from app.services.gis_harness.requirement_ir.digest import requirement_digest

        doc = _doc("成都的小学分布情况，做一张图")
        ambiguity = Ambiguity(
            code="aoi_unresolved", path="aoi.name", context_key="recorded-ctx",
            blocking=True, question_zh="分析范围是哪个城市/区域？")
        record = _patch(
            op="record_ambiguity", value=ambiguity.model_dump(),
            actor="system", op_id="record-1", turn=4)
        new, applied = apply_patch(doc, record)
        assert applied and new.revision == doc.revision + 1
        assert new.lifecycle == "clarifying"
        assert new.intent.ambiguities[0].asked_turn == 4
        assert new.intent.ambiguities[0].state == "open"
        assert requirement_digest(new) == requirement_digest(doc)
        assert replay(doc, [record]) == new

    def test_record_ambiguity_rejects_user_and_cannot_reopen_answer(self):
        from app.services.gis_harness.requirement_ir.contracts import Ambiguity

        doc = _doc("成都的小学分布情况，做一张图")
        ambiguity = Ambiguity(
            code="aoi_unresolved", path="aoi.name", context_key="recorded-ctx",
            blocking=True, question_zh="分析范围是哪个城市/区域？")
        payload = ambiguity.model_dump()
        with pytest.raises(PatchConflict):
            apply_patch(doc, _patch(
                op="record_ambiguity", value=payload,
                actor="user", op_id="record-user"))
        doc, applied = apply_patch(doc, _patch(
            op="record_ambiguity", value=payload,
            actor="system", op_id="record-system", turn=2))
        assert applied
        doc, applied = apply_patch(doc, _patch(
            op="answer_ambiguity",
            value={"context_key": ambiguity.context_key, "answer": "成都"},
            op_id="answer-recorded", turn=3))
        assert applied and doc.intent.ambiguities[0].state == "answered"
        snapshot = doc.model_dump()
        unchanged, applied = apply_patch(doc, _patch(
            op="record_ambiguity", value=payload,
            actor="system", op_id="record-again", turn=4))
        assert not applied and unchanged.model_dump() == snapshot

    def test_answer_and_waive(self):
        from app.services.gis_harness.requirement_ir import (
            plan_clarifications, record_ambiguities)
        doc = build_document(
            "统计各片区土地利用占比",
            resolve_map_request_intent("统计各片区土地利用占比"), turn=1)
        doc = record_ambiguities(doc, plan_clarifications(doc), turn=1)
        blocking = [a for a in doc.intent.ambiguities if a.blocking and a.state == "open"]
        if not blocking:
            pytest.skip("该话语未产生 blocking 歧义")
        target = blocking[0]
        doc, applied = apply_patch(doc, _patch(
            op="answer_ambiguity",
            value={"context_key": target.context_key, "answer": "青羊区"},
            op_id="ans1"))
        assert applied
        answered = next(a for a in doc.intent.ambiguities
                        if a.context_key == target.context_key)
        assert answered.state == "answered"
        assert answered.answer == "青羊区"


class TestAccept:
    def test_accept_requires_user(self):
        with pytest.raises(PatchConflict):
            apply_patch(_doc(), _patch(op="accept", actor="agent", op_id="ac1"))

    def test_accept_transitions_items(self):
        doc = _doc()
        doc, applied = apply_patch(doc, _patch(op="accept", actor="user", op_id="ac1"))
        assert applied and doc.lifecycle == "accepted"
        assert all(item.state == "accepted" for item in doc.requirements.items)


class TestReplayInvariant:
    def test_replay_equals_final(self):
        doc = _doc("成都的小学分布情况，做一张图")
        journal = [
            _patch(path="statistics.group_by", value="grid", op_id="p1"),
            _patch(path="representation.palette", value="blue", op_id="p2",
                   actor="user"),
            _patch(op="add_measure", value={"statistic": "density"}, op_id="p3"),
        ]
        final = doc
        for record in journal:
            final, _ = apply_patch(final, record)
        rebuilt = replay(doc, journal)
        assert rebuilt == final

    def test_replay_skips_duplicates(self):
        doc = _doc()
        journal = [
            _patch(path="representation.palette", value="blue", op_id="dup"),
            _patch(path="representation.palette", value="blue", op_id="dup"),
        ]
        rebuilt = replay(doc, journal)
        assert rebuilt.revision == doc.revision + 1


class TestDiffAndAttribution:
    def test_diff_paths(self):
        old = _doc()
        new, _ = apply_patch(old, _patch(
            path="representation.palette", value="blue", op_id="d1"))
        diff = diff_documents(old, new)
        paths = {c["path"] for c in diff["changes"]}
        assert any("palette" in p for p in paths)
        assert diff["revision"] == {"before": old.revision, "after": new.revision}

    def test_attribution_names_actor(self):
        old = _doc()
        new, _ = apply_patch(old, _patch(
            path="representation.palette", value="blue", op_id="att1",
            actor="user", turn=7, reason="user picked blue"))
        attribution = attribute_changes(old, new)
        assert attribution and attribution[0]["actor"] == "user"
        assert attribution[0]["turn"] == 7
        assert attribution[0]["op_id"] == "att1"

    def test_diff_ignores_provenance_only_change(self):
        # provenance 变化（不同 actor 同值）不影响语义核 diff
        old = _doc()
        doc_a, _ = apply_patch(old, _patch(
            path="representation.palette", value="blue", op_id="x1", actor="user"))
        doc_b, _ = apply_patch(old, _patch(
            path="representation.palette", value="blue", op_id="x2", actor="agent"))
        assert diff_documents(doc_a, doc_b)["changes"] == []


def test_is_default_value_does_not_treat_zero_as_default():
    from app.services.gis_harness.requirement_ir.patch import _is_default_value

    assert _is_default_value(False) is True
    assert _is_default_value(0) is False
    assert _is_default_value(0.0) is False
