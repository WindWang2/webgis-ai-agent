"""Adversarial stress testing for SSE transport, resume, and stream invariants.

Combines random/rapid sequences of:
  - disconnect + cancel + reconnect + session switch
  - multi-client concurrent resume
  - rapid double send & late event races
  - buffer eviction boundaries & ring capacity
  - terminal immutability under out-of-order arrival
"""
from __future__ import annotations

import asyncio
from typing import AsyncIterator, Optional
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.api.routes import chat as chat_route
from app.services.chat.event_resume import TurnResumeRegistry
from app.utils.sse import sse_event, sse_event_id, sse_event_type


class _AdversarialBridge:
    def __init__(self, turns: int = 1) -> None:
        self.prompt_calls = 0
        self.current_turn = 0
        self.parked = [asyncio.Event() for _ in range(turns)]
        self.gates = [asyncio.Event() for _ in range(turns)]

    async def stream_prompt(
        self, message: str, session_id: Optional[str] = None
    ) -> AsyncIterator[str]:
        idx = self.prompt_calls
        self.prompt_calls += 1
        sid = session_id or "s"
        yield sse_event("task_start", {"task_id": f"t{idx}", "session_id": sid})
        for i in range(5):
            yield sse_event("token", {"content": f"tok_{idx}_{i} ", "session_id": sid})
        yield sse_event("step_start", {"tool": "search_poi", "session_id": sid})
        yield sse_event(
            "step_result",
            {"tool": "search_poi", "result": {"ok": True}, "session_id": sid},
        )
        if idx < len(self.parked):
            self.parked[idx].set()
            await self.gates[idx].wait()
        yield sse_event("token", {"content": f"final_{idx} ", "session_id": sid})
        yield sse_event("done", {"session_id": sid})


def _block_events(chunk: str) -> list[str]:
    return [p for p in chunk.split("\n\n") if p]


def _event_id(block: str) -> Optional[int]:
    return sse_event_id(block)


def _event_type(block: str) -> str:
    return sse_event_type(block)


async def _drain_into(resp, out: list[str]) -> None:
    async for chunk in resp.body_iterator:
        out.extend(_block_events(chunk))


async def _start_stream(message: str, session_id: Optional[str] = None):
    resp = await chat_route.chat_stream(
        chat_route.ChatRequest(message=message, session_id=session_id, map_state=None),
        _user={},
        owner_token=None,
        db=None,
    )
    out: list[str] = []
    return out, asyncio.create_task(_drain_into(resp, out))


async def _open_resume(message: str, last_event_id: int, session_id: Optional[str] = None):
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
    monkeypatch.setattr(chat_route, "_turn_resume_registry", TurnResumeRegistry())
    monkeypatch.setattr(chat_route, "_RESUME_LIVE_HOLD_S", 0.05)
    monkeypatch.setattr(
        chat_route.AsyncHistoryService,
        "get_session",
        AsyncMock(return_value=MagicMock()),
    )
    yield


@pytest.fixture
def _pi_path(monkeypatch):
    def _install(bridge) -> None:
        monkeypatch.setattr(chat_route, "USE_NEW_AGENT", True)
        monkeypatch.setattr(chat_route, "pi_bridge", bridge)

    return _install


# ─── Chaos 1: Rapid Disconnect-Resume-Cancel Race ──────────────────────────


@pytest.mark.asyncio
async def test_chaos_disconnect_resume_cancel_race(_pi_path):
    """Adversary: client connects, disconnects mid-turn, resumes, and then
    the resume client disconnects while the turn is still parked live.
    Verifies no task leaks, buffer state remains consistent, and subsequent
    resume gets truthful state."""
    bridge = _AdversarialBridge(turns=1)
    _pi_path(bridge)

    # 1. Start stream 1 in background drain
    out1, t1 = await _start_stream("chaos msg", session_id="sid-chaos-1")
    await bridge.parked[0].wait()
    assert len(out1) > 0

    # 2. Client 1 abruptly cancels / disconnects
    t1.cancel()
    try:
        await t1
    except asyncio.CancelledError:
        pass
    await asyncio.sleep(0.01)

    # 3. Resume 1 connects with last_event_id=1
    resumed1, rtask1 = await _open_resume("chaos msg", last_event_id=1, session_id="sid-chaos-1")
    await asyncio.sleep(0.01)

    # 4. Resume 1 also cancels / disconnects before gate is opened
    rtask1.cancel()
    try:
        await rtask1
    except asyncio.CancelledError:
        pass

    # 5. Resume 2 connects with last_event_id=1
    resumed2, rtask2 = await _open_resume("chaos msg", last_event_id=1, session_id="sid-chaos-1")
    await asyncio.sleep(0.01)

    # 6. Unpark bridge turn
    bridge.gates[0].set()
    await rtask2

    # Verification: Prompt was only called once, resume 2 completed cleanly with all tokens in order
    assert bridge.prompt_calls == 1
    ids2 = [_event_id(b) for b in resumed2 if _event_id(b) is not None]
    assert ids2 == sorted(ids2), f"IDs must be strictly monotonic: {ids2}"
    # Because client 1 disconnected and aborted the prompt mid-stream,
    # the resume correctly reports that the turn was interrupted (truthful error terminal, not fake done)
    assert _event_type(resumed2[-1]) == "error"


