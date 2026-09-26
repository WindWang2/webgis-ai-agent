"""F02 service 层单测（会话生命周期 / CAS / 幂等 / edit 路由 / 超替携带）。

store 为内存实现（SessionStore map_state 键值面同形）；全离线确定性。
"""
from __future__ import annotations

import asyncio

import pytest

from app.services.gis_harness.requirement_ir import service
from app.services.gis_harness.requirement_ir.digest import document_digest


class MemStore:
    """SessionStore map_state 键值面的内存同形实现。"""

    def __init__(self):
        self.data: dict = {}
        self.fail = False

    async def get_map_state(self, session_id):
        if self.fail:
            raise RuntimeError("store down")
        return self.data.get(session_id, {})

    async def set_map_state(self, session_id, key, value, seq=None):
        if self.fail:
            raise RuntimeError("store down")
        self.data.setdefault(session_id, {})[key] = value
        return True


def run(coro):
    return asyncio.run(coro)


class TestEnsureDocument:
    def test_create_then_summary(self):
        store = MemStore()
        result = run(service.ensure_document(
            store, "s1", "统计成都各区小学数量", turn=1))
        assert result["created"] and result["patched"] == 0
        summary = result["summary"]
        assert summary["task_kind"] in ("map", "analysis")
        assert summary["requirement_digest"].startswith("rd1:")
        assert summary["revision"] == 1

    def test_repeat_same_query_idempotent(self):
        store = MemStore()
        run(service.ensure_document(store, "s1", "统计成都各区小学数量", turn=1))
        again = run(service.ensure_document(
            store, "s1", "统计成都各区小学数量", turn=2))
        assert again["created"] is False and again["superseded"] is False
        assert again["summary"]["revision"] == 1

    def test_edit_routes_to_patch(self):
        store = MemStore()
        run(service.ensure_document(
            store, "s1", "成都的小学分布情况，做一张图", turn=1))
        result = run(service.ensure_document(
            store, "s1", "改成各区统计", turn=2))
        assert result["patched"] >= 1 and not result["superseded"]
        assert result["summary"]["user_owned_paths"] and \
            "task.task_type" in result["summary"]["user_owned_paths"]

    def test_new_task_supersedes_and_carries_locks(self):
        store = MemStore()
        run(service.ensure_document(
            store, "s1", "成都的小学分布情况，做一张图", turn=1))
        run(service.ensure_document(store, "s1", "换成蓝色", turn=2))
        result = run(service.ensure_document(
            store, "s1", "统计重庆各区医院数量", turn=3))
        assert result["superseded"] is True
        # user lock 存活
        assert any(lock["scope"] == "palette"
                   for lock in result["summary"]["locks"])

    def test_store_failure_never_raises(self):
        store = MemStore()
        store.fail = True
        result = run(service.ensure_document(
            store, "s1", "统计成都各区小学数量", turn=1))
        # fail-open：任何 store 异常都被吞掉（summary 缺省）
        assert result is None or isinstance(result, dict)


class TestPatchSubmission:
    def test_apply_user_patch_with_cas(self):
        store = MemStore()
        run(service.ensure_document(store, "s1", "统计成都各区小学数量", turn=1))
        result = run(service.apply_user_patches(store, "s1", [
            service.TurnPatch(
                op="set", path="representation.palette", value="blue",
                turn=2, actor="user", expected_revision=1,
                reason="user picked blue"),
        ]))
        assert result["applied"] == 1 and result["errors"] == []
        assert result["summary"]["revision"] == 2

    def test_cas_rejection_reported(self):
        store = MemStore()
        run(service.ensure_document(store, "s1", "统计成都各区小学数量", turn=1))
        result = run(service.apply_user_patches(store, "s1", [
            service.TurnPatch(
                op="set", path="representation.palette", value="blue",
                turn=2, actor="user", expected_revision=99),
        ]))
        assert result["applied"] == 0
        assert "patch_stale_revision" in result["errors"]

    def test_patch_absent_document(self):
        store = MemStore()
        result = run(service.apply_user_patches(store, "s1", [
            service.TurnPatch(op="accept", turn=1, actor="user"),
        ]))
        assert result["errors"] == ["requirement_document_absent"]


class TestClarificationSession:
    def test_answer_clarification_roundtrip(self):
        store = MemStore()
        run(service.ensure_document(
            store, "s1", "统计成都各片区土地利用占比", turn=1))
        view = run(service.get_requirement_view(store, "s1"))
        if not view["open_clarifications"]:
            pytest.skip("该话语无 open 澄清")
        # context_key 从 pending 拿（summary 不带 key 的场景跳过）
        state = run(service.load_state(store, "s1"))
        doc = service._doc_from(state.document)
        from app.services.gis_harness.requirement_ir import pending_questions
        questions = pending_questions(doc)
        target = questions[0]
        result = run(service.answer_clarification(
            store, "s1", target.context_key, "青羊区", turn=2))
        assert result["applied"] == 1
        view2 = run(service.get_requirement_view(store, "s1"))
        answered_codes = {c["code"] for c in view2["open_clarifications"]}
        assert target.code not in answered_codes


class TestReplayDrift:
    def test_session_replay_invariant(self):
        store = MemStore()
        run(service.ensure_document(
            store, "s1", "成都的小学分布情况，做一张图", turn=1))
        for query, turn in (("改成各区统计", 2), ("隐藏道路图层", 3),
                            ("换成蓝色", 4), ("再导出PDF", 5)):
            run(service.ensure_document(store, "s1", query, turn=turn))
        state = run(service.load_state(store, "s1"))
        rebuilt = service.replay_document(state)
        current = service._doc_from(state.document)
        assert document_digest(rebuilt) == document_digest(current)

    def test_sessions_isolated(self):
        store = MemStore()
        run(service.ensure_document(store, "s1", "统计成都各区小学数量", turn=1))
        run(service.ensure_document(store, "s2", "统计重庆各区医院数量", turn=1))
        v1 = run(service.get_requirement_view(store, "s1"))
        v2 = run(service.get_requirement_view(store, "s2"))
        assert v1["document_id"] != v2["document_id"]
        assert v1["requirement_digest"] != v2["requirement_digest"]
