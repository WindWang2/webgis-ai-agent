"""Sink 对抗面：fail-open、有界 drop、env 门控、重复计数。"""
from __future__ import annotations

import asyncio

import pytest

from app.services.turn_journal import sink as sink_mod
from app.services.turn_journal.contracts import TurnEventRecord
from app.services.turn_journal.sink import (
    NullTurnJournalSink,
    TurnJournalSink,
    get_turn_journal_sink,
    journal_enabled,
    reset_turn_journal_sink,
)


def _rec(event_id: str, **kw) -> TurnEventRecord:
    return TurnEventRecord(session_id="s-sink", kind="tool_started",
                           event_id=event_id, **kw)


def test_env_off_switches_to_null_sink(monkeypatch) -> None:
    monkeypatch.setenv("GIS_TURN_JOURNAL", "off")
    reset_turn_journal_sink()
    assert journal_enabled() is False
    sink = get_turn_journal_sink()
    assert isinstance(sink, NullTurnJournalSink)
    assert sink.record(_rec("x:1")) is False
    assert asyncio.run(sink.flush()) == 0


def test_fail_open_on_ledger_failure(caplog) -> None:
    """账本抛错 → 事件丢弃 + 不反噬（record/flush 正常返回）。"""

    class _Boom:
        async def append(self, record):  # noqa: ANN001
            raise RuntimeError("db down")

    boom_sink = TurnJournalSink(ledger=_Boom(), queue_max=4)
    assert boom_sink.record(_rec("a:1")) is True
    assert asyncio.run(boom_sink.flush()) == 0  # 丢事件，不抛
    assert boom_sink.record(_rec("a:2")) is True  # sink 仍可用


def test_queue_full_drops_new_event_keeps_oldest(ledger) -> None:
    """有界背压：满则拒绝新事件（最老的因果行最有价值，不驱逐）。"""
    small = TurnJournalSink(ledger=ledger, queue_max=2)
    assert small.record(_rec("q:1")) is True
    assert small.record(_rec("q:2")) is True
    assert small.record(_rec("q:3")) is False  # 满：新事件被拒 + 计 drop
    assert small.pending == 2
    assert asyncio.run(small.flush()) == 0
    events = asyncio.run(ledger.list_events("s-sink"))
    assert [e["event_id"] for e in events] == ["q:1", "q:2"]


def test_duplicate_append_counted_not_errored(ledger) -> None:
    sink = TurnJournalSink(ledger=ledger, queue_max=8)
    sink.record(_rec("dup:t:c", causal_id="c"))
    sink.record(_rec("dup:t:c", causal_id="c"))  # 同 event_id 重投
    assert asyncio.run(sink.flush()) == 0
    events = asyncio.run(ledger.list_events("s-sink"))
    assert len(events) == 1  # exactly-once observable


def test_singleton_respects_installed_test_sink(journal_sink) -> None:
    assert get_turn_journal_sink() is journal_sink
    reset_turn_journal_sink()
    # 复位后重新按 env 构造（默认 on）
    assert isinstance(get_turn_journal_sink(), TurnJournalSink)


def test_record_without_loop_is_queued_not_lost(ledger) -> None:
    """启动期/同步上下文（无 running loop）：只入队，不丢。"""
    sink = TurnJournalSink(ledger=ledger, queue_max=8)
    # 当前为同步上下文——record 不应抛、不应丢
    assert sink.record(_rec("noloop:1")) is True
    assert sink.pending == 1
    assert asyncio.run(sink.flush()) == 0
    events = asyncio.run(ledger.list_events("s-sink"))
    assert len(events) == 1


@pytest.mark.parametrize("bad", [("", "kind"), ("sess", "")])
def test_dirty_rows_rejected(bad) -> None:
    session_id, kind = bad
    sink = TurnJournalSink(ledger=None, queue_max=4)
    assert sink.record(TurnEventRecord(session_id=session_id, kind=kind,
                                       event_id="dirty:1")) is False
