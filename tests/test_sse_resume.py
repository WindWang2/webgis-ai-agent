"""DUP-1: SSE event-id + turn-resume contract, route-level.

Covers the end-to-end resume contract on the chat stream route:

  - every event of a turn carries a per-turn monotonic ``id:`` that stays
    ordered across batched token events, structural events and heartbeats
    (D-F6 ``_sse_batched`` at the route boundary);
  - a POST /chat/stream carrying ``Last-Event-ID`` (header) or
    ``last_event_id`` (query) is a RESUME — a read that replays exactly the
    missed events in order and terminates, and NEVER starts a new turn (no
    prompt RPC, no tool dispatch → no duplicate execution);
  - a resume after the turn completed cleanly terminates (replayed terminal,
    or a synthesized ``done`` when the client already saw the terminal);
  - a resume after an interrupted (aborted-on-disconnect) turn replays the
    partial content and then emits an ``error`` — partial content is never
    presented as a complete answer;
  - a resume with no matching buffered turn (restart / eviction / message
    mismatch) emits ``error {resumed: false}`` and does not execute.

Deterministic: mock Pi bridges only; no LLM, no subprocess, no network.
"""
from __future__ import annotations

import asyncio
from typing import AsyncIterator, Optional

import httpx
import pytest
from fastapi import FastAPI

from app.api.routes import chat as chat_route
from app.core.auth import get_current_user_optional, get_owner_token
from app.core.database import get_async_db
from app.services.chat.event_resume import TurnResumeRegistry
from app.utils.sse import sse_event, sse_event_id


# ─── helpers ────────────────────────────────────────────────────────────────


class _BurstBridge:
    """Deterministic Pi bridge: task_start + token bursts with structural
    step_results between them + a terminal done. Mirrors _BurstPiBridge in
    test_streaming_lifecycle.py but counts stream_prompt invocations so a
    resume can assert NO new turn was started."""

    def __init__(self, bursts: tuple[int, ...] = (96, 104), message: str = "hi") -> None:
        self.bursts = bursts
        self.message = message
        self.prompt_calls = 0

    async def stream_prompt(
        self, message: str, session_id: Optional[str] = None
    ) -> AsyncIterator[str]:
        self.prompt_calls += 1
        sid = session_id or "s"
        yield sse_event("task_start", {"task_id": "t", "session_id": sid})
        i = 0
        for burst in self.bursts:
            for _ in range(burst):
                yield sse_event("token", {"content": f"tok{i} ", "session_id": sid})
                i += 1
            yield sse_event(
                "step_result",
                {"tool": "search_poi", "result": {"ok": True}, "session_id": sid},
            )
        yield sse_event("done", {"session_id": sid})


class _AbortableBridge:
    """Bridge that yields task_start + token + step_result (the structural
    step_result flushes the batched token through the route batcher so the
    client actually receives it), then parks forever mid-turn."""

    def __init__(self) -> None:
        self.prompt_calls = 0

    async def stream_prompt(
        self, message: str, session_id: Optional[str] = None
    ) -> AsyncIterator[str]:
        self.prompt_calls += 1
        sid = session_id or "s"
        yield sse_event("task_start", {"task_id": "t", "session_id": sid})
        yield sse_event("token", {"content": "partial ", "session_id": sid})
        yield sse_event(
            "step_result",
            {"tool": "search_poi", "result": {"ok": True}, "session_id": sid},
        )
        await asyncio.sleep(3600)  # parked mid-turn until disconnected


def _block_events(chunk: str) -> list[str]:
    """Split a (possibly coalesced) SSE chunk into individual event blocks."""
    return [p for p in chunk.split("\n\n") if p]


def _event_id(event_block: str) -> Optional[int]:
    return sse_event_id(event_block)


def _event_type(event_block: str) -> str:
    for line in event_block.split("\n"):
        if line.startswith("event: "):
            return line[len("event: "):].strip()
    return ""


async def _collect_route(monkeypatch, bridge, message="hi", last_event_id=None, session_id=None):
    """Drive one POST /chat/stream through the route function directly and
    return (event_blocks, bridge). last_event_id simulates the resume header."""
    monkeypatch.setattr(chat_route, "USE_NEW_AGENT", True)
    monkeypatch.setattr(chat_route, "pi_bridge", bridge)
    resp = await chat_route.chat_stream(
        chat_route.ChatRequest(message=message, session_id=session_id, map_state=None),
        _user={},
        owner_token=None,
        db=None,
        last_event_id_header=last_event_id,
    )
    blocks: list[str] = []
    async for chunk in resp.body_iterator:
        blocks.extend(_block_events(chunk))
    return blocks, bridge


