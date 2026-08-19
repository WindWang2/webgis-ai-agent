"""Streaming connection lifecycle regression tests.

Covers the transport goal's connection-lifecycle guarantees that the
compute/route tests don't exercise:

  - DB-session early-release over a long stream (C-F2): an open SSE stream
    must hold zero Postgres connections after the ownership guard, so the
    pool (10+20) is not exhausted by concurrent turns.
  - (more cases added with their fixes: disconnect→cancel, leak-freedom…)

Deterministic: the Pi path is forced on with a mock bridge that yields
events with controllable timing; no real LLM, no subprocess, no network.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import json
from typing import AsyncIterator
import uuid
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi import FastAPI

from app.agent_pi_bridge import PiBridge
from app.api.routes import chat as chat_route
from app.core.auth import get_async_db, get_current_user_optional, get_owner_token
from app.tools._utils import async_db_session
from app.utils.sse import sse_event


def _build_app() -> FastAPI:
    """Minimal app with only the chat router and anonymous/no-DB deps."""
    app = FastAPI()
    app.include_router(chat_route.router, prefix="/api/v1")
    app.dependency_overrides[get_current_user_optional] = lambda: {"user_id": None}
    app.dependency_overrides[get_owner_token] = lambda: None
    return app


class _SlowPiBridge:
    """Mock Pi bridge that yields task_start, then holds the stream open.

    The ``hold`` await happens *after* the first event, so a reader that has
    consumed task_start knows the stream is mid-flight (guard done, generator
    suspended inside the simulated model latency).
    """

    def __init__(self, hold: float = 0.3) -> None:
        self.hold = hold

    async def stream_prompt(
        self, message: str, session_id: str | None = None, **_kwargs
    ) -> AsyncIterator[str]:
        yield sse_event("task_start", {"task_id": "t", "session_id": session_id or "s"})
        await asyncio.sleep(self.hold)  # simulated model latency; stream stays open
        yield sse_event("token", {"content": "x", "session_id": session_id or "s"})
        yield sse_event("done", {"session_id": session_id or "s"})


async def _read_until_first_event(client: httpx.AsyncClient) -> float:
    t0 = asyncio.get_event_loop().time()
    async with client.stream("POST", "/api/v1/chat/stream", json={"message": "hi"}) as resp:
        assert resp.status_code == 200, await resp.aread()
        async for _chunk in resp.aiter_bytes():
            return asyncio.get_event_loop().time() - t0
    return asyncio.get_event_loop().time() - t0


@pytest.mark.asyncio
async def test_chat_stream_releases_db_connection_before_streaming(monkeypatch):
    """C-F2: the request DB connection is released before the first SSE event.

    The route keeps ``Depends(get_async_db)`` (the test override point) but
    closes it immediately after the ownership guard, so the connection returns
    to the pool before any event is yielded. We assert:
      1. ``db.close()`` was called at/before the first event (connection freed),
      2. no ``async_db_session()`` context stays open during streaming (the
         dead-weight generator wrapper was removed).
    A slow mock bridge holds the stream open so the mid-stream state is
    observable.
    """
    app = _build_app()
    # monkeypatch guarantees teardown of module globals even on assertion
    # failure, so the next test sees an unmodified chat_route module.
    monkeypatch.setattr(chat_route, "USE_NEW_AGENT", True)
    monkeypatch.setattr(chat_route, "pi_bridge", _SlowPiBridge(hold=0.3))

    loop = asyncio.get_event_loop()
    closed_at: list[float] = []

    class _TrackingSession:
        """Closeable DB-session stand-in (no query needed: session_id is None)."""

        async def close(self) -> None:
            closed_at.append(loop.time())

    async def _tracking_db():
        yield _TrackingSession()

    app.dependency_overrides[get_async_db] = _tracking_db

    # Track async_db_session contexts — the dead-weight generator wrapper that
    # used to wrap each stream must no longer be open during streaming.
    ctx_live = 0
    real_ctx = async_db_session

    @asynccontextmanager
    async def _tracking_ctx():
        nonlocal ctx_live
        ctx_live += 1
        try:
            async with real_ctx() as db:
                yield db
        finally:
            ctx_live -= 1

    monkeypatch.setattr(chat_route, "async_db_session", _tracking_ctx)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        async with client.stream(
            "POST", "/api/v1/chat/stream", json={"message": "hi"}
        ) as resp:
            assert resp.status_code == 200, await resp.aread()
            checked_mid_stream = False
            async for _chunk in resp.aiter_bytes():
                if not checked_mid_stream:
                    first_event_at = loop.time()
                    assert closed_at, (
                        "db.close() must be called before the first SSE event so "
                        "the connection is released (C-F2)"
                    )
                    assert all(t <= first_event_at for t in closed_at), (
                        "db.close() must precede the first SSE event (C-F2)"
                    )
                    assert ctx_live == 0, (
                        "no async_db_session() context may be open during streaming "
                        "(C-F2); the dead-weight generator wrapper was removed"
                    )
                    checked_mid_stream = True
            assert checked_mid_stream, "stream produced no events"


def _make_mock_rpc() -> MagicMock:
    """MagicMock PiRpcClient with an asyncio events queue (no subprocess)."""
    rpc = MagicMock()
    rpc.events = asyncio.Queue()
    rpc.start = AsyncMock()
    rpc.stop = AsyncMock()
    rpc.process_died = False
    # abort() calls fail_all_pending synchronously (no await).
    rpc.fail_all_pending = MagicMock()
    return rpc


@pytest.mark.asyncio
async def test_stream_prompt_aborts_pi_on_disconnect():
    """B-P0-1 (P0): client disconnect must send the abort RPC.

    Before the fix, ``stream_prompt``'s finally only drained the queue and
    cleared caches — the Pi subprocess kept generating tokens and executing
    GIS tools via the ``/pi-tools/execute`` HTTP callback against the
    abandoned session. After the fix, a GeneratorExit/CancelledError schedules
    a detached ``abort()`` so Pi is told to stop, and the turn lock is still
    released (no leak).
    """
    rpc = _make_mock_rpc()

    async def fake_request(cmd: str, data: dict | None = None):
        if cmd == "prompt":
            # Push one token event but NO agent_end → the stream stays open
            # (waiting for the next event) when we disconnect.
            await rpc.events.put({
                "type": "message_update",
                "message": {"role": "assistant",
                            "content": [{"type": "text", "text": "Hi"}]},
                "assistantMessageEvent": {"type": "text_delta", "content": "Hi"},
            })
        elif cmd == "abort":
            return {"ok": True}

    rpc.request = AsyncMock(side_effect=fake_request)

    bridge = PiBridge(rpc=rpc)
    gen = bridge.stream_prompt("hi", session_id="sess-disc")
    first = await gen.__anext__()          # task_start
    assert "task_start" in first
    token = await gen.__anext__()          # message_update → token SSE
    assert token                           # non-empty token event

    # Disconnect: close the generator mid-turn. Throws GeneratorExit at the
    # yield → finally schedules the detached abort + drains + releases lock.
    await gen.aclose()
    # Let the fire-and-forget abort task run on the loop.
    await asyncio.sleep(0.05)

    called_cmds = [c.args[0] for c in rpc.request.call_args_list if c.args]
    assert "abort" in called_cmds, (
        f"abort RPC must be sent on client disconnect (B-P0-1); calls={called_cmds}"
    )
    # No leaked lock: released in finally even on cancellation.
    assert bridge._lock.locked() is False, "turn lock leaked after disconnect"


@pytest.mark.asyncio
async def test_stream_prompt_no_abort_on_normal_completion():
    """B-P0-1 guard: a normally-completed turn must NOT send an abort.

    Ensures the disconnect-abort only fires on cancellation, not when the
    stream runs to ``agent_end``.
    """
    rpc = _make_mock_rpc()

    async def fake_request(cmd: str, data: dict | None = None):
        if cmd == "prompt":
            await rpc.events.put({
                "type": "message_update",
                "assistantMessageEvent": {"type": "text_delta", "content": "done"},
            })
            await rpc.events.put({"type": "agent_end"})

    rpc.request = AsyncMock(side_effect=fake_request)

    bridge = PiBridge(rpc=rpc)
    events = []
    async for ev in bridge.stream_prompt("hi", session_id="sess-ok"):
        events.append(ev)

    await asyncio.sleep(0.05)  # let any (should-be-absent) abort task run
    called_cmds = [c.args[0] for c in rpc.request.call_args_list if c.args]
    assert "abort" not in called_cmds, (
        f"abort must not fire on normal completion; calls={called_cmds}"
    )
    assert bridge._lock.locked() is False
    assert any("done" in e for e in events)


@pytest.mark.asyncio
async def test_dead_pi_bridge_falls_back_to_legacy_engine(monkeypatch):
    """C-F15: a dead Pi subprocess must fall through to the legacy ChatEngine.

    Before the fix ``_use_pi_bridge`` ignored ``process_died`` and every chat
    request errored permanently after a Pi crash. Now the legacy engine (always
    initialised by lifespan) handles the turn instead.
    """
    app = _build_app()
    monkeypatch.setattr(chat_route, "USE_NEW_AGENT", True)

    # A "dead" Pi bridge: present but process_died=True.
    dead_bridge = MagicMock()
    dead_bridge._process_died = True
    dead_bridge.stream_prompt = AsyncMock()
    monkeypatch.setattr(chat_route, "pi_bridge", dead_bridge)

    # Legacy engine captures the turn.
    legacy = MagicMock()

    async def _legacy_stream(*a, **kw):
        yield sse_event("task_start", {"task_id": "t", "session_id": "s"})
        yield sse_event("done", {"session_id": "s"})

    legacy.chat_stream = _legacy_stream
    monkeypatch.setattr(chat_route, "engine", legacy)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        async with client.stream(
            "POST", "/api/v1/chat/stream", json={"message": "hi"}
        ) as resp:
            assert resp.status_code == 200
            body = await resp.aread()
    # The legacy engine handled the turn; the dead Pi bridge was NOT called.
    assert dead_bridge.stream_prompt.call_count == 0
    assert b"task_start" in body


# ─── D-F6: Pi-path SSE token batching (route boundary) ───────────────────────
#
# The Pi route used to yield every bridge event straight to StreamingResponse —
# one HTTP write per token (~200 writes/turn). The route now coalesces
# high-frequency events (token/content) through the shared SSEBatcher, mirroring
# the legacy engine. These tests prove the batching contract at the seam that
# actually observes per-write granularity: StreamingResponse.body_iterator.
# (httpx's ASGITransport coalesces every body part into a single stream, so
# HTTP-level chunk counts are unobservable there.)


def _chunk_event_types(chunk: str | bytes) -> list[str]:
    """Event types carried by one yielded SSE chunk (possibly coalesced)."""
    text = chunk.decode() if isinstance(chunk, bytes) else chunk
    types: list[str] = []
    for block in text.split("\n\n"):
        for line in block.split("\n"):
            if line.startswith("event: "):
                types.append(line[len("event: "):].strip())
    return types


class _BurstPiBridge:
    """Deterministic Pi bridge with configurable token bursts + step_results.

    Yields ``task_start``, then each burst of tokens followed by a
    ``step_result``, then ``done``. With bursts=(96, 104) that is 200 tokens
    with a structural ``step_result`` between token runs — the D-F6 scenario.
    """

    def __init__(self, bursts: tuple[int, ...]) -> None:
        self.bursts = bursts

    async def stream_prompt(
        self, message: str, session_id: str | None = None, **_kwargs
    ) -> AsyncIterator[str]:
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


class _ExplodingPiBridge:
    """Bridge that yields ``n_tokens`` tokens, then raises mid-stream."""

    def __init__(self, n_tokens: int = 40) -> None:
        self.n_tokens = n_tokens

    async def stream_prompt(
        self, message: str, session_id: str | None = None, **_kwargs
    ) -> AsyncIterator[str]:
        sid = session_id or "s"
        yield sse_event("task_start", {"task_id": "t", "session_id": sid})
        for i in range(self.n_tokens):
            yield sse_event("token", {"content": f"tok{i} ", "session_id": sid})
        raise RuntimeError("bridge exploded")


async def _collect_route_chunks(monkeypatch, bridge) -> list[str]:
    """Stream one turn through the Pi route; return the per-write SSE chunks.

    The chunk sequence is exactly what StreamingResponse would put on the wire
    (one write per generator yield) — the seam at which batching is observable.
    """
    monkeypatch.setattr(chat_route, "USE_NEW_AGENT", True)
    monkeypatch.setattr(chat_route, "pi_bridge", bridge)
    resp = await chat_route.chat_stream(
        chat_route.ChatRequest(message="hi", session_id=None, map_state=None),
        _user={},
        owner_token=None,
        db=None,
    )
    return [chunk async for chunk in resp.body_iterator]


@pytest.mark.asyncio
async def test_pi_stream_assigns_nonempty_session_before_first_turn(monkeypatch):
    chunks = await _collect_route_chunks(monkeypatch, _BurstPiBridge(bursts=(1,)))
    payloads = []
    for chunk in chunks:
        for line in chunk.splitlines():
            if line.startswith("data: "):
                payloads.append(json.loads(line[6:]))

    session_ids = {
        payload.get("session_id")
        for payload in payloads
        if isinstance(payload, dict) and payload.get("session_id")
    }
    assert len(session_ids) == 1
    uuid.UUID(next(iter(session_ids)))


@pytest.mark.asyncio
async def test_pi_stream_batches_200_tokens_into_few_chunks(monkeypatch):
    """D-F6: route-boundary batching collapses ~200 token writes into ~10.

    Unbatched, a 200-token turn emits one write per event
    (task_start + 200 tokens + 2 step_results + done = 204 chunks). With the
    route batcher (max 32 events / 80ms) the same stream coalesces to 11
    chunks, while every structural event (task_start, step_result, done) still
    lands in its own chunk with any buffered tokens flushed before it.
    """
    chunks = await _collect_route_chunks(monkeypatch, _BurstPiBridge(bursts=(96, 104)))
    assert len(chunks) <= 12, f"expected ~10 coalesced chunks, got {len(chunks)}"

    all_types = [t for chunk in chunks for t in _chunk_event_types(chunk)]
    assert all_types.count("token") == 200
    assert all_types.count("step_result") == 2
    assert all_types.count("task_start") == 1
    assert all_types.count("done") == 1

    # task_start and done each arrive in their own chunk: first-byte latency
    # for the "thinking" indicator must not regress, and terminal events must
    # never be coalesced (the frontend closes on them).
    assert _chunk_event_types(chunks[0]) == ["task_start"]
    assert _chunk_event_types(chunks[-1]) == ["done"]

    # A structural step_result between token bursts flushes the *partial*
    # buffered tail (< 32 tokens — a threshold flush would be exactly 32)
    # before it, so the frontend sees content strictly before the step_result.
    partial_flush_before_structural = False
    for i, chunk in enumerate(chunks):
        types = _chunk_event_types(chunk)
        assert types.count("step_result") <= 1, "step_result must be its own chunk"
        if types == ["step_result"] and i > 0:
            prev_types = _chunk_event_types(chunks[i - 1])
            if (
                prev_types
                and all(t == "token" for t in prev_types)
                and 0 < len(prev_types) < 32
            ):
                partial_flush_before_structural = True
    assert partial_flush_before_structural, (
        "a structural event between token bursts must flush the partial "
        "buffered tail (0 < n < 32 tokens) before it (D-F6)"
    )


@pytest.mark.asyncio
async def test_pi_stream_flushes_buffered_tokens_before_error(monkeypatch):
    """D-F6: on an upstream failure, buffered tokens precede the error event.

    The bridge raises after 40 tokens; the route batcher has flushed 32 and
    holds 8. The exception path flushes those 8 as their own chunk, then the
    route yields the ``error`` event — the frontend never loses buffered
    content before an error, and never sees content after it.
    """
    chunks = await _collect_route_chunks(monkeypatch, _ExplodingPiBridge(n_tokens=40))
    types_by_chunk = [_chunk_event_types(c) for c in chunks]
    # task_start | 32 tokens | 8 tokens (exception flush) | error
    assert len(chunks) == 4, f"expected 4 chunks, got {types_by_chunk}"
    assert types_by_chunk[0] == ["task_start"]
    assert types_by_chunk[-1] == ["error"]
    token_chunks = [t for t in types_by_chunk if all(x == "token" for x in t)]
    assert sum(len(t) for t in token_chunks) == 40
    assert any(0 < len(t) < 32 for t in token_chunks), (
        "the partial tail must flush as its own chunk before the error event"
    )


@pytest.mark.asyncio
async def test_pi_batched_stream_disconnect_drops_buffered_tokens():
    """D-F6: disconnect mid-batch cancels cleanly; buffered tokens are dropped.

    We pick "deterministically dropped" over "flushed" for disconnects: the
    client is gone, so writing buffered tokens to a dead connection is
    pointless (and yielding during cancellation handling can itself raise).
    CancelledError is a BaseException, so it bypasses the exception-flush path
    and unwinds the generator — the bridge stream's finally (abort/drain/
    cleanup) still runs. Driven at the batched-wrapper seam because that is
    where the real StreamingResponse cancellation delivers CancelledError.
    """
    pushed_tokens = 0
    parked = asyncio.Event()
    bridge_unwound = asyncio.Event()

    async def _stream(
        message: str, session_id: str | None = None
    ) -> AsyncIterator[str]:
        sid = session_id or "s"
        yield sse_event("task_start", {"task_id": "t", "session_id": sid})
        try:
            for i in range(34):
                yield sse_event("token", {"content": f"tok{i} ", "session_id": sid})
                nonlocal pushed_tokens
                pushed_tokens += 1
            parked.set()
            await asyncio.sleep(3600)  # parked awaiting the next Pi event
        finally:
            bridge_unwound.set()

    gen = chat_route._sse_batched(_stream("hi", session_id="s"))
    first = await gen.__anext__()
    assert _chunk_event_types(first) == ["task_start"]
    second = await gen.__anext__()
    assert _chunk_event_types(second) == ["token"] * 32

    # Pull once more as a task: it consumes tok32..33 (2 tokens buffered
    # mid-batch) and parks inside the bridge stream awaiting the next event.
    pull = asyncio.ensure_future(gen.__anext__())
    await asyncio.wait_for(parked.wait(), timeout=2.0)
    assert pushed_tokens == 34, "must be mid-batch with tokens buffered"

    # Client disconnect: cancel the reader task (StreamingResponse does the
    # same on disconnect). Only CancelledError may surface; the bridge stream
    # unwinds cleanly and no error event is emitted.
    pull.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pull
    assert bridge_unwound.is_set(), (
        "the bridge stream must unwind on disconnect (abort/drain/cleanup)"
    )
