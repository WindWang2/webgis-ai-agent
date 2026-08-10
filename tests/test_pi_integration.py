"""Tests for Pi bridge and GIS tools endpoint."""
import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest



from app.agent_pi_bridge import (
    PiBridge,
    PiRpcError,
    PiToolRequest,
    dispatch_tool,
    set_tool_registry,
)
import app.agent_pi_bridge as pi_bridge_module  # 读取 monkeypatched 的超时常量
from app.tools.registry import ToolRegistry


# ============================================================================
# PiBridge unit tests
# ============================================================================

class TestPiRpcError:
    def test_raise_and_catch(self):
        with pytest.raises(PiRpcError, match="test error"):
            raise PiRpcError("test error")

    def test_str(self):
        err = PiRpcError("something failed")
        assert "something failed" in str(err)


class TestPiBridgeBasics:
    @pytest.fixture
    def bridge(self):
        return PiBridge(extension_paths=[])

    def test_create_with_defaults(self, bridge):
        assert bridge._rpc._process is None
        assert bridge._rpc._pending_requests == {}
        assert bridge._rpc._event_queue.empty()

    def test_create_with_custom_paths(self):
        bridge = PiBridge(
            pi_rpc_entry=None,
            session_dir=None,
            cwd=None,
            extension_paths=["/ext/one", "/ext/two"],
        )
        assert bridge._rpc._extension_paths == ["/ext/one", "/ext/two"]


