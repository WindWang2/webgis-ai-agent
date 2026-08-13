"""Runtime-chaos hardening tests for the Pi bridge / RPC client (WP-PI).

Covers four defects, one test class each:

  - F5:  ``PiBridge.abort(session_id=...)`` must not kill a DIFFERENT
    session's in-flight turn on the singleton bridge.
  - F10: the stall-timeout path (``PI_EVENT_STREAM_TIMEOUT``) must send the
    abort RPC, not just yield error+done while Pi keeps executing tools.
  - F19: ``PiRpcClient.request`` must drop its ``_pending_requests`` entry
    when the caller is cancelled (previously only TimeoutError/BrokenPipeError
    popped it).
  - F24: the Pi HTTP-callback ``dispatch_tool`` must run under the active
    turn's ``CancellationToken`` so checkpoint()-cooperative tools stop when
    the turn is aborted.

Deterministic: fake RPC clients with asyncio.Queue event streams, Event
barriers, and tiny patched timeout constants (no wall-clock-dependent
assertions).
"""
from __future__ import annotations

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from app import agent_pi_bridge as bridge_mod
from app.agent_pi_bridge import PiBridge, PiToolRequest, dispatch_tool
from app.services.chat.pi_rpc_client import PiRpcClient
from app.services.jobs.cancellation import (
    OperationCancelled,
    checkpoint,
    current_token,
)
from app.services.tool_dispatch_service import ToolDispatchResult
from tests.fixtures.pi_mocks import make_token_event


# ── shared fakes ─────────────────────────────────────────────────────


def _make_rpc(seed_events: list[dict] | None = None) -> MagicMock:
    """MagicMock PiRpcClient: ``prompt`` seeds events, ``abort`` is recorded.

    No ``agent_end`` is seeded unless asked, so the turn parks awaiting the
    next event — the mid-turn state every chaos test here needs.
    """
    rpc = MagicMock()
    rpc.events = asyncio.Queue()
    rpc.start = AsyncMock()
    rpc.stop = AsyncMock()
    rpc.process_died = False
    rpc.fail_all_pending = MagicMock()

    async def _request(cmd: str, data: dict | None = None):
        if cmd == "prompt":
            for ev in seed_events or []:
                await rpc.events.put(ev)
            return {"ok": True}
        if cmd == "abort":
            return {"ok": True}
        return {}

    rpc.request = AsyncMock(side_effect=_request)
    return rpc


def _rpc_commands(rpc: MagicMock) -> list[str]:
    return [c.args[0] for c in rpc.request.call_args_list if c.args]


@pytest.fixture(autouse=True)
def _clean_bridge_state():
    bridge_mod._session_executed_sets.clear()
    bridge_mod._dispatch_result_cache.clear()
    yield
    bridge_mod._session_executed_sets.clear()
    bridge_mod._dispatch_result_cache.clear()


# ── F5: session-scoped abort ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_abort_skips_when_other_session_turn_in_flight(caplog):
    """F5: abort(session_id=B) while session A's turn is in flight must NOT
    send the global abort RPC nor fail pending futures — that would kill A's
    turn (the singleton bridge serializes turns; the in-flight turn owns the
    subprocess). It must log a warning instead.
    """
    rpc = _make_rpc([make_token_event("hi")])
    bridge = PiBridge(rpc=rpc)

    gen = bridge.stream_prompt("hi", session_id="sess-A")
    first = await gen.__anext__()
    assert "task_start" in first  # turn A is in flight, parked on next event

    with caplog.at_level(logging.WARNING, logger="app.agent_pi_bridge"):
        result = await bridge.abort(session_id="sess-B")

    assert result == {}
    assert "abort" not in _rpc_commands(rpc), (
        f"abort RPC must be skipped when another session's turn is in flight; "
        f"calls={_rpc_commands(rpc)}"
    )
    rpc.fail_all_pending.assert_not_called()
    assert any(
        "sess-B" in rec.message and "sess-A" in rec.message
        for rec in caplog.records
        if rec.levelno == logging.WARNING
    ), "a warning naming both sessions must be logged"

    # Cleanup: disconnecting turn A still aborts (existing B-P0-1 behavior).
    await gen.aclose()
    assert "abort" in _rpc_commands(rpc)