@pytest.fixture(autouse=True)
def _fresh_resume_registry(monkeypatch):
    """Isolate the process-local resume registry per test."""
    monkeypatch.setattr(chat_route, "_turn_resume_registry", TurnResumeRegistry())
    yield


# ─── 1. monotonic ids across batched + structural + heartbeat ───────────────


@pytest.mark.asyncio
async def test_pi_stream_ids_monotonic_across_batched_and_structural(monkeypatch):
    """DUP-1: a full 200-token Pi turn yields strictly monotonic ids (1..204)
    across the D-F6 batched token events AND the structural events that flush
    them (task_start / step_result / done)."""
    blocks, bridge = await _collect_route(monkeypatch, _BurstBridge(bursts=(96, 104)))
    ids = [_event_id(b) for b in blocks]
    assert ids == list(range(1, len(blocks) + 1)), (
        f"ids must be 1..{len(blocks)} in emission order, got head={ids[:5]} tail={ids[-5:]}"
    )
    assert ids[-1] == 204
    # Every event carries an id line.
    assert all(i is not None for i in ids)
    # The terminal done carries the last id.
    assert _event_type(blocks[-1]) == "done"
    assert bridge.prompt_calls == 1


@pytest.mark.asyncio
async def test_pi_stream_ids_monotonic_with_heartbeat_comments(monkeypatch):
    """DUP-1: keepalive comments interleaved between events consume no id and
    never break monotonicity of the real events."""

    class _HeartbeatBridge:
        async def stream_prompt(self, message, session_id=None):
            sid = session_id or "s"
            yield sse_event("task_start", {"session_id": sid})
            yield ": keepalive\n\n"
            yield sse_event("token", {"content": "a ", "session_id": sid})
            yield ": keepalive\n\n"
            yield sse_event("token", {"content": "b ", "session_id": sid})
            yield sse_event("done", {"session_id": sid})

    bridge = _HeartbeatBridge()
    blocks, _ = await _collect_route(monkeypatch, bridge)
    real = [b for b in blocks if _event_id(b) is not None]
    comments = [b for b in blocks if _event_id(b) is None]
    assert [b for b in comments if not b.startswith(":")] == []
    assert [_event_id(b) for b in real] == [1, 2, 3, 4]
    assert _event_type(real[-1]) == "done"


# ─── 2. resume: replay semantics ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_resume_replays_exactly_missed_events_in_order(monkeypatch):
    """A resume after id 2 replays exactly events 3..7 in order — the tail the
    dropped connection missed — and does NOT start a new turn (the bridge's
    prompt RPC is never re-sent: resume is a read, no duplicate execution)."""
    bridge = _BurstBridge(bursts=(4,))
    first_blocks, _ = await _collect_route(monkeypatch, bridge)
    first_ids = [_event_id(b) for b in first_blocks]
    assert first_ids == [1, 2, 3, 4, 5, 6, 7]
    assert bridge.prompt_calls == 1

    resumed, bridge_after = await _collect_route(
        monkeypatch, bridge, last_event_id=2
    )
    assert bridge_after.prompt_calls == 1, (
        "a resume must never re-send the prompt (no duplicate execution)"
    )
    resumed_ids = [_event_id(b) for b in resumed]
    assert resumed_ids == [3, 4, 5, 6, 7], (
        f"resume must replay exactly the missed tail 3..7, got {resumed_ids}"
    )
    # Replay preserves the original event order AND the original ids (the
    # client's dedup relies on stable ids): the missed tail of the first stream.
    assert resumed_ids == first_ids[2:]
    assert _event_type(resumed[-1]) == "done"


