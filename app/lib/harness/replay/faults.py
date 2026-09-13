"""故障注入（B6，ADR-0183 决策八 / D8）：在 replayer 环境边界编译故障。

故障规格 ``{type, target_turn?, target?}`` 由 :func:`apply_faults` 纯变换到
场景（op 收据 / ref 表 / visual judge 声明），**不给生产代码埋测试钩子**。
每个故障绑定 fail-closed 断言（:data:`FAULT_CONTRACTS`）：注入后场景必须
red（gate / goal / ok 至少一处劣化），禁止静默 pass。
"""
from __future__ import annotations

import copy
from typing import Any, Dict, List, Optional

from app.lib.harness.replay.replayer import Scenario, ScenarioOp

FAULT_TYPES = (
    "source_unavailable",     # 源拒绝/不可达 → 收据 error
    "timeout",                # 工具超时 → error 收据（timeout）
    "invalid_tool_result",    # 畸形收据 → 变异无有效证据 → MSV fail
    "stale_ref",              # ref 表清除 → cursor NOT_FOUND
    "map_revision_conflict",  # 收据带 superseded 世代标签 → CQ superseded
    "pi_restart",             # turn 中断：目标轮之前证据清空 → 缺证据红
    "late_sse",               # 结果迟到：收据缺指纹 → 收敛缺失红
    "renderer_failure",       # runtime_validate 失败 → headless 检查红
    "judge_unavailable",      # 视觉裁判缺席 → L5 not_evaluated（fail-closed）
    "store_transient",        # 首写失败 + 重试成功 → 恢复语义
)

#: 故障 → 最小劣化面（"any" = ok 翻红即可；具体键 = 必须出现的劣化证据）。
FAULT_CONTRACTS: Dict[str, str] = {
    "source_unavailable": "gate_red",
    "timeout": "gate_red",
    "invalid_tool_result": "gate_red",
    "stale_ref": "cursor_fail",
    "map_revision_conflict": "cq_not_pass",
    "pi_restart": "gate_red",
    "late_sse": "gate_red",
    "renderer_failure": "cq_not_pass",
    "judge_unavailable": "goal_not_evaluated",
    "store_transient": "recovered",
}


def _first_mutation_op(turn) -> Optional[ScenarioOp]:
    for op in turn.ops:
        if "upsert" in op.tool or "layer" in op.tool:
            return op
    return turn.ops[0] if turn.ops else None


def apply_faults(scenario: Scenario) -> Scenario:
    """把 scenario.faults 编译进场景（纯函数；返回深拷贝）。"""
    broken = copy.deepcopy(scenario)
    for fault in broken.faults:
        ftype = str(fault.get("type") or "")
        target_turn = int(fault.get("target_turn") or 0)
        if not broken.turns or target_turn >= len(broken.turns):
            continue
        turn = broken.turns[target_turn]
        if ftype in ("source_unavailable", "timeout"):
            op = _first_mutation_op(turn)
            if op is not None:
                op.result = {"success": False, "is_error": True, "error_msg":
                             ("source unavailable" if ftype == "source_unavailable"
                              else "tool timeout after 30000ms")}
                op.is_error = True
                op.error_msg = op.result["error_msg"]
        elif ftype == "invalid_tool_result":
            op = _first_mutation_op(turn)
            if op is not None:
                op.result = {"unexpected_shape": True}
                op.is_error = False
        elif ftype == "stale_ref":
            turn.refs = {}
        elif ftype == "map_revision_conflict":
            # 世代冲突：目标轮全部收据落在新指纹代际之外（superseded）。
            for op in turn.ops:
                op.result = {"success": False, "is_error": True,
                             "error_code": "superseded",
                             "mapspec_fingerprint":
                             "fingerprint-superseded-generation"}
                op.is_error = True
                op.error_msg = "map revision conflict"
        elif ftype == "pi_restart":
            for prior in broken.turns[:target_turn]:
                prior.ops = []
        elif ftype == "late_sse":
            # 结果迟到：本轮收据全部缺失世代标签 → 收敛缺失。
            for op in turn.ops:
                op.result.pop("mapspec_fingerprint", None)
        elif ftype == "renderer_failure":
            # 渲染失败 = 前端观测不可信：style 未加载、reconcile 报错。
            fixture = turn.cartography
            if isinstance(fixture, dict):
                observation = ((fixture.get("map_state") or {})
                               .get("_cartographic_observation"))
                if isinstance(observation, dict):
                    observation["style_loaded"] = False
                    observation["reconcile_error"] = "renderer failure"
        elif ftype == "judge_unavailable":
            fixture = turn.cartography
            if isinstance(fixture, dict):
                fixture.pop("visual_judge", None)
        elif ftype == "store_transient":
            op = _first_mutation_op(turn)
            if op is not None:
                failed = copy.deepcopy(op)
                failed.call_id = f"{op.call_id}-transient"
                failed.result = {"success": False, "is_error": True,
                                 "error_msg": "store transient"}
                failed.is_error = True
                failed.error_msg = "store transient"
                turn.ops.insert(turn.ops.index(op), failed)
        # 未知故障类型：诚实保留（未编译），由契约断言环节暴露。
    return broken


def assert_fault_contract(
    scenario: Scenario, gate_results: List[Dict[str, Any]],
    goal_results: List[Dict[str, Any]], ok: bool,
) -> Optional[str]:
    """校验 fail-closed 契约；返回违规描述（None = 通过）。"""
    violations: List[str] = []
    for fault in scenario.faults:
        ftype = str(fault.get("type") or "")
        contract = FAULT_CONTRACTS.get(ftype)
        if contract is None:
            violations.append(f"{scenario.scenario_id}: unknown fault {ftype!r}")
            continue
        target_turn = int(fault.get("target_turn") or 0)
        gate = gate_results[target_turn] if target_turn < len(gate_results) else {}
        goal = (goal_results[target_turn]
                if target_turn < len(goal_results) else {})
        gate_checks = gate.get("checks") or {}
        if contract == "gate_red" and ok:
            violations.append(f"{scenario.scenario_id}: {ftype} did not turn red")
        if contract == "cursor_fail":
            cursor = gate_checks.get("CursorResolutionRate") or {}
            if cursor.get("passed") is not False:
                violations.append(f"{scenario.scenario_id}: {ftype} cursor not red")
        if contract == "cq_not_pass":
            cq = gate_checks.get("CartographicQuality") or {}
            if cq.get("passed") is True:
                violations.append(f"{scenario.scenario_id}: {ftype} CQ still green")
        if contract == "goal_not_evaluated":
            if goal.get("status") != "not_evaluated":
                violations.append(f"{scenario.scenario_id}: {ftype} goal not honest")
        if contract == "recovered" and ok:
            violations.append(
                f"{scenario.scenario_id}: {ftype} transient + retry must keep "
                "MSV red (session-proportional honesty)")
    return "; ".join(violations) if violations else None
