"""统一 turn 错误分类（H03：transport/tool/abort/policy/retryable 词表）。

背景：错误词表此前有三套互不相通的来源 ——
1. legacy 工具面 ``turn_recovery.classify_failure`` → planning ``FailureClass``
   （validation/missing_ref/.../internal）+ turn 级字面量（turn_timeout /
   no_progress / empty_result / max_rounds / chat_stream_exception）；
2. Pi bridge 私有词表（pi_aborted / pi_turn_budget / pi_stall /
   pi_process_died / pi_send_error / pi_unclassified_error / pi_abort_*）；
3. route 层 catch-all（无分类，一律裸 error 事件）。

前端无法在不解析中文文案的情况下区分「可重连」「该重发」「该停止」。
本模块提供**单一映射表**：所有路径的错误事件（task_error / step_error /
error）additive 携带 ``error_class`` 字段，语义跨 Pi/legacy 一致。

纪律：
- additive：既有 ``failure_class`` 字段与取值一个不删不改；``error_class``
  是新增旁证。前端旧版本忽略未知字段，零破坏。
- 诚实：未知/未分类一律落 ``internal_error``，绝不猜测成可重试类；
  分类器自身不抛异常（分类失败 = internal_error + 日志）。
- 本模块不做恢复决策，只提供分类与 ``is_retryable`` 语义标注；自动重试
  策略仍归 planning.recovery / vendor auto-retry 所有。
"""
from __future__ import annotations

import logging
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)


class TurnErrorClass(str, Enum):
    """跨 Pi/legacy 统一的 turn/step 错误分类（SSE ``error_class`` 字段词表）。"""

    #: 传输/桥接面故障（Pi 进程死亡、RPC send 失败）。客户端可重连续读。
    transport_error = "transport_error"
    #: 客户端断连导致的 turn 中断（服务端视角「正常」取消，可重连续读）。
    retryable_disconnect = "retryable_disconnect"
    #: 工具执行失败（step 层默认归类；含校验/缺数据/工具内部错误）。
    tool_error = "tool_error"
    #: 用户/系统主动取消（cooperative cancel、abort RPC）。
    agent_abort = "agent_abort"
    #: 策略/权限/配额门拒绝（session clearing 中、auth、resource_limit）。
    policy_reject = "policy_reject"
    #: 回合总预算耗尽（legacy 900s / Pi 300s；两预算刻意独立）。
    turn_timeout = "turn_timeout"
    #: 连续无进展熔断（重复/失败工具结果）。
    no_progress = "no_progress"
    #: provider 空补全（模型返回空响应）。
    empty_result = "empty_result"
    #: 达到最大工具调用轮数。
    max_rounds = "max_rounds"
    #: 模型侧异常/停滞（vendor 停滞、auto-retry 耗尽）。
    provider_error = "provider_error"
    #: 未分类（兜底：未知异常、分类器失败）。诚实降级，绝不标成可重试。
    internal_error = "internal_error"


#: 客户端可以安全地「重连续读 / 重试传输」的分类。turn 级预算类
#: （turn_timeout/no_progress/max_rounds）刻意不在内 —— 重发同一任务
#: 只会复现同样的失败，客户端应缩小任务或换策略，而不是自动重试。
RETRYABLE_ERROR_CLASSES = frozenset({
    TurnErrorClass.transport_error.value,
    TurnErrorClass.retryable_disconnect.value,
    TurnErrorClass.provider_error.value,
})

# planning FailureClass（工具面）→ 统一分类。validation/missing_ref/no_data/
# empty_result 等工具语义失败在 step 层一律是 tool_error（step_error 的
# 语义就是「这次工具调用失败」，细分类已由 failure_class 携带）。
_TOOL_FAILURE_CLASS_MAP = {
    "validation": TurnErrorClass.tool_error,
    "missing_ref": TurnErrorClass.tool_error,
    "empty_result": TurnErrorClass.tool_error,
    "no_data": TurnErrorClass.tool_error,
    "auth": TurnErrorClass.policy_reject,
    "tool_unavailable": TurnErrorClass.tool_error,
    "resource_limit": TurnErrorClass.policy_reject,
    "transient_network": TurnErrorClass.transport_error,
    "cancelled": TurnErrorClass.agent_abort,
    "internal": TurnErrorClass.internal_error,
}