@pytest.mark.asyncio
async def test_abort_proceeds_for_matching_session_and_when_idle():
    """F5 contract: abort(session_id) matching the active turn — or called
    while no turn is active — behaves exactly as the old unscoped abort.
    """
    rpc = _make_rpc([make_token_event("hi")])
    bridge = PiBridge(rpc=rpc)

    # Idle bridge: session_id given, no turn in flight -> abort fires.
    result = await bridge.abort(session_id="sess-idle")
    assert result == {"ok": True}
    assert "abort" in _rpc_commands(rpc)
    rpc.fail_all_pending.assert_called_once_with("abort requested")

    rpc.request.reset_mock()
    rpc.fail_all_pending.reset_mock()

    # Matching session: abort fires while the turn is parked mid-stream.
    gen = bridge.stream_prompt("hi", session_id="sess-A")
    first = await gen.__anext__()
    assert "task_start" in first
    result = await bridge.abort(session_id="sess-A")
    assert result == {"ok": True}
    assert "abort" in _rpc_commands(rpc)
    rpc.fail_all_pending.assert_called_once_with("abort requested")

    await gen.aclose()


# ── F10: stall timeout must abort ────────────────────────────────────


@pytest.mark.asyncio
async def test_stall_timeout_sends_abort_and_terminates_stream(monkeypatch):
    """F10: when Pi goes silent past PI_EVENT_STREAM_TIMEOUT the stream must
    tell Pi to abort (same bounded shielded abort as the disconnect path) —
    otherwise Pi keeps executing tools up to the 300s RPC timeout and a user
    retry duplicates side effects.
    """
    monkeypatch.setattr(bridge_mod, "PI_HEARTBEAT_INTERVAL", 0.02)
    monkeypatch.setattr(bridge_mod, "PI_EVENT_STREAM_TIMEOUT", 0.05)

    rpc = _make_rpc([make_token_event("partial")])
    bridge = PiBridge(rpc=rpc)

    events = [ev async for ev in bridge.stream_prompt("hi", session_id="sess-stall")]

    # The abort RPC was sent even though the client never disconnected.
    assert "abort" in _rpc_commands(rpc), (
        f"stall-timeout must send the abort RPC; calls={_rpc_commands(rpc)}"
    )

    # Terminal sequence: an error event, then done, and NOTHING after done —
    # no late Pi event may be processed once the turn is declared stalled.
    structured = [e for e in events if not e.startswith(":")]
    assert structured[-1].startswith("event: done"), structured
    assert structured[-2].startswith("event: error"), structured
    assert "stalled" in structured[-2]

    # Leftover events were drained so the next turn starts clean.
    assert rpc.events.empty()
    assert bridge._lock.locked() is False


@pytest.mark.asyncio
async def test_stall_abort_also_cancels_turn_token(monkeypatch):
    """F10+F24 integration: the stall-path abort ignites the turn's
    CancellationToken, so a dispatch in flight via the HTTP callback stops at
    its next checkpoint()."""
    monkeypatch.setattr(bridge_mod, "PI_HEARTBEAT_INTERVAL", 0.02)
    monkeypatch.setattr(bridge_mod, "PI_EVENT_STREAM_TIMEOUT", 0.05)

    class _StubRegistry:
        def list_tools(self):
            return ["query_map_features"]

        def metadata(self, name):
            return {"tier": 1}

    monkeypatch.setattr(bridge_mod, "get_tool_registry", lambda: _StubRegistry())

    started = asyncio.Event()
    release = asyncio.Event()
    observed_tokens: list = []

    async def _fake_dispatch(self, tc, session_id, executed):
        observed_tokens.append(current_token())
        started.set()
        await release.wait()
        checkpoint()  # cooperative cancellation point
        return ToolDispatchResult(
            status="ok", llm_payload="ok", slim_event={}, geojson_ref=None,
            raw_result={}, error_msg=None,
        )

    monkeypatch.setattr(bridge_mod.ToolDispatchService, "dispatch", _fake_dispatch)

    rpc = _make_rpc([])  # no events at all -> immediate stall
    bridge = PiBridge(rpc=rpc)

    async def _consume():
        return [ev async for ev in bridge.stream_prompt("hi", session_id="sess-stall2")]

    stream = asyncio.create_task(_consume())
    # Wait for the turn to be active, then dispatch a tool via the callback.
    for _ in range(1000):
        if bridge._active_turn_sid == "sess-stall2":
            break
        await asyncio.sleep(0)
    dispatch = asyncio.create_task(dispatch_tool(PiToolRequest(
        name="query_map_features", toolCallId="tc-stall", arguments={},
        sessionId=None,
    )))
    await asyncio.wait_for(started.wait(), timeout=2.0)

    events = await stream  # stall fires -> abort -> token cancelled
    assert any(e.startswith("event: error") for e in events)

    token = observed_tokens[0]
    assert token is not None, "dispatch must run under the turn's token"
    assert token.cancelled, "stall-path abort must cancel the turn token"

    release.set()
    with pytest.raises(OperationCancelled):
        await dispatch


# ── F19: request() cancellation must clear _pending_requests ─────────


