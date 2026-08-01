"""Tests for Pi bridge and GIS tools endpoint."""
import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.fixtures.pi_mocks import make_mock_process, make_readline


from app.agent_pi_bridge import (
    PiBridge,
    PiRpcError,
    PiToolRequest,
    PiToolResponse,
    dispatch_tool,
    set_tool_registry,
)
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

        event_types = [e.split("\n")[0].replace("event: ", "") for e in events if e.strip()]
        assert "task_start" in event_types
        assert "token" in event_types, f"Expected 'token' in events, got: {event_types}"
        assert "task_complete" in event_types
        assert event_types[-1] == "done"

    @pytest.mark.asyncio
    async def test_stream_prompt_timeout_yields_error_event(self, monkeypatch):
        """When no events arrive within timeout, stream_prompt yields error SSE + done."""
        rpc = MagicMock()
        rpc.events = asyncio.Queue()
        rpc.request = AsyncMock()
        bridge = PiBridge(rpc=rpc)

        monkeypatch.setattr("app.agent_pi_bridge.PI_EVENT_STREAM_TIMEOUT", 0.01)

        events = []
        async for ev in bridge.stream_prompt("slow"):
            events.append(ev)

        event_types = [e.split("\n")[0].replace("event: ", "") for e in events if e.strip()]
        assert event_types[0] == "task_start"
        assert "error" in event_types, f"Expected 'error' event on timeout, got: {event_types}"
        assert event_types[-1] == "done"

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
