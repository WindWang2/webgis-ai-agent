"""回合失败分类与恢复（E-8 / #899 拆分：自 execution_engine 原样搬移）。

HonestTurnFailure 语义异常族（#685）与 design-v3 §recovery 的失败分类
（failure_class + recovery_action）——纯函数性质，与引擎的锁/SSE 编排
职责分离。execution_engine 保留 re-export 兼容既有 import。
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional

logger = logging.getLogger(__name__)

if TYPE_CHECKING:  # 仅类型注解需要，避免运行时 import 环
    from app.services.tool_dispatch_service import ToolDispatchResult


class HonestTurnFailure(RuntimeError):
    """#685: 非流式诚实失败的语义异常（外层 chat() 按类型 settle failure_class）。

    仓内先例：SessionClearingError/PiRpcError —— 不用异常文本匹配做分类，
    改文案不会脱靶。
    """

    failure_class = "turn_failure"

class EmptyCompletionError(HonestTurnFailure):
    failure_class = "empty_result"

class MaxRoundsExhaustedError(HonestTurnFailure):
    failure_class = "max_rounds"

class NoProgressError(HonestTurnFailure):
    failure_class = "no_progress"

class TurnTimeoutError(HonestTurnFailure):
    """H-2（#857）：legacy 回合总时长预算耗尽（对齐 Pi 路径 900s 整轮预算）。"""

    failure_class = "turn_timeout"



def classify_failure(
    outcome: Optional[ToolDispatchResult],
    exception: Optional[Exception] = None,
) -> tuple[Optional[str], Optional[str]]:
    """design-v3 §recovery：把一次工具失败分类成 failure_class + recovery_action。

    供 step_error SSE / decision_log 附加字段使用（additive，不改变任何
    现有行为与自愈路径）。
    """
    from app.services.planning.models import FailureClass as _FailureClass
    from app.services.planning.recovery import (
        classify_error as _classify_error,
        recovery_action_for as _recovery_action_for,
    )
    try:
        if exception is not None:
            fc = _classify_error(exception=exception)
        else:
            raw = outcome.raw_result if isinstance(outcome.raw_result, dict) else {}
            if raw.get("cancelled"):
                fc = _FailureClass.cancelled
            else:
                fc = _classify_error(
                    status=outcome.status,
                    code=raw.get("code"),
                    error_type=raw.get("error_type"),
                    message=outcome.error_msg,
                )
        ra = _recovery_action_for(fc)
        return fc.value, ra.value
    except Exception as e:  # noqa: BLE001 分类失败不拖垮主流程
        logger.warning(f"[chat_execution_engine] failure 分类失败: {e}")
        return None, None
