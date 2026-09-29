"""Ledger 对抗面：幂等判重、有界读、压缩后因果正确、retention、重入。"""
from __future__ import annotations

from datetime import timedelta

from app.lib.runtime.clock import from_db_utc, utc_now
from app.services.turn_journal.contracts import TurnEventRecord
from app.services.turn_journal.ledger import (
    MAX_JOURNAL_QUERY,
    TERMINAL_TURN_KIND,
    TurnEventLedger,
)


def _rec(
    ledger: TurnEventLedger,
    event_id: str,
    *,
    session_id: str = "s-led",
    turn_id: str = "t1",
    kind: str = "tool_started",
    seq: int = 0,
    causal_id: str = "",
    detail: dict | None = None,
    mutation_revision: int | None = None,
    occurred_at=None,
) -> TurnEventRecord:
    return TurnEventRecord(
        session_id=session_id, kind=kind, turn_id=turn_id,
        event_id=event_id, seq=seq, causal_id=causal_id,
        detail=detail or {}, mutation_revision=mutation_revision,
        occurred_at=occurred_at or utc_now(),
    )


def test_append_is_idempotent_on_event_id(ledger) -> None:
    import asyncio

    rec = _rec(ledger, "tool_started:t1:call-1", causal_id="call-1")
    first = asyncio.run(ledger.append(rec))
    dup = asyncio.run(ledger.append(_rec(ledger, "tool_started:t1:call-1",
                                         causal_id="call-1")))
    assert first.status == "appended"
    assert dup.status == "duplicate"
    assert dup.row_id == first.row_id  # 同一行——exactly-once observable


def test_unique_constraint_race_still_dedups(ledger) -> None:
    """绕过先查后插的窗口（模拟并发对撞）：唯一约束兜底路径。"""
    import asyncio

    rec = _rec(ledger, "race:t1:x")
    from app.services.turn_journal.contracts import TurnEventRecord as R
    # 直接插一次
    asyncio.run(ledger.append(rec))
    # 再用"跳过预查"的底层同步路径模拟窗口内对撞——应走约束兜底判重
    result = ledger._append_sync(R(
        session_id="s-led", kind="tool_started", turn_id="t1",
        event_id="race:t1:x"))
    assert result.status == "duplicate"


def test_list_events_is_bounded(ledger) -> None:
    import asyncio

    for i in range(MAX_JOURNAL_QUERY + 30):
        asyncio.run(ledger.append(
            _rec(ledger, f"bulk:t1:#{i}", kind="phase_changed", seq=i)))
    events = asyncio.run(ledger.list_events("s-led", limit=10_000))
    assert len(events) == MAX_JOURNAL_QUERY  # 硬帽，不随请求放大
    ids = [e["id"] for e in events]
    assert ids == sorted(ids)  # 因果序


def test_turn_summaries_aggregate_terminal_and_revision(ledger) -> None:
    import asyncio

    base = utc_now() - timedelta(hours=1)
    asyncio.run(ledger.append(_rec(
        ledger, "tool_started:t9:c1", turn_id="t9", causal_id="c1",
        occurred_at=base)))
    asyncio.run(ledger.append(_rec(
        ledger, "tool_succeeded:t9:c1", turn_id="t9", kind="tool_succeeded",
        causal_id="c1", occurred_at=base)))
    asyncio.run(ledger.append(_rec(
        ledger, "map_mutated:t9:m1", turn_id="t9", kind="map_mutated",
        causal_id="m1", mutation_revision=12, occurred_at=base)))
    asyncio.run(ledger.append(_rec(
        ledger, "legacy:t9:turn_ended", turn_id="t9", kind="turn_ended",
        detail={"status": "completed"}, occurred_at=base)))
    summaries = asyncio.run(ledger.turn_summaries("s-led"))
    assert len(summaries) == 1
    slot = summaries[0]
    assert slot["terminal"] is True
    assert slot["terminal_status"] == "completed"
    assert slot["last_mutation_revision"] == 12
    assert slot["kinds_count"]["tool_started"] == 1


