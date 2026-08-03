"""Unified telemetry event model shared by PiAgentHarness and tool_metrics.

Defines a single ToolCallEvent dataclass that both the evaluation harness
(PiAgentHarness) and the production metrics logger (tool_metrics.py) consume,
ensuring schema consistency across the two telemetry systems.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional


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
    result: Dict[str, Any] = field(default_factory=dict)
    arg_bytes: int = 0
    result_bytes: int = 0
