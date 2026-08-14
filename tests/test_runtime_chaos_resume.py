"""Runtime chaos hardening: SSE resume edge cases (WP-ROUTE).

Regression coverage for the resume/turn-lifecycle defects:

  - F2: two concurrent ANONYMOUS (shared ``""`` session key) same-message turns
    must never replay each other's events — an ambiguous resume fails safe with
    ``error {resumed: false}`` instead of leaking one user's turn into
    another's stream (cross-session leak).
  - F4: a queued/running second POST must not clobber the live turn's resume
    buffer — resume of turn A works while turn B is queued for the same
    session, and a reconnect with Last-Event-ID never re-executes completed
    tool calls (dispatch invocations are counted).
  - F20: a resume racing a LIVE turn (or a never-yet-started turn) must not
    fabricate a terminal ``done {resumed: true}`` — it replays the buffered
    tail, then holds for the real terminal; a stalled turn ends with a
    truthful ``error {resumed: true, pending: true}`` instead.
  - F5: DELETE /chat/sessions/{id} passes the deleted session's id to
    ``pi_bridge.abort(session_id=...)`` (session-blind abort killed OTHER
    sessions' in-flight turns).
  - F26: the map-action-ack endpoint distinguishes backend-DROPPED events
    (e.g. Redis unreachable) from true duplicates via an additive ``dropped``
    field, so clients can retry real losses.

  - P1 (review follow-up): anonymous (``""``-key) sessions never live-hold a
    matched turn — the ``""`` key is shared by ALL anonymous clients and skips
    ownership checks, so holding would let anyone who knows the victim's
    message + Last-Event-ID tail a stranger's live turn (shadow-stream). An
    anonymous resume of a live/never-started buffer terminates immediately
    with the truthful pending error; ended buffers replay as before.
  - P2 (review follow-up): per-session buffer eviction prefers ENDED buffers,
    so five concurrent anonymous POSTs can't evict a still-live turn's buffer.
  - P2 (round-2 review): DELETE /sessions/{X} purges X's resume buffers (a
    deleted session must not stay replayable); anonymous (''-key) resumes
    require Last-Event-ID > 0 — id 0 (no proof of prior contact) is refused
    with a terminal ``error {resumed: false}`` so a stranger can't replay a
    shared-key turn from the start.

Deterministic: gated fake bridges/engines + asyncio.Event barriers; no wall
clocks (the only timeout is the monkeypatched live-hold cap set to 0.0 — a
zero-wait ``asyncio.wait_for`` that raises TimeoutError immediately when the
event is unset, so the stalled-turn outcome is deterministic and instant).
"""
from __future__ import annotations

import asyncio
from typing import AsyncIterator, Optional
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from starlette.requests import Request

import app.services.session_data as session_data_mod
import app.agent_pi_bridge as agent_pi_bridge
from app.api.routes import chat as chat_route
from app.services.chat.event_resume import TurnEventBuffer, TurnResumeRegistry
from app.services.session_data import MemorySessionStore
from app.utils.sse import sse_event, sse_event_id


# ─── helpers ────────────────────────────────────────────────────────────────


class _GatedBridge:
    """Deterministic Pi bridge with per-call asyncio.Event gates.

    Each ``stream_prompt`` invocation (call index ``idx``):
      * sets ``entered[idx]`` as soon as the route starts consuming it (the
        route has registered its resume buffer by then);
      * with ``idx`` in ``park_before``: waits on ``gates[idx]`` BEFORE the
        first yield (models a turn queued behind the bridge/engine lock —
        buffer registered, zero events);
      * otherwise yields task_start/token/step_result, sets ``parked[idx]``
        (by then the route has recorded all three into the resume buffer),
        waits on ``gates[idx]``, then yields token + done.
    """

    def __init__(self, turns: int = 1, park_before: frozenset[int] = frozenset()) -> None:
        self.prompt_calls = 0
        self.entered = [asyncio.Event() for _ in range(turns)]
        self.parked = [asyncio.Event() for _ in range(turns)]
        self.gates = [asyncio.Event() for _ in range(turns)]
        self._park_before = set(park_before)

    async def stream_prompt(
        self, message: str, session_id: Optional[str] = None
    ) -> AsyncIterator[str]:
        idx = self.prompt_calls
        self.prompt_calls += 1
        sid = session_id or "s"
        self.entered[idx].set()
        if idx in self._park_before:
            await self.gates[idx].wait()  # queued behind the (simulated) lock
            yield sse_event("task_start", {"task_id": f"t{idx}", "session_id": sid})
            yield sse_event("token", {"content": f"a{idx} ", "session_id": sid})
            yield sse_event("done", {"session_id": sid})
            return
        yield sse_event("task_start", {"task_id": f"t{idx}", "session_id": sid})
        yield sse_event("token", {"content": f"a{idx} ", "session_id": sid})
        yield sse_event(
            "step_result",
            {"tool": "search_poi", "result": {"ok": True}, "session_id": sid},
        )
        self.parked[idx].set()
        await self.gates[idx].wait()
        yield sse_event("token", {"content": f"b{idx} ", "session_id": sid})
        yield sse_event("done", {"session_id": sid})