class TestPiBridgeSubprocessFlow:
    """Test the bridge start/stop and request/response flow with mocked RPC client."""

    @pytest.mark.asyncio
    async def test_prompt_returns_content_from_events(self):
        """prompt() drains events and returns concatenated text."""
        rpc = MagicMock()
        rpc.events = asyncio.Queue()
        rpc.start = AsyncMock()
        rpc.stop = AsyncMock()
        bridge = PiBridge(rpc=rpc)

        async def fake_request(cmd, data=None):
            if cmd == "prompt":
                await rpc.events.put({
                    "type": "message_update",
                    "message": {"role": "assistant", "content": [{"type": "text", "text": "Hi there"}]},
                })
                await rpc.events.put({"type": "agent_end"})

        rpc.request = AsyncMock(side_effect=fake_request)

        result = await bridge.prompt("Say hi")
        assert result["content"] == "Hi there"
        assert "sessionId" in result

    @pytest.mark.asyncio
    async def test_prompt_pi_error_raises_exception(self):
        """When Pi returns error, prompt() raises PiRpcError instead of returning error dict."""
        rpc = MagicMock()
        rpc.request = AsyncMock(side_effect=PiRpcError("No provider configured"))
        rpc.events = asyncio.Queue()
        bridge = PiBridge(rpc=rpc)

        with pytest.raises(PiRpcError, match="No provider configured"):
            await bridge.prompt("test")

    @pytest.mark.asyncio
    async def test_stream_prompt_yields_sse_sequence(self):
        """stream_prompt yields task_start → token → task_complete → done."""
        rpc = MagicMock()
        rpc.events = asyncio.Queue()
        rpc.start = AsyncMock()
        rpc.stop = AsyncMock()
        bridge = PiBridge(rpc=rpc)

        async def fake_request(cmd, data=None):
            if cmd == "prompt":
                await rpc.events.put({
                    "type": "message_update",
                    "message": {"role": "assistant", "content": []},
                    "assistantMessageEvent": {"type": "text_delta", "content": "streamed"},
                })
                await rpc.events.put({"type": "agent_end"})

        rpc.request = AsyncMock(side_effect=fake_request)

        events = []
        async for ev in bridge.stream_prompt("stream me"):
            events.append(ev)

        # Coalesced batches may carry multiple events per yielded frame (split
        # on the SSE \\n\\n boundary). Flatten to the ordered event-type list.
        event_types: list[str] = []
        for e in events:
            if not e.strip():
                continue
            for line in e.split("\n"):
                if line.startswith("event: "):
                    event_types.append(line[len("event: "):])
        assert "task_start" in event_types
        assert "token" in event_types, f"Expected 'token' in events, got: {event_types}"
        assert "task_complete" in event_types
        assert event_types[-1] == "done"

    @pytest.mark.asyncio
    async def test_stream_prompt_timeout_yields_error_event(self, monkeypatch):
        """When no events arrive within the stall budget, stream_prompt yields error SSE + done.

        B-P1-3: the stall detector is now activity-based with heartbeats, so
        the test pins BOTH the heartbeat interval (per-wait) and the stall
        budget (continuous silence) low to keep it fast.
        """
        rpc = MagicMock()
        rpc.events = asyncio.Queue()
        rpc.request = AsyncMock()
        bridge = PiBridge(rpc=rpc)

        monkeypatch.setattr("app.agent_pi_bridge.PI_HEARTBEAT_INTERVAL", 0.01)
        monkeypatch.setattr("app.agent_pi_bridge.PI_EVENT_STREAM_TIMEOUT", 0.01)

        events = []
        async for ev in bridge.stream_prompt("slow"):
            events.append(ev)

        # Coalesced batches may carry multiple events per yielded frame.
        event_types: list[str] = []
        for e in events:
            if not e.strip():
                continue
            for line in e.split("\n"):
                if line.startswith("event: "):
                    event_types.append(line[len("event: "):])
        assert event_types[0] == "task_start"
        assert "error" in event_types, f"Expected 'error' event on timeout, got: {event_types}"
        assert event_types[-1] == "done"

        # The stall message must reference the constant (no hardcoded "30s").
        error_event = next(e for e in events if e.startswith("event: error"))
        error_data = json.loads(error_event.split("data: ", 1)[1])
        assert error_data["error"] == (
            f"Pi agent stalled — no events for "
            f"{int(pi_bridge_module.PI_EVENT_STREAM_TIMEOUT)}s. "
            "The agent may be stuck; please retry."
        )

    @pytest.mark.asyncio
    async def test_stream_prompt_emits_heartbeats_during_silence(self, monkeypatch):
        """B-P1-3: silent phases emit SSE keepalive comments up to the stall budget.

        Heartbeats keep the connection alive through proxies/LBs and signal
        progress to the browser without entering chat history (comment lines
        are ignored by the client parser). A normal event arriving after a
        heartbeat must still be delivered and reset the silence accumulator.
        """
        rpc = MagicMock()
        rpc.events = asyncio.Queue()
        rpc.request = AsyncMock()
        bridge = PiBridge(rpc=rpc)

        monkeypatch.setattr("app.agent_pi_bridge.PI_HEARTBEAT_INTERVAL", 0.02)
        # Stall budget large enough for several heartbeats before agent_end.
        monkeypatch.setattr("app.agent_pi_bridge.PI_EVENT_STREAM_TIMEOUT", 10.0)

        async def feed_after_delay():
            await asyncio.sleep(0.07)  # ~3 heartbeat intervals of silence
            await rpc.events.put({
                "type": "message_update",
                "assistantMessageEvent": {"type": "text_delta", "content": "ok"},
            })
            await rpc.events.put({"type": "agent_end"})

        asyncio.create_task(feed_after_delay())

        events = []
        async for ev in bridge.stream_prompt("hi", session_id="s"):
            events.append(ev)

        joined = "\n".join(events)
        assert ": keepalive" in joined, (
            f"expected at least one heartbeat comment during silence; got:\n{joined}"
        )
        # The real token event after the silent phase is still delivered.
        assert "event: token" in joined or "event: content" in joined, (
            f"post-heartbeat token event missing; got:\n{joined}"
        )
        assert joined.rstrip().endswith("event: done") or "event: done" in joined

    @pytest.mark.asyncio
    async def test_stream_prompt_rpc_error_yields_task_error(self):
        """When _rpc.request fails, stream_prompt yields task_error + done."""
        rpc = MagicMock()
        rpc.request = AsyncMock(side_effect=PiRpcError("connection refused"))
        rpc.events = asyncio.Queue()
        bridge = PiBridge(rpc=rpc)

        events = []
        async for ev in bridge.stream_prompt("test"):
            events.append(ev)

        event_types = [e.split("\n")[0].replace("event: ", "") for e in events if e.strip()]
        assert "task_error" in event_types, f"Expected 'task_error', got: {event_types}"
        error_ev = next(e for e in events if "task_error" in e)
        assert "connection refused" in error_ev
        assert event_types[-1] == "done"

    # ─── Session attribution regression tests (review §3 item 1) ────────────

    @pytest.mark.asyncio
    async def test_stream_prompt_drains_stale_events_from_prior_turn(self):
        """Residual events from a prior turn must not bleed into this turn's SSE.

        Regression for the singleton-bridge attribution bug: one shared queue
        served all sessions, a prior turn's leftover events (after timeout or
        client disconnect) were dequeued by the next turn and stamped with the
        new session_id. stream_prompt must drain stale events at turn start.
        """
        rpc = MagicMock()
        rpc.events = asyncio.Queue(maxsize=1024)
        rpc.start = AsyncMock()
        rpc.stop = AsyncMock()
        bridge = PiBridge(rpc=rpc)

        # Seed the queue with a stale event from a *prior* turn (e.g. one whose
        # consumer timed out before reaching agent_end).
        stale_event = {
            "type": "message_update",
            "message": {"role": "assistant", "content": [{"type": "text", "text": "STALE FROM PRIOR TURN"}]},
            "assistantMessageEvent": {"type": "text_delta", "content": "STALE FROM PRIOR TURN"},
        }
        await rpc.events.put(stale_event)

        async def fake_request(cmd, data=None):
            if cmd == "prompt":
                # This turn's own events arrive only AFTER the prompt is sent.
                await rpc.events.put({
                    "type": "message_update",
                    "message": {"role": "assistant", "content": []},
                    "assistantMessageEvent": {"type": "text_delta", "content": "fresh"},
                })
                await rpc.events.put({"type": "agent_end"})

        rpc.request = AsyncMock(side_effect=fake_request)

        events = []
        async for ev in bridge.stream_prompt("next turn", session_id="sess-B"):
            events.append(ev)

        # The stale event's content must NOT appear in this turn's SSE stream.
        joined = "\n".join(events)
        assert "STALE FROM PRIOR TURN" not in joined, (
            "stale event from prior turn leaked into this turn's SSE stream"
        )
        # The fresh event's content must still flow.
        assert "fresh" in joined

    @pytest.mark.asyncio
    async def test_stream_prompt_attributes_events_to_request_session(self):
        """Every emitted SSE carries the request's session_id, not bridge state.

        Locks turn-scoped attribution: the session_id stamped on SSE payloads
        comes from the request argument, not a mutable instance field a prior
        turn could have overwritten.
        """
        rpc = MagicMock()
        rpc.events = asyncio.Queue(maxsize=1024)
        rpc.start = AsyncMock()
        rpc.stop = AsyncMock()
        bridge = PiBridge(rpc=rpc)

        async def fake_request(cmd, data=None):
            if cmd == "prompt":
                await rpc.events.put({
                    "type": "message_update",
                    "message": {"role": "assistant", "content": []},
                    "assistantMessageEvent": {"type": "text_delta", "content": "hello"},
                })
                await rpc.events.put({"type": "agent_end"})

        rpc.request = AsyncMock(side_effect=fake_request)

        events = []
        async for ev in bridge.stream_prompt("hi", session_id="sess-attrib-target"):
            events.append(ev)

        # Every SSE payload that carries a session_id must carry THIS turn's id.
        for ev in events:
            if "session_id" in ev:
                assert '"session_id": "sess-attrib-target"' in ev, (
                    f"SSE payload attributed to wrong session: {ev!r}"
                )

    @pytest.mark.asyncio
    async def test_stream_prompt_lock_serializes_concurrent_turns(self):
        """The whole-turn lock serializes turns: turn B cannot send its prompt
        until turn A has fully drained and emitted its final ``done``.

        Without the lock (old behavior: lock only around the send RPC), turn B's
        ``request("prompt")`` would run while turn A is still draining the shared
        queue, so both turns' events interleave in the queue and get attributed
        to whichever session drains them. This test pins the invariant directly:
        while turn A is parked mid-drain (waiting on ``events.get()`` for its
        agent_end), turn B must still be blocked on the lock and must NOT have
        sent its prompt yet.
        """
        rpc = MagicMock()
        rpc.events = asyncio.Queue(maxsize=1024)
        rpc.start = AsyncMock()
        rpc.stop = AsyncMock()
        bridge = PiBridge(rpc=rpc)

        # Turn A's send returns immediately after emitting one non-terminal
        # event; its drain loop then parks on events.get() waiting for
        # agent_end, which a feeder task supplies only after `a_release`.
        a_drained_first_event = asyncio.Event()
        a_release = asyncio.Event()
        b_prompt_sent = asyncio.Event()

        async def fake_request_a(cmd, data=None):
            if cmd == "prompt":
                await rpc.events.put({
                    "type": "message_update",
                    "message": {"role": "assistant", "content": []},
                    "assistantMessageEvent": {"type": "text_delta", "content": "AAA"},
                })

        async def fake_request_b(cmd, data=None):
            if cmd == "prompt":
                b_prompt_sent.set()
                await rpc.events.put({
                    "type": "message_update",
                    "message": {"role": "assistant", "content": []},
                    "assistantMessageEvent": {"type": "text_delta", "content": "BBB"},
                })
                await rpc.events.put({"type": "agent_end"})

        async def feed_a_agent_end():
            """Wait until turn A has drained its first event, then hold for release."""
            await a_drained_first_event.wait()
            await a_release.wait()
            await rpc.events.put({"type": "agent_end"})

        a_events: list[str] = []
        b_events: list[str] = []

        async def run_a():
            rpc.request = AsyncMock(side_effect=fake_request_a)
            async for ev in bridge.stream_prompt("A", session_id="sess-A"):
                if "AAA" in ev:
                    a_drained_first_event.set()
                a_events.append(ev)

        async def run_b():
            rpc.request = AsyncMock(side_effect=fake_request_b)
            async for ev in bridge.stream_prompt("B", session_id="sess-B"):
                b_events.append(ev)

        feeder = asyncio.ensure_future(feed_a_agent_end())
        task_a = asyncio.ensure_future(run_a())
        # Let turn A drain its first event and park in events.get().
        await asyncio.wait_for(a_drained_first_event.wait(), timeout=2.0)
        # Ensure turn A is actually parked waiting for the next event.
        await asyncio.sleep(0.02)

        # Now start turn B. Under the whole-turn lock it must block on
        # self._lock.acquire() (turn A holds it across its drain) and NOT send
        # its prompt yet. Give the scheduler a chance to run B if it could.
        task_b = asyncio.ensure_future(run_b())
        await asyncio.sleep(0.05)
        assert not b_prompt_sent.is_set(), (
            "turn B sent its prompt while turn A was still parked mid-drain — "
            "the whole-turn lock is not serializing turns (B should block on "
            "self._lock until A's drain + done completes)"
        )

        # Release turn A's agent_end; it drains to completion and releases the
        # lock, then turn B can proceed.
        a_release.set()
        await asyncio.wait_for(asyncio.gather(task_a, task_b, feeder), timeout=5.0)

        assert a_events, "turn A produced no SSE events"
        assert b_events, "turn B produced no SSE events"
        assert b_prompt_sent.is_set(), "turn B never sent its prompt after A released"
        # Turn A's events must all be attributed to sess-A, B's to sess-B.
        assert all('"session_id": "sess-A"' in e or "session_id" not in e for e in a_events)
        assert all('"session_id": "sess-B"' in e or "session_id" not in e for e in b_events)


