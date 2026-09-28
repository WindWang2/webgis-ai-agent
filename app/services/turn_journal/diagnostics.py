"""Turn journal 诊断读模型（只读）——因果树 + 未完成副作用 + 恢复建议。

消费面：``/api/v1/harness/turn-journal/{session_id}``（routes）与
``scripts/turn_journal_inspect.py``（CLI）。全部输出 aware-UTC ISO。

因果树（H04 DoD：任一最终地图 mutation 可追到触发它的 turn/tool/plan/
evidence）：turn → 步骤（tool_started/succeeded/failed/late 同 causal_id
配对）→ mutation（map_mutated：mutation_id + revision）→ observation。
账本压缩后（turn_compacted 摘要行）树在 turn 粒度仍成立：摘要行携带
counts/terminal_status/last_mutation_revision/preserved_event_ids。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.services.turn_journal.ledger import (
    MAX_JOURNAL_QUERY,
    TERMINAL_TURN_KIND,
    TurnEventLedger,
)
from app.services.turn_journal.resume import _build_plan

_RESULT_KINDS = ("tool_succeeded", "tool_failed", "tool_late")


def build_causal_tree_sync(
    ledger: TurnEventLedger,
    session_id: str,
    *,
    turn_id: str = "",
) -> Dict[str, Any]:
    """turn 粒度因果树（同步核心；异常面由调用方处理）。"""
    events = ledger._list_events_sync(  # noqa: SLF001 — 模块内同族访问
        session_id, turn_id=turn_id, limit=MAX_JOURNAL_QUERY)
    by_turn: Dict[str, List[Dict[str, Any]]] = {}
    for event in events:
        by_turn.setdefault(event.get("turn_id") or "", []).append(event)
    tree: List[Dict[str, Any]] = []
    for tid, turn_events in by_turn.items():
        if not tid:
            continue
        steps: Dict[str, Dict[str, Any]] = {}
        mutations: List[Dict[str, Any]] = []
        kind_counts: Dict[str, int] = {}
        for e in turn_events:
            kind_counts[e["kind"]] = kind_counts.get(e["kind"], 0) + 1
            if e["kind"] == "map_mutated":
                mutations.append({
                    "mutation_id": e.get("causal_id") or "",
                    "revision": e.get("mutation_revision"),
                    "tool_call_id": str(
                        (e.get("detail") or {}).get("tool_call_id") or ""),
                    "occurred_at": e.get("occurred_at"),
                    "late": bool((e.get("detail") or {}).get("late")),
                })
            causal = e.get("causal_id") or ""
            if e["kind"] == "tool_started" and causal:
                slot = steps.setdefault(causal, {
                    "causal_id": causal,
                    "tool": str((e.get("detail") or {}).get("tool") or ""),
                    "step_id": e.get("step_id") or "",
                    "started_at": e.get("occurred_at"),
                    "result": None,
                })
            elif e["kind"] in _RESULT_KINDS and causal and causal in steps:
                steps[causal]["result"] = {
                    "kind": e["kind"],
                    "at": e.get("occurred_at"),
                }
        terminal = next(
            (e for e in reversed(turn_events)
             if e["kind"] == TERMINAL_TURN_KIND), None)
        compacted = next(
            (e for e in turn_events if e["kind"] == "turn_compacted"), None)
        tree.append({
            "turn_id": tid,
            "run_id": next((e.get("run_id") for e in turn_events if e.get("run_id")), ""),
            "terminal_status": str(
                (terminal.detail or {}).get("status")
                or (compacted.detail or {}).get("terminal_status") or ""
            ) if (terminal or compacted) else "",
            "settled": terminal is not None,
            "compacted": compacted is not None,
            "event_count": len(turn_events),
            "kind_counts": kind_counts,
            "steps": list(steps.values()),
            "mutations": mutations,
        })
    return {"session_id": session_id, "turns": tree}


def session_journal_report_sync(
    ledger: TurnEventLedger,
    session_id: str,
    *,
    turn_id: str = "",
) -> Dict[str, Any]:
    """API/CLI 的完整只读报告（因果树 + 未终局 + 恢复建议）。"""
    tree = build_causal_tree_sync(ledger, session_id, turn_id=turn_id)
    plan = _build_plan(ledger, session_id)
    summaries = ledger._turn_summaries_sync(session_id, limit=50)  # noqa: SLF001
    return {
        "session_id": session_id,
        "requested_turn_id": turn_id,
        "causal_tree": tree,
        "turn_summaries": summaries,
        "unsettled_turns": plan["unsettled_turns"],
        "last_consistent_revision": plan["last_consistent_revision"],
        "recovery_suggestions": plan["recovery_suggestions"],
    }