class _GatedEngine:
    """Legacy-path fake engine: counts stream invocations and (simulated)
    side-effecting tool dispatches. With ``queue_second=True`` the second call
    parks at entry (queued behind the per-session engine lock) until released.
    """

    def __init__(self, queue_second: bool = False) -> None:
        self.stream_calls = 0
        self.tool_dispatches = 0
        self.second_entered = asyncio.Event()
        self.second_gate = asyncio.Event()
        self._queue_second = queue_second

    async def chat_stream(
        self,
        message: str,
        session_id: Optional[str] = None,
        map_state: Optional[dict] = None,
        skill_name: Optional[str] = None,
        user_id: Optional[str] = None,
        project_id: Optional[str] = None,  # #348: route passes project_id through
    ) -> AsyncIterator[str]:
        self.stream_calls += 1
        if self._queue_second and self.stream_calls == 2:
            self.second_entered.set()
            await self.second_gate.wait()
            return
        sid = session_id or "s"
        yield sse_event("task_start", {"task_id": "t", "session_id": sid})
        yield sse_event("tool_call", {"tool": "search_poi", "call_id": "c1", "session_id": sid})
        self.tool_dispatches += 1  # side effect: must never happen twice
        yield sse_event(
            "step_result",
            {"tool": "search_poi", "call_id": "c1", "result": {"ok": True}, "session_id": sid},
        )
        yield sse_event("token", {"content": "answer", "session_id": sid})
        yield sse_event("done", {"session_id": sid})


def _block_events(chunk: str) -> list[str]:
    return [p for p in chunk.split("\n\n") if p]


def _event_id(block: str) -> Optional[int]:
    return sse_event_id(block)


def _event_type(block: str) -> str:
    for line in block.split("\n"):
        if line.startswith("event: "):
            return line[len("event: "):].strip()
    return ""


async def _drain_into(resp, out: list[str]) -> None:
    async for chunk in resp.body_iterator:
        out.extend(_block_events(chunk))


async def _until(cond, attempts: int = 500) -> None:
    """Event-loop-turn barrier (no wall clock): spin on sleep(0) until cond."""
    for _ in range(attempts):
        if cond():
            return
        await asyncio.sleep(0)
    raise AssertionError("barrier condition not reached")


async def _start_stream(message: str, session_id: Optional[str] = None):
    """POST a turn and consume it in a dedicated task (each stream gets its
    own task context so per-turn ``sse_event_id_scope`` ContextVars never leak
    across interleaved streams)."""
    resp = await chat_route.chat_stream(
        chat_route.ChatRequest(message=message, session_id=session_id, map_state=None),
        _user={},
        owner_token=None,
        db=None,
    )
    out: list[str] = []
    return out, asyncio.create_task(_drain_into(resp, out))


async def _open_resume(message: str, last_event_id: int, session_id: Optional[str] = None):
    """POST a resume (Last-Event-ID) and consume it in a dedicated task."""
    resp = await chat_route.chat_stream(
        chat_route.ChatRequest(message=message, session_id=session_id, map_state=None),
        _user={},
        owner_token=None,
        db=None,
        last_event_id_header=last_event_id,
    )
    out: list[str] = []
    return out, asyncio.create_task(_drain_into(resp, out))


@pytest.fixture(autouse=True)
def _fresh_resume_registry(monkeypatch):
    """Isolate the process-local resume registry per test."""
    monkeypatch.setattr(chat_route, "_turn_resume_registry", TurnResumeRegistry())
    yield


@pytest.fixture
def _pi_path(monkeypatch):
    """Force the Pi streaming path; caller installs the fake bridge."""

    def _install(bridge) -> None:
        monkeypatch.setattr(chat_route, "USE_NEW_AGENT", True)
        monkeypatch.setattr(chat_route, "pi_bridge", bridge)

    return _install


@pytest.fixture
def _pass_ownership(monkeypatch):
    """Session-scoped tests call the route with db=None; make the body-session
    guard see the session as owned (same seam as
    tests/unit/test_map_action_acks.py::_pass_ownership)."""
    monkeypatch.setattr(
        chat_route.AsyncHistoryService,
        "get_session",
        AsyncMock(return_value=MagicMock()),
    )


# ─── F2: concurrent anonymous same-message turns never cross-replay ─────────


@pytest.mark.asyncio
async def test_concurrent_anonymous_same_message_turns_never_cross_replay(_pi_path):
    """F2: two live anonymous turns (shared ``""`` key) with the SAME message.

    A resume matches both buffered turns — the route must fail safe with a
    terminal ``error {resumed: false}`` (client stops auto-retrying; it is
    never told to resend) instead of replaying one user's turn into the
    other's stream. No new turn is started.
    """
    bridge = _GatedBridge(turns=2)
    _pi_path(bridge)

    out_a, task_a = await _start_stream("same message")
    await bridge.parked[0].wait()
    out_b, task_b = await _start_stream("same message")
    await bridge.parked[1].wait()
    assert bridge.prompt_calls == 2

    resumed, rtask = await _open_resume("same message", last_event_id=1)
    await rtask

    assert bridge.prompt_calls == 2, "a failed-safe resume must never start a turn"
    assert len(resumed) == 1, (
        f"ambiguous resume must fail safe, got {[_event_type(b) for b in resumed]}"
    )
    assert _event_type(resumed[0]) == "error"
    assert '"resumed": false' in resumed[0]

    # cleanup: release both live turns
    bridge.gates[0].set()
    bridge.gates[1].set()
    await task_a
    await task_b