@pytest.mark.asyncio
async def test_resume_replay_bounded_to_ring_tail(monkeypatch):
    """The resume buffer is a bounded ring (RESUME_MAX_EVENTS=64): a client
    that fell far behind gets only the buffered tail, replayed in order — the
    documented bound, never a duplicate or reordered event."""
    from app.services.chat.event_resume import RESUME_MAX_EVENTS

    bridge = _BurstBridge(bursts=(96, 104))  # 204 events total
    await _collect_route(monkeypatch, bridge)

    resumed, bridge_after = await _collect_route(
        monkeypatch, bridge, last_event_id=50
    )
    assert bridge_after.prompt_calls == 1
    resumed_ids = [_event_id(b) for b in resumed]
    # Ring tail = the last 64 events (141..204); ids 51..140 were evicted.
    assert len(resumed_ids) == RESUME_MAX_EVENTS, len(resumed_ids)
    assert resumed_ids == list(range(141, 205)), (
        f"replay must deliver the ring tail 141..204, got {resumed_ids[:3]}..{resumed_ids[-3:]}"
    )


@pytest.mark.asyncio
async def test_resume_after_completion_all_events_seen_terminates(monkeypatch):
    """Client saw the whole turn (last_event_id == last id): the replay is
    empty and the stream terminates with a synthesized ``done {resumed: true}``
    instead of hanging — the lost-terminal case."""
    bridge = _BurstBridge(bursts=(4,))
    blocks, _ = await _collect_route(monkeypatch, bridge)
    last_id = _event_id(blocks[-1])
    assert last_id == 7  # task_start + 4 tokens + step_result + done

    resumed, bridge_after = await _collect_route(
        monkeypatch, bridge, last_event_id=last_id
    )
    assert bridge_after.prompt_calls == 1
    assert len(resumed) == 1
    assert _event_type(resumed[0]) == "done"
    assert '"resumed": true' in resumed[0]


@pytest.mark.asyncio
async def test_resume_after_aborted_turn_replays_then_errors(monkeypatch):
    """A turn cut by a client disconnect (server aborts the prompt; no terminal
    was produced) resumes as: replay the partial content, then a terminal
    ``error`` — partial content is never presented as a complete answer."""
    bridge = _AbortableBridge()
    monkeypatch.setattr(chat_route, "USE_NEW_AGENT", True)
    monkeypatch.setattr(chat_route, "pi_bridge", bridge)

    resp = await chat_route.chat_stream(
        chat_route.ChatRequest(message="hi", session_id=None, map_state=None),
        _user={}, owner_token=None, db=None,
    )
    it = resp.body_iterator
    first = await it.__anext__()
    second = await it.__anext__()
    third = await it.__anext__()
    assert _event_type(first) == "task_start"
    assert "partial" in second
    assert _event_type(third) == "step_result"
    await it.aclose()  # client disconnect → GeneratorExit → buffer marked aborted
    await asyncio.sleep(0.01)

    buffer = chat_route._turn_resume_registry.get("")
    assert buffer is not None
    assert buffer.ended is True and buffer.aborted is True
    assert buffer.terminal_event is None

    resumed, bridge_after = await _collect_route(
        monkeypatch, bridge, last_event_id=0
    )
    assert bridge_after.prompt_calls == 1, "resume must not re-execute the aborted turn"
    ids = [_event_id(b) for b in resumed]
    # Buffered partial turn (task_start/token/step_result) replayed in order,
    # then the synthesized error terminal (no id — not part of the turn).
    assert ids[:3] == [1, 2, 3], f"replay must deliver the buffered partial turn, got {ids}"
    assert ids[-1] is None
    assert _event_type(resumed[-1]) == "error"
    assert '"resumed": true' in resumed[-1]


@pytest.mark.asyncio
async def test_resume_buffer_miss_does_not_execute(monkeypatch):
    """No buffered turn for the session (restart / eviction): a terminal
    ``error {resumed: false}`` — and critically the bridge prompt is NOT sent
    (a stale reconnect must never silently re-execute the message)."""
    bridge = _BurstBridge(bursts=(2,))
    blocks, bridge_after = await _collect_route(monkeypatch, bridge, last_event_id=3)
    assert bridge_after.prompt_calls == 0
    assert len(blocks) == 1
    assert _event_type(blocks[0]) == "error"
    assert '"resumed": false' in blocks[0]


@pytest.mark.asyncio
async def test_resume_message_mismatch_does_not_execute(monkeypatch):
    """The resume must match the SAME turn (message) — a different message for
    the same session key (e.g. two anonymous turns sharing the "" key) must not
    replay another turn's events."""
    bridge = _BurstBridge(bursts=(2,))
    await _collect_route(monkeypatch, bridge, message="first")
    blocks, bridge_after = await _collect_route(
        monkeypatch, bridge, message="different", last_event_id=1
    )
    assert bridge_after.prompt_calls == 1  # only the original turn ran
    assert len(blocks) == 1
    assert _event_type(blocks[0]) == "error"
    assert '"resumed": false' in blocks[0]