# ============================================================================
# /pi-tools/execute endpoint tests
# ============================================================================

class TestPiToolsEndpoint:
    """Test the /pi-tools/execute endpoint dispatches to real GIS tools."""

    @pytest.mark.asyncio
    async def test_execute_known_tool(self):
        """A tool registered in ToolRegistry can be executed."""
        registry = ToolRegistry()
        registry.register(
            "pi_test_echo",
            "Echo back the input",
            lambda msg: f"echo:{msg}",
            parameters={
                "type": "object",
                "properties": {"msg": {"type": "string"}},
                "required": ["msg"],
            },
        )
        set_tool_registry(registry)

        req = PiToolRequest(toolCallId="tc-1", name="pi_test_echo", arguments={"msg": "hello"})
        resp = await dispatch_tool(req)
        assert resp.toolCallId == "tc-1"
        assert not resp.isError
        assert "echo:hello" in resp.content[0]["text"]

    @pytest.mark.asyncio
    async def test_execute_unknown_tool_returns_error(self):
        """Unknown tool returns isError=True with helpful message."""
        registry = ToolRegistry()
        set_tool_registry(registry)

        req = PiToolRequest(toolCallId="tc-2", name="does_not_exist", arguments={})
        resp = await dispatch_tool(req)
        assert resp.isError
        assert "not found" in resp.content[0]["text"].lower()

    @pytest.mark.asyncio
    async def test_execute_tool_with_exception_returns_error_content(self):
        """Tool that raises returns error content (registry catches and normalizes)."""
        registry = ToolRegistry()
        registry.register(
            "pi_test_fail",
            "Always fails",
            lambda: (_ for _ in ()).throw(RuntimeError("boom")),
            parameters={"type": "object", "properties": {}},
        )
        set_tool_registry(registry)

        req = PiToolRequest(toolCallId="tc-3", name="pi_test_fail", arguments={})
        resp = await dispatch_tool(req)
        # The registry catches exceptions and returns a structured error dict.
        # pi_tools wraps it as content with isError=False so the LLM can read
        # the error details and decide how to recover.
        assert resp.isError is True
        assert resp.details.get("error_type") == "RuntimeError"

    @pytest.mark.asyncio
    async def test_execute_async_tool(self):
        """Async tools are awaited correctly."""
        registry = ToolRegistry()

        async def async_tool(x):
            return f"async:{x}"

        registry.register(
            "pi_test_async",
            "Async test tool",
            async_tool,
            parameters={
                "type": "object",
                "properties": {"x": {"type": "string"}},
                "required": ["x"],
            },
        )
        set_tool_registry(registry)

        req = PiToolRequest(toolCallId="tc-4", name="pi_test_async", arguments={"x": "42"})
        resp = await dispatch_tool(req)
        assert not resp.isError
        assert "async:42" in resp.content[0]["text"]

    @pytest.mark.asyncio
    async def test_session_id_passed_to_dispatch(self):
        """sessionId is forwarded to registry.dispatch."""
        registry = ToolRegistry()
        captured: list[str] = []

        async def session_tool(name: str, session_id: str = None):
            captured.append(session_id)
            return f"sid={session_id}"

        registry.register(
            "pi_test_session",
            "Session-aware tool",
            session_tool,
            parameters={
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "session_id": {"type": "string"},
                },
                "required": ["name"],
            },
        )
        set_tool_registry(registry)

        req = PiToolRequest(
            toolCallId="tc-5",
            name="pi_test_session",
            arguments={"name": "x"},
            sessionId="my-session-1",
        )
        resp = await dispatch_tool(req)
        assert not resp.isError
        assert captured == ["my-session-1"]
        assert "sid=my-session-1" in resp.content[0]["text"]