@pytest.mark.asyncio
async def test_anonymous_resume_without_server_session_id_is_refused(_pi_path):
    """A reconnect without the server-issued session UUID cannot address the
    original turn and must fail closed instead of treating Last-Event-ID as an
    ownership capability."""
    bridge = _GatedBridge(turns=1)
    _pi_path(bridge)

    out_a, task_a = await _start_stream("solo")
    await bridge.parked[0].wait()
    bridge.gates[0].set()
    await task_a
    assert [_event_id(b) for b in out_a] == [1, 2, 3, 4, 5]

    resumed, rtask = await _open_resume("solo", last_event_id=2)
    await rtask
    assert [_event_id(b) for b in resumed] == [None]
    assert _event_type(resumed[-1]) == "error"
    assert '"resumed": false' in resumed[-1]
    assert bridge.prompt_calls == 1


# ─── F4: queued second turn must not clobber the live turn's buffer ─────────


@pytest.mark.asyncio
async def test_resume_turn_a_while_turn_b_queued_same_session(_pi_path, _pass_ownership):
    """F4(a): turn B (same session, different message) is queued behind the
    bridge lock — its buffer registration must NOT clobber live turn A's
    buffer. Resuming A replays A's buffered tail, holds while A is live, then
    delivers A's real terminal; no turn is re-executed."""
    bridge = _GatedBridge(turns=2, park_before=frozenset({1}))
    _pi_path(bridge)

    out_a, task_a = await _start_stream("first", session_id="s9")
    await bridge.parked[0].wait()
    # Turn B POSTed while A live: registers its (empty) buffer and parks.
    out_b, task_b = await _start_stream("second", session_id="s9")
    await bridge.entered[1].wait()
    assert bridge.prompt_calls == 2

    resumed, rtask = await _open_resume("first", last_event_id=1, session_id="s9")
    await _until(lambda: len(resumed) >= 2)  # replayed A's buffered tail 2..3
    assert [_event_id(b) for b in resumed] == [2, 3]

    bridge.gates[0].set()  # live turn A resumes producing
    await rtask
    assert [_event_id(b) for b in resumed] == [2, 3, 4, 5], resumed
    assert _event_type(resumed[-1]) == "done", "must deliver A's REAL terminal"
    assert bridge.prompt_calls == 2, "resume never starts a new turn"

    # cleanup: release queued turn B
    bridge.gates[1].set()
    await task_a
    await task_b


@pytest.mark.asyncio
async def test_resume_never_reexecutes_completed_tool_calls(monkeypatch, _pass_ownership):
    """F4(b), legacy path: turn A completed (its tool dispatched exactly once);
    turn B is queued behind the engine lock. A reconnect with Last-Event-ID
    must replay A's tail and NEVER re-dispatch the completed tool call."""
    engine = _GatedEngine(queue_second=True)
    monkeypatch.setattr(chat_route, "USE_NEW_AGENT", False)
    monkeypatch.setattr(chat_route, "pi_bridge", None)
    monkeypatch.setattr(chat_route, "engine", engine)

    out_a, task_a = await _start_stream("run tool", session_id="s-resume-28d95a9e")
    await task_a
    assert [_event_id(b) for b in out_a] == [1, 2, 3, 4, 5]
    assert engine.stream_calls == 1
    assert engine.tool_dispatches == 1

    # Turn B queued (buffer registered, no events yet) — must not clobber A.
    out_b, task_b = await _start_stream("queued work", session_id="s-resume-28d95a9e")
    try:
        await asyncio.wait_for(engine.second_entered.wait(), timeout=15)
    except asyncio.TimeoutError:  # pragma: no cover - diagnostic fast-fail
        engine.second_gate.set()
        await asyncio.gather(task_b, return_exceptions=True)
        raise AssertionError(
            "turn B never entered the engine — per-session lock stalled by "
            "earlier state on this session id (full-suite order pollution)"
        )
    assert engine.stream_calls == 2

    resumed, rtask = await _open_resume("run tool", last_event_id=2, session_id="s-resume-28d95a9e")
    await rtask
    assert [_event_id(b) for b in resumed] == [3, 4, 5], resumed
    assert _event_type(resumed[-1]) == "done"
    assert engine.stream_calls == 2, "resume is a read — no new turn"
    assert engine.tool_dispatches == 1, "completed tool calls never re-execute"

    # cleanup: release queued turn B
    engine.second_gate.set()
    await task_b


# ─── F20: resume racing a live/never-started turn must not fabricate done ───


@pytest.mark.asyncio
async def test_resume_racing_live_turn_holds_then_delivers_real_terminal(_pi_path, _pass_ownership):
    """F20: resume lands mid-turn — replay the buffered tail, HOLD (no fake
    done), then stream the rest live and end with the turn's real terminal."""
    bridge = _GatedBridge(turns=1)
    _pi_path(bridge)

    out_a, task_a = await _start_stream("live", session_id="s7")
    await bridge.parked[0].wait()

    resumed, rtask = await _open_resume("live", last_event_id=1, session_id="s7")
    await _until(lambda: len(resumed) >= 2)
    assert [_event_id(b) for b in resumed] == [2, 3]
    assert not rtask.done(), "resume must HOLD for the live turn, not terminate"

    bridge.gates[0].set()
    await rtask
    await task_a
    assert [_event_id(b) for b in resumed] == [2, 3, 4, 5]
    assert _event_type(resumed[-1]) == "done"
    assert bridge.prompt_calls == 1


