"""G28 P5 —— 有限修复：goal 失败规则 → 可解释、有界、受预算的修复计划。

红线（全部由测试钉死）：

- **不执行任何动作**：本模块是「规则 → 注册动作」的纯映射 + 披露面；
  真正执行走既有 mutation/CAS/approval 路径（runtime_repair / export
  lineage / data_delivery / tool dispatch），消费方（Pi harness 下一轮）
  必须先过 ``loop_budget.loop_retry_admissible``；
- **只读/写入显式分类**（A23）：``read_only`` 动作（受控重观察、stale
  重读、资格重判）可自动重试；``write`` 动作必须携带既有 CAS 路径名，
  绝不盲目重复 —— 每个动作带 ``expected_new_evidence``（A22：无新证据
  的重复修复 = 无进展）；
- **停止条件优先**（A12/A21）：user-owned 分歧 / 用户锁 / 权限不足 /
  数据缺失 / unsupported renderer / 预算耗尽 → 具体 blocker 码，不产生
  写动作；绝不私自放宽目标或删除 required 项；
- **有界**：动作 ≤6、blocker ≤4、全部可序列化。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .contracts import GoalSatisfactionReport, RequirementState

# ── 动作词表（注册面；与既有 continuation/runtime_repair/loop_budget 同族）──

#: 只读动作（可自动重试；无副作用）。
ACTION_REOBSERVE = "reobserve"            # 受控显示重观察（render observation）
ACTION_STALE_REREAD = "stale_reread"      # 陈旧引用重读当前状态
ACTION_REQUALIFY = "requalify"            # 数据资格重判（data_qualification 只读）
ACTION_RESOLVE_DATA = "resolve_data"      # 数据缺口定位（只读分类）

#: 写入动作（必须经既有 CAS/approval 路径；消费方过预算闸）。
ACTION_REPAIR_CARTOGRAPHY = "repair_cartography"  # runtime_repair（重断言 spec 意图）
ACTION_REEXPORT = "reexport"                      # 导出重出（export lineage，新 revision）
ACTION_EXPORT_PRODUCT = "export_product"          # 产品导出（首次）
ACTION_RUN_DELIVERY = "run_delivery"              # 数据交付 job（data_delivery）
ACTION_RETRY_STEP = "retry_step"                  # 失败步骤重试（tool dispatch）
ACTION_RERUN_COMPLETE = "rerun_complete"          # 以完整口径重跑被截断的计算

#: 动作 → 副作用类别 + 既有执行路径（写入动作必须登记真实路径）。
ACTION_REGISTRY: Dict[str, Dict[str, str]] = {
    ACTION_REOBSERVE: {"side_effect": "read_only", "path": "render_observation"},
    ACTION_STALE_REREAD: {"side_effect": "read_only", "path": "session_state_read"},
    ACTION_REQUALIFY: {"side_effect": "read_only", "path": "data_qualification"},
    ACTION_RESOLVE_DATA: {"side_effect": "read_only", "path": "workflow_contract"},
    ACTION_REPAIR_CARTOGRAPHY: {
        "side_effect": "write", "path": "gis_harness.runtime_repair"},
    ACTION_REEXPORT: {"side_effect": "write", "path": "export_lineage"},
    ACTION_EXPORT_PRODUCT: {"side_effect": "write", "path": "export_lineage"},
    ACTION_RUN_DELIVERY: {"side_effect": "write", "path": "data_delivery.service"},
    ACTION_RETRY_STEP: {"side_effect": "write", "path": "tool_dispatch_service"},
    ACTION_RERUN_COMPLETE: {"side_effect": "write", "path": "tool_dispatch_service"},
}

#: 动作 → loop_budget 重试类（消费方以此查 loop_retry_admissible）。
ACTION_BUDGET_CLASS: Dict[str, str] = {
    ACTION_REOBSERVE: "repair",          # SELF_HEAL
    ACTION_REPAIR_CARTOGRAPHY: "repair",  # SELF_HEAL
    ACTION_REEXPORT: "repair",
    ACTION_EXPORT_PRODUCT: "repair",
    ACTION_RUN_DELIVERY: "repair",
    ACTION_RETRY_STEP: "repair",
    ACTION_RERUN_COMPLETE: "repair",
    ACTION_STALE_REREAD: "repair",
    ACTION_REQUALIFY: "requalify",        # DATA_FABRIC
    ACTION_RESOLVE_DATA: "deepen",        # DATA_FABRIC
}

# ── blocker 词表（停止条件；具体、可执行恢复动作缺失 = 停）────────────────
BLOCKER_USER_OWNED = "user_owned_divergence"       # 用户显式决策分歧（user-wins）
BLOCKER_USER_LOCK = "user_lock_active"             # 用户锁（优先，不触发破坏性修复）
BLOCKER_PERMISSION = "permission_denied"           # 权限不足
BLOCKER_DATA_MISSING = "data_missing"              # 数据缺失/不可达
BLOCKER_UNSUPPORTED = "unsupported_capability"     # 能力未合入/渲染器不支持
BLOCKER_BUDGET = "repair_budget_exhausted"         # 修复预算耗尽（runtime_repair）

# ── 失败规则 → 动作映射（只覆盖可自动修复的注册错误）──────────────────────

_RULE_ACTIONS: Dict[str, str] = {
    # 地图/显示面
    "map_render_stale": ACTION_REOBSERVE,
    "frame_product_incomplete": ACTION_REOBSERVE,
    "frame_product_snapshot_only": ACTION_REOBSERVE,
    "cartography_blocking": ACTION_REPAIR_CARTOGRAPHY,
    "map_needs_repair": ACTION_REPAIR_CARTOGRAPHY,
    # 导出/交付面
    "export_receipt_stale": ACTION_REEXPORT,
    "export_receipt_absent": ACTION_EXPORT_PRODUCT,
    "delivery_receipt_absent": ACTION_RUN_DELIVERY,
    "artifact_unverified": ACTION_REEXPORT,
    # 回执语义面
    "receipt_population_partial": ACTION_RERUN_COMPLETE,
    "receipt_artifact_mismatch": ACTION_RERUN_COMPLETE,
    "receipt_failed": ACTION_RETRY_STEP,
    "receipt_cancelled": ACTION_RETRY_STEP,
    "receipt_scope_subset": ACTION_RESOLVE_DATA,
    "receipt_density_without_denominator": ACTION_REQUALIFY,
    "receipt_rate_without_timebase": ACTION_REQUALIFY,
    "receipt_unit_undeclared": ACTION_REQUALIFY,
    "receipt_crs_unverified": ACTION_REQUALIFY,
    "receipt_observation_snapshot": ACTION_REOBSERVE,
}

#: 每轮每规则最多出现一次；全局动作上限。
MAX_ACTIONS = 6
MAX_BLOCKERS = 6

#: 每个动作的「新证据」期望（A22：重复修复必须有新证据才判进展）。
_EXPECTED_NEW_EVIDENCE = {
    ACTION_REOBSERVE: "render_observation@revision>current",
    ACTION_REEXPORT: "export_receipt@revision==current",
    ACTION_RUN_DELIVERY: "delivery_receipt@status==completed",
    ACTION_RERUN_COMPLETE: "receipt@computed_rows==row_count",
    ACTION_RETRY_STEP: "row_status@complete+receipt@present",
    ACTION_REQUALIFY: "qualification@units_declared",
    ACTION_REPAIR_CARTOGRAPHY: "cartography.checks@no_blocking_fail",
    ACTION_EXPORT_PRODUCT: "export_receipt@revision==current",
    ACTION_RESOLVE_DATA: "workflow_contract@data_blockers_empty",
    ACTION_STALE_REREAD: "state@revision==current",
}


def repair_plan_for(
    report: Optional[GoalSatisfactionReport],
    *,
    user_owned_divergence: bool = False,
    user_lock_active: bool = False,
    permission_denied: bool = False,
    data_missing: bool = False,
    unsupported_capability: bool = False,
    repair_budget_exhausted: bool = False,
) -> Optional[Dict[str, Any]]:
    """goal 报告 → 有界修复计划（纯函数；None = 无可修复缺口，键省略）。

    停止条件入参由调用方按既有事实填充（finalizer 已知 user-wins 披露 /
    runtime_repair exhaustion；权限/锁面由消费方核实后填充）。
    """
    if report is None:
        return None
    if report.verdict.value == "satisfied":
        return None
    blockers: List[str] = []
    if user_owned_divergence:
        blockers.append(BLOCKER_USER_OWNED)
    if user_lock_active:
        blockers.append(BLOCKER_USER_LOCK)
    if permission_denied:
        blockers.append(BLOCKER_PERMISSION)
    if data_missing:
        blockers.append(BLOCKER_DATA_MISSING)
    if unsupported_capability:
        blockers.append(BLOCKER_UNSUPPORTED)
    if repair_budget_exhausted:
        blockers.append(BLOCKER_BUDGET)
    # 停止条件优先：任一硬 blocker → 只披露、无动作（不放宽、不删需求、
    # 不触发破坏性修复 —— A12/A21）。
    actions: List[Dict[str, str]] = []
    seen_rules: set = set()
    if not blockers:
        for row in report.requirements:
            if row.verdict == RequirementState.FULFILLED:
                continue
            rule = row.failed_rule
            if not rule or rule in seen_rules:
                continue
            action = _RULE_ACTIONS.get(rule)
            if action is None:
                continue  # 未注册规则 → 无自动修复（诚实缺席，不猜）
            seen_rules.add(rule)
            meta = ACTION_REGISTRY[action]
            actions.append({
                "rule": rule[:48],
                "requirement_id": row.requirement_id[:64],
                "action": action,
                "side_effect": meta["side_effect"],
                "path": meta["path"][:48],
                "budget_class": ACTION_BUDGET_CLASS.get(action, "repair"),
                "expected_new_evidence":
                    _EXPECTED_NEW_EVIDENCE.get(action, "")[:64],
            })
            if len(actions) >= MAX_ACTIONS:
                break
    if not actions and not blockers:
        return None  # 无注册动作、无停止条件 → 无计划（键省略）
    return {
        "goal_id": report.goal_id[:128],
        "verdict": report.verdict.value,
        "signal": report.signal.value,
        "actions": actions,
        "blockers": blockers[:MAX_BLOCKERS],
        # A22：消费方每轮必须携带新证据；同证据重复 → 无进展 → 停。
        "progress_rule": "new_evidence_required_per_round",
        "max_actions_per_round": MAX_ACTIONS,
    }


__all__ = [
    "ACTION_REOBSERVE",
    "ACTION_STALE_REREAD",
    "ACTION_REQUALIFY",
    "ACTION_RESOLVE_DATA",
    "ACTION_REPAIR_CARTOGRAPHY",
    "ACTION_REEXPORT",
    "ACTION_EXPORT_PRODUCT",
    "ACTION_RUN_DELIVERY",
    "ACTION_RETRY_STEP",
    "ACTION_RERUN_COMPLETE",
    "ACTION_REGISTRY",
    "ACTION_BUDGET_CLASS",
    "BLOCKER_USER_OWNED",
    "BLOCKER_USER_LOCK",
    "BLOCKER_PERMISSION",
    "BLOCKER_DATA_MISSING",
    "BLOCKER_UNSUPPORTED",
    "BLOCKER_BUDGET",
    "repair_plan_for",
]
