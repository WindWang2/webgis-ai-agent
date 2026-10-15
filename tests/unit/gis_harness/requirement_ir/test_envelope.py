"""ClarificationEnvelope 闭环单测（方向 3 / ADR-0222）。

覆盖任务书 D 测试矩阵的服务层部分：
- envelope 契约（bounded/versioned/确定性 request_id/options 构造）；
- suppression（同 context 不重复问）/ supersede（新目标作废旧澄清）；
- answer（幂等/CAS/stale/重复/改主意/typed parser/自由文本映射）；
- waive（安全默认 + 不可 waive 门）/ non-blocking 不入 envelope；
- agent 代答拒绝 / 跨会话 request_id 拒绝；
- telemetry（asked/answered/duplicate/stale/suppressed/default_used）。

store 为内存实现（SessionStore map_state 键值面同形）；全离线确定性。
"""
from __future__ import annotations

import asyncio

import pytest

from app.services.gis_harness.requirement_ir import envelope as env_mod
from app.services.gis_harness.requirement_ir import service
from app.services.gis_harness.requirement_ir.clarify import (
    plan_clarifications,
    record_ambiguities,
)
from app.services.gis_harness.requirement_ir.contracts import Ambiguity
from app.services.gis_harness.requirement_ir.patch import (
    PatchBlocked,
    PatchConflict,
    PatchRecord,
    apply_patch,
)


class MemStore:
    def __init__(self):
        self.data: dict = {}

    async def get_map_state(self, session_id):
        return self.data.get(session_id, {})

    async def set_map_state(self, session_id, key, value, seq=None):
        self.data.setdefault(session_id, {})[key] = value
        return True


def run(coro):
    return asyncio.run(coro)


def _make_doc(store, session_id, query, turn=1):
    return run(service.ensure_document(store, session_id, query, turn=turn))


def _pending(store, session_id):
    return run(env_mod.pending_envelope(store, session_id))


def _open_codes(store, session_id):
    env = _pending(store, session_id)
    return [q.code for q in env.questions] if env else []


def _make_measure_gap(store, session_id="s1", query="统计成都各片区的数据"):
    """确定性构造 measure_missing_for_statistics（清空 measures 后重记账）。"""
    _make_doc(store, session_id, query)
    state = run(service.load_state(store, session_id))
    d = service._doc_from(state.document)
    spec = d.intent.model_copy(update={"measures": []})
    d = d.model_copy(update={"intent": spec})
    d = record_ambiguities(d, plan_clarifications(d), turn=2)
    state.document = d.model_dump()
    run(service.save_state(store, session_id, state))
    return d


# ── 契约：bounded / versioned / 确定性 ────────────────────────────────


class TestEnvelopeContract:
    def test_schema_versioned_and_deterministic_request_id(self):
        store = MemStore()
        _make_doc(store, "s1", "统计各区小学数量")
        e1 = _pending(store, "s1")
        e2 = _pending(store, "s1")
        assert e1.schema_version == "clarification_envelope.v1"
        assert e1.request_id == e2.request_id
        assert e1.request_id.startswith("clar-")
        assert len(e1.request_id) <= 64

    def test_questions_bounded_to_two(self):
        store = MemStore()
        _make_doc(store, "s1", "统计各区小学数量")
        # 构造 ≥3 个 open blocking：在文档上补 time.series + normalization
        state = run(service.load_state(store, "s1"))
        d = service._doc_from(state.document)
        d, _ = apply_patch(d, PatchRecord(
            op_id="p1", turn=1, actor="user", op="set",
            path="time.series", value=True))
        d, _ = apply_patch(d, PatchRecord(
            op_id="p2", turn=1, actor="user", op="set",
            path="statistics.normalization", value="per_area"))
        d = record_ambiguities(d, plan_clarifications(d), turn=2)
        state.document = d.model_dump()
        run(service.save_state(store, "s1", state))
        env = _pending(store, "s1")
        assert env is not None
        assert len(env.questions) <= 2

    def test_options_from_policy(self):
        store = MemStore()
        _make_measure_gap(store)
        env = _pending(store, "s1")
        assert env is not None
        q = next((x for x in env.questions
                  if x.code == "measure_missing_for_statistics"), None)
        assert q is not None, "measure 缺口未入 envelope"
        values = {o.value for o in q.options}
        assert {"count", "density", "share"} <= values
        for question in env.questions:
            assert len(question.options) <= 4

    def test_no_pending_returns_none(self):
        store = MemStore()
        assert _pending(store, "s1") is None


