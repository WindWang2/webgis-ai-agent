"""G42 —— goal repair 执行契约面（typed 词表 + 有界视图）。

红线（与 repair_actions.py 同族）：

- 本模块只定义词表与视图形状，不做任何执行；
- ``GoalRepairErrorCode`` 是唯一 typed 拒绝通道（code 词表封闭，HTTP/工具
  面统一映射，不把内部异常文本当语义）；
- 视图全部有界（字符串截断、列表截断），与 goal_satisfaction 投影同纪律。
"""
from __future__ import annotations

from typing import Any, Dict, Optional

# ── typed 拒绝码（封闭词表；start/preview/cancel/status 的失败语义）────────
ERROR_PLAN_NOT_FOUND = "PLAN_NOT_FOUND"          # 存储块无 goal_repair_plan
ERROR_GOAL_SATISFIED = "GOAL_SATISFIED"          # verdict 已 satisfied（无待补全）
ERROR_SESSION_BUSY = "SESSION_BUSY"              # UI 启动时会话有活跃 Pi turn
ERROR_ACTIVE_ATTEMPT = "ACTIVE_ATTEMPT_EXISTS"   # 同 goal 已有活跃 attempt（复用）
ERROR_NO_PROGRESS = "NO_PROGRESS"                # 同计划+同证据重复（A22 停止）
ERROR_BUDGET_DENIED = "BUDGET_DENIED"            # 预算 admission 拒绝（副作用前）
ERROR_GOAL_SUPERSEDED = "GOAL_SUPERSEDED"        # 目标已被新任务替换
ERROR_REVISION_DRIFT = "REVISION_DRIFT"          # 快照 revision 与当前不一致
ERROR_ACK_REQUIRED = "ACK_REQUIRED"              # 交付损失确认缺席（零提交）
ERROR_PERMISSION_DENIED = "PERMISSION_DENIED"
ERROR_ATTEMPT_NOT_FOUND = "ATTEMPT_NOT_FOUND"
ERROR_ATTEMPT_FINISHED = "ATTEMPT_FINISHED"      # 已终态 attempt 不可取消/续跑
ERROR_INVALID_ACTIONS = "INVALID_ACTION_IDS"     # 提交的 action 选择不在计划内
ERROR_START_FAILED = "START_FAILED"

#: 执行绑定裁决（WP01：每个计划动作 → 可执行/需用户/不支持）。
DISPOSITION_EXECUTABLE = "executable"
DISPOSITION_NEEDS_ACK = "needs_acknowledgment"   # 交付损失确认（复用既有确认面）
DISPOSITION_NEEDS_USER = "needs_user_input"      # 数据缺口/用户决定（停止请求）
DISPOSITION_UNSUPPORTED = "unsupported"          # 无既有安全执行入口（typed blocked）
DISPOSITION_BLOCKED = "blocked"                  # 前置条件不满足（预算/权限/锁）

#: attempt 内单动作状态（有界状态机；终态不可逆）。
ACTION_PENDING = "pending"
ACTION_RUNNING = "running"
ACTION_SUCCEEDED = "succeeded"
ACTION_FAILED = "failed"
ACTION_SKIPPED_CANCELLED = "skipped_cancelled"   # 取消后未启动（未提交副作用）
ACTION_NEEDS_ACK = "needs_acknowledgment"        # 终态：等用户确认（零提交）
ACTION_NEEDS_USER = "needs_user_input"           # 终态：请求用户资料/决定
ACTION_UNSUPPORTED = "unsupported"               # 终态：typed blocked（保留原需求）
ACTION_BLOCKED = "blocked"                       # 终态：预算/权限/前置失败
ACTION_WAITING_OBSERVATION = "waiting_observation"  # 终态：有界等待后仍无新观察
ACTION_WAITING_DELIVERY = "waiting_delivery"     # 终态：job 已提交仍在跑（读路径惰性对账）
ACTION_STALE = "stale"                           # 终态：goal/revision 已换代

_TERMINAL_ACTION_STATES = frozenset({
    ACTION_SUCCEEDED, ACTION_FAILED, ACTION_SKIPPED_CANCELLED,
    ACTION_NEEDS_ACK, ACTION_NEEDS_USER, ACTION_UNSUPPORTED,
    ACTION_BLOCKED, ACTION_WAITING_OBSERVATION, ACTION_WAITING_DELIVERY,
    ACTION_STALE,
})

