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
        "get_session",
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
