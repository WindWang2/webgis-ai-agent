"""确定性 trace 重放校验（ADR-0101 D12，V4 §32）。

**这是测试工具，不是第二个执行引擎**：给定一段有界 trace（tracing
ring 的形状），按节点状态机断言不变式 —— 不可能转移、取消后完成、
重试超限、run 终态后事件等。纯函数、确定性、无 IO。
"""
from __future__ import annotations

from typing import Any, Optional

#: 节点级终态事件（一个节点至多一个）。
_TERMINAL_EVENTS = frozenset({
    "node_completed", "node_reused", "node_failed",
    "node_cancelled", "node_skipped", "node_marked",
})
#: node_marked 只允许携带的终态（取消/deadline 收敛）。
_MARKED_ALLOWED_STATUS = frozenset({"cancelled"})
#: 重试次数硬上界（与 RetryPolicy.max_attempts le=4 一致）。
_MAX_ATTEMPTS = 4

_RUN_LEVEL = frozenset({"run_started", "run_finished"})


def replay_trace(events: list[dict[str, Any]]) -> dict[str, Any]:
    """校验 trace 的状态机不变式。返回 ``{valid, violations, nodes, runs}``。"""
    violations: list[str] = []
    nodes: dict[str, dict[str, Any]] = {}
    run_started = 0
    run_finished_index: Optional[int] = None

    for i, ev in enumerate(events):
        name = str(ev.get("event") or "")
        if not name:
            violations.append(f"[{i}] event without name")
            continue
        if run_finished_index is not None:
            violations.append(
                f"[{i}] event '{name}' after run_finished "
                f"(at [{run_finished_index}])")
            continue
        if name == "run_started":
            run_started += 1
            if run_started > 1:
                violations.append(f"[{i}] duplicate run_started")
            continue
        if name == "run_finished":
            if run_started == 0:
                violations.append(f"[{i}] run_finished before run_started")
            run_finished_index = i
            continue
        if name in _RUN_LEVEL:
            continue
        node_id = ev.get("node_id")
        if not node_id:
            violations.append(f"[{i}] node event '{name}' without node_id")
            continue
        state = nodes.setdefault(node_id, {
            "admitted": 0, "terminal": None, "attempts": 0,
            "attempt_failures": 0, "cancelled_before_terminal": False,
        })
        if name == "node_admitted":
            if state["terminal"] is not None:
                violations.append(
                    f"[{i}] node '{node_id}' admitted after terminal "
                    f"'{state['terminal']}'")
            state["admitted"] += 1
            if state["admitted"] > 1:
                violations.append(
                    f"[{i}] node '{node_id}' admitted {state['admitted']} times")
        elif name == "node_attempt_failed":
            if state["terminal"] is not None:
                violations.append(
                    f"[{i}] node '{node_id}' attempt-failure after terminal")
            state["attempts"] += 1
            state["attempt_failures"] += 1
            if state["attempt_failures"] > _MAX_ATTEMPTS:
                violations.append(
                    f"[{i}] node '{node_id}' exceeded retry ceiling "
                    f"({_MAX_ATTEMPTS})")
        elif name in _TERMINAL_EVENTS:
            if state["terminal"] is not None:
                violations.append(
                    f"[{i}] node '{node_id}' second terminal event "
                    f"'{name}' (first: '{state['terminal']}')")
                continue
            if name == "node_marked":
                status = ev.get("status")
                if status not in _MARKED_ALLOWED_STATUS:
                    violations.append(
                        f"[{i}] node_marked with non-terminal status '{status}'")
            if name == "node_attempt_failed":
                pass
            state["terminal"] = name
        else:
            # 计数型/进度型事件（node_dispatched 等）：不构成转移约束。
            continue

    # 终态收尾校验：run 未结束也算一个 violation（截断 trace 是异常）。
    if run_finished_index is None and events:
        violations.append("trace truncated: no run_finished")

    return {
        "valid": not violations,
        "violations": violations,
        "nodes": nodes,
        "event_count": len(events),
    }
