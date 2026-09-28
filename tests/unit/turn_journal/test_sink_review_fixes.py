"""Review 修复回归（sink 面）：合并调度 / 批放弃 / flush 限时 / 快照。"""
from __future__ import annotations

import asyncio
import time

from app.services.turn_journal.contracts import TurnEventRecord
from app.services.turn_journal.sink import (
    DEFAULT_FLUSH_DEADLINE_S,
    TurnJournalSink,
    _DRAIN_TASKS,
)


def _rec(event_id: str) -> TurnEventRecord:
    return TurnEventRecord(session_id="s-sink2", kind="tool_started",
                           event_id=event_id)


def test_burst_coalesces_into_single_drain_task(ledger) -> None:
    """P2-1：burst 入队只排一个 drain task（合并调度 + 强引用）。"""
    sink = TurnJournalSink(ledger=ledger, queue_max=64)

    async def burst():
        for i in range(50):
            sink.record(_rec(f"c:{i}"))
        scheduled = sink._drain_scheduled
        tasks_before = len(_DRAIN_TASKS)
        await asyncio.sleep(0)  # 让 drain 跑
        await sink.flush()
        return scheduled, tasks_before

    scheduled, tasks_before = asyncio.run(burst())
    assert scheduled is True
    assert tasks_before >= 1
    assert sink.pending == 0
    events = asyncio.run(ledger.list_events("s-sink2"))
    assert len(events) == 50
    # 排空后无残留 task 引用
    assert len(_DRAIN_TASKS) == 0


def test_db_failure_aborts_whole_batch(ledger) -> None:
    """P3-8：DB 挂 → 一次失败往返清空整队（不逐条烧超时）。"""
    attempts = {"n": 0}

    class _SlowBoom:
        async def append(self, record):  # noqa: ANN001
            attempts["n"] += 1
            raise RuntimeError("db down")

    sink = TurnJournalSink(ledger=_SlowBoom(), queue_max=64)

    async def run():
        for i in range(10):
            sink.record(_rec(f"boom:{i}"))
        return await sink.flush()

    asyncio.run(run())
    assert attempts["n"] == 1  # 首条失败即整批放弃
    assert sink.pending == 0


def test_flush_respects_deadline() -> None:
    """P2-4：预算耗尽不再启动下一条 append（in-flight 一条不可中断）。

    形状：慢成功 append（0.3s/条）× 10 条，预算 0.2s → 只允许 in-flight
    的那 1 条完成，~9 条留队，总耗时 ≈ 单条时长（而非 3s）。
    """
    attempts = {"n": 0}

    class _SlowOk:
        async def append(self, record):  # noqa: ANN001
            attempts["n"] += 1
            await asyncio.sleep(0.3)
            from app.services.turn_journal.ledger import AppendResult

            return AppendResult(record.event_id, attempts["n"], "appended")

    sink = TurnJournalSink(ledger=_SlowOk(), queue_max=64)

    async def run():
        for i in range(10):
            sink.record(_rec(f"slow:{i}"))
        t0 = time.monotonic()
        pending = await sink.flush(deadline_s=0.2)
        return pending, time.monotonic() - t0

    pending, elapsed = asyncio.run(run())
    assert attempts["n"] == 1  # 预算后不再启动新 append
    assert pending == 9        # 未落账的留在队里（不假完成）
    assert elapsed < 1.0       # ≈ 单条时长，不是 10 条串行


def test_record_snapshots_detail_against_late_mutation(ledger) -> None:
    """P3-4：入队后改写原 detail dict 不影响投影内容。"""
    detail = {"tool": "query"}
    record = TurnEventRecord(session_id="s-sink2", kind="tool_started",
                             event_id="snap:1", detail=detail)
    sink = TurnJournalSink(ledger=ledger, queue_max=8)
    assert sink.record(record) is True
    detail["tool"] = "MUTATED-IN-FLIGHT"
    detail["injected"] = True
    asyncio.run(sink.flush())
    events = asyncio.run(ledger.list_events("s-sink2"))
    assert events[0]["detail"]["tool"] == "query"
    assert "injected" not in events[0]["detail"]
