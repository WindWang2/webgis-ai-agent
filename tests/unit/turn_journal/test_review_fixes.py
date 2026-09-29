"""Review 修复回归（P1-1 / P2-2 / P2-5）：尾读、压缩截止线、约束兜底。"""
from __future__ import annotations

import asyncio
from datetime import timedelta

from app.lib.runtime.clock import utc_now
from app.services.turn_journal.contracts import TurnEventRecord
from app.services.turn_journal.diagnostics import (
    build_causal_tree_sync,
    session_journal_report_sync,
)
from app.services.turn_journal.ledger import MAX_JOURNAL_QUERY
from app.services.turn_journal.resume import (
    CLASS_RECEIPT_CHECK,
    build_recovery_plan_sync,
)


def _put(ledger, event_id, *, turn_id, kind, causal_id="", detail=None):
    return asyncio.run(ledger.append(TurnEventRecord(
        session_id="s-fix", kind=kind, turn_id=turn_id,
        event_id=event_id, causal_id=causal_id, detail=detail or {},
        occurred_at=utc_now())))


def test_forensics_read_the_tail_not_the_head(ledger) -> None:
    """P1-1：>200 行会话里，未终局的最新 turn 必须被看到（头读会假阴性）。"""
    for i in range(MAX_JOURNAL_QUERY):
        _put(ledger, f"ancient:t-old:#{i}", turn_id="t-old",
             kind="phase_changed")
    _put(ledger, "legacy:t-old:end", turn_id="t-old",
         kind="turn_ended", detail={"status": "completed"})
    # 崩溃的 turn 落在 200 行窗口之外（最新）。
    _put(ledger, "tool_started:t-crash:c1", turn_id="t-crash",
         kind="tool_started", causal_id="c1",
         detail={"tool": "add_layer", "mutation_id": "m1"})
    plan = build_recovery_plan_sync(ledger, "s-fix")
    turn_ids = {t["turn_id"] for t in plan["unsettled_turns"]}
    assert "t-crash" in turn_ids, "尾读必须看到最新 turn"
    assert "t-old" not in turn_ids
    hanging = next(
        t for t in plan["unsettled_turns"] if t["turn_id"] == "t-crash"
    )["hanging_steps"]
    assert hanging[0]["classification"] == CLASS_RECEIPT_CHECK
    tree = build_causal_tree_sync(ledger, "s-fix")
    tree_ids = {t["turn_id"] for t in tree["turns"]}
    assert "t-crash" in tree_ids


def test_compaction_only_touches_rows_before_cutoff(ledger) -> None:
    """P2-2 语义收紧：截止线后的 late 行不被压缩误删。"""
    from datetime import timezone

    old = utc_now() - timedelta(days=30)
    _put(ledger, "phase:t9:#1", turn_id="t9", kind="phase_changed")
    _put(ledger, "legacy:t9:end", turn_id="t9",
         kind="turn_ended", detail={"status": "completed"})
    # 把前两行的时间戳手工搬到 30 天前（append 统一用 now）。
    with ledger._factory() as db:  # noqa: SLF001
        from app.models.harness_journal import TurnEventRow

        for row in db.query(TurnEventRow).all():
            row.occurred_at = old.replace(tzinfo=None)
        db.commit()
    # late callback：终局后落账的 phase 行（时间 = now，不老）。
    _put(ledger, "phase:t9:#late", turn_id="t9", kind="phase_changed")

    stats = asyncio.run(ledger.compact_and_sweep(
        now=utc_now(),
        compaction_age_s=timedelta(days=7).total_seconds(),
        retention_age_s=0))
    assert stats["compacted_rows"] == 1  # 只压老 phase 行
    events = asyncio.run(ledger.list_events("s-fix", turn_id="t9"))
    kinds = sorted(e["kind"] for e in events)
    # 摘要 + 终局 + late 新行都在
    assert kinds == ["phase_changed", "turn_compacted", "turn_ended"]


def test_unique_constraint_fallback_dedups_without_precheck(
    ledger, ledger_factory, monkeypatch,
) -> None:
    """P2-5：绕过先查后插窗口的对撞（预查失明）必须由唯一约束兜底判重。"""
    asyncio.run(ledger.append(TurnEventRecord(
        session_id="s-race", kind="tool_started", turn_id="t",
        event_id="race:win")))

    from tests.unit.turn_journal._blind_factory import blind_factory

    race_ledger = type(ledger)(factory=blind_factory(ledger_factory))
    result = asyncio.run(race_ledger.append(TurnEventRecord(
        session_id="s-race", kind="tool_started", turn_id="t",
        event_id="race:win")))
    assert result.status == "duplicate"  # 约束兜底路径命中
    rows = asyncio.run(ledger.list_events("s-race"))
    assert len(rows) == 1


def test_report_light_mode_skips_tree(ledger) -> None:
    """P3-3：with_tree=False 不构建树；单 turn 查询按需补。"""
    _put(ledger, "tool_started:tL:c1", turn_id="tL", kind="tool_started",
         causal_id="c1", detail={"tool": "query"})
    report = session_journal_report_sync(ledger, "s-fix", with_tree=False)
    assert report["causal_tree"] is None
    assert report["unsettled_turns"], "恢复面照常工作"
