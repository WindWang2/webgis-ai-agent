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
        assert bridge._process is None
        assert bridge._pending_requests == {}
        assert bridge._event_queue.empty()

    def test_create_with_custom_paths(self):
        bridge = PiBridge(
            pi_rpc_entry=None,
            session_dir=None,
            cwd=None,
            extension_paths=["/ext/one", "/ext/two"],
        )
        assert bridge._extension_paths == ["/ext/one", "/ext/two"]


class TestPiBridgeEventMapping:
    """Test _map_event_to_sse without subprocess."""

    @pytest.fixture
    def bridge(self):
        b = PiBridge()
        b._session_id = "sess-123"
        return b

    @staticmethod
    def _parse_sse(sse: str) -> tuple[str, dict]:
        lines = sse.strip().split("\n")
        event_type = lines[0].replace("event: ", "").strip()
        data_line = lines[1].replace("data: ", "").strip()
        return event_type, json.loads(data_line)

    # --- token events ---

    def test_text_delta_maps_to_token(self, bridge):
        event = {
            "type": "message_update",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "Hello"}],
            },
            "assistantMessageEvent": {
                "type": "text_delta",
                "content": "Hello",
            },
        }
        sse = bridge._map_event_to_sse(event)
        assert sse is not None
        ev, data = self._parse_sse(sse)
        assert ev == "token"
        assert data["content"] == "Hello"
        assert data["session_id"] == "sess-123"

    def test_thinking_delta_maps_to_token_with_reasoning(self, bridge):
        event = {
            "type": "message_update",
            "message": {"role": "assistant", "content": [{"type": "thinking", "text": "hmm"}]},
            "assistantMessageEvent": {
                "type": "thinking_delta",
                "content": "hmm",
            },
        }
        sse = bridge._map_event_to_sse(event)
        assert sse is not None
        ev, data = self._parse_sse(sse)
        assert ev == "token"
        assert data["is_reasoning"] is True

    def test_tool_call_message_maps_to_tool_call(self, bridge):
        event = {
            "type": "message_update",
            "message": {
                "role": "assistant",
                "content": [{"type": "tool_call", "name": "spatial_analyze", "arguments": "{}"}],
            },
            "assistantMessageEvent": {
                "type": "tool_call",
                "name": "spatial_analyze",
                "arguments": "{}",
            },
        }
        sse = bridge._map_event_to_sse(event)
        assert sse is not None
        ev, data = self._parse_sse(sse)
        assert ev == "tool_call"
        assert data["name"] == "spatial_analyze"

    # --- tool execution events ---

    def test_tool_execution_start_maps_to_step_start(self, bridge):
        event = {
            "type": "tool_execution_start",
            "toolCallId": "tc-1",
            "toolName": "spatial_analyze",
            "args": {"layer": "buildings"},
        }
        sse = bridge._map_event_to_sse(event)
        assert sse is not None
        ev, data = self._parse_sse(sse)
        assert ev == "step_start"
        assert data["tool"] == "spatial_analyze"
        assert data["step_id"] == "tc-1"

    def test_tool_execution_end_success_maps_to_step_result(self, bridge):
        event = {
            "type": "tool_execution_end",
            "toolCallId": "tc-1",
            "toolName": "spatial_analyze",
            "result": {"features": 10},
            "isError": False,
        }
        sse = bridge._map_event_to_sse(event)
        assert sse is not None
        ev, data = self._parse_sse(sse)
        assert ev == "step_result"
        assert data["tool"] == "spatial_analyze"

    def test_tool_execution_end_error_maps_to_step_error(self, bridge):
        event = {
            "type": "tool_execution_end",
            "toolCallId": "tc-1",
            "toolName": "spatial_analyze",
            "result": {"content": [{"type": "text", "text": "invalid layer"}]},
            "isError": True,
        }
        sse = bridge._map_event_to_sse(event)
        assert sse is not None
        ev, data = self._parse_sse(sse)
        assert ev == "step_error"
        assert "invalid layer" in data["error"]

    # --- lifecycle events ---

    def test_agent_end_maps_to_task_complete(self, bridge):
        event = {"type": "agent_end"}
        sse = bridge._map_event_to_sse(event)
        assert sse is not None
        ev, data = self._parse_sse(sse)
        assert ev == "task_complete"

    def test_compaction_start_maps_to_content(self, bridge):
        event = {"type": "compaction_start"}
        sse = bridge._map_event_to_sse(event)
        assert sse is not None
        ev, data = self._parse_sse(sse)
        assert ev == "content"
        assert "压缩" in data["content"]

    def test_compaction_end_maps_to_content(self, bridge):
        event = {"type": "compaction_end"}
        sse = bridge._map_event_to_sse(event)
        assert sse is not None
        ev, data = self._parse_sse(sse)
        assert ev == "content"
        assert "完成" in data["content"]

    def test_unknown_event_returns_none(self, bridge):
        assert bridge._map_event_to_sse({"type": "unknown"}) is None

    def test_extract_text_from_string_content(self, bridge):
        text = bridge._extract_text_from_event({
            "type": "message_update",
            "message": {"content": "plain text"},
        })
        assert text == "plain text"

    def test_extract_text_from_list_content(self, bridge):
        text = bridge._extract_text_from_event({
            "type": "message_update",
            "message": {
                "content": [
                    {"type": "text", "text": "part1"},
                    {"type": "text", "text": "part2"},
                ]
            },
        })
        assert text == "part1part2"

    def test_extract_error_text_from_dict(self, bridge):
        err = bridge._extract_error_text({
            "content": [{"type": "text", "text": "boom"}],
        })
        assert err == "boom"

    def test_extract_error_text_fallback(self, bridge):
        err = bridge._extract_error_text("simple error")
        assert err == "simple error"


