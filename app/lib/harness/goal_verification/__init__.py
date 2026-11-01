"""Harness 目标验收对账层（GOAL 05 / ADR-0279）。

唯一入口 ``reconcile_goal_verification``：ADR-0183 goal 报告 ↔ 渲染观察
↔ 数据资格 ↔ 完成回执 的确定性对账（false-PASS 守门）。
只读消费、零 IO、不重算 ADR-0183 判定。
"""
from app.lib.harness.goal_verification.reconcile import (
    SCHEMA_VERSION,
    GoalVerificationReport,
    VerificationSlot,
    reconcile_goal_verification,
)

__all__ = [
    "SCHEMA_VERSION",
    "GoalVerificationReport",
    "VerificationSlot",
    "reconcile_goal_verification",
]