@pytest.mark.asyncio
async def test_request_cancellation_clears_pending_request():
    """F19: cancelling a caller of PiRpcClient.request must not leak the
    _pending_requests entry — a later response for that id would resolve a
    dead future and the registry would grow by one entry per cancelled call.
    """
    client = PiRpcClient()
    mock_proc = MagicMock()
    mock_proc.stdin = MagicMock()
    client._process = mock_proc

    task = asyncio.create_task(client.request("prompt", {"message": "hi"}))
    for _ in range(1000):
        if client._pending_requests:
            break
        await asyncio.sleep(0)
    assert client._pending_requests, "request never registered its future"

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert client._pending_requests == {}, (
        f"cancelled request leaked pending entries: {client._pending_requests}"
    )


@pytest.mark.asyncio
async def test_request_normal_response_path_still_pops():
    """F19 guard: the finally-pop must not break normal response delivery —
    _handle_response pops and resolves the future, request returns its data.
    """
    client = PiRpcClient()
    mock_proc = MagicMock()
    mock_proc.stdin = MagicMock()
    client._process = mock_proc

    task = asyncio.create_task(client.request("get_state", {}))
    for _ in range(1000):
        if client._pending_requests:
            break
        await asyncio.sleep(0)
    rid = next(iter(client._pending_requests))
    await client._handle_response(
        {"type": "response", "id": rid, "success": True, "data": {"state": "ok"}}
    )
    assert await task == {"state": "ok"}
    assert client._pending_requests == {}


# ── F24: HTTP-callback dispatch bound to the turn's token ────────────


@pytest.mark.asyncio
async def test_dispatch_tool_binds_active_turn_cancellation_token(monkeypatch):
    """F24: dispatch_tool (Pi HTTP callback) must run under the active turn's
    CancellationToken so abort/disconnect stops checkpoint()-cooperative
    tools instead of letting them run to completion against an abandoned turn.
    """
    class _StubRegistry:
        def list_tools(self):
            return ["query_map_features"]

        def metadata(self, name):
            return {"tier": 1}

    monkeypatch.setattr(bridge_mod, "get_tool_registry", lambda: _StubRegistry())

    started = asyncio.Event()
    release = asyncio.Event()
    observed_tokens: list = []

    async def _fake_dispatch(self, tc, session_id, executed):
        observed_tokens.append(current_token())
        started.set()
        await release.wait()
        checkpoint()  # raises OperationCancelled once the token is ignited
        return ToolDispatchResult(
            status="ok", llm_payload="ok", slim_event={}, geojson_ref=None,
            raw_result={}, error_msg=None,
        )

    monkeypatch.setattr(bridge_mod.ToolDispatchService, "dispatch", _fake_dispatch)

    rpc = _make_rpc([make_token_event("hi")])
    bridge = PiBridge(rpc=rpc)

    gen = bridge.stream_prompt("hi", session_id="sess-cancel")
    first = await gen.__anext__()
    assert "task_start" in first  # turn parked mid-stream

    dispatch = asyncio.create_task(dispatch_tool(PiToolRequest(
        name="query_map_features", toolCallId="tc-1", arguments={},
        sessionId=None,
    )))
    await asyncio.wait_for(started.wait(), timeout=2.0)

    # Client disconnects -> finally aborts -> turn token ignites.
    await gen.aclose()

    token = observed_tokens[0]
    assert token is not None, (
        "dispatch_tool must bind the active turn's CancellationToken via "
        "use_token so in-flight tools observe abort"
    )
    assert token.cancelled, "abort must ignite the bound token"

    release.set()
    with pytest.raises(OperationCancelled):
        await dispatch


@pytest.mark.asyncio
async def test_dispatch_tool_without_active_turn_runs_uncancelled(monkeypatch):
    """F24 guard: with no active turn the token is None and dispatch behaves
    exactly as before (use_token(None) is a no-op)."""
    class _StubRegistry:
        def list_tools(self):
            return ["query_map_features"]

        def metadata(self, name):
            return {"tier": 1}

    monkeypatch.setattr(bridge_mod, "get_tool_registry", lambda: _StubRegistry())
    observed_tokens: list = []

    async def _fake_dispatch(self, tc, session_id, executed):
        observed_tokens.append(current_token())
        return ToolDispatchResult(
            status="ok", llm_payload="ok", slim_event={}, geojson_ref=None,
            raw_result={}, error_msg=None,
        )

    monkeypatch.setattr(bridge_mod.ToolDispatchService, "dispatch", _fake_dispatch)

    resp = await dispatch_tool(PiToolRequest(
        name="query_map_features", toolCallId="tc-solo", arguments={},
        sessionId=None,
    ))
    assert resp.isError is False
    assert observed_tokens == [None]


# ── P1 round-2: prompt() must publish the active-turn markers ─────────


