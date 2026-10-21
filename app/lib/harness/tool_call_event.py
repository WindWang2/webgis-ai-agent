"""Unified telemetry event model shared by PiAgentHarness and tool_metrics.

Defines a single ToolCallEvent dataclass that both the evaluation harness
(PiAgentHarness) and the production metrics logger (tool_metrics.py) consume,
ensuring schema consistency across the two telemetry systems.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional

# #1550：result 载荷是 CartographicResultEvidence 投影（evidence.py 单一
# 投影入口的产出），不再是匿名 dict[str, Any]。total=False 让取消/异常路径
# 的退化形状（{} 或仅 cancelled）仍是合法载体。
from app.lib.harness.evidence import CartographicResultEvidence


def _empty_result_evidence() -> CartographicResultEvidence:
    """TypedDict 字段的可变缺省工厂（mypy 不接受裸 ``dict`` 工厂）。"""
    return {}


@dataclass
class ToolCallEvent:
    """A single tool invocation event — shared schema for telemetry."""
    tool_call_id: str
    tool_name: str
    arguments: Dict[str, Any] = field(default_factory=dict)
    duration_ms: int = 0
    is_error: bool = False
    error_msg: str = ""
    cache_hit: bool = False
    session_id: Optional[str] = None
    # #1550：具名证据载体（键集见 CartographicResultEvidence）。
    result: CartographicResultEvidence = field(
        default_factory=_empty_result_evidence
    )
    arg_bytes: int = 0
    result_bytes: int = 0