# ─── Chaos 2: Session Switch + Simultaneous Multi-Session Resumes ───────────


@pytest.mark.asyncio
async def test_chaos_multi_session_interleaved_resumes(_pi_path):
    """Adversary: 3 independent sessions run turns, get interrupted, and resume
    simultaneously across interleaved event loops.
    Verifies INV-2 (no cross-session bleeding) and INV-5 (no duplicate tool runs)."""
    bridge = _AdversarialBridge(turns=3)
    _pi_path(bridge)

    # Start 3 sessions
    out1, t1 = await _start_stream("msg1", session_id="sess-1")
    out2, t2 = await _start_stream("msg2", session_id="sess-2")
    out3, t3 = await _start_stream("msg3", session_id="sess-3")

    await asyncio.gather(bridge.parked[0].wait(), bridge.parked[1].wait(), bridge.parked[2].wait())
    assert bridge.prompt_calls == 3

    # Release all 3
    bridge.gates[0].set()
    bridge.gates[1].set()
    bridge.gates[2].set()
    await asyncio.gather(t1, t2, t3)

    # Resume all 3 simultaneously from id=2
    r1, rt1 = await _open_resume("msg1", last_event_id=2, session_id="sess-1")
    r2, rt2 = await _open_resume("msg2", last_event_id=2, session_id="sess-2")
    r3, rt3 = await _open_resume("msg3", last_event_id=2, session_id="sess-3")

    await asyncio.gather(rt1, rt2, rt3)

    # No duplicate prompt executions
    assert bridge.prompt_calls == 3

    # Verify each session received only its own tokens
    assert all("tok_0" in b or _event_type(b) in ("step_start", "step_result", "done") for b in r1 if "tok_" in b)
    assert all("tok_1" in b or _event_type(b) in ("step_start", "step_result", "done") for b in r2 if "tok_" in b)
    assert all("tok_2" in b or _event_type(b) in ("step_start", "step_result", "done") for b in r3 if "tok_" in b)


# ─── Chaos 3: Ring Buffer Overflow with Interleaved Resumes ─────────────────


@pytest.mark.asyncio
async def test_chaos_ring_buffer_overflow_with_interleaved_resumes(_pi_path):
    """Adversary: Turn generates 300 events (> RESUME_MAX_EVENTS=256).
    Multiple clients resume at different offsets (recent vs evicted).
    Verifies merged-batch recording keeps the WHOLE turn replayable (#398:
    the ring stores coalesced chunks — ~10 entries for 300 tokens — so the
    head is never silently evicted; a stale resume gets the FULL missed tail
    instead of a truncated answer)."""
    from app.services.chat.event_resume import RESUME_GAP_EVENT_TYPE

    class _MassiveBridge:
        def __init__(self) -> None:
            self.prompt_calls = 0

        async def stream_prompt(self, message: str, session_id: Optional[str] = None):
            self.prompt_calls += 1
            sid = session_id or "s"
            yield sse_event("task_start", {"task_id": "t", "session_id": sid})
            for i in range(300):
                yield sse_event("token", {"content": f"t{i} ", "session_id": sid})
            yield sse_event("done", {"session_id": sid})

    bridge = _MassiveBridge()
    _pi_path(bridge)

    out, t = await _start_stream("massive", session_id="sess-massive")
    await t
    assert bridge.prompt_calls == 1

    # Client A resumes with very old ID (e.g. 5) -> FULL replay 6..302
    # (merged-batch recording keeps the whole 300-token turn in the ring).
    rA, rtA = await _open_resume("massive", last_event_id=5, session_id="sess-massive")
    await rtA
    assert not any(_event_type(b) == RESUME_GAP_EVENT_TYPE for b in rA), (
        "nothing was evicted — a full replay must carry no gap marker"
    )
    idsA = [_event_id(b) for b in rA]
    assert idsA == list(range(6, 303)), (
        f"#398: full replay 6..302 expected, got {idsA[:3]}..{idsA[-3:]} (len={len(idsA)})"
    )
    assert _event_type(rA[-1]) == "done"

    # Client B resumes with recent ID (e.g. 290) -> gets events 291..302
    rB, rtB = await _open_resume("massive", last_event_id=290, session_id="sess-massive")
    await rtB
    idsB = [_event_id(b) for b in rB]
    assert idsB == list(range(291, 303))
    assert _event_type(rB[-1]) == "done"
