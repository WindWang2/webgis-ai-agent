"""#409: legacy engine token-stream heartbeat + lossless error flush.

Two defects on the legacy chat engine's LLM token loop:

  - no keepalive during token streaming: the provider read timeout is 180s
    (llm_client) while the SSE proxies in front of the API idle-kill at
    ~60s, so a silently stalled provider hung the turn until the proxy
    dropped the connection;
  - the engine's internal SSEBatcher holds up to 31 un-flushed token events
    and only flushed them on the next push — a mid-stream exception went
    straight to the route's error event, silently dropping the buffered
    partial answer.

Deterministic: fake provider streams (no network); the keepalive timeout is
monkeypatched to milliseconds so no wall-clock waits.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.api.routes import chat as chat_route
from app.services.chat import execution_engine as ee_mod
from app.services.chat.event_resume import TurnResumeRegistry
from app.services.chat_engine import ChatEngine
from app.tools.registry import ToolRegistry
from app.utils.sse import sse_event_type


def _event_type(block: str) -> str:
    return sse_event_type(block)


def _block_events(chunk: str) -> list[str]:
    return [p for p in chunk.split("\n\n") if p]


# ─── fixtures (mirror test_runtime_chaos_engine.py / test_sse_resume.py) ─────


@pytest.fixture
def engine(monkeypatch):
    """Real ChatEngine with DB/planner/title side effects stubbed."""
    eng = ChatEngine(ToolRegistry())

    async def fake_get_or_create_session(session_id, user_id=None):
        return []

    async def fake_maybe_plan(*a, **kw):
        return None

    monkeypatch.setattr(eng, "_get_or_create_session", fake_get_or_create_session)
    monkeypatch.setattr(eng, "_maybe_plan", fake_maybe_plan)
    monkeypatch.setattr(eng, "_generate_title", AsyncMock())
    monkeypatch.setattr(eng, "_save_msg_async", AsyncMock())
    return eng


@pytest.fixture(autouse=True)
def _fresh_resume_registry(monkeypatch):
    """Isolate the process-local resume registry per test."""
    monkeypatch.setattr(chat_route, "_turn_resume_registry", TurnResumeRegistry())
    monkeypatch.setattr(
        chat_route.AsyncHistoryService,
        # #525: the body-session guard now uses the metadata-only variant.
        "get_session_meta",
        AsyncMock(return_value=MagicMock()),
    )
    yield


@pytest.fixture
def _legacy_path(monkeypatch):
    """Force the legacy engine path; install the engine behind get_engine()."""

    def _install(eng) -> None:
        monkeypatch.setattr(chat_route, "USE_NEW_AGENT", False)
        monkeypatch.setattr(chat_route, "get_engine", lambda: eng)

    return _install


# ─── _stream_with_token_keepalive (unit) ─────────────────────────────────────


async def _collect(agen):
    out = []
    async for item in agen:
        out.append(item)
    return out


@pytest.mark.asyncio
async def test_keepalive_helper_passthrough_without_stall():
    """A provider that keeps producing never triggers a keepalive; items pass
    through in order, unchanged."""
    async def fast():
        yield ("token", {"content": "a"})
        yield ("done", {"message": {"content": "a"}})

    items = await _collect(ee_mod._stream_with_token_keepalive(fast(), timeout_s=5))
    assert items == [
        ("token", {"content": "a"}),
        ("done", {"message": {"content": "a"}}),
    ]


@pytest.mark.asyncio
async def test_keepalive_helper_emits_heartbeat_during_stall():
    """A provider silent longer than the timeout yields keep_alive tuples
    (without cancelling the stream), then the real events continue in order."""
    async def stalled():
        yield ("token", {"content": "a"})
        await asyncio.sleep(0.06)  # silence >> timeout
        yield ("done", {"message": {"content": "a"}})

    items = await _collect(ee_mod._stream_with_token_keepalive(stalled(), timeout_s=0.02))
    kinds = [t for t, _ in items]
    assert "keep_alive" in kinds, kinds
    assert kinds[-1] == "done", "the real terminal must arrive after the heartbeats"
    real = [d for t, d in items if t != "keep_alive"]
    assert real == [{"content": "a"}, {"message": {"content": "a"}}]


@pytest.mark.asyncio
async def test_keepalive_helper_propagates_provider_exception():
    """A provider exception is re-raised in the consumer (never swallowed by
    the pump), even when it fires after a stall."""
    async def exploding():
        yield ("token", {"content": "x"})
        await asyncio.sleep(0.05)
        raise RuntimeError("provider down")

    with pytest.raises(RuntimeError, match="provider down"):
        await _collect(
            ee_mod._stream_with_token_keepalive(exploding(), timeout_s=0.02)
        )


# ─── engine-level: heartbeat on stall ────────────────────────────────────────


@pytest.mark.asyncio
async def test_engine_token_stall_emits_keepalive_then_done(engine, monkeypatch):
    """#409: the legacy engine's token loop emits an SSE keep_alive event when
    the provider stays silent past the (monkeypatched, 20ms) timeout — the
    idle proxy would otherwise kill the connection — and the real terminal
    still arrives afterwards."""
    monkeypatch.setattr(ee_mod, "_LLM_TOKEN_KEEPALIVE_S", 0.02)

    async def stalled_stream(*args, **kwargs):
        yield ("token", {"content": "a "})
        await asyncio.sleep(0.06)  # stall past the heartbeat interval
        yield ("done", {"message": {"content": "a"}})

    monkeypatch.setattr(engine, "_call_llm_stream", stalled_stream)

    events: list[str] = []
    async for event in engine.chat_stream("测试", session_id="s-keepalive"):
        events.append(event)

    types = [_event_type(e) for e in events]
    assert "keep_alive" in types, types
    assert types.index("keep_alive") > types.index("task_start"), (
        "the heartbeat must be emitted during the token stall"
    )
    assert types[-1] == "done"


# ─── engine-level: mid-stream error flushes buffered tokens ──────────────────


@pytest.mark.asyncio
async def test_engine_mid_stream_error_flushes_buffered_tokens(engine, monkeypatch):
    """#409: a provider error mid-token-stream must not drop the already
    buffered tokens — they are flushed (yielded) BEFORE the exception
    propagates to the route, so the client's error event follows the partial
    answer instead of replacing it."""
    async def exploding_stream(*args, **kwargs):
        for i in range(5):  # below the 32-event batch threshold → all buffered
            yield ("token", {"content": f"t{i} "})
        raise RuntimeError("provider exploded")

    monkeypatch.setattr(engine, "_call_llm_stream", exploding_stream)

    events: list[str] = []
    with pytest.raises(RuntimeError, match="provider exploded"):
        async for event in engine.chat_stream("测试", session_id="s-flush"):
            events.append(event)

    # The flush emits the 5 tokens as one coalesced chunk — count wire blocks.
    blocks = [b for e in events for b in _block_events(e)]
    types = [_event_type(b) for b in blocks]
    assert types[0] == "task_start"
    assert types.count("token") == 5, (
        f"all buffered tokens must be flushed before the error, got {types}"
    )


# ─── route-level: tokens before error + replayable tail ──────────────────────


@pytest.mark.asyncio
async def test_route_mid_stream_error_delivers_tokens_then_error(
    _legacy_path, engine, monkeypatch
):
    """#409 end-to-end through the chat route (legacy path): the client sees
    the 5 buffered tokens BEFORE the terminal error, and the resume buffer
    recorded the same tail so a reconnect replays tokens + error."""
    async def exploding_stream(*args, **kwargs):
        for i in range(5):
            yield ("token", {"content": f"t{i} "})
        raise RuntimeError("provider exploded")

    monkeypatch.setattr(engine, "_call_llm_stream", exploding_stream)
    _legacy_path(engine)

    resp = await chat_route.chat_stream(
        chat_route.ChatRequest(message="hi", session_id="s-route-flush", map_state=None),
        _user={}, owner_token=None, db=None,
    )
    blocks: list[str] = []
    async for chunk in resp.body_iterator:
        blocks.extend(_block_events(chunk))

    types = [_event_type(b) for b in blocks]
    assert types[0] == "task_start"
    assert types.count("token") == 5, types
    assert types[-1] == "error", types
    assert types.index("error") == len(types) - 1, "the error must terminate the stream"

    # The resume buffer holds the same tail (tokens + error terminal): a
    # reconnect replays the partial answer, not a fabricated done.
    resumed_resp = await chat_route.chat_stream(
        chat_route.ChatRequest(message="hi", session_id="s-route-flush", map_state=None),
        _user={}, owner_token=None, db=None,
        last_event_id_header=1,
    )
    resumed_blocks: list[str] = []
    async for chunk in resumed_resp.body_iterator:
        resumed_blocks.extend(_block_events(chunk))
    resumed_types = [_event_type(b) for b in resumed_blocks]
    assert resumed_types.count("token") == 5, resumed_types
    assert resumed_types[-1] == "error", resumed_types


@pytest.mark.asyncio
async def test_route_token_stall_emits_keepalive(_legacy_path, engine, monkeypatch):
    """#409 route-level: the keep_alive event crosses the route boundary on
    the legacy path (a stalled provider stream stays alive on the wire)."""
    monkeypatch.setattr(ee_mod, "_LLM_TOKEN_KEEPALIVE_S", 0.02)

    async def stalled_stream(*args, **kwargs):
        yield ("token", {"content": "a "})
        await asyncio.sleep(0.06)
        yield ("done", {"message": {"content": "a"}})

    monkeypatch.setattr(engine, "_call_llm_stream", stalled_stream)
    _legacy_path(engine)

    resp = await chat_route.chat_stream(
        chat_route.ChatRequest(message="hi", session_id="s-route-ka", map_state=None),
        _user={}, owner_token=None, db=None,
    )
    blocks: list[str] = []
    async for chunk in resp.body_iterator:
        blocks.extend(_block_events(chunk))

    types = [_event_type(b) for b in blocks]
    assert "keep_alive" in types, types
    assert types[-1] == "done"


@pytest.mark.asyncio
@pytest.mark.parametrize('partial_token', [False, True])
async def test_engine_cancel_interrupts_silent_provider_and_closes_stream(
    engine, monkeypatch, partial_token,
):
    """Task cancellation must stop the HTTP read, not merely flip its badge."""
    entered = asyncio.Event()
    closed = asyncio.Event()

    async def silent_stream(*args, **kwargs):
        try:
            if partial_token:
                yield ('token', {'content': 'partial answer'})
            entered.set()
            await asyncio.Event().wait()
            yield ('done', {'message': {'content': 'late answer'}})
        finally:
            closed.set()

    monkeypatch.setattr(engine, '_call_llm_stream', silent_stream)
    session_id = f's-cancel-silent-{partial_token}'
    events = []

    async def consume():
        async for event in engine.chat_stream('测试', session_id=session_id):
            events.extend(_block_events(event))

    consumer = asyncio.create_task(consume())
    await asyncio.wait_for(entered.wait(), timeout=2)
    task = engine.tracker.list_by_session(session_id)[0]
    assert engine.tracker.cancel(task.id)
    await asyncio.wait_for(consumer, timeout=1)
    assert closed.is_set(), 'cancel must close the upstream generator before settling'
    kinds = [_event_type(event) for event in events]
    assert kinds[-2:] == ['task_cancelled', 'done'], kinds
    assert 'task_complete' not in kinds and 'error' not in kinds, kinds
    assert task.status.value == 'cancelled'
    assert not any(call.args[1] == 'assistant' for call in engine._save_msg_async.await_args_list)


@pytest.mark.asyncio
async def test_engine_cancel_wins_over_queued_late_completion(engine, monkeypatch):
    """A provider completion already queued at cancel cannot persist success."""
    closed = asyncio.Event()
    session_id = 's-cancel-late-completion'

    async def late_stream(*args, **kwargs):
        try:
            task = engine.tracker.list_by_session(session_id)[0]
            engine.tracker.cancel(task.id)
            yield ('token', {'content': 'late answer'})
            yield ('done', {'message': {'content': 'late answer'}})
        finally:
            closed.set()

    monkeypatch.setattr(engine, '_call_llm_stream', late_stream)
    events = []
    async for event in engine.chat_stream('测试', session_id=session_id):
        events.extend(_block_events(event))
    kinds = [_event_type(event) for event in events]
    assert kinds[-2:] == ['task_cancelled', 'done'], kinds
    assert 'task_complete' not in kinds and 'token' not in kinds, kinds
    assert closed.is_set()
    assert not any(call.args[1] == 'assistant' for call in engine._save_msg_async.await_args_list)


@pytest.mark.asyncio
@pytest.mark.parametrize('disconnect', [False, True])
async def test_keepalive_cancel_and_disconnect_reap_provider_and_waiters(disconnect):
    from app.lib.cancellation import CancellationToken, OperationCancelled

    token = CancellationToken('keepalive-cleanup')
    closed = asyncio.Event()
    baseline = asyncio.all_tasks()

    async def provider():
        try:
            yield ('token', {'content': 'partial'})
            await asyncio.Event().wait()
        finally:
            closed.set()

    stream = ee_mod._stream_with_token_keepalive(provider(), timeout_s=10, cancel_token=token)
    assert await anext(stream) == ('token', {'content': 'partial'})
    if disconnect:
        # Early consumer close must clean up without trying to yield terminals.
        await stream.aclose()
    else:
        token.cancel()
        with pytest.raises(OperationCancelled):
            await anext(stream)
    assert closed.is_set()
    assert not (asyncio.all_tasks() - baseline), 'provider or queue/cancel waiter leaked'


@pytest.mark.asyncio
async def test_engine_cancel_continuous_tokens_releases_session_for_next_turn(engine, monkeypatch):
    closed = asyncio.Event()
    session_id = 's-cancel-continuous'

    async def continuous(*args, **kwargs):
        try:
            while True:
                yield ('token', {'content': 'x'})
                await asyncio.sleep(0)  # network readers also yield to the event loop
        finally:
            closed.set()

    monkeypatch.setattr(engine, '_call_llm_stream', continuous)
    events = []
    async for event in engine.chat_stream('测试', session_id=session_id):
        events.extend(_block_events(event))
        if 'token' in [_event_type(block) for block in _block_events(event)]:
            task = engine.tracker.list_by_session(session_id)[0]
            engine.tracker.cancel(task.id)
    kinds = [_event_type(event) for event in events]
    assert kinds.count('task_cancelled') == kinds.count('done') == 1, kinds
    assert 'task_complete' not in kinds
    assert closed.is_set()
    assert not any(call.args[1] == 'assistant' for call in engine._save_msg_async.await_args_list)

    async def completed(*args, **kwargs):
        yield ('done', {'message': {'content': 'new answer'}})

    monkeypatch.setattr(engine, '_call_llm_stream', completed)

    async def next_turn():
        return [event async for event in engine.chat_stream('继续', session_id=session_id)]

    following = await asyncio.wait_for(next_turn(), timeout=2)
    following_kinds = [_event_type(event) for event in following]
    assert following_kinds[-2:] == ['task_complete', 'done'], following_kinds
    assert engine.tracker.list_by_session(session_id)[-1].status.value == 'completed'
    saved_answers = [call for call in engine._save_msg_async.await_args_list if call.args[1] == 'assistant']
    assert len(saved_answers) == 1
    assert saved_answers[0].args[2] == 'new answer'


@pytest.mark.asyncio
async def test_engine_cancel_during_provider_cleanup_cannot_persist_answer(engine, monkeypatch):
    """Cancellation arriving in helper cleanup must win before persistence."""
    session_id = 's-cancel-provider-cleanup'
    cleanup_cancelled = asyncio.Event()
    original_gather = asyncio.gather

    async def cancel_during_cleanup(*tasks, **kwargs):
        if (
            len(tasks) == 3 and isinstance(tasks[0], asyncio.Task)
            and tasks[0].get_coro().__name__ == '_pump'
        ):
            task = engine.tracker.list_by_session(session_id)[0]
            engine.tracker.cancel(task.id)
            cleanup_cancelled.set()
        return await original_gather(*tasks, **kwargs)

    async def completion(*args, **kwargs):
        yield ('done', {'message': {'content': 'do not persist'}})

    monkeypatch.setattr(asyncio, 'gather', cancel_during_cleanup)
    monkeypatch.setattr(engine, '_call_llm_stream', completion)
    events = []
    async for event in engine.chat_stream('测试', session_id=session_id):
        events.extend(_block_events(event))
    assert cleanup_cancelled.is_set()
    kinds = [_event_type(event) for event in events]
    assert kinds[-2:] == ['task_cancelled', 'done'], kinds
    assert 'task_complete' not in kinds
    assert not any(call.args[1] == 'assistant' for call in engine._save_msg_async.await_args_list)


@pytest.mark.parametrize('include_plan', [False, True])
@pytest.mark.parametrize('include_done', [False, True])
def test_cancelled_events_preserves_plan_settle_and_terminal_timing(include_plan, include_done):
    """Shared cancellation tail preserves both legacy wire variants exactly."""
    from app.services.chat.stream_turn_context import StreamTurnContext

    ctx = StreamTurnContext(engine=MagicMock(), session_id='s-tail', message='测试')
    ctx.task = MagicMock(id='task-tail')
    ctx.emitter = MagicMock()
    ctx.emitter.task_cancelled.return_value = 'cancelled'
    ctx.emitter.done.return_value = 'done'
    ctx.plan_finalized_event = MagicMock(return_value='plan' if include_plan else None)
    settled = []
    tail = ctx.cancelled_events(lambda: settled.append('cancelled'), include_done=include_done)
    if include_plan:
        assert next(tail) == 'plan'
        assert settled == [] and ctx.turn_finished is False
    assert next(tail) == 'cancelled'
    assert settled == ['cancelled'] and ctx.turn_finished is False
    if include_done:
        assert next(tail) == 'done'
        assert ctx.turn_finished is False
    with pytest.raises(StopIteration):
        next(tail)
    assert ctx.turn_finished is True
    ctx.plan_finalized_event.assert_called_once()
    ctx.emitter.task_cancelled.assert_called_once_with('task-tail')
    assert ctx.emitter.done.call_count == int(include_done)


@pytest.mark.parametrize('stop_after', ['plan', 'cancelled', 'done'])
def test_cancelled_events_close_preserves_unfinished_tail(stop_after):
    """Disconnect at a yielded frame cannot advance settlement or completion."""
    from app.services.chat.stream_turn_context import StreamTurnContext

    ctx = StreamTurnContext(engine=MagicMock(), session_id='s-tail-close', message='测试')
    ctx.task = MagicMock(id='task-tail')
    ctx.emitter = MagicMock()
    ctx.emitter.task_cancelled.return_value = 'cancelled'
    ctx.emitter.done.return_value = 'done'
    ctx.plan_finalized_event = MagicMock(return_value='plan')
    settled = []
    tail = ctx.cancelled_events(lambda: settled.append('cancelled'), include_done=True)
    for event in tail:
        if event == stop_after:
            break
    tail.close()
    assert settled == ([] if stop_after == 'plan' else ['cancelled'])
    assert ctx.turn_finished is False
    assert ctx.emitter.done.call_count == int(stop_after == 'done')