# ── D 矩阵：missing area / 密度语义 / 字段 / 时序 ─────────────────────


class TestDetectionCases:
    def test_missing_area_asks_and_answer_writes_aoi(self):
        store = MemStore()
        _make_doc(store, "s1", "统计各区小学数量")
        assert "aoi_unresolved" in _open_codes(store, "s1")
        env = _pending(store, "s1")
        q = next(q for q in env.questions if q.code == "aoi_unresolved")
        assert q.allow_free_text is True
        res = run(env_mod.submit_answers(
            store, "s1", env.request_id,
            [{"context_key": q.context_key, "value": "成都"}],
            expected_revision=env.requirement_revision))
        assert res["status"] == "answered"
        view = run(service.get_requirement_view(store, "s1"))
        # 答案进 provenance：aoi.name 现为 user-owned
        assert "aoi.name" in view["user_owned_paths"]

    def test_density_semantic_options(self):
        """『密度』歧义：数量密度 vs 其他口径由封闭选项承载，不静默默认。"""
        store = MemStore()
        _make_measure_gap(store)
        env = _pending(store, "s1")
        q = next((q for q in env.questions
                  if q.code == "measure_missing_for_statistics"), None)
        assert q is not None
        values = {o.value for o in q.options}
        assert "count" in values and "density" in values

    def test_field_ambiguity_transportable(self):
        """measure_multiple_candidates：检测接线（field_resolver 回流）是
        后续工作，但协议面必须可传输（构造带候选的歧义入档）。"""
        from app.services.gis_harness.requirement_ir.contracts import (
            AmbiguityOption,
        )
        store = MemStore()
        _make_doc(store, "s1", "统计各区小学数量")
        state = run(service.load_state(store, "s1"))
        d = service._doc_from(state.document)
        spec = d.intent.model_copy(deep=True)
        spec.ambiguities.append(Ambiguity(
            code="measure_multiple_candidates", path="measures.m1.field",
            context_key="manualkey0001", blocking=True,
            question_zh="该指标对应多个同名字段，请指定使用哪个",
            question_en="pick field",
            options=[
                AmbiguityOption(value="school_cnt", label_zh="学校数",
                                label_en="count"),
                AmbiguityOption(value="school_area", label_zh="学校面积",
                                label_en="area"),
            ]))
        d = d.model_copy(update={"intent": spec})
        state.document = d.model_dump()
        run(service.save_state(store, "s1", state))
        env = _pending(store, "s1")
        assert env is not None
        codes = [q.code for q in env.questions]
        assert "measure_multiple_candidates" in codes

    def test_temporal_ambiguity_asked(self):
        store = MemStore()
        _make_doc(store, "s1", "统计各区小学数量")
        state = run(service.load_state(store, "s1"))
        d = service._doc_from(state.document)
        d, _ = apply_patch(d, PatchRecord(
            op_id="t1", turn=1, actor="user", op="set",
            path="time.series", value=True))
        d = record_ambiguities(d, plan_clarifications(d), turn=2)
        state.document = d.model_dump()
        run(service.save_state(store, "s1", state))
        assert "time_range_missing_for_series" in _open_codes(store, "s1")

    def test_non_blocking_never_blocks(self):
        store = MemStore()
        # 非发布导出缺格式 → 非 blocking 安全默认，绝不出现在 envelope
        _make_doc(store, "s1", "导出各区小学统计")
        env = _pending(store, "s1")
        if env is not None:
            assert all(q.code != "export_format_defaulted" for q in env.questions)


# ── suppression / supersede ──────────────────────────────────────────