@pytest.mark.asyncio
async def test_resume_racing_stalled_turn_emits_truthful_error_not_done(
    _pi_path, monkeypatch, _pass_ownership
):
    """F20: the live turn stalls past the hold cap — the resume ends with a
    truthful terminal ``error {resumed: true, pending: true}`` (turn still in
    progress, reconnect again with Last-Event-ID), NEVER a fabricated done.

    Spec (review follow-up): the hold cap is monkeypatched to 0.0 — a ZERO-WAIT
    ``asyncio.wait_for(timeout=0)`` that raises TimeoutError immediately when
    the event is unset (no wall-clock sleep), so the outcome is deterministic.
    """
    monkeypatch.setattr(chat_route, "_RESUME_LIVE_HOLD_S", 0.0)
    bridge = _GatedBridge(turns=1)
    _pi_path(bridge)

    out_a, task_a = await _start_stream("stalled", session_id="s8")
    await bridge.parked[0].wait()

    resumed, rtask = await _open_resume("stalled", last_event_id=1, session_id="s8")
    await rtask

    assert [_event_id(b) for b in resumed] == [2, 3, None]
    assert _event_type(resumed[-1]) == "error"
    assert '"resumed": true' in resumed[-1]
    assert '"pending": true' in resumed[-1]
    assert all(
        not (_event_type(b) == "done" and '"resumed": true' in b) for b in resumed
    ), "a live turn must never produce a fabricated done"
    assert bridge.prompt_calls == 1

    # cleanup: release the stalled turn
    bridge.gates[0].set()
    await task_a


@pytest.mark.asyncio
async def test_resume_never_started_turn_replays_real_events_not_fake_done(_pi_path, _pass_ownership):
    """F20: the turn was registered but never started (queued behind the lock,
    zero events). A resume must hold for the real events — not claim success
    with a fabricated ``done`` for a turn that hasn't terminated."""
    bridge = _GatedBridge(turns=1, park_before=frozenset({0}))
    _pi_path(bridge)

    out_a, task_a = await _start_stream("queued", session_id="s6")
    await bridge.entered[0].wait()  # buffer registered, zero events

    resumed, rtask = await _open_resume("queued", last_event_id=0, session_id="s6")
    await asyncio.sleep(0)  # let the resume replay (nothing) and park
    assert not rtask.done(), "never-started turn must not claim success"

    bridge.gates[0].set()
    await rtask
    await task_a
    assert [_event_id(b) for b in resumed] == [1, 2, 3], resumed
    assert _event_type(resumed[0]) == "task_start"
    assert _event_type(resumed[-1]) == "done"
    assert bridge.prompt_calls == 1


# ─── REVIEW FOLLOW-UP: anonymous live-hold + buffer eviction ────────────────


@pytest.mark.asyncio
async def test_anonymous_resume_of_live_turn_does_not_tail(_pi_path):
    """An anonymous reconnect without the original server-issued UUID neither
    replays the existing tail nor live-holds the original turn."""
    bridge = _GatedBridge(turns=1)
    _pi_path(bridge)

    out_a, task_a = await _start_stream("secret")
    await bridge.parked[0].wait()

    resumed, rtask = await _open_resume("secret", last_event_id=1)
    await rtask

    bridge.gates[0].set()  # live turn keeps producing — must NOT be tailed
    await task_a
    assert [_event_id(b) for b in resumed] == [None], resumed
    assert _event_type(resumed[-1]) == "error"
    assert '"resumed": false' in resumed[-1]
    assert '"pending"' not in resumed[-1]
    assert bridge.prompt_calls == 1, "the resume never starts a new turn"


@pytest.mark.asyncio
async def test_anonymous_resume_of_never_started_turn_does_not_tail(_pi_path):
    """P1 companion + P2 (round-2): a queued/never-started anonymous turn
    (buffer registered, zero events) resumed with ``last_event_id=0`` has no
    proof of prior contact — the resume is REFUSED outright (terminal
    ``error {resumed: false}``, no pending, no fabricated done) and never
    parks to tail the turn's events once it starts."""
    bridge = _GatedBridge(turns=1, park_before=frozenset({0}))
    _pi_path(bridge)

    out_a, task_a = await _start_stream("queued")
    await bridge.entered[0].wait()  # buffer registered, zero events

    resumed, rtask = await _open_resume("queued", last_event_id=0)
    await rtask  # refused immediately — no hold, no wait
    assert [_event_type(b) for b in resumed] == ["error"], resumed
    assert '"resumed": false' in resumed[0]
    assert '"pending"' not in resumed[0], "refusal is terminal, not pending"

    bridge.gates[0].set()  # the turn starts now — the resume is already closed
    await task_a
    assert len(resumed) == 1, "never-started anonymous turn must not be tailed"
    assert bridge.prompt_calls == 1


