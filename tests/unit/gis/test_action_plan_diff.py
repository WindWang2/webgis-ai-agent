"""H10：Plan diff / replan / legacy adapter / 遥测测试。"""
from __future__ import annotations


from app.lib.gis.action_ir import GISAction, GISActionPlan
from app.services.gis_action.diff import diff_plans, plans_equal
from app.services.gis_action.service import GISActionService


def _plan(plan_id, *actions) -> GISActionPlan:
    return GISActionPlan(plan_id=plan_id, actions=list(actions))


_A = dict(action_id="act-a", kind="cartograph", tool="t1", params={"k": 5, "palette": "YlOrRd"})


class TestPlanDiff:
    def test_identical_plans_empty_diff(self):
        p1 = _plan("gap-1", GISAction(**_A))
        p2 = _plan("gap-2", GISAction(**_A))
        d = diff_plans(p1, p2)
        assert d.empty
        assert plans_equal(p1, p2)

    def test_param_change_reports_keys(self):
        p1 = _plan("gap-1", GISAction(**_A))
        changed = dict(_A, params={"k": 7, "palette": "YlOrRd"})
        p2 = _plan("gap-2", GISAction(**changed))
        d = diff_plans(p1, p2)
        assert not d.added and not d.removed
        changes = d.param_changed["act-a"]
        assert [c.key for c in changes] == ["k"]
        assert d.describe().startswith("参数变化")

    def test_added_and_removed(self):
        p1 = _plan("gap-1", GISAction(**_A),
                   GISAction(action_id="act-old", kind="inspect", tool="t9"))
        p2 = _plan("gap-2", GISAction(**_A),
                   GISAction(action_id="act-new", kind="export", tool="t8"))
        d = diff_plans(p1, p2)
        assert d.added == ["act-new"]
        assert d.removed == ["act-old"]

    def test_side_effect_change(self):
        p1 = _plan("gap-1", GISAction(**_A))
        p2 = _plan("gap-2", GISAction(**dict(_A, side_effect="session_state")))
        d = diff_plans(p1, p2)
        assert d.side_effect_changed == {"act-a": ["pure", "session_state"]}

    def test_tool_change(self):
        p1 = _plan("gap-1", GISAction(**_A))
        p2 = _plan("gap-2", GISAction(**dict(_A, tool="t2")))
        d = diff_plans(p1, p2)
        assert d.tool_changed == {"act-a": ["t1", "t2"]}

    def test_reorder_detected_insertion_not_reorder(self):
        base = GISAction(**_A)
        b = GISAction(action_id="act-b", kind="inspect", tool="t2")
        p1 = _plan("gap-1", base, b)
        p2 = _plan("gap-2", b, base)
        assert diff_plans(p1, p2).reordered is True
        # 插入新动作不算 reorder（共同动作相对序保持）。
        p3 = _plan("gap-3", GISAction(**_A), b,
                   GISAction(action_id="act-c", kind="export", tool="t3"))
        assert diff_plans(p1, p3).reordered is False

    def test_service_replan_creates_superseding_revision(self):
        svc = GISActionService()
        p1 = _plan("gap-1", GISAction(**_A))
        new_actions = [GISAction(**dict(
            _A, params={"k": 7, "palette": "YlOrRd"}))]
        p2 = svc.replan(p1, new_actions)
        assert p2.revision == 2
        assert p2.supersedes == "gap-1"
        assert p2.plan_id != p1.plan_id
        d = svc.diff(p1, p2)
        assert [c.key for c in d.param_changed["act-a"]] == ["k"]