class TestSuppressionAndSupersede:
    def test_repeated_same_context_not_reasked(self):
        store = MemStore()
        _make_doc(store, "s1", "统计各区小学数量")
        e1, out1 = run(env_mod.collect_turn_clarification(store, "s1", "turn-1"))
        e2, out2 = run(env_mod.collect_turn_clarification(store, "s1", "turn-2"))
        assert out1 == "emitted" and e1 is not None
        assert out2 == "suppressed" and e2 is None
        ledger = run(env_mod.load_ledger(store, "s1"))
        assert any(t.event == "suppressed" for t in ledger.telemetry)

    def test_new_goal_supersedes_old_clarification(self):
        store = MemStore()
        _make_doc(store, "s1", "统计各区小学数量")
        run(env_mod.collect_turn_clarification(store, "s1", "turn-1"))
        # 新任务（语义核变化）→ 超替 → 新文档新歧义
        res = _make_doc(store, "s1", "统计重庆各区医院数量", turn=3)
        assert res["superseded"] is True
        # 伪造的旧 request_id + 未知 context_key 必须被拒
        with pytest.raises(env_mod.ClarificationError) as exc:
            run(env_mod.submit_answers(
                store, "s1", "clar-req-deadbeefdead-000000000000",
                [{"context_key": "nope", "value": "x"}]))
        assert exc.value.code in ("clarification_unknown_context",
                                  "clarification_unknown_request")

    def test_emit_again_after_context_change(self):
        """context 变 → 新 context_key → 可再问（补齐 time.series 后）。"""
        store = MemStore()
        _make_doc(store, "s1", "统计各区小学数量")
        run(env_mod.collect_turn_clarification(store, "s1", "turn-1"))
        state = run(service.load_state(store, "s1"))
        d = service._doc_from(state.document)
        d, _ = apply_patch(d, PatchRecord(
            op_id="t1", turn=2, actor="user", op="set",
            path="time.series", value=True))
        d = record_ambiguities(d, plan_clarifications(d), turn=2)
        state.document = d.model_dump()
        run(service.save_state(store, "s1", state))
        e2, out2 = run(env_mod.collect_turn_clarification(store, "s1", "turn-2"))
        assert out2 == "emitted" and e2 is not None
        assert "time_range_missing_for_series" in [q.code for q in e2.questions]


# ── 应答：幂等 / CAS / stale / 改主意 ────────────────────────────────