@pytest.mark.asyncio
async def test_anonymous_resume_without_prior_contact_is_refused(_pi_path):
    """P2 (round-2 review): an anonymous (''-key) resume with Last-Event-ID 0
    is REFUSED — no proof of prior contact with THAT turn. The '' key is
    shared by ALL anonymous clients and skips ownership checks, so a resume
    from id 0 would let anyone who knows the victim's message replay a
    stranger's whole buffered turn. Refusal is a terminal
    ``error {resumed: false}``: no replay of any event, no fabricated done."""
    bridge = _GatedBridge(turns=1)
    _pi_path(bridge)

    out_a, task_a = await _start_stream("secret")
    bridge.gates[0].set()
    await task_a
    assert [_event_id(b) for b in out_a] == [1, 2, 3, 4, 5]

    resumed, rtask = await _open_resume("secret", last_event_id=0)
    await rtask
    assert [_event_type(b) for b in resumed] == ["error"], [
        _event_type(b) for b in resumed
    ]
    assert '"resumed": false' in resumed[0]
    assert len(resumed) == 1, "the refused resume replays nothing"
    assert bridge.prompt_calls == 1, "the refused resume never starts a turn"


def test_register_evicts_ended_before_live_when_over_cap():
    """P2 (review): over the per-session buffer cap an ENDED buffer is evicted
    first — a still-LIVE turn's buffer must survive N concurrent registrations
    on the same key (e.g. five anonymous POSTs sharing ''), or a resume of the
    live turn fails → client resends → duplicate execution. A live buffer is
    only evicted when every buffer in the deque is live."""
    reg = TurnResumeRegistry(max_buffers_per_session=4)
    live = TurnEventBuffer("", "live")
    reg.register("", live)  # oldest registration, still LIVE
    for i in range(4):
        ended = TurnEventBuffer("", f"ended{i}")
        ended.record(sse_event("done", {"session_id": ""}))  # terminal → ended
        reg.register("", ended)

    survivors = list(reg._buffers[""])  # noqa: SLF001 — eviction asserted directly
    assert len(survivors) == 4
    assert live in survivors, "the still-live buffer must survive eviction"
    assert all(b.ended for b in survivors if b is not live)

    # All-live overflow still caps (falls back to oldest-first eviction).
    all_live = TurnResumeRegistry(max_buffers_per_session=2)
    for _ in range(3):
        all_live.register("", TurnEventBuffer("", "x"))
    assert len(all_live._buffers[""]) == 2  # noqa: SLF001


@pytest.mark.asyncio
async def test_wait_for_update_zero_timeout_returns_immediately():
    """Spec (review follow-up): ``wait_for_update(timeout=0.0)`` must return
    False right away when the buffer has not changed — ``asyncio.wait_for``
    with timeout=0 raises TimeoutError immediately for an unset event (this is
    what the stalled-turn test's monkeypatched ``_RESUME_LIVE_HOLD_S = 0.0``
    relies on — no wall-clock sleep)."""
    buffer = TurnEventBuffer("s", "msg")
    assert await buffer.wait_for_update(timeout=0.0) is False
    assert buffer._waiters == []  # noqa: SLF001 — waiter must be drained


@pytest.mark.asyncio
async def test_waiters_drained_when_buffer_ends():
    """F20 (review follow-up): a parked ``wait_for_update`` is woken AND removed
    when the turn ends (``mark_ended``) — no unbounded ``_waiters`` growth for
    aborted/ended buffers."""
    buffer = TurnEventBuffer("s", "msg")

    async def park():
        return await buffer.wait_for_update(timeout=60)

    task = asyncio.create_task(park())
    await _until(lambda: len(buffer._waiters) == 1)  # noqa: SLF001
    buffer.mark_ended(aborted=False)
    assert await task is True, "mark_ended must wake the parked waiter"
    assert buffer._waiters == []  # noqa: SLF001 — drained after the wake


# ─── P2 (round-2): clear_session must purge the deleted session's buffers ────


def test_registry_clear_session_purges_one_key():
    """P2 (round-2 review): ``TurnResumeRegistry.clear_session(key)`` drops only
    that key's buffers (and its LRU order entry) — other sessions' buffers
    survive, a purged key finds nothing, and an unknown key is a no-op."""
    reg = TurnResumeRegistry()
    a = TurnEventBuffer("s1", "m1")
    b = TurnEventBuffer("s2", "m2")
    reg.register("s1", a)
    reg.register("s2", b)

    reg.clear_session("s1")
    assert reg.find("s1", "m1", 0) == (None, False), (
        "a purged key must be a plain miss (no replay of deleted events)"
    )
    assert reg.find("s2", "m2", 0)[0] is b, "other sessions' buffers survive"
    assert len(reg) == 1

    reg.clear_session("no-such-key")  # no-op, no exception
    assert len(reg) == 1


