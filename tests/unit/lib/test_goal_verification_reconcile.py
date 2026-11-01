"""GoalVerification 对账层测试（GOAL 05 / M4，ADR-0279）。

钉死 false-PASS 红线：
- 地图观察缺失 / 渲染未验证 → actual_rendering 永不 pass；goal verdict
  ==satisfied 也必须记 contradiction；
- 科学质量证据不足（L4/L5 未评估 / cartographic review 缺席）→ 不 pass；
- 数据角色 unresolved / qualification blocked → data_coverage fail；
- task_complete=True 但 slot fail → contradiction（完成回执与证据不一致）；
- user-wins：用户显式隐藏层 → warning + user_wins_notes，不擅自判 fail；
- 缺证据 → not_evaluated，绝不升级 pass；
- 纯函数确定性 + 全字段有界。
"""
from __future__ import annotations

import pytest

from app.lib.harness.goal_verification import reconcile_goal_verification

_GOAL_SATISFIED = {
    "schema": "goal_satisfaction.v1",
    "verdict": "satisfied",
    "signal": "complete",
    "counts": {"fulfilled": 4},
    "requirements": [
        {"id": "req-map", "kind": "map", "verdict": "fulfilled"},
        {"id": "req-analysis", "kind": "analysis", "verdict": "fulfilled"},
        {"id": "req-stat", "kind": "statistics", "verdict": "fulfilled"},
        {"id": "req-export", "kind": "export", "verdict": "fulfilled"},
    ],
    "missing_summary": [],
    "uncertainties": [],
}

_HEALTH_OK = {
    "layers": [{"id": "l1", "state": "data_present"}],
    "charts": [],
    "aggregate": "data_present",
    "health": "ok",
    "observed_revision": 3,
}

_ROLES_BOUND = {"workflow_contract": {"roles": [
    {"role": "schools", "required": True, "status": "bound"},
    {"role": "roads", "required": True, "status": "external"},
]}}


def _reconcile(**kw):
    defaults = dict(
        goal_report=_GOAL_SATISFIED,
        observation_health=_HEALTH_OK,
        chapter=_ROLES_BOUND,
        task_complete=True,
        quality_review_present=True,
    )
    defaults.update(kw)
    return reconcile_goal_verification(**defaults)


# ── 基线：全证据在場 → 全 pass ───────────────────────────────────────────

class TestFullPass:
    def test_all_slots_pass(self):
        rep = _reconcile()
        assert rep is not None
        assert rep.overall == "pass"
        by_slot = {s.slot: s.status for s in rep.slots}
        assert by_slot["actual_rendering"] == "pass"
        assert by_slot["data_coverage"] == "pass"
        assert by_slot["scientific_quality"] == "pass"
        assert by_slot["export_delivery"] == "pass"
        assert rep.contradictions == []

    def test_absent_goal_report_returns_none(self):
        assert _reconcile(goal_report=None) is None


# ── false-PASS 红线 ──────────────────────────────────────────────────────

class TestFalsePassGuards:
    def test_rendering_observation_missing_never_pass(self):
        rep = _reconcile(observation_health=None)
        by_slot = {s.slot: s.status for s in rep.slots}
        assert by_slot["actual_rendering"] in ("fail", "not_evaluated")
        assert by_slot["actual_rendering"] != "pass"
        assert rep.overall != "pass"

    def test_goal_satisfied_without_rendering_is_contradiction(self):
        """ADR-0183 判 satisfied 但无渲染证据 → 对账必须记矛盾。"""
        rep = _reconcile(observation_health=None)
        assert any("rendering" in c for c in rep.contradictions)

    def test_task_complete_with_failing_slot_is_contradiction(self):
        rep = _reconcile(observation_health={
            "layers": [{"id": "l1", "state": "unknown"}],
            "aggregate": "unknown", "health": "blocked",
            "observed_revision": 0,
        })
        assert any("task_complete" in c for c in rep.contradictions)

    def test_science_not_evaluated_blocks_scientific_quality(self):
        goal = dict(_GOAL_SATISFIED)
        goal["requirements"] = [
            {"id": "req-map", "kind": "map", "verdict": "fulfilled"},
            {"id": "req-analysis", "kind": "analysis",
             "verdict": "not_evaluated"},
        ]
        rep = _reconcile(goal_report=goal)
        by_slot = {s.slot: s.status for s in rep.slots}
        assert by_slot["scientific_quality"] in ("not_evaluated", "fail")
        assert rep.overall != "pass"

    def test_quality_review_absent_science_capped(self):
        rep = _reconcile(quality_review_present=False)
        by_slot = {s.slot: s.status for s in rep.slots}
        assert by_slot["scientific_quality"] == "not_evaluated"

    def test_data_role_unresolved_fails_coverage(self):
        rep = _reconcile(chapter={"workflow_contract": {"roles": [
            {"role": "schools", "required": True, "status": "unresolved"},
        ]}})
        by_slot = {s.slot: s.status for s in rep.slots}
        assert by_slot["data_coverage"] == "fail"

    def test_qualification_blocked_fails_coverage_with_reason(self):
        rep = _reconcile(chapter={"workflow_contract": {"roles": [
            {"role": "schools", "required": True, "status": "bound"},
        ]}, "compilation": {"data_qualifications": [
            {"role": "schools", "state": "blocked",
             "reason_code": "missing_field:score"},
        ]}})
        by_slot = {s.slot: s.status for s in rep.slots}
        assert by_slot["data_coverage"] == "fail"
        slot = next(s for s in rep.slots if s.slot == "data_coverage")
        assert "missing_field:score" in slot.detail

    def test_export_requirement_unfulfilled_fails(self):
        goal = dict(_GOAL_SATISFIED)
        goal["requirements"] = [
            {"id": "req-map", "kind": "map", "verdict": "fulfilled"},
            {"id": "req-export", "kind": "export", "verdict": "partial"},
        ]
        goal["verdict"] = "partial"
        rep = _reconcile(goal_report=goal)
        by_slot = {s.slot: s.status for s in rep.slots}
        assert by_slot["export_delivery"] == "fail"