#: attempt 收口结果（durable job 终态之外的领域语义）。
OUTCOME_COMPLETED = "completed"
OUTCOME_FAILED = "failed"
OUTCOME_CANCELLED = "cancelled"
OUTCOME_OUTCOME_UNKNOWN = "outcome_unknown"      # 副作用后回执前崩溃且不可对账
OUTCOME_NO_PROGRESS = "no_progress"
OUTCOME_SUPERSEDED = "superseded"

#: attempt 执行体入口（生命周期与 G30 skill job 同族）。
ENTRY_UI = "ui"
ENTRY_PI_TOOL = "pi_tool"

MAX_ACTIONS = 6                     # 与 repair_actions.MAX_ACTIONS 一致
MAX_ACK_CODES = 8
MAX_BINDINGS = 8
MAX_TEXT = 200

PREVIEW_SCHEMA = "goalrepair.preview/v1"
ATTEMPT_SCHEMA = "goalrepair.attempt/v1"
TASK_TYPE = "goal_repair_attempt"
JOB_KIND = "goal_repair"


class GoalRepairErrorCode(Exception):
    """typed 拒绝（code ∈ 封闭词表；HTTP/工具面按 code 映射）。"""

    def __init__(self, code: str, message: str,
                 detail: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(message)
        self.code = str(code)[:48]
        self.message = str(message)[:MAX_TEXT]
        self.detail = {str(k): v for k, v in (detail or {}).items()} \
            if isinstance(detail, dict) else {}


def action_state_is_terminal(state: str) -> bool:
    return str(state or "") in _TERMINAL_ACTION_STATES


def bounded_action_receipt(
    *, action_id: str, action: str, requirement_id: str, status: str,
    detail: str = "", evidence_ref: str = "", download_url: str = "",
    job_id: str = "", error_code: str = "",
) -> Dict[str, Any]:
    """单动作回执（有界；attempt result_summary 的行形状）。"""
    return {
        "action_id": str(action_id)[:16],
        "action": str(action)[:32],
        "requirement_id": str(requirement_id)[:64],
        "status": str(status)[:24],
        "detail": str(detail)[:MAX_TEXT],
        "evidence_ref": str(evidence_ref)[:128],
        "download_url": str(download_url)[:256],
        "job_id": str(job_id)[:48],
        "error_code": str(error_code)[:40],
    }


__all__ = [
    "ERROR_PLAN_NOT_FOUND", "ERROR_GOAL_SATISFIED", "ERROR_SESSION_BUSY",
    "ERROR_ACTIVE_ATTEMPT", "ERROR_NO_PROGRESS", "ERROR_BUDGET_DENIED",
    "ERROR_GOAL_SUPERSEDED", "ERROR_REVISION_DRIFT", "ERROR_ACK_REQUIRED",
    "ERROR_PERMISSION_DENIED", "ERROR_ATTEMPT_NOT_FOUND",
    "ERROR_ATTEMPT_FINISHED", "ERROR_INVALID_ACTIONS", "ERROR_START_FAILED",
    "DISPOSITION_EXECUTABLE", "DISPOSITION_NEEDS_ACK", "DISPOSITION_NEEDS_USER",
    "DISPOSITION_UNSUPPORTED", "DISPOSITION_BLOCKED",
    "ACTION_PENDING", "ACTION_RUNNING", "ACTION_SUCCEEDED", "ACTION_FAILED",
    "ACTION_SKIPPED_CANCELLED", "ACTION_NEEDS_ACK", "ACTION_NEEDS_USER",
    "ACTION_UNSUPPORTED", "ACTION_BLOCKED", "ACTION_WAITING_OBSERVATION",
    "ACTION_WAITING_DELIVERY", "ACTION_STALE",
    "OUTCOME_COMPLETED", "OUTCOME_FAILED", "OUTCOME_CANCELLED",
    "OUTCOME_OUTCOME_UNKNOWN", "OUTCOME_NO_PROGRESS", "OUTCOME_SUPERSEDED",
    "ENTRY_UI", "ENTRY_PI_TOOL",
    "MAX_ACTIONS", "MAX_ACK_CODES", "MAX_BINDINGS", "MAX_TEXT",
    "PREVIEW_SCHEMA", "ATTEMPT_SCHEMA", "TASK_TYPE", "JOB_KIND",
    "GoalRepairErrorCode", "action_state_is_terminal", "bounded_action_receipt",
]