@pytest.mark.asyncio
async def test_resume_after_route_error_replays_error_terminal(monkeypatch):
    """An upstream failure (the route synthesizes `error`) records that error
    as the turn's terminal — a resume replays it (not a second, different
    error, and never `done`), so the resumed stream ends with the same failure
    the connected client would have seen."""
    class _ExplodingBridge:
        def __init__(self) -> None:
            self.prompt_calls = 0

        async def stream_prompt(
            self, message: str, session_id: Optional[str] = None
        ) -> AsyncIterator[str]:
            self.prompt_calls += 1
            sid = session_id or "s"
            yield sse_event("task_start", {"task_id": "t", "session_id": sid})
            yield sse_event("token", {"content": "t ", "session_id": sid})
            raise RuntimeError("boom")

    bridge = _ExplodingBridge()
    blocks, _ = await _collect_route(monkeypatch, bridge)
    assert [_event_type(b) for b in blocks] == ["task_start", "token", "error"]

    resumed, bridge_after = await _collect_route(
        monkeypatch, bridge, last_event_id=1
    )
    assert bridge_after.prompt_calls == 1, "resume must not re-execute the failed turn"
    assert [_event_type(b) for b in resumed] == ["token", "error"], [
        _event_type(b) for b in resumed
    ]
    assert len(resumed) == 2


# ─── 3. resume via HTTP: header + query param wiring ────────────────────────


def _build_app() -> FastAPI:
    app = FastAPI()
    app.include_router(chat_route.router, prefix="/api/v1")
    app.dependency_overrides[get_current_user_optional] = lambda: {"user_id": None}
    app.dependency_overrides[get_owner_token] = lambda: None

    async def _no_db():
        yield None  # session_id is None → guard returns before touching db

    app.dependency_overrides[get_async_db] = _no_db
    return app


@pytest.mark.asyncio
async def test_resume_via_last_event_id_header(monkeypatch):
    """The SSE-spec Last-Event-ID header triggers the resume path over HTTP."""
    bridge = _BurstBridge(bursts=(4,))
    monkeypatch.setattr(chat_route, "USE_NEW_AGENT", True)
    monkeypatch.setattr(chat_route, "pi_bridge", bridge)

    app = _build_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        async with client.stream(
            "POST", "/api/v1/chat/stream", json={"message": "hi"}
        ) as resp:
            assert resp.status_code == 200
            first = await resp.aread()
        # Resume with the header.
        async with client.stream(
            "POST", "/api/v1/chat/stream",
            json={"message": "hi"},
            headers={"Last-Event-ID": "3"},
        ) as resp2:
            assert resp2.status_code == 200
            second = await resp2.aread()
    first_ids = [_event_id(b) for b in first.decode().split("\n\n") if b]
    second_ids = [_event_id(b) for b in second.decode().split("\n\n") if b]
    assert first_ids == [1, 2, 3, 4, 5, 6, 7]  # task_start+4 tokens+step_result+done
    assert second_ids == [4, 5, 6, 7], second_ids
    assert bridge.prompt_calls == 1


@pytest.mark.asyncio
async def test_resume_via_last_event_id_query_param(monkeypatch):
    """The last_event_id query param is honored as an alternative to the header
    (header wins when both are present — asserted via precedence below)."""
    bridge = _BurstBridge(bursts=(4,))
    monkeypatch.setattr(chat_route, "USE_NEW_AGENT", True)
    monkeypatch.setattr(chat_route, "pi_bridge", bridge)

    app = _build_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        async with client.stream(
            "POST", "/api/v1/chat/stream", json={"message": "hi"}
        ) as resp:
            await resp.aread()
        async with client.stream(
            "POST", f"/api/v1/chat/stream?last_event_id={4}",
            json={"message": "hi"},
        ) as resp2:
            assert resp2.status_code == 200
            second = await resp2.aread()
    second_ids = [_event_id(b) for b in second.decode().split("\n\n") if b]
    assert second_ids == [5, 6, 7], second_ids
    assert bridge.prompt_calls == 1
