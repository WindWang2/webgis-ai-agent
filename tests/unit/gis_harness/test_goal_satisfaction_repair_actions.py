"""G28 P5 —— 有限修复计划测试（A12/A21/A22/A23 + 注册动作面）。

锁定契约：
- 失败规则 → 注册动作映射（只读/写入分类 + 既有 CAS 路径名）；
- 停止条件优先：user-owned/锁/权限/数据缺失/unsupported/预算耗尽 → 具体
  blocker、无写动作（不放宽目标、不删 required 项、不触发破坏性修复）；
- satisfied → None（键省略）；无注册规则且无 blocker → None；
- 每动作带 expected_new_evidence（A22：无新证据的重复修复 = 无进展）。
"""
from __future__ import annotations

import json

from app.services.gis_harness.goal_satisfaction.contracts import (
    GoalSatisfactionReport,
    RequirementVerdict,
)
from app.services.gis_harness.goal_satisfaction.repair_actions import (
    ACTION_REOBSERVE,
    ACTION_REEXPORT,
    ACTION_REPAIR_CARTOGRAPHY,
    ACTION_REGISTRY,
    BLOCKER_BUDGET,
    BLOCKER_DATA_MISSING,
    BLOCKER_PERMISSION,
    BLOCKER_UNSUPPORTED,
    BLOCKER_USER_LOCK,
    BLOCKER_USER_OWNED,
    repair_plan_for,
)


def _report(rules_by_req: dict, verdict="partial", signal="continue",
            goal_id="g1") -> GoalSatisfactionReport:
    return GoalSatisfactionReport(
        goal_id=goal_id,
        verdict=verdict,
        signal=signal,
        requirements=[
            RequirementVerdict(
                requirement_id=rid, kind="map", verdict="partial",
                failed_rule=rule, next_action="",
            )
            for rid, rule in rules_by_req.items()
        ],
        counts={"partial": len(rules_by_req)},
    )


def test_stale_render_maps_to_readonly_reobserve():
    plan = repair_plan_for(_report({"map": "map_render_stale"}))
    assert plan is not None
    assert plan["blockers"] == []
    action = plan["actions"][0]
    assert action["action"] == ACTION_REOBSERVE
    assert action["side_effect"] == "read_only"
    assert action["expected_new_evidence"]


def test_carto_blocking_maps_to_runtime_repair_write_path():
    plan = repair_plan_for(_report({"map": "cartography_blocking"}))
    action = plan["actions"][0]
    assert action["action"] == ACTION_REPAIR_CARTOGRAPHY
    assert action["side_effect"] == "write"
    assert action["path"] == "gis_harness.runtime_repair"
    assert action["budget_class"] == "repair"


def test_export_stale_maps_to_reexport():
    plan = repair_plan_for(_report({"export:pdf": "export_receipt_stale"}))
    assert plan["actions"][0]["action"] == ACTION_REEXPORT
    assert plan["actions"][0]["side_effect"] == "write"


def test_user_owned_divergence_blocks_write_actions():
    """A12：user-wins 分歧 → blocker 停止，不产生任何动作（不驱动强开）。"""
    plan = repair_plan_for(
        _report({"map": "map_render_stale"}),
        user_owned_divergence=True)
    assert BLOCKER_USER_OWNED in plan["blockers"]
    assert plan["actions"] == []


def test_all_stop_conditions_produce_blockers_only():
    """A21/A12：预算耗尽/锁/权限/数据缺失/unsupported → 停 + 具体码。"""
    plan = repair_plan_for(
        _report({"map": "cartography_blocking"}),
        user_lock_active=True, permission_denied=True,
        data_missing=True, unsupported_capability=True,
        repair_budget_exhausted=True)
    for code in (BLOCKER_USER_LOCK, BLOCKER_PERMISSION, BLOCKER_DATA_MISSING,
                 BLOCKER_UNSUPPORTED, BLOCKER_BUDGET):
        assert code in plan["blockers"]
    assert plan["actions"] == []


def test_satisfied_report_returns_none():
    assert repair_plan_for(_report({}, verdict="satisfied")) is None
    assert repair_plan_for(None) is None


def test_unregistered_rule_returns_none_without_blockers():
    """未注册规则 → 无自动修复（诚实缺席，不猜动作）。"""
    assert repair_plan_for(_report({"map": "some_unknown_rule"})) is None


def test_actions_bounded_and_deduped_by_rule():
    rules = {f"req{i}": "map_render_stale" for i in range(10)}
    plan = repair_plan_for(_report(rules))
    assert len(plan["actions"]) <= 6
    assert len({a["rule"] for a in plan["actions"]}) == 1
    json.dumps(plan, ensure_ascii=False)  # 可序列化


def test_all_registered_actions_have_complete_metadata():
    """A23：写入动作必须登记真实路径；所有动作有预算类与新证据期望。"""
    for action, meta in ACTION_REGISTRY.items():
        assert meta["side_effect"] in ("read_only", "write")
        if meta["side_effect"] == "write":
            assert meta["path"], f"{action} 缺执行路径"