class TestAnswerFlow:
    def _one_question_setup(self, query="统计各区小学数量"):
        store = MemStore()
        _make_doc(store, "s1", query)
        env = _pending(store, "s1")
        assert env is not None
        return store, env

    def test_duplicate_answer_same_result(self):
        store, env = self._one_question_setup()
        q = env.questions[0]
        ans = [{"context_key": q.context_key, "value": "成都"}]
        r1 = run(env_mod.submit_answers(
            store, "s1", env.request_id, ans,
            expected_revision=env.requirement_revision))
        r2 = run(env_mod.submit_answers(
            store, "s1", env.request_id, ans,
            expected_revision=env.requirement_revision))
        assert r1["status"] == "answered"
        assert r2["status"] == "duplicate"
        assert r2["resolved_all"] == r1["resolved_all"]

    def test_stale_revision_typed_conflict(self):
        store, env = self._one_question_setup()
        q = env.questions[0]
        with pytest.raises(env_mod.ClarificationError) as exc:
            run(env_mod.submit_answers(
                store, "s1", env.request_id,
                [{"context_key": q.context_key, "value": "成都"}],
                expected_revision=env.requirement_revision + 5))
        assert exc.value.code == "clarification_stale_revision"
        # 拒绝无副作用：revision 未动
        state = run(service.load_state(store, "s1"))
        assert service._doc_from(state.document).revision == env.requirement_revision

    def test_answer_then_no_reask_and_accept_unblocked(self):
        store, env = self._one_question_setup()
        q = env.questions[0]
        run(env_mod.submit_answers(
            store, "s1", env.request_id,
            [{"context_key": q.context_key, "value": "成都"}],
            expected_revision=env.requirement_revision))
        # 下一个 turn 的 ensure：答案已改写语义核（core 同步 aoi.name）→
        # 同文本再解析不再逐字相等 → 走超替携带（user 字段连值带 provenance
        # 存活）；恢复后重新记账时 aoi.name 已在 → 澄清不重问。
        _make_doc(store, "s1", "统计各区小学数量", turn=5)
        assert _pending(store, "s1") is None
        state = run(service.load_state(store, "s1"))
        d = service._doc_from(state.document)
        assert d.intent.aoi.name == "成都"   # user-wins 携带
        # accept 门禁：blocking 全答 → 放行（或仅剩非 blocking）
        try:
            d2, _ = apply_patch(d, PatchRecord(
                op_id="acc1", turn=6, actor="user", op="accept"))
            assert d2.lifecycle == "accepted"
        except PatchBlocked:
            open_blocking = [
                a.code for a in d.intent.ambiguities
                if a.state == "open" and a.blocking]
            assert not open_blocking, f"仍存 open blocking: {open_blocking}"
            raise

    def test_user_changes_mind_later_via_edit(self):
        store, env = self._one_question_setup()
        q = env.questions[0]
        run(env_mod.submit_answers(
            store, "s1", env.request_id,
            [{"context_key": q.context_key, "value": "成都"}],
            expected_revision=env.requirement_revision))
        # 用户改主意：后续 user patch set → user-wins 生效
        state = run(service.load_state(store, "s1"))
        d = service._doc_from(state.document)
        d, applied = apply_patch(d, PatchRecord(
            op_id="edit1", turn=7, actor="user", op="set",
            path="aoi.name", value="重庆"))
        assert applied and d.intent.aoi.name == "重庆"

    def test_answer_different_value_conflict(self):
        store, env = self._one_question_setup()
        q = env.questions[0]
        run(env_mod.submit_answers(
            store, "s1", env.request_id,
            [{"context_key": q.context_key, "value": "成都"}],
            expected_revision=env.requirement_revision))
        with pytest.raises(env_mod.ClarificationError) as exc:
            run(env_mod.submit_answers(
                store, "s1", env.request_id,
                [{"context_key": q.context_key, "value": "重庆"}]))
        assert exc.value.code == "clarification_already_answered"

    def _publish_doc(self, query="统计成都各片区的数据"):
        """确定性构造：measure 缺口 + publish 导出缺口（aoi 已解析不占位）。"""
        store = MemStore()
        _make_doc(store, "s1", query)
        state = run(service.load_state(store, "s1"))
        d = service._doc_from(state.document)
        d, _ = apply_patch(d, PatchRecord(
            op_id="p1", turn=1, actor="user", op="set",
            path="output.publish", value=True))
        spec = d.intent.model_copy(update={"measures": []})
        d = d.model_copy(update={"intent": spec})
        d = record_ambiguities(d, plan_clarifications(d), turn=2)
        state.document = d.model_dump()
        run(service.save_state(store, "s1", state))
        return store, _pending(store, "s1")

    def test_free_text_label_mapping(self):
        """中文标签/大小写 → 封闭值（词表映射，非 NLP）。"""
        store, env = self._publish_doc()
        q = next((x for x in env.questions
                  if x.code == "export_format_missing_when_publish"), None)
        assert q is not None, "export 格式歧义未入 envelope（≤2 截断）"
        assert q.can_waive is True
        assert env_mod.resolve_answer_value(q, "PNG") == "png"
        assert env_mod.resolve_answer_value(q, "PDF（矢量，出版推荐）") == "pdf"
        assert env_mod.resolve_answer_value(q, "svg") == "svg"

    def test_statistic_label_mapping(self):
        store = MemStore()
        _make_measure_gap(store)
        env = _pending(store, "s1")
        q = next((x for x in env.questions
                  if x.code == "measure_missing_for_statistics"), None)
        assert q is not None
        assert env_mod.resolve_answer_value(q, "数量") == "count"
        assert env_mod.resolve_answer_value(q, "密度") == "density"
        assert env_mod.resolve_answer_value(q, "Count (number of features)") == "count"

    def test_measure_answer_lands_as_measure(self):
        store = MemStore()
        _make_measure_gap(store)
        env = _pending(store, "s1")
        q = next((x for x in env.questions
                  if x.code == "measure_missing_for_statistics"), None)
        assert q is not None
        run(env_mod.submit_answers(
            store, "s1", env.request_id,
            [{"context_key": q.context_key, "value": "count"}],
            expected_revision=env.requirement_revision))
        view = run(service.get_requirement_view(store, "s1"))
        assert view["revision"] > env.requirement_revision
        state = run(service.load_state(store, "s1"))
        d = service._doc_from(state.document)
        assert any(m.statistic == "count" for m in d.intent.measures)


# ── waive ────────────────────────────────────────────────────────────


class TestWaive:
    def test_waive_applies_default_with_disclosure(self):
        store, env = TestAnswerFlow()._publish_doc()
        q = next((x for x in env.questions
                  if x.code == "export_format_missing_when_publish"), None)
        assert q is not None, "export 格式歧义未入 envelope（≤2 截断）"
        res = run(env_mod.submit_answers(
            store, "s1", env.request_id, [], waives=[q.context_key],
            expected_revision=env.requirement_revision))
        assert res["status"] == "answered" and res["default_used"] is True
        state = run(service.load_state(store, "s1"))
        d = service._doc_from(state.document)
        assert "pdf" in d.intent.output.formats

    def test_waive_without_default_rejected(self):
        store = MemStore()
        _make_doc(store, "s1", "统计各区小学数量")
        env = _pending(store, "s1")
        q = next(x for x in env.questions if not x.can_waive)
        with pytest.raises(env_mod.ClarificationError) as exc:
            run(env_mod.submit_answers(
                store, "s1", env.request_id, [], waives=[q.context_key],
                expected_revision=env.requirement_revision))
        assert exc.value.code == "clarification_waive_not_allowed"



# ── actor / 跨会话 ───────────────────────────────────────────────────


