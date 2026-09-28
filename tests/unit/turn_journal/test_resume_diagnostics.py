"""恢复分类与诊断读模型：崩溃注入 / receipt 判定 / 压缩后可查。"""
from __future__ import annotations

import asyncio

from app.lib.runtime.clock import utc_now
from app.services.turn_journal.contracts import TurnEventRecord
from app.services.turn_journal.diagnostics import (
    build_causal_tree_sync,
    session_journal_report_sync,
)
from app.services.turn_journal.ledger import TERMINAL_TURN_KIND
from app.services.turn_journal.resume import (
    CLASS_NEEDS_REPLAN,
    CLASS_RECEIPT_CHECK,
    CLASS_RECEIPT_PRESENT,
    CLASS_SAFE_REPLAY,
    build_recovery_plan_sync,
    classify_step,
)


def _put(ledger, event_id, *, turn_id, kind, causal_id="", detail=None,
         mutation_revision=None):
    return asyncio.run(ledger.append(TurnEventRecord(
        session_id="s-res", kind=kind, turn_id=turn_id,
        event_id=event_id, causal_id=causal_id, detail=detail or {},
        mutation_revision=mutation_revision, occurred_at=utc_now())))


def test_classify_step_pure_function() -> None:
    started = {"detail": {"tool": "query_poi"}, "causal_id": "c1"}
    assert classify_step(started, has_result=False, receipt=None) == CLASS_SAFE_REPLAY
    mutating = {"detail": {"tool": "add_layer", "mutation_id": "m1"},
                "causal_id": "c2"}
    assert classify_step(mutating, has_result=False, receipt=None) == CLASS_RECEIPT_CHECK
    assert classify_step(mutating, has_result=False,
                         receipt={"id": 1}) == CLASS_RECEIPT_PRESENT
    unknown = {"detail": {"tool": "weird_op"}, "causal_id": "c3"}
    assert classify_step(unknown, has_result=False, receipt=None) == CLASS_NEEDS_REPLAN


def test_crash_injection_hanging_mutation_classified(ledger) -> None:
    """崩溃注入：tool_started 后进程死亡（无 result/receipt/终局）。"""
    _put(ledger, "tool_started:t1:c-mut", turn_id="t1", kind="tool_started",
         causal_id="c-mut",
         detail={"tool": "add_layer", "mutation_id": "m-pending"})
    plan = build_recovery_plan_sync(ledger, "s-res")
    assert len(plan["unsettled_turns"]) == 1
    hanging = plan["unsettled_turns"][0]["hanging_steps"]
    assert hanging[0]["classification"] == CLASS_RECEIPT_CHECK
    assert plan["recovery_suggestions"][0]["suggestion"] == \
        "replay_with_same_mutation_id"


def test_receipt_present_after_commit_is_no_action(ledger) -> None:
    """fail-after-commit 注入：mutation receipt 已落账 → 无需动作。"""
    _put(ledger, "tool_started:t2:c1", turn_id="t2", kind="tool_started",
         causal_id="c1", detail={"tool": "add_layer", "mutation_id": "m1"})
    _put(ledger, "map_mutated:t2:m1", turn_id="t2", kind="map_mutated",
         causal_id="m1", mutation_revision=9)
    plan = build_recovery_plan_sync(ledger, "s-res")
    hanging = plan["unsettled_turns"][0]["hanging_steps"]
    assert hanging[0]["classification"] == CLASS_RECEIPT_PRESENT
    assert plan["last_consistent_revision"] == 9


def test_settled_turn_is_not_unsettled(ledger) -> None:
    _put(ledger, "tool_started:t3:c1", turn_id="t3", kind="tool_started",
         causal_id="c1", detail={"tool": "query"})
    _put(ledger, "tool_succeeded:t3:c1", turn_id="t3",
         kind="tool_succeeded", causal_id="c1")
    _put(ledger, "legacy:t3:end", turn_id="t3", kind=TERMINAL_TURN_KIND,
         detail={"status": "completed"})
    plan = build_recovery_plan_sync(ledger, "s-res")
    assert plan["unsettled_turns"] == []
    assert plan["recovery_suggestions"] == []


def test_empty_session_report_is_honest_zero(ledger) -> None:
    plan = build_recovery_plan_sync(ledger, "s-empty")
    assert plan == {
        "session_id": "s-empty",
        "unsettled_turns": [],
        "last_consistent_revision": None,
        "recovery_suggestions": [],
    }


def test_causal_tree_links_step_to_mutation(ledger) -> None:
    """DoD：最终 mutation 可追到 turn/tool/evidence 链。"""
    _put(ledger, "tool_started:t4:c9", turn_id="t4", kind="tool_started",
         causal_id="c9", detail={"tool": "add_layer"})
    _put(ledger, "tool_succeeded:t4:c9", turn_id="t4",
         kind="tool_succeeded", causal_id="c9")
    _put(ledger, "map_mutated:t4:m9", turn_id="t4", kind="map_mutated",
         causal_id="m9", mutation_revision=3,
         detail={"tool_call_id": "c9"})
    tree = build_causal_tree_sync(ledger, "s-res", turn_id="t4")
    turn = tree["turns"][0]
    assert turn["steps"][0]["causal_id"] == "c9"
    assert turn["steps"][0]["result"]["kind"] == "tool_succeeded"
    assert turn["mutations"][0]["mutation_id"] == "m9"
    assert turn["mutations"][0]["tool_call_id"] == "c9"


def test_report_after_compaction_still_answers(ledger) -> None:
    """DoD：压缩后诊断面在 turn 粒度仍正确（摘要行参与聚合）。"""
    for i in range(3):
        _put(ledger, f"phase:t5:#{i}", turn_id="t5", kind="phase_changed")
    _put(ledger, "map_mutated:t5:m5", turn_id="t5", kind="map_mutated",
         causal_id="m5", mutation_revision=77)
    _put(ledger, "legacy:t5:end", turn_id="t5", kind=TERMINAL_TURN_KIND,
         detail={"status": "completed"})
    from datetime import timedelta

    asyncio.run(ledger.compact_and_sweep(
        now=utc_now() + timedelta(days=8),  # 未来 now ⇒ 全部可压缩
        compaction_age_s=timedelta(days=7).total_seconds(),
        retention_age_s=0))
    report = session_journal_report_sync(ledger, "s-res")
    summaries = {s["turn_id"]: s for s in report["turn_summaries"]}
    assert summaries["t5"]["terminal"] is True
    assert summaries["t5"]["last_mutation_revision"] == 77
    tree_turns = report["causal_tree"]["turns"]
    t5 = next(t for t in tree_turns if t["turn_id"] == "t5")
    assert t5["compacted"] is True
    assert t5["mutations"][0]["mutation_id"] == "m5"  # receipt 仍可追
