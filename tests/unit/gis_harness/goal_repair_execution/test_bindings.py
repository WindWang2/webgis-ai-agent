"""G42 WP01/WP02 —— 绑定矩阵与计划指纹测试（纯函数面）。

锁定契约：
- 每个注册动作获得确定裁决（executable / needs_ack / needs_user /
  unsupported / blocked），裁决只消费服务端权威状态；
- ``export:<fmt>`` 按 fmt 语义分流：地图格式 → webgis_map_export（blocking
  保留 + expected_revision CAS）；数据交付格式 → submit_data_delivery
  （needs_ack，未确认零提交）；
- retry_step / rerun_complete 一律 typed blocked（无完整实参/幂等保证，
  不自动重放未知副作用）；
- plan_version 对动作身份稳定、对动作变化敏感（attempt 幂等键成分）。
"""
from __future__ import annotations

from app.services.gis_harness.goal_repair_execution.bindings import (
    adjudicate_actions,
    executable_selection,
    plan_version_for,
)


def _plan(actions):
    return {
        "goal_id": "goal-1",
        "verdict": "partial",
        "signal": "continue",
        "actions": actions,
        "blockers": [],
    }


def _act(action, rid, rule="r"):
    return {
        "rule": rule,
        "requirement_id": rid,
        "action": action,
        "side_effect": "write",
        "path": "x",
        "budget_class": "repair",
        "expected_new_evidence": "e",
    }


def _goal(requirements):
    return {"requirements": requirements}


def test_map_export_format_binds_blocking_cas_entry():
    plan = _plan([_act("export_product", "export:pdf", "export_receipt_absent")])
    goal = _goal([{
        "requirement_id": "export:pdf", "kind": "export",
        "export_format": "pdf", "delivery_expected": False,
    }])
    rows = adjudicate_actions(plan, stored_goal=goal, chapter={},
                              mapspec_revision=7)
    assert len(rows) == 1
    row = rows[0]
    assert row["disposition"] == "executable"
    assert row["entry"] == "webgis_map_export"
    assert row["params"]["format"] == "pdf"
    assert row["params"]["expected_revision"] == 7


def test_delivery_format_binds_delivery_entry_needs_ack():
    plan = _plan([_act("run_delivery", "export:gpkg", "delivery_receipt_absent")])
    goal = _goal([{
        "requirement_id": "export:gpkg", "kind": "export",
        "export_format": "gpkg", "delivery_expected": True,
    }])
    chapter = {"analysis_steps": [
        {"capability": "buffer", "status": "done",
         "bound_ref": "ref:geojson-abc123"}]}
    rows = adjudicate_actions(plan, stored_goal=goal, chapter=chapter,
                              mapspec_revision=0)
    assert rows[0]["disposition"] == "needs_acknowledgment"
    assert rows[0]["entry"] == "submit_data_delivery"
    assert rows[0]["params"]["format"] == "gpkg"
    assert rows[0]["params"]["source_ref"] == "ref:geojson-abc123"


def test_delivery_without_bound_ref_needs_user():
    plan = _plan([_act("run_delivery", "export:csv", "delivery_receipt_absent")])
    goal = _goal([{
        "requirement_id": "export:csv", "kind": "export",
        "export_format": "csv", "delivery_expected": True,
    }])
    rows = adjudicate_actions(plan, stored_goal=goal, chapter={},
                              mapspec_revision=0)
    assert rows[0]["disposition"] == "needs_user_input"


def test_retry_step_and_rerun_complete_typed_blocked():
    plan = _plan([
        _act("retry_step", "analysis:buffer", "receipt_failed"),
        _act("rerun_complete", "analysis:density", "receipt_population_partial"),
    ])
    rows = adjudicate_actions(plan, stored_goal=_goal([]), chapter={},
                              mapspec_revision=0)
    assert all(r["disposition"] == "unsupported" for r in rows)
    assert all("幂等" in r["reason"] or "实参" in r["reason"] for r in rows)


def test_reobserve_and_readonly_actions_executable():
    plan = _plan([
        _act("reobserve", "map", "map_render_stale"),
        _act("stale_reread", "map", "receipt_observation_snapshot"),
        _act("requalify", "analysis:density",
             "receipt_density_without_denominator"),
        _act("resolve_data", "analysis:buffer", "receipt_scope_subset"),
    ])
    rows = adjudicate_actions(plan, stored_goal=_goal([]), chapter={},
                              mapspec_revision=3)
    by_action = {r["action"]: r for r in rows}
    assert by_action["reobserve"]["disposition"] == "executable"
    assert by_action["stale_reread"]["disposition"] == "executable"
    assert by_action["requalify"]["disposition"] == "executable"
    assert by_action["resolve_data"]["disposition"] == "needs_user_input"


def test_unknown_format_unsupported_and_unknown_action_unsupported():
    plan = _plan([
        _act("export_product", "export:dwg", "export_receipt_absent"),
        _act("invented_action", "map", "some_rule"),
    ])
    goal = _goal([{
        "requirement_id": "export:dwg", "kind": "export",
        "export_format": "dwg", "delivery_expected": False,
    }])
    rows = adjudicate_actions(plan, stored_goal=goal, chapter={},
                              mapspec_revision=0)
    assert all(r["disposition"] == "unsupported" for r in rows)


def test_plan_version_stable_and_sensitive():
    a = plan_version_for(_plan([_act("export_product", "export:pdf")]))
    b = plan_version_for(_plan([_act("export_product", "export:pdf")]))
    c = plan_version_for(_plan([_act("reexport", "export:pdf")]))
    d = plan_version_for(_plan([_act("export_product", "export:svg")]))
    assert a == b
    assert a != c
    assert a != d


def test_executable_selection_excludes_user_and_unsupported():
    plan = _plan([
        _act("reobserve", "map", "map_render_stale"),
        _act("resolve_data", "analysis:buffer", "receipt_scope_subset"),
        _act("retry_step", "analysis:x", "receipt_failed"),
    ])
    rows = adjudicate_actions(plan, stored_goal=_goal([]), chapter={},
                              mapspec_revision=0)
    ids = executable_selection(rows)
    assert len(ids) == 1  # 只有 reobserve
    by_id = {r["action_id"]: r for r in rows}
    assert by_id[ids[0]]["action"] == "reobserve"