# turn 级 failure_class 字面量（legacy engine / Pi bridge settle 词表）
_TURN_FAILURE_CLASS_MAP = {
    # legacy turn 级
    "turn_timeout": TurnErrorClass.turn_timeout,
    "no_progress": TurnErrorClass.no_progress,
    "empty_result": TurnErrorClass.empty_result,
    "max_rounds": TurnErrorClass.max_rounds,
    "turn_failure": TurnErrorClass.internal_error,
    "chat_stream_exception": TurnErrorClass.internal_error,
    # 工具面 cancelled 出现在 turn 级（全局取消）同样是 abort 语义
    "cancelled": TurnErrorClass.agent_abort,
    # Pi bridge 词表
    "pi_aborted": TurnErrorClass.agent_abort,
    "pi_turn_budget": TurnErrorClass.turn_timeout,
    "pi_stall": TurnErrorClass.provider_error,
    "pi_process_died": TurnErrorClass.transport_error,
    "process_died": TurnErrorClass.transport_error,
    "pi_send_error": TurnErrorClass.transport_error,
    "pi_unclassified_error": TurnErrorClass.internal_error,
    "pi_turn_error": TurnErrorClass.internal_error,
}


def error_class_for_tool_failure(failure_class: Optional[str]) -> str:
    """工具面 failure_class（planning FailureClass 值）→ 统一 error_class。

    未知/None → ``tool_error``：调用方语境已确认是工具失败（step_error），
    未细分不是「未知错误」而是「工具失败未细分」。
    """
    if not failure_class:
        return TurnErrorClass.tool_error.value
    mapped = _TOOL_FAILURE_CLASS_MAP.get(failure_class)
    if mapped is None:
        # 未登记的工具面分类（前向兼容新 FailureClass）：step 语境下仍诚实
        # 归 tool_error，细语义继续由原 failure_class 字段携带。
        return TurnErrorClass.tool_error.value
    return mapped.value


def error_class_for_turn_failure(failure_class: Optional[str]) -> str:
    """turn 级 failure_class → 统一 error_class。未知/None → internal_error
    （turn 层的未知必须诚实地不可重试）。"""
    if not failure_class:
        return TurnErrorClass.internal_error.value
    if failure_class.startswith("pi_abort"):
        # pi_abort_<source> 家族（session_deleted / user / system ...）
        return TurnErrorClass.agent_abort.value
    mapped = _TURN_FAILURE_CLASS_MAP.get(failure_class)
    if mapped is None:
        return TurnErrorClass.internal_error.value
    return mapped.value


def is_retryable(error_class: str) -> bool:
    """该分类是否允许客户端重连续读/重试传输（决策辅助，非自动重试）。"""
    return error_class in RETRYABLE_ERROR_CLASSES


def classify_turn_exception(exc: BaseException) -> str:
    """路由层 catch-all 的异常分类（route 合成 error 事件的 error_class）。

    已知语义异常按类型映射（仓内先例：不用异常文本匹配）；其余一律
    internal_error。本函数不抛异常。
    """
    try:
        # 调用期惰性 import：execution_engine 在模块头 import 本模块，
        # 顶层互引会成环；调用时引擎必已加载。
        from app.services.chat.execution_engine import SessionClearingError

        clearing = (SessionClearingError,)
    except Exception:  # noqa: BLE001 — import 失败按未知处理
        clearing = ()
    if clearing and isinstance(exc, clearing):
        return TurnErrorClass.policy_reject.value
    if not isinstance(exc, Exception):
        # CancelledError/GeneratorExit 是取消语义（正常不走到这 —— 断连路径
        # 不合成 error 事件；防御兜底归 abort 而非 internal）
        return TurnErrorClass.agent_abort.value
    return TurnErrorClass.internal_error.value