@pytest.mark.asyncio
async def test_clear_session_route_purges_resume_buffers(_pi_path, monkeypatch, _pass_ownership):
    """P2 (round-2 review): DELETE /chat/sessions/{X} must purge X's resume
    buffers — after the delete, anyone holding session_id X + the message must
    NOT be able to replay X's buffered events (they'd be replayable until LRU
    eviction otherwise). The resume fails safe with ``error {resumed: false}``
    and never starts a new turn."""

    class _ClearableEngine:
        async def clear_session(self, session_id, user_id=None, owner_token=None):
            return True

    bridge = _GatedBridge(turns=1)
    _pi_path(bridge)

    # Turn runs and completes; its buffer is registered under the session key.
    session_id = f"victim-session-{id(bridge)}"
    out_a, task_a = await _start_stream("victim msg", session_id=session_id)
    bridge.gates[0].set()
    await task_a
    assert [_event_id(b) for b in out_a] == [1, 2, 3, 4, 5]

    # Resume works BEFORE the delete.
    resumed, rtask = await _open_resume(
        "victim msg", last_event_id=1, session_id=session_id
    )
    await rtask
    assert [_event_id(b) for b in resumed] == [2, 3, 4, 5], resumed

    # DELETE the session (legacy path — engine reports success).
    monkeypatch.setattr(chat_route, "USE_NEW_AGENT", False)
    monkeypatch.setattr(chat_route, "pi_bridge", None)
    monkeypatch.setattr(chat_route, "engine", _ClearableEngine())
    result = await chat_route.clear_session(
        session_id=session_id,
        _user={"user_id": None},
        owner_token=None,
        _conv=MagicMock(),
    )
    assert result == {"status": "ok"}

    # The durable deletion tombstone rejects the request before any replay
    # generator can expose buffered content.
    with pytest.raises(HTTPException) as deleted:
        await _open_resume("victim msg", last_event_id=1, session_id=session_id)
    assert deleted.value.status_code == 404
    assert bridge.prompt_calls == 1, "a refused resume never starts a new turn"


# ─── F5: clear_session aborts the DELETED session, not whatever is live ─────


@pytest.mark.asyncio
async def test_clear_session_aborts_deleted_session(monkeypatch):
    """F5: the route must pass the deleted session's id to
    ``pi_bridge.abort(session_id=...)`` so the bridge can skip the abort when
    a DIFFERENT session's turn is in flight (contract:
    ``PiBridge.abort(session_id: str | None = None)``)."""

    class _RecordingBridge:
        def __init__(self) -> None:
            self.abort_calls: list[Optional[str]] = []

        async def abort(self, session_id: Optional[str] = None) -> dict:
            self.abort_calls.append(session_id)
            return {}

    class _ClearableEngine:
        async def clear_session(self, session_id, user_id=None, owner_token=None):
            return True

    bridge = _RecordingBridge()
    monkeypatch.setattr(chat_route, "USE_NEW_AGENT", True)
    monkeypatch.setattr(chat_route, "pi_bridge", bridge)
    monkeypatch.setattr(chat_route, "engine", _ClearableEngine())

    result = await chat_route.clear_session(
        session_id="victim-session",
        _user={"user_id": None},
        owner_token=None,
        _conv=MagicMock(),
    )
    assert result == {"status": "ok"}
    assert bridge.abort_calls == ["victim-session"], (
        "clear_session must scope the abort to the deleted session"
    )


# ─── F26: map-action-ack endpoint exposes dropped-vs-duplicate ───────────────


class _DroppingStore:
    """Degraded backend: every append is dropped (Redis-unreachable path of
    session_data_redis returns False), nothing is stored."""

    def __init__(self) -> None:
        self.append_calls = 0

    async def append_map_action_event(self, session_id: str, event: dict) -> bool:
        self.append_calls += 1
        return False

    async def get_map_action_events(self, session_id: str) -> list[dict]:
        return []

    async def get_map_state(self, session_id: str) -> dict:
        return {}


def _fake_request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/",
            "headers": [],
            "client": ("127.0.0.1", 12345),
        }
    )


def _ack_payload(action_id: str) -> chat_route.MapActionAck:
    return chat_route.MapActionAck(action_id=action_id, command="fly_to", status="succeeded")


@pytest.fixture(autouse=True)
def _stub_ack_rate_limiter(monkeypatch):
    """Real get_rate_limiter() would try to reach Redis (no network in unit
    tests); stub it to always allow (limit itself is covered elsewhere)."""
    limiter = MagicMock()

    async def _is_allowed(key, max_requests, window_seconds):
        return True

    limiter.is_allowed = _is_allowed

    async def _get_stub():
        return limiter

    monkeypatch.setattr(chat_route, "get_rate_limiter", _get_stub)


@pytest.mark.asyncio
async def test_ack_endpoint_reports_dropped_separately_from_duplicates(monkeypatch):
    """F26: a backend-dropped ACK is NOT a duplicate — the response must expose
    the loss (additive ``dropped`` field) so the client knows to retry."""
    store = _DroppingStore()
    monkeypatch.setattr(session_data_mod, "session_data_manager", store)

    resp = await chat_route.push_map_action_acks(
        "sess-x",
        chat_route.MapActionAckRequest(acks=[_ack_payload("ma-1"), _ack_payload("ma-2")]),
        _fake_request(),
        MagicMock(),
    )
    assert resp == {"accepted": 0, "duplicates": 0, "dropped": 2}, resp
    assert store.append_calls == 2


@pytest.mark.asyncio
async def test_ack_endpoint_true_duplicate_not_reported_as_dropped(monkeypatch):
    """F26 companion: a real duplicate (first-terminal-wins) stays
    ``duplicates`` — and when nothing was dropped the ``dropped`` key is
    absent (backward-compatible response shape)."""
    store = MemorySessionStore()
    monkeypatch.setattr(session_data_mod, "session_data_manager", store)

    first = await chat_route.push_map_action_acks(
        "sess-x",
        chat_route.MapActionAckRequest(acks=[_ack_payload("ma-1")]),
        _fake_request(),
        MagicMock(),
    )
    assert first == {"accepted": 1, "duplicates": 0}, first

    dup = await chat_route.push_map_action_acks(
        "sess-x",
        chat_route.MapActionAckRequest(acks=[_ack_payload("ma-1")]),
        _fake_request(),
        MagicMock(),
    )
    assert dup == {"accepted": 0, "duplicates": 1}, dup