@pytest.mark.asyncio
async def test_prompt_publishes_active_turn_markers(monkeypatch, caplog):
    """P1: prompt() (non-streaming Pi path) must set/clear
    _active_turn_sid/_active_turn_token like stream_prompt — otherwise
    abort(session_id=other) sees active=None and the GLOBAL abort kills this
    turn, and dispatch_tool binds no token (F24 no-op) on this path."""
    class _StubRegistry:
        def list_tools(self):
            return ["query_map_features"]

        def metadata(self, name):
            return {"tier": 1}

    monkeypatch.setattr(bridge_mod, "get_tool_registry", lambda: _StubRegistry())
    monkeypatch.setattr(bridge_mod, "PI_EVENT_DRAIN_TIMEOUT", 3600.0)  # park the drain

    started = asyncio.Event()
    release = asyncio.Event()
    observed_tokens: list = []

    async def _fake_dispatch(self, tc, session_id, executed):
        observed_tokens.append(current_token())
        started.set()
        await release.wait()
        checkpoint()  # raises OperationCancelled once the token is ignited
        return ToolDispatchResult(
            status="ok", llm_payload="ok", slim_event={}, geojson_ref=None,
            raw_result={}, error_msg=None,
        )

    monkeypatch.setattr(bridge_mod.ToolDispatchService, "dispatch", _fake_dispatch)

    rpc = _make_rpc([make_token_event("hi")])  # one event, no agent_end
    bridge = PiBridge(rpc=rpc)

    prompt_task = asyncio.create_task(bridge.prompt("hi", session_id="sess-P"))
    for _ in range(1000):
        if bridge._active_turn_sid == "sess-P":
            break
        await asyncio.sleep(0)
    assert bridge._active_turn_sid == "sess-P", (
        "prompt() did not publish the active sid (P1)"
    )

    # F5: abort for ANOTHER session is skipped (would otherwise be a global kill)
    with caplog.at_level(logging.WARNING, logger="app.agent_pi_bridge"):
        result = await bridge.abort(session_id="sess-OTHER")
    assert result == {}
    assert any(
        "sess-OTHER" in rec.message and "sess-P" in rec.message
        for rec in caplog.records
        if rec.levelno == logging.WARNING
    ), "abort for another session must be skipped + logged during prompt()"
    rpc.fail_all_pending.assert_not_called()

    rpc.request.reset_mock()
    rpc.fail_all_pending.reset_mock()

    # F5: abort for the ACTIVE session proceeds
    result = await bridge.abort(session_id="sess-P")
    assert result == {"ok": True}
    assert "abort" in _rpc_commands(rpc)
    rpc.fail_all_pending.assert_called_once_with("abort requested")

    # F24: dispatch during prompt() is bound to the turn's token
    dispatch = asyncio.create_task(dispatch_tool(PiToolRequest(
        name="query_map_features", toolCallId="tc-prompt", arguments={},
        sessionId=None,
    )))
    await asyncio.wait_for(started.wait(), timeout=2.0)
    token = observed_tokens[0]
    assert token is not None, "dispatch during prompt() must bind the turn token"
    assert token.cancelled, "abort must ignite the prompt-turn token"

    release.set()
    with pytest.raises(OperationCancelled):
        await dispatch

    # teardown: cancel the parked prompt turn; markers + lock must clear
    prompt_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await prompt_task
    assert bridge._active_turn_sid is None
    assert bridge_mod._active_turn_token is None


# ── P1 round-2: abort() TOCTOU sid re-read ───────────────────────────


@pytest.mark.asyncio
async def test_abort_toctou_sid_flip_logs_warning(caplog):
    """P1: abort() reads _active_turn_sid lock-free, then awaits the abort
    RPC — in that gap the matched turn can end and a different session's
    turn start, so the GLOBAL abort kills the wrong turn. The post-RPC
    re-read must log a warning naming both sessions and must not raise."""
    rpc = _make_rpc([make_token_event("hi")])
    bridge = PiBridge(rpc=rpc)

    original_request = rpc.request

    async def flipping_request(cmd, data=None):
        if cmd == "abort":
            # Simulate the flip DURING the abort RPC: the matched turn ends
            # and a different session's turn takes over the bridge.
            bridge._active_turn_sid = "sess-OTHER"
            return {"ok": True}
        return await original_request(cmd, data)

    rpc.request = AsyncMock(side_effect=flipping_request)

    gen = bridge.stream_prompt("hi", session_id="sess-A")
    first = await gen.__anext__()
    assert "task_start" in first  # turn A in flight

    with caplog.at_level(logging.WARNING, logger="app.agent_pi_bridge"):
        result = await bridge.abort(session_id="sess-A")

    assert result == {"ok": True}
    assert any(
        "TOCTOU" in rec.message
        and "sess-A" in rec.message
        and "sess-OTHER" in rec.message
        for rec in caplog.records
        if rec.levelno == logging.WARNING
    ), "sid flip during the abort RPC must log a TOCTOU warning (P1)"

    await gen.aclose()
