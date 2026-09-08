"""18 阶段证据链发射辅助（V4 Wave 8 — ADR-0104 决策 9）。

Phase-0 审计 07：``GisTraceChain`` 的 18 个规范阶段中只有 5 个在生产
路径有发射点（MODEL_ROUTING / TOOL_CALLS / ARGUMENTS / TOOL_RESULTS /
MAP_MUTATIONS），真实流量 ``completeness()`` ≈28%。本模块提供统一的
薄发射面：

- ``emit_chain(stage, ...)``：从当前 RuntimeContext 解析 turn_id（生产
  路径在 Pi dispatch / chat 路由 / 引擎回 合处绑定），record_stage；
  无上下文 → 静默 False（记录绝不阻断执行、绝不伪造链）。
- ``emit_chain_once(stage, ...)``：同链同阶段已覆盖时跳过 —— 双路径
  （引擎侧 + 调度侧）不产生重复记录。

发射点纪律：payload 全部走 bound_meta 消毒 + 有界（gis_trace 同门）；
阶段事实取自已有计算结果（planner 裁决 / dispatch 结果 / finalizer
findings），零额外计算、零 I/O。
"""
from __future__ import annotations

from typing import Any

from app.lib.runtime.gis_trace import Stage, get_gis_trace_registry


def _current_turn_id() -> str:
    try:
        from app.lib.runtime.context import current_runtime_context

        ctx = current_runtime_context()
        return str(getattr(ctx, "turn_id", "") or "") if ctx is not None else ""
    except Exception:  # noqa: BLE001 — 上下文缺席 → 不发射
        return ""


def emit_chain(stage: Stage, **payload: Any) -> bool:
    """从当前 RuntimeContext 发射链记录（任何失败静默 False）。"""
    turn_id = _current_turn_id()
    if not turn_id:
        return False
    try:
        from app.lib.runtime.gis_trace import record_stage

        return record_stage(turn_id, stage, **payload)
    except Exception:  # noqa: BLE001
        return False


def emit_chain_once(stage: Stage, **payload: Any) -> bool:
    """同链同阶段首条生效（双路径去重）；链不存在时照常发射。"""
    turn_id = _current_turn_id()
    if not turn_id:
        return False
    try:
        registry = get_gis_trace_registry()
        chain = registry.get(turn_id)
        if chain is not None and chain.first(stage) is not None:
            return False
        return registry.record(turn_id, stage, **payload)
    except Exception:  # noqa: BLE001
        return False


def emit_chain_for(turn_id: str, stage: Stage, **payload: Any) -> bool:
    """显式 turn_id 发射（bridge/chat 等已持有 turn 上下文的调用方）。"""
    if not turn_id:
        return False
    try:
        from app.lib.runtime.gis_trace import record_stage

        return record_stage(turn_id, stage, **payload)
    except Exception:  # noqa: BLE001
        return False


__all__ = ["emit_chain", "emit_chain_once", "emit_chain_for", "Stage"]