@pytest.mark.asyncio
async def test_ack_endpoint_evaluates_on_fresh_replica_without_local_harness(
    monkeypatch,
):
    """An accepted ACK must reach durable-context rehydration on every pod."""
    store = MemorySessionStore()
    monkeypatch.setattr(session_data_mod, "session_data_manager", store)
    evaluated: list[str] = []

    async def _evaluate(session_id: str, *, session_lock_held: bool = False):
        evaluated.append(session_id)
        assert session_lock_held is True
        return {
            "session_id": session_id,
            "cartography": {
                "status": "not_evaluated",
                "termination_reason": "no_session_harness",
            },
            "overall_passed": False,
        }

    monkeypatch.setattr(agent_pi_bridge, "evaluate_cartographic_session", _evaluate)

    response = await chat_route.push_map_action_acks(
        "fresh-replica",
        chat_route.MapActionAckRequest(acks=[_ack_payload("ma-fresh")]),
        _fake_request(),
        MagicMock(),
    )

    assert response == {"accepted": 1, "duplicates": 0}
    assert evaluated == ["fresh-replica"]


class _ReadCountingStore:
    """Wraps a real store while counting ``get_map_action_events`` readbacks
    (the O(n^2) vector: the route used to re-read per rejected ack)."""

    def __init__(self, inner=None) -> None:
        self._inner = inner or MemorySessionStore()
        self.read_calls = 0

    async def append_map_action_event(self, session_id: str, event: dict) -> bool:
        return await self._inner.append_map_action_event(session_id, event)

    async def get_map_action_events(self, session_id: str) -> list[dict]:
        self.read_calls += 1
        return await self._inner.get_map_action_events(session_id)

    async def get_map_state(self, session_id: str) -> dict:
        getter = getattr(self._inner, "get_map_state", None)
        if getter is None:
            return {}
        return await getter(session_id)


@pytest.mark.asyncio
async def test_ack_endpoint_all_rejected_reads_store_once(monkeypatch):
    """P2 (round-2 review): a batch where every ack is rejected (dropped) must
    trigger exactly ONE readback, not one read per rejected ack (50 reads was
    the O(n^2) path — e.g. a retrying client spamming 50 duplicates)."""
    store = _ReadCountingStore(inner=_DroppingStore())
    monkeypatch.setattr(session_data_mod, "session_data_manager", store)

    resp = await chat_route.push_map_action_acks(
        "sess-x",
        chat_route.MapActionAckRequest(
            acks=[_ack_payload(f"ma-{i:02d}") for i in range(50)]
        ),
        _fake_request(),
        MagicMock(),
    )
    assert resp == {"accepted": 0, "duplicates": 0, "dropped": 50}, resp
    assert store.read_calls == 1, (
        f"one hoisted readback must classify all 50 rejects, got {store.read_calls}"
    )


@pytest.mark.asyncio
async def test_ack_endpoint_mixed_batch_bounded_reads_keeps_semantics(monkeypatch):
    """P2 companion: mixed accept/reject batch — readbacks stay bounded
    (hoisted snapshot; refreshed only after an accepted append) while
    dropped-vs-duplicate semantics are preserved (in-batch duplicates of
    just-appended ids still count as duplicates)."""
    store = _ReadCountingStore()
    monkeypatch.setattr(session_data_mod, "session_data_manager", store)
    # Pre-seed two already-stored acks so a later reject of them is a duplicate.
    await store._inner.append_map_action_event(
        "sess-x", _ack_payload("ma-old-1").model_dump(exclude_none=True)
    )
    await store._inner.append_map_action_event(
        "sess-x", _ack_payload("ma-old-2").model_dump(exclude_none=True)
    )

    resp = await chat_route.push_map_action_acks(
        "sess-x",
        chat_route.MapActionAckRequest(acks=[
            _ack_payload("ma-new-1"),  # accepted (new)
            _ack_payload("ma-old-1"),  # duplicate of pre-seeded
            _ack_payload("ma-new-2"),  # accepted (new)
            _ack_payload("ma-new-1"),  # duplicate of the just-accepted id
            _ack_payload("ma-old-2"),  # duplicate of pre-seeded
        ]),
        _fake_request(),
        MagicMock(),
    )
    assert resp == {"accepted": 2, "duplicates": 3}, resp
    assert store.read_calls <= 3, (
        f"readbacks must stay bounded by accepted appends, got {store.read_calls}"
    )


@pytest.mark.asyncio
async def test_ack_persist_redis_tombstone_is_410(monkeypatch):
    """ACK persist must honor the Redis/map_state tombstone, not only the
    process-local is_cartographic_session_deleted set (cross-replica delete)."""

    class _TombstoneStore(MemorySessionStore):
        async def get_map_state(self, session_id: str) -> dict:
            return {"_cartographic_deleted": True}

    monkeypatch.setattr(session_data_mod, "session_data_manager", _TombstoneStore())
    monkeypatch.setattr(agent_pi_bridge, "is_cartographic_session_deleted", lambda _sid: False)
    with pytest.raises(HTTPException) as ei:
        await chat_route._persist_map_action_acks_locked(
            "sess-deleted",
            chat_route.MapActionAckRequest(acks=[_ack_payload("ma-late")]),
        )
    assert ei.value.status_code == 410