class TestActorAndCrossSession:
    def test_agent_cannot_answer_blocking(self):
        store = MemStore()
        _make_doc(store, "s1", "统计各区小学数量")
        env = _pending(store, "s1")
        q = env.questions[0]
        with pytest.raises(env_mod.ClarificationError) as exc:
            run(env_mod.submit_answers(
                store, "s1", env.request_id,
                [{"context_key": q.context_key, "value": "成都"}],
                actor="agent"))
        assert exc.value.code == "clarification_actor_denied"
        ledger = run(env_mod.load_ledger(store, "s1"))
        assert any(t.event == "actor_denied" for t in ledger.telemetry)

    def test_patch_layer_agent_blocking_answer_denied(self):
        store = MemStore()
        _make_doc(store, "s1", "统计各区小学数量")
        state = run(service.load_state(store, "s1"))
        d = service._doc_from(state.document)
        target = next(a for a in d.intent.ambiguities if a.blocking)
        with pytest.raises(PatchConflict):
            apply_patch(d, PatchRecord(
                op_id="ag1", turn=1, actor="agent", op="answer_ambiguity",
                value={"context_key": target.context_key, "answer": "x"}))

    def test_cross_session_context_key_denied(self):
        store = MemStore()
        _make_doc(store, "s1", "统计各区小学数量")
        _make_doc(store, "s2", "统计重庆各区医院数量")
        env1 = _pending(store, "s1")
        q1 = env1.questions[0]
        # 用 s1 的 request_id/context_key 打 s2
        with pytest.raises(env_mod.ClarificationError) as exc:
            run(env_mod.submit_answers(
                store, "s2", env1.request_id,
                [{"context_key": q1.context_key, "value": "成都"}],
                expected_revision=env1.requirement_revision))
        assert exc.value.code in ("clarification_unknown_context",
                                  "clarification_unknown_request")

    def test_forged_request_id_denied_for_open_questions(self):
        store = MemStore()
        _make_doc(store, "s1", "统计各区小学数量")
        env = _pending(store, "s1")
        q = env.questions[0]
        with pytest.raises(env_mod.ClarificationError) as exc:
            run(env_mod.submit_answers(
                store, "s1", "clar-req-forgedforged-abcdef123456",
                [{"context_key": q.context_key, "value": "成都"}],
                expected_revision=env.requirement_revision))
        assert exc.value.code == "clarification_unknown_request"


# ── 恢复 / telemetry ─────────────────────────────────────────────────


class TestRestoreAndTelemetry:
    def test_pending_envelope_restorable_until_answered(self):
        store = MemStore()
        _make_doc(store, "s1", "统计各区小学数量")
        run(env_mod.collect_turn_clarification(store, "s1", "turn-1"))
        env = _pending(store, "s1")       # GET 恢复面
        assert env is not None
        q = env.questions[0]
        run(env_mod.submit_answers(
            store, "s1", env.request_id,
            [{"context_key": q.context_key, "value": "成都"}],
            expected_revision=env.requirement_revision))
        assert _pending(store, "s1") is None   # 答完即无 pending

    def test_telemetry_events_recorded(self):
        store = MemStore()
        _make_doc(store, "s1", "统计各区小学数量")
        env = _pending(store, "s1")
        run(env_mod.collect_turn_clarification(store, "s1", "turn-1"))
        q = env.questions[0]
        ans = [{"context_key": q.context_key, "value": "成都"}]
        run(env_mod.submit_answers(
            store, "s1", env.request_id, ans,
            expected_revision=env.requirement_revision))
        run(env_mod.submit_answers(
            store, "s1", env.request_id, ans))   # duplicate
        ledger = run(env_mod.load_ledger(store, "s1"))
        events = [t.event for t in ledger.telemetry]
        assert "asked" in events and "answered" in events and "duplicate" in events
        # telemetry 不存答案文本
        for t in ledger.telemetry:
            assert "成都" not in str(t.model_dump())

    def test_ledger_failure_fail_open(self):
        """ledger 读写故障不影响需求真相（fail-open，只丢记账）。"""
        class FailingStore(MemStore):
            async def set_map_state(self, session_id, key, value, seq=None):
                if key == env_mod.CLARIFICATION_LEDGER_KEY:
                    raise RuntimeError("ledger down")
                return await super().set_map_state(session_id, key, value, seq)

        store = FailingStore()
        _make_doc(store, "s1", "统计各区小学数量")
        env = _pending(store, "s1")
        assert env is not None   # 记账失败不阻塞 envelope 构造
