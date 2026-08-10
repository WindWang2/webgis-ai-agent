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
from typing import AsyncIterator
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
        self, message: str, session_id: str | None = None
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