# ============================================================================
# PiBridge subprocess flow tests (mocked)
# ============================================================================

class TestPiBridgeSubprocessFlow:
    """Test the bridge start/stop and request/response flow with mocked subprocess."""

    @pytest.fixture
    def bridge(self):
        return PiBridge(extension_paths=[])

    @pytest.mark.asyncio
    async def test_prompt_returns_content_from_events(self, bridge):
        """prompt() drains events and returns concatenated text."""
        mock_proc = MagicMock()
        mock_proc.stdin = MagicMock()
        mock_proc.stdout = MagicMock()
        mock_proc.stderr = MagicMock()
        mock_proc.poll.return_value = None
        mock_proc.stdout.readline.side_effect = make_readline([
            '{"type":"response","id":"1","success":true}\n',
            '{"type":"message_update","message":{"role":"assistant","content":[{"type":"text","text":"Hi there"}]}}\n',
            '{"type":"agent_end"}\n',
            '',
        ])

        with patch("subprocess.Popen", return_value=mock_proc):
            with patch("asyncio.sleep", return_value=None):
                with patch.object(bridge, "_send_request", new_callable=AsyncMock, return_value=None):
                    await bridge.start()

        try:
            result = await bridge.prompt("Say hi")
            assert result["content"] == "Hi there"
            assert "sessionId" in result
        finally:
            await bridge.stop()

    @pytest.mark.asyncio
    async def test_prompt_pi_error_raises_exception(self, bridge):
        """When Pi returns error, prompt() raises PiRpcError instead of returning error dict."""
        mock_proc = MagicMock()
        mock_proc.stdin = MagicMock()
        mock_proc.stdout = MagicMock()
        mock_proc.stderr = MagicMock()
        mock_proc.poll.return_value = None
        mock_proc.stdout.readline.side_effect = make_readline([
            '{"type":"response","id":"1","success":false,"error":"No provider configured"}\n',
            '',
        ])

        with patch("subprocess.Popen", return_value=mock_proc):
            with patch("asyncio.sleep", return_value=None):
                with patch.object(bridge, "_send_request", new_callable=AsyncMock, return_value=None):
                    await bridge.start()

        try:
            with pytest.raises(PiRpcError, match="No provider configured"):
                await bridge.prompt("test")
        finally:
            await bridge.stop()

    @pytest.mark.asyncio
    async def test_stream_prompt_yields_sse_sequence(self, bridge):
        """stream_prompt yields task_start → token → task_complete → done."""
        mock_proc = MagicMock()
        mock_proc.stdin = MagicMock()
        mock_proc.stdout = MagicMock()
        mock_proc.stderr = MagicMock()
        mock_proc.poll.return_value = None
        mock_proc.stdout.readline.side_effect = make_readline([
            '{"type":"response","id":"1","success":true}\n',
            # Include assistantMessageEvent so _map_event_to_sse can detect token
            '{"type":"message_update","message":{"role":"assistant","content":[]},"assistantMessageEvent":{"type":"text_delta","content":"streamed"}}\n',
            '{"type":"agent_end"}\n',
            '',
        ])

        with patch("subprocess.Popen", return_value=mock_proc):
            with patch("asyncio.sleep", return_value=None):
                with patch.object(bridge, "_send_request", new_callable=AsyncMock, return_value=None):
                    await bridge.start()

        try:
            events = []
            async for ev in bridge.stream_prompt("stream me"):
                events.append(ev)

            event_types = [e.split("\n")[0].replace("event: ", "") for e in events if e.strip()]
            assert "task_start" in event_types
            assert "token" in event_types, f"Expected 'token' in events, got: {event_types}"
            assert "task_complete" in event_types
            assert event_types[-1] == "done"
        finally:
            await bridge.stop()

    @pytest.mark.asyncio
    async def test_stream_prompt_timeout_yields_error_event(self, bridge):
        """When no events arrive within timeout, stream_prompt yields error SSE + done."""
        mock_proc = MagicMock()
        mock_proc.stdin = MagicMock()
        mock_proc.stdout = MagicMock()
        mock_proc.stderr = MagicMock()
        mock_proc.poll.return_value = None
        mock_proc.stdout.readline.side_effect = make_readline([
            '{"type":"response","id":"1","success":true}\n',
            '',
        ])

        with patch("subprocess.Popen", return_value=mock_proc):
            with patch("asyncio.sleep", return_value=None):
                with patch.object(bridge, "_send_request", new_callable=AsyncMock, return_value=None):
                    await bridge.start()

        try:
            events = []
            async for ev in bridge.stream_prompt("slow"):
                events.append(ev)

            event_types = [e.split("\n")[0].replace("event: ", "") for e in events if e.strip()]
            assert event_types[0] == "task_start"
            assert "error" in event_types, f"Expected 'error' event on timeout, got: {event_types}"
            assert event_types[-1] == "done"
        finally:
            await bridge.stop()

    @pytest.mark.asyncio
    async def test_stream_prompt_rpc_error_yields_task_error(self, bridge):
        """When _send_request fails, stream_prompt yields task_error + done."""
        mock_proc = MagicMock()
        mock_proc.stdin = MagicMock()
        mock_proc.stdout = MagicMock()
        mock_proc.stderr = MagicMock()
        mock_proc.poll.return_value = None
        # readline never called because send fails immediately
        mock_proc.stdout.readline.side_effect = make_readline([''])

        with patch("subprocess.Popen", return_value=mock_proc):
            with patch("asyncio.sleep", return_value=None):
                with patch.object(bridge, "_send_request", new_callable=AsyncMock, return_value=None):
                    await bridge.start()

        try:
            # Patch _send_request to raise PiRpcError
            async def failing_send(cmd, data=None):
                raise PiRpcError("connection refused")
            bridge._send_request = failing_send

            events = []
            async for ev in bridge.stream_prompt("test"):
                events.append(ev)

            event_types = [e.split("\n")[0].replace("event: ", "") for e in events if e.strip()]
            assert "task_error" in event_types, f"Expected 'task_error', got: {event_types}"
            # Verify error payload mentions the failure reason
            error_ev = next(e for e in events if "task_error" in e)
            assert "connection refused" in error_ev
            assert event_types[-1] == "done"
        finally:
            await bridge.stop()


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
        assert "boom" in resp.content[0]["text"]
        assert resp.details.get("success") is False

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