# ─── F18: terminal type preservation (cancelled != failed != succeeded) ────


@pytest.mark.asyncio
async def test_resume_after_cancelled_turn_replays_cancelled_terminal(_pi_path, _pass_ownership):
    """INV-4 / F18: a turn that ended in `task_cancelled` must NOT produce a
    synthesized `done` on reconnect — reconnecting for a clean close when the
    client already saw the terminal must synthesize `task_cancelled`, preserving
    cancellation status (cancelled != succeeded)."""

    class _CancelledBridge:
        def __init__(self) -> None:
            self.prompt_calls = 0

        async def stream_prompt(self, message: str, session_id: Optional[str] = None):
            self.prompt_calls += 1
            sid = session_id or "s"
            yield sse_event("task_start", {"task_id": "t", "session_id": sid})
            yield sse_event("token", {"content": "partial ", "session_id": sid})
            yield sse_event("task_cancelled", {"task_id": "t", "session_id": sid})

    bridge = _CancelledBridge()
    _pi_path(bridge)

    out_a, task_a = await _start_stream("cancel me", session_id="s-cancel")
    await task_a
    ids = [_event_id(b) for b in out_a]
    assert _event_type(out_a[-1]) == "task_cancelled"
    terminal_id = ids[-1]
    assert terminal_id is not None

    # Reconnect after consuming the terminal event (last_event_id == terminal_id)
    resumed, rtask = await _open_resume(
        "cancel me", last_event_id=terminal_id, session_id="s-cancel"
    )
    await rtask
    assert bridge.prompt_calls == 1
    assert len(resumed) == 1
    # MUST NOT be synthesized "done" — must preserve cancellation!
    assert _event_type(resumed[0]) == "task_cancelled", (
        f"reconnecting after task_cancelled must synthesize task_cancelled, got {_event_type(resumed[0])}"
    )


@pytest.mark.asyncio
async def test_resume_after_task_error_replays_error_terminal_not_done(_pi_path, _pass_ownership):
    """INV-4 / F18: a turn that ended in `task_error` must NOT produce a
    synthesized `done` on reconnect (failed != succeeded)."""

    class _TaskErrorBridge:
        def __init__(self) -> None:
            self.prompt_calls = 0

        async def stream_prompt(self, message: str, session_id: Optional[str] = None):
            self.prompt_calls += 1
            sid = session_id or "s"
            yield sse_event("task_start", {"task_id": "t", "session_id": sid})
            yield sse_event("task_error", {"task_id": "t", "error": "failed", "session_id": sid})

    bridge = _TaskErrorBridge()
    _pi_path(bridge)

    out_a, task_a = await _start_stream("err turn", session_id="s-err")
    await task_a
    ids = [_event_id(b) for b in out_a]
    assert _event_type(out_a[-1]) == "task_error"
    terminal_id = ids[-1]
    assert terminal_id is not None

    resumed, rtask = await _open_resume(
        "err turn", last_event_id=terminal_id, session_id="s-err"
    )
    await rtask
    assert bridge.prompt_calls == 1
    assert len(resumed) == 1
    assert _event_type(resumed[0]) == "task_error", (
        f"reconnecting after task_error must synthesize task_error, got {_event_type(resumed[0])}"
    )


# ─── F8: two browser tabs resume same session concurrently ───────────────────


@pytest.mark.asyncio
async def test_two_tabs_concurrent_resume_same_session(_pi_path, _pass_ownership):
    """F8: two tabs reconnecting for the same completed turn with Last-Event-ID
    both receive the exact same replay without corrupting the buffer or
    triggering duplicate prompt dispatches."""

    class _MultiTokenBridge:
        def __init__(self) -> None:
            self.prompt_calls = 0

        async def stream_prompt(self, message: str, session_id: Optional[str] = None):
            self.prompt_calls += 1
            sid = session_id or "s"
            yield sse_event("task_start", {"task_id": "t", "session_id": sid})
            yield sse_event("token", {"content": "1 ", "session_id": sid})
            yield sse_event("token", {"content": "2 ", "session_id": sid})
            yield sse_event("done", {"session_id": sid})

    bridge = _MultiTokenBridge()
    _pi_path(bridge)

    out_a, task_a = await _start_stream("two tabs", session_id="s-tabs")
    await task_a
    assert bridge.prompt_calls == 1

    # Tab 1 resumes after id 1; Tab 2 resumes after id 2 concurrently
    resumed1, rtask1 = await _open_resume("two tabs", last_event_id=1, session_id="s-tabs")
    resumed2, rtask2 = await _open_resume("two tabs", last_event_id=2, session_id="s-tabs")
    await asyncio.gather(rtask1, rtask2)

    assert bridge.prompt_calls == 1, "concurrent resumes must never dispatch new turns"
    assert [_event_id(b) for b in resumed1] == [2, 3, 4]
    assert [_event_id(b) for b in resumed2] == [3, 4]
    assert _event_type(resumed1[-1]) == "done"
    assert _event_type(resumed2[-1]) == "done"