def test_compaction_preserves_mutation_receipt_and_causal_query(ledger) -> None:
    """DoD：compaction 后 causal query 仍正确；receipt 链永不丢。"""
    import asyncio

    old = utc_now() - timedelta(days=30)
    for i in range(5):
        asyncio.run(ledger.append(_rec(
            ledger, f"phase:t-old:#{i}", turn_id="t-old",
            kind="phase_changed", seq=i, occurred_at=old)))
    asyncio.run(ledger.append(_rec(
        ledger, "map_mutated:t-old:m1", turn_id="t-old",
        kind="map_mutated", causal_id="m1", mutation_revision=42,
        occurred_at=old)))
    asyncio.run(ledger.append(_rec(
        ledger, "legacy:t-old:turn_ended", turn_id="t-old",
        kind=TERMINAL_TURN_KIND,
        detail={"status": "completed"}, occurred_at=old)))

    stats = asyncio.run(ledger.compact_and_sweep(
        now=utc_now(), compaction_age_s=timedelta(days=7).total_seconds(),
        retention_age_s=0))
    assert stats["compacted_turns"] == 1
    # 因果查询仍正确：turn 粒度完整——摘要行 + 凭证行 + 终局行
    events = asyncio.run(ledger.list_events("s-led", turn_id="t-old"))
    kinds = [e["kind"] for e in events]
    assert "turn_compacted" in kinds
    assert kinds.count("map_mutated") == 1  # receipt 全量保留
    assert kinds.count(TERMINAL_TURN_KIND) == 1
    summary = next(e for e in events if e["kind"] == "turn_compacted")
    assert summary["status"] == "compacted"
    assert summary["mutation_revision"] == 42
    assert summary["detail"]["kinds_count"]["phase_changed"] == 5
    # 汇总面：终局态 + 最后 revision 仍可答
    summaries = asyncio.run(ledger.turn_summaries("s-led"))
    slot = next(s for s in summaries if s["turn_id"] == "t-old")
    assert slot["terminal"] is True
    assert slot["last_mutation_revision"] == 42


def test_compaction_reentry_is_noop(ledger) -> None:
    import asyncio

    old = utc_now() - timedelta(days=30)
    asyncio.run(ledger.append(_rec(
        ledger, "phase:t2:#1", turn_id="t2", kind="phase_changed",
        occurred_at=old)))
    asyncio.run(ledger.append(_rec(
        ledger, "legacy:t2:end", turn_id="t2", kind=TERMINAL_TURN_KIND,
        detail={"status": "completed"}, occurred_at=old)))
    cfg = {"compaction_age_s": timedelta(days=7).total_seconds()}
    first = asyncio.run(ledger.compact_and_sweep(now=utc_now(), **cfg))
    second = asyncio.run(ledger.compact_and_sweep(now=utc_now(), **cfg))
    assert first["compacted_rows"] == 1
    assert second["compacted_turns"] == 0 and second["compacted_rows"] == 0


def test_retention_deletes_only_aged_rows(ledger) -> None:
    import asyncio

    old = utc_now() - timedelta(days=60)
    fresh = utc_now()
    asyncio.run(ledger.append(_rec(
        ledger, "old:t3:#1", turn_id="t3", kind="phase_changed",
        occurred_at=old)))
    asyncio.run(ledger.append(_rec(
        ledger, "fresh:t4:#1", turn_id="t4", kind="phase_changed",
        occurred_at=fresh)))
    stats = asyncio.run(ledger.compact_and_sweep(
        now=utc_now(),
        retention_age_s=timedelta(days=30).total_seconds()))
    assert stats["retention_deleted"] == 1
    remaining = asyncio.run(ledger.list_events("s-led"))
    assert [e["event_id"] for e in remaining] == ["fresh:t4:#1"]


def test_timestamps_roundtrip_aware_iso(ledger) -> None:
    """时序边界：DB naive-UTC 存储，读出 aware-UTC ISO（旧 naive 兼容）。"""
    import asyncio

    at = utc_now()
    asyncio.run(ledger.append(_rec(
        ledger, "tz:t5:#1", turn_id="t5", occurred_at=at)))
    row = asyncio.run(ledger.list_events("s-led", turn_id="t5"))[0]
    parsed = from_db_utc(row["occurred_at"])
    assert parsed is not None and parsed.tzinfo is not None
    assert abs((parsed - at).total_seconds()) < 1.0
