"""Kernel seam → durable ledger 端到端接线（H04 核心闭环）。

真实 GISSessionRuntime 流程（in-memory envelope，USE_REDIS=false）+
指向测试 SQLite 的 sink：断言 canonical 行幂等键一致、map_mutated 带
revision、终局行结构化、重复回调/锁重试不双记。
"""
from __future__ import annotations

import asyncio
import uuid

import pytest

from app.services.harness_kernel import get_runtime
from app.services.session_data import session_data_manager


def _sid() -> str:
    return f"sess-tj-{uuid.uuid4().hex[:8]}"


@pytest.fixture()
async def session_id():
    sid = _sid()
    await session_data_manager.clear_session(sid)
    yield sid
    await session_data_manager.clear_session(sid)


async def _drain(ledger) -> list:
    """无 running loop 的 flush 场景：直接驱动一次排空。"""
    await asyncio.sleep(0)  # 让 record 里 create_task 的排空任务跑起来
    from app.services.turn_journal.sink import get_turn_journal_sink

    await get_turn_journal_sink().flush()
    return []


async def test_turn_lifecycle_lands_in_ledger(session_id, journal_sink, ledger) -> None:
    rt = get_runtime(session_id)
    turn_id = f"turn-{uuid.uuid4().hex[:8]}"
    await rt.begin_turn(turn_id, host="pi", message="q")
    await rt.end_turn(turn_id, host="pi", status="completed", checkpoint=False)
    await _drain(ledger)

    events = await ledger.list_events(session_id)
    kinds = [e["kind"] for e in events]
    assert "turn_ended" in kinds
    ended = next(e for e in events if e["kind"] == "turn_ended")
    assert ended["detail"].get("status") == "completed"  # 结构化终局
    assert ended["event_id"].startswith("legacy:turn_ended:")
    started = next(e for e in events if e["kind"] == "turn_started" or
                   e["event_id"].startswith("legacy:turn_started"))
    assert started["turn_id"] == turn_id


async def test_duplicate_event_id_single_ledger_row(session_id, journal_sink, ledger) -> None:
    """重复 tool 回调（同 causal_id）：envelope 判重 → 账本单行。"""
    rt = get_runtime(session_id)
    turn_id = f"turn-{uuid.uuid4().hex[:8]}"
    await rt.begin_turn(turn_id, host="pi", message="q")
    from app.services.harness_kernel.runtime import _event
    from app.services.session_plan import load_session_plan

    plan = await load_session_plan(session_id)
    assert plan is not None
    first = _event(plan, "tool_started", host="pi", turn_id=turn_id,
                   causal_id="call-1", detail={"tool": "query"})
    second = _event(plan, "tool_started", host="pi", turn_id=turn_id,
                    causal_id="call-1", detail={"tool": "query"})
    assert first is True and second is False  # envelope 幂等
    from app.services.turn_journal.sink import get_turn_journal_sink

    await get_turn_journal_sink().flush()
    rows = await ledger.list_events(session_id, turn_id=turn_id,
                                    kinds=["tool_started"])
    assert len(rows) == 1
    assert rows[0]["event_id"] == f"tool_started:{turn_id}:call-1"


async def test_map_mutation_row_carries_revision(session_id, journal_sink, ledger) -> None:
    rt = get_runtime(session_id)
    turn_id = f"turn-{uuid.uuid4().hex[:8]}"
    await rt.begin_turn(turn_id, host="pi", message="q")
    appended = await rt.record_map_mutation(
        mutation_id=f"mut-{uuid.uuid4().hex[:8]}",
        revision=17, kind="add_layer", turn_id=turn_id,
        tool_call_id="call-mut", host="pi")
    assert appended is True
    from app.services.turn_journal.sink import get_turn_journal_sink

    await get_turn_journal_sink().flush()
    rows = await ledger.list_events(session_id, turn_id=turn_id,
                                    kinds=["map_mutated"])
    assert len(rows) == 1
    assert rows[0]["mutation_revision"] == 17
    assert rows[0]["causal_id"].startswith("mut-")

    # 幂等重放：同 mutation_id 不双记
    again = await rt.record_map_mutation(
        mutation_id=rows[0]["causal_id"], revision=17, kind="add_layer",
        turn_id=turn_id, tool_call_id="call-mut", host="pi")
    assert again is False
    await get_turn_journal_sink().flush()
    rows2 = await ledger.list_events(session_id, turn_id=turn_id,
                                     kinds=["map_mutated"])
    assert len(rows2) == 1


async def test_ledger_failure_never_breaks_kernel(session_id, ledger) -> None:
    """账本故障注入：kernel 全流程照常完成（fail-open 契约）。"""
    from app.services.turn_journal import sink as sink_mod
    from app.services.turn_journal.sink import TurnJournalSink

    class _Boom:
        async def append(self, record):  # noqa: ANN001
            raise RuntimeError("db exploded")

    sink_mod.reset_turn_journal_sink()
    sink_mod._sink = TurnJournalSink(ledger=_Boom(), queue_max=4)
    try:
        rt = get_runtime(session_id)
        turn_id = f"turn-{uuid.uuid4().hex[:8]}"
        await rt.begin_turn(turn_id, host="pi", message="q")
        await rt.record_map_mutation(
            mutation_id=f"mut-{uuid.uuid4().hex[:6]}", revision=1,
            kind="add_layer", turn_id=turn_id, tool_call_id="c", host="pi")
        await rt.end_turn(turn_id, host="pi", status="completed",
                          checkpoint=False)
        from app.services.session_plan import load_session_plan

        plan = await load_session_plan(session_id)
        assert plan is not None
        record = next(t for t in plan.turns if t.turn_id == turn_id)
        assert record.status == "completed"  # 权威路径完好
        assert len(plan.decisions) >= 2      # 事件照常入 envelope
    finally:
        sink_mod.reset_turn_journal_sink()
