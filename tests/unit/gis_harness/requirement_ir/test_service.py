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


class TestReviewP2Fixes:
    """review P2：超替携带的三条不变式。"""

    def test_restore_user_fields_carries_beyond_sixteen(self):
        """反证：_restore_user_fields 只遍历 user_fields[:16] —— >16 个
        user-origin 字段时超出部分静默不携带（user-wins 违约），硬编码 16
        也与 MAX_FIELD_PROVENANCE=32 不符。"""
        from app.services.gis_harness.intent import resolve_map_request_intent
        from app.services.gis_harness.requirement_ir.build import build_document
        from app.services.gis_harness.requirement_ir.contracts import (
            MAX_FIELD_PROVENANCE,
            Provenance,
        )

        base = build_document(
            "成都小学分布", resolve_map_request_intent("成都小学分布"), turn=1)
        prov = Provenance(origin="user", turn=1)
        paths = [
            "output.formats", "output.report", "output.publish",
            "output.live_map", "output.dpi", "output.medium",
            "representation.palette", "representation.geometry_kind",
            "components.title", "components.legend", "components.scale_bar",
            "components.north_arrow", "components.labels",
            "components.attribution", "time.series", "audience",
            "time.range_start", "time.range_end", "datasets", "purpose",
        ]
        values = {
            "output.formats": ["png", "pdf"], "output.report": True,
            "output.publish": True, "output.live_map": True,
            "output.dpi": 150, "output.medium": "print",
            "representation.palette": "blue",
            "representation.geometry_kind": "heatmap",
            "components.title": True, "components.legend": True,
            "components.scale_bar": True, "components.north_arrow": True,
            "components.labels": True, "components.attribution": True,
            "time.series": True, "audience": "public",
            "time.range_start": "2020-01-01", "time.range_end": "2023-12-31",
            "datasets": ["ds-a"], "purpose": "briefing",
        }
        assert len(paths) > 16 and len(paths) <= MAX_FIELD_PROVENANCE
        user_fields = {p: (values[p], prov) for p in paths}
        restored = service._restore_user_fields(base, user_fields, 2)
        carried = [
            p for p in paths
            if restored.intent.field_provenance.get(p) is not None
            and restored.intent.field_provenance[p].is_user()
        ]
        assert carried == paths, f"未携带：{set(paths) - set(carried)}"

    def test_valueless_camera_lock_dropped_on_carry(self):
        """反证：scene_camera 携带锁在该轴字段无值时失去保护对象 —— 留着
        会让 agent 写该轴必被 _lock_conflict 拒绝且无用户解锁途径。"""
        from app.services.gis_harness.intent import resolve_map_request_intent
        from app.services.gis_harness.requirement_ir.build import build_document
        from app.services.gis_harness.requirement_ir.contracts import (
            Provenance,
            UserLock,
        )

        base = build_document(
            "成都小学分布", resolve_map_request_intent("成都小学分布"), turn=1)
        assert base.intent.scene.camera_pitch is None
        lock = UserLock(scope="scene_camera", value="pitch",
                        provenance=Provenance(origin="user"))
        carried = base.model_copy(update={
            "intent": base.intent.model_copy(update={"locks": [lock]})})
        restored = service._restore_user_fields(carried, {}, 2)
        assert not any(
            item.scope == "scene_camera" for item in restored.intent.locks)

    def test_valued_camera_lock_survives_carry(self):
        """对照：该轴确有用户值时锁必须存活（user-wins 不回退）。"""
        from app.services.gis_harness.intent import resolve_map_request_intent
        from app.services.gis_harness.requirement_ir.build import build_document
        from app.services.gis_harness.requirement_ir.contracts import (
            Provenance,
            UserLock,
        )

        base = build_document(
            "成都小学分布", resolve_map_request_intent("成都小学分布"), turn=1)
        lock = UserLock(scope="scene_camera", value="pitch",
                        provenance=Provenance(origin="user"))
        prov = Provenance(origin="user", turn=1)
        carried = base.model_copy(update={
            "intent": base.intent.model_copy(update={"locks": [lock]})})
        restored = service._restore_user_fields(
            carried, {"scene.camera_pitch": (45.0, prov)}, 2)
        assert restored.intent.scene.camera_pitch == 45.0
        assert any(item.scope == "scene_camera" and item.value == "pitch"
                   for item in restored.intent.locks)

    def test_rejected_patch_does_not_truncate_remaining_diff(self, monkeypatch):
        """反证：edit 轮任一 patch 被拒（非 CAS）后，其余 patch 不得全部
        PatchStale 被静默丢弃 —— diff_to_patches 的链式 expected_revision
        假设全量应用，本轮差分被截断。"""
        from app.services.gis_harness.requirement_ir.contracts import PatchRecord

        store = MemStore()
        run(service.ensure_document(
            store, "s1", "成都的小学分布情况，做一张图", turn=1))

        def _fake_diff(old, new_core, *, turn, query=""):
            base = int(old.revision)
            return [
                PatchRecord(op_id="e1", turn=turn, actor="user", op="set",
                            path="output.dpi", value=99999,
                            expected_revision=base),
                PatchRecord(op_id="e2", turn=turn, actor="user", op="set",
                            path="representation.palette", value="blue",
                            expected_revision=base + 1),
            ]

        monkeypatch.setattr(service, "diff_to_patches", _fake_diff)
        result = run(service.ensure_document(
            store, "s1", "改成各区统计", turn=2))
        # 首条被拒（值非法）且不得产生 stale —— 第二条必须仍然生效
        assert result["patch_errors"], result
        assert "patch_stale_revision" not in result["patch_errors"]
        assert result["patched"] == 1
        doc = service._doc_from(
            run(service.load_state(store, "s1")).document)
        assert doc.intent.representation.palette == "blue"


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
