"""崩溃后恢复分类（advisory，只读）——H04 resume 面。

**绝不自动执行副作用**。输出是给人工/agent 重新规划用的分类建议：
envelope 权威在崩溃瞬间可能整体丢失（Redis/内存），账本是唯一幸存的
事实链；这里把"最后一个未终局 turn"里的悬挂步骤按凭证可用性分类。

分类规则（保守优先）：
- ``settled_receipt_present``：``map_mutated`` 已落账（mutation receipt
  在账）——该副作用已完成，无需动作；
- ``needs_receipt_check``：mutation 类工具 started 无结果、且本 turn 无
  对应 receipt——建议**同 mutation_id 重放**（engine dedup receipt 保证
  幂等），重放前先查 MapSpec 当前 revision；
- ``safe_replay``：已知纯读工具 started 无结果——直接重放无副作用；
- ``needs_replan``：其他/未知工具悬挂——保守默认，交还规划层。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.services.turn_journal.ledger import (
    MAX_JOURNAL_QUERY,
    TERMINAL_TURN_KIND,
    TurnEventLedger,
)

#: 已知纯读工具 kind（保守白名单；命中即 safe_replay）。
READ_ONLY_TOOLS = frozenset({
    "query", "search", "identify", "measure", "inspect", "preview",
    "legend", "metadata", "describe", "list", "read", "export_view",
})
#: mutation 侧信号（工具名/detail 键命中即视为 mutation 通道）。
_MUTATION_KINDS = frozenset({
    "add_layer", "remove_layer", "update_layer", "set_style", "apply_style",
    "mutate", "set_view", "add_source", "remove_source",
})

CLASS_RECEIPT_PRESENT = "settled_receipt_present"
CLASS_RECEIPT_CHECK = "needs_receipt_check"
CLASS_SAFE_REPLAY = "safe_replay"
CLASS_NEEDS_REPLAN = "needs_replan"


def _is_read_only(tool: str) -> bool:
    name = (tool or "").strip().lower()
    if not name:
        return False
    if name in READ_ONLY_TOOLS:
        return True
    return any(name.startswith(prefix + "_") or name.startswith(prefix + ".")
               for prefix in READ_ONLY_TOOLS)


def _is_mutation_tool(tool: str, detail: Dict[str, Any]) -> bool:
    name = (tool or "").strip().lower()
    if name in _MUTATION_KINDS:
        return True
    if any(name.startswith(prefix + "_") or name.startswith(prefix + ".")
           for prefix in _MUTATION_KINDS):
        return True
    return bool(detail.get("mutation_id") or detail.get("mutating"))


def classify_step(
    started: Dict[str, Any],
    *,
    has_result: bool,
    receipt: Optional[Dict[str, Any]],
) -> str:
    """单步分类（纯函数，测试友好）。"""
    if receipt is not None:
        return CLASS_RECEIPT_PRESENT
    tool = str(started.get("detail", {}).get("tool")
               or started.get("causal_id") or "")
    if not has_result and _is_mutation_tool(tool, started.get("detail") or {}):
        return CLASS_RECEIPT_CHECK
    if _is_read_only(tool):
        return CLASS_SAFE_REPLAY
    return CLASS_NEEDS_REPLAN


def _receipt_index(events: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """causal_id → map_mutated 行（mutation receipt 索引）。"""
    return {
        e["causal_id"]: e for e in events
        if e.get("kind") == "map_mutated" and e.get("causal_id")
    }


def build_recovery_plan_sync(
    ledger: TurnEventLedger,
    session_id: str,
) -> Dict[str, Any]:
    """从账本构建恢复建议（同步核心；诊断层包 to_thread）。

    崩溃语义：进程死亡 = 事件停止追加。账本视角"未终局 turn 的 started
    无结果步骤"即崩溃时在飞的工作——不区分"正在跑"与"死了"，因为
    重启后两者都不可续跑（in-process 执行态已失）。
    """
    return _build_plan(ledger, session_id)


def _build_plan(ledger: TurnEventLedger, session_id: str) -> Dict[str, Any]:
    # 尾读（review P1-1）：取证关心最近的 turn，>200 行会话下头读会
    # 永远盯住最老历史（"一切安好"的假阴性）。
    events = ledger._list_events_sync(  # noqa: SLF001 — 模块内同族访问
        session_id, limit=MAX_JOURNAL_QUERY, latest_first=True)
    if not events:
        return {
            "session_id": session_id,
            "unsettled_turns": [],
            "last_consistent_revision": None,
            "recovery_suggestions": [],
        }
    # 按 turn 分组（账本序 = 因果序）。
    by_turn: Dict[str, List[Dict[str, Any]]] = {}
    for event in events:
        by_turn.setdefault(event.get("turn_id") or "", []).append(event)
    unsettled: List[Dict[str, Any]] = []
    suggestions: List[Dict[str, Any]] = []
    last_revision: Optional[int] = None
    for turn_id, turn_events in by_turn.items():
        if not turn_id:
            continue
        terminal = any(e.get("kind") == TERMINAL_TURN_KIND
                       for e in turn_events)
        for e in turn_events:
            rev = e.get("mutation_revision")
            if rev is not None:
                last_revision = max(last_revision or 0, int(rev))
        if terminal:
            continue
        receipts = _receipt_index(turn_events)
        started_no_result: List[Dict[str, Any]] = []
        for e in turn_events:
            if e.get("kind") != "tool_started":
                continue
            causal = e.get("causal_id") or ""
            has_result = any(
                o.get("kind") in ("tool_succeeded", "tool_failed", "tool_late")
                and (o.get("causal_id") or "") == causal
                for o in turn_events
            )
            if has_result:
                continue
            started_no_result.append(e)
        if not started_no_result:
            # 未终局但无悬挂步骤（例如崩溃在结算 seam 之前）——仍列出，
            # 分类为需重规划（保守：结算未发生）。
            unsettled.append({
                "turn_id": turn_id,
                "status": "unsettled",
                "hanging_steps": [],
            })
            suggestions.append({
                "turn_id": turn_id,
                "suggestion": "replan",
                "reason": "turn never settled (no turn_ended row)",
            })
            continue
        hang_entries = []
        for started in started_no_result:
            causal = started.get("causal_id") or ""
            receipt = receipts.get(causal) if causal else None
            # receipt 与 tool_call_id 不同域：mutation receipt 按
            # mutation_id 索引；started 的 causal 是 tool_call_id。当前 turn
            # 内存在任意 receipt 且 started 的 detail 携带 mutation_id 时
            # 才可判 receipt-present。
            detail = started.get("detail") or {}
            mid = str(detail.get("mutation_id") or "")
            mutation_receipt = receipts.get(mid) if mid else None
            classification = classify_step(
                started,
                has_result=False,
                receipt=mutation_receipt,
            )
            hang_entries.append({
                "step_id": started.get("step_id") or "",
                "causal_id": causal,
                "tool": str(detail.get("tool") or ""),
                "classification": classification,
                "mutation_id": mid or None,
                "reason": _reason_for(classification),
            })
            suggestions.append({
                "turn_id": turn_id,
                "step_id": started.get("step_id") or "",
                "classification": classification,
                "suggestion": _suggestion_for(classification),
                "reason": _reason_for(classification),
            })
        unsettled.append({
            "turn_id": turn_id,
            "status": "unsettled",
            "hanging_steps": hang_entries,
        })
    return {
        "session_id": session_id,
        "unsettled_turns": unsettled,
        "last_consistent_revision": last_revision,
        "recovery_suggestions": suggestions,
    }


def _reason_for(classification: str) -> str:
    return {
        CLASS_RECEIPT_PRESENT: "mutation receipt already journaled",
        CLASS_RECEIPT_CHECK: (
            "mutation started without receipt — replay with the same "
            "mutation_id (engine dedup makes it idempotent)"),
        CLASS_SAFE_REPLAY: "read-only step — safe to replay",
        CLASS_NEEDS_REPLAN: "unknown hanging step — needs re-planning",
    }.get(classification, "unknown")


def _suggestion_for(classification: str) -> str:
    return {
        CLASS_RECEIPT_PRESENT: "no_action",
        CLASS_RECEIPT_CHECK: "replay_with_same_mutation_id",
        CLASS_SAFE_REPLAY: "replay",
        CLASS_NEEDS_REPLAN: "replan",
    }.get(classification, "replan")