class TestClearSessionRoute:
    """Test clear_session route delegates to the engine and does not require the Pi bridge.

    These behavioral tests replace the old source-text inspection
    (``assert "if USE_NEW_AGENT" not in source``). The original concern was a
    dead/inline Pi conditional branch in clear_session; the route now uses the
    ``_use_pi_bridge()`` helper. We verify the observable behavior instead: with
    ``pi_bridge is None`` (default), clearing a session delegates to the engine
    and never touches the Pi bridge.
    """

    @pytest.mark.asyncio
    async def test_clear_session_delegates_to_engine_when_pi_disabled(self):
        """With pi_bridge=None, clear_session calls engine.clear_session and returns 200.

        This is the real behavior the old source-inspection guarded: the legacy
        (non-Pi) path must work end-to-end and not depend on a Pi branch.
        """
        import app.api.routes.chat as chat_module

        mock_engine = MagicMock()
        mock_engine.clear_session = AsyncMock(return_value=True)
        # pi_bridge 默认就是 None（模块级占位），_use_pi_bridge() 应返回 False
        assert chat_module.pi_bridge is None, "测试前提：pi_bridge 未初始化"
        assert chat_module._use_pi_bridge() is False

        with patch.object(chat_module, "engine", mock_engine):
            resp = await chat_module.clear_session(
                session_id="sess-1",
                _user={"user_id": "anonymous"},
                owner_token=None,
            )

        # 委托给 engine.clear_session，带 user_id / owner_token
        mock_engine.clear_session.assert_awaited_once_with(
            "sess-1", user_id="anonymous", owner_token=None,
        )
        assert resp == {"status": "ok"}

    @pytest.mark.asyncio
    async def test_clear_session_returns_404_when_not_found(self):
        """When engine.clear_session returns False, route raises 404 (not a Pi error)."""
        import app.api.routes.chat as chat_module
        from fastapi import HTTPException

        mock_engine = MagicMock()
        mock_engine.clear_session = AsyncMock(return_value=False)

        with patch.object(chat_module, "engine", mock_engine):
            with pytest.raises(HTTPException) as exc_info:
                await chat_module.clear_session(
                    session_id="missing",
                    _user={"user_id": "u1"},
                    owner_token=None,
                )
        assert exc_info.value.status_code == 404