# ── user-wins 尊重 ───────────────────────────────────────────────────────

class TestUserWins:
    def test_user_hidden_layer_is_warning_not_fail(self):
        rep = _reconcile(
            user_hidden_layers=["l1"],
            observation_health={
                "layers": [{"id": "l1", "state": "unknown",
                            "presentation_owner": "user"}],
                "aggregate": "unknown", "health": "degraded",
                "observed_revision": 3,
            })
        by_slot = {s.slot: s.status for s in rep.slots}
        assert by_slot["layer_visibility"] == "warning"
        assert any("user" in n.lower() for n in rep.user_wins_notes)
        # user-wins 不构成 contradiction
        assert rep.contradictions == []


# ── 阶梯与诚实性 ─────────────────────────────────────────────────────────

class TestHonesty:
    def test_partial_aggregate_is_warning(self):
        rep = _reconcile(observation_health={
            "layers": [{"id": "l1", "state": "rendered"}],
            "aggregate": "rendered", "health": "partial",
            "observed_revision": 3,
        })
        by_slot = {s.slot: s.status for s in rep.slots}
        assert by_slot["layer_visibility"] == "warning"
        assert rep.overall == "warning"

    def test_missing_summary_drives_unfinished_warning(self):
        goal = dict(_GOAL_SATISFIED)
        goal["missing_summary"] = ["export:pdf"]
        rep = _reconcile(goal_report=goal)
        by_slot = {s.slot: s.status for s in rep.slots}
        assert by_slot["unfinished_work"] == "warning"

    def test_not_evaluated_not_upgraded(self):
        rep = _reconcile(chapter=None)
        by_slot = {s.slot: s.status for s in rep.slots}
        assert by_slot["data_coverage"] == "not_evaluated"
        assert rep.overall != "pass"

    def test_slots_have_evidence_refs(self):
        rep = _reconcile()
        for slot in rep.slots:
            assert isinstance(slot.evidence_refs, list)
            assert len(slot.evidence_refs) <= 6


# ── 确定性与有界 ─────────────────────────────────────────────────────────

class TestDeterminism:
    def test_same_input_same_output(self):
        assert _reconcile().to_bounded_dict() == _reconcile().to_bounded_dict()

    def test_bounded_payload_shape(self):
        payload = _reconcile().to_bounded_dict()
        assert payload["schema_version"] == "goal_verification.v1"
        assert set(payload) == {
            "schema_version", "goal_verdict", "overall", "slots",
            "contradictions", "user_wins_notes"}

    def test_slot_vocabulary_closed(self):
        rep = _reconcile()
        valid_slots = {
            "required_outputs", "data_coverage", "scientific_quality",
            "layer_visibility", "actual_rendering", "legend_statistics",
            "export_delivery", "unfinished_work",
        }
        assert {s.slot for s in rep.slots} == valid_slots
        assert all(s.status in ("pass", "fail", "warning", "not_evaluated")
                   for s in rep.slots)

    def test_never_raises_on_garbage(self):
        rep = reconcile_goal_verification(
            goal_report="garbage", observation_health=42,
            chapter=[1, 2], task_complete=None)
        # 垃圾输入 → 诚实缺席（None），绝不抛出、绝不伪造对账
        assert rep is None


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
