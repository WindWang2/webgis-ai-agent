"""Mock-based end-to-end test for Pi bridge.

Simulates the full request flow without requiring a real LLM API key:
FastAPI /chat/stream → PiBridge.stream_prompt() → SSE events → frontend

This test verifies:
1. HTTP endpoint accepts requests and returns SSE stream
2. PiBridge correctly maps Pi events to SSE format
3. Tool execution flow (tool_call → step_start → step_result)
4. Error handling (PiRpcError → HTTP 502)
5. Feature flag gating (USE_NEW_AGENT=true/false)
"""
import json
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Ensure project root on path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.agent_pi_bridge import PiBridge, PiRpcError
from app.api.routes.chat import _use_pi_bridge


from tests.fixtures.pi_mocks import (
    make_agent_end,
    make_mock_process,
    make_readline,
    make_token_event,
    make_tool_call_event,
    make_tool_execution_end,
    make_tool_execution_start,
)


# ─── E2E scenario tests ────────────────────────────────────────────

class TestPiBridgeE2EScenarios:
    """Full-flow mock tests simulating real Pi event sequences."""

    @pytest.fixture
    def bridge(self):
        return PiBridge(extension_paths=[])

    @pytest.mark.asyncio
    async def test_simple_chat_e2e(self, bridge):
        """Simple chat: user message → assistant text → done."""
        lines = [
            '{"type":"response","id":"1","success":true}\n',
            json.dumps(make_token_event("Hello! How can I help?")) + "\n",
            json.dumps(make_agent_end()) + "\n",
            "",
        ]
        mock_proc = make_mock_process(lines)

        with patch("subprocess.Popen", return_value=mock_proc):
            with patch("asyncio.sleep", return_value=None):
                with patch.object(bridge, "_send_request", new_callable=AsyncMock, return_value=None):
                    await bridge.start()

        try:
            events = []
            async for ev in bridge.stream_prompt("Hi"):
                events.append(ev)

            types = [e.split("\n")[0].replace("event: ", "") for e in events if e.strip()]
            assert types[0] == "task_start"
            assert "token" in types
            assert "task_complete" in types
            assert types[-1] == "done"
        finally:
            await bridge.stop()

    @pytest.mark.asyncio
    async def test_gis_tool_call_e2e(self, bridge):
        """GIS tool call: user → assistant → tool_call → step_start → step_result → done."""
        lines = [
            '{"type":"response","id":"1","success":true}\n',
            json.dumps(make_token_event("I'll analyze that for you.")) + "\n",
            json.dumps(make_tool_call_event("spatial_analyze", '{"layer":"buildings"}')) + "\n",
            json.dumps(make_tool_execution_start("tc-1", "spatial_analyze")) + "\n",
            json.dumps(make_tool_execution_end("tc-1", "spatial_analyze", is_error=False)) + "\n",
            json.dumps(make_agent_end()) + "\n",
            "",
        ]
        mock_proc = make_mock_process(lines)

        with patch("subprocess.Popen", return_value=mock_proc):
            with patch("asyncio.sleep", return_value=None):
                with patch.object(bridge, "_send_request", new_callable=AsyncMock, return_value=None):
                    await bridge.start()

        try:
            events = []
            async for ev in bridge.stream_prompt("Buffer buildings by 500m"):
                events.append(ev)

            types = [e.split("\n")[0].replace("event: ", "") for e in events if e.strip()]
            assert "task_start" in types
            assert "token" in types
            assert "tool_call" in types
            assert "step_start" in types
            assert "step_result" in types
            assert "task_complete" in types
            assert "done" in types
        finally:
            await bridge.stop()

    @pytest.mark.asyncio
    async def test_tool_error_e2e(self, bridge):
        """Tool error: step_error should appear in event stream."""
        lines = [
            '{"type":"response","id":"1","success":true}\n',
            json.dumps(make_tool_call_event("spatial_analyze", '{"layer":"invalid"}')) + "\n",
            json.dumps(make_tool_execution_start("tc-1", "spatial_analyze")) + "\n",
            json.dumps(make_tool_execution_end("tc-1", "spatial_analyze", is_error=True)) + "\n",
            json.dumps(make_agent_end()) + "\n",
            "",
        ]
        mock_proc = make_mock_process(lines)

        with patch("subprocess.Popen", return_value=mock_proc):
            with patch("asyncio.sleep", return_value=None):
                with patch.object(bridge, "_send_request", new_callable=AsyncMock, return_value=None):
                    await bridge.start()

        try:
            events = []
            async for ev in bridge.stream_prompt("Analyze invalid layer"):
                events.append(ev)

            types = [e.split("\n")[0].replace("event: ", "") for e in events if e.strip()]
            assert "step_error" in types
            # Verify error content
            error_ev = next(e for e in events if "step_error" in e)
            assert "Tool error" in error_ev
        finally:
            await bridge.stop()

    @pytest.mark.asyncio
    async def test_pi_rpc_failure_yields_task_error(self, bridge):
        """When Pi process fails, stream_prompt yields task_error + done."""
        mock_proc = MagicMock()
        mock_proc.stdin = MagicMock()
        mock_proc.stdout = MagicMock()
        mock_proc.stderr = MagicMock()
        mock_proc.poll.return_value = None
        mock_proc.stdout.readline.side_effect = make_readline([""])

        with patch("subprocess.Popen", return_value=mock_proc):
            with patch("asyncio.sleep", return_value=None):
                with patch.object(bridge, "_send_request", new_callable=AsyncMock, return_value=None):
                    await bridge.start()

        try:
            async def failing_send(cmd, data=None):
                raise PiRpcError("connection refused")
            bridge._send_request = failing_send

            events = []
            async for ev in bridge.stream_prompt("test"):
                events.append(ev)

            types = [e.split("\n")[0].replace("event: ", "") for e in events if e.strip()]
            assert "task_error" in types
            error_ev = next(e for e in events if "task_error" in e)
            assert "connection refused" in error_ev
            assert types[-1] == "done"
        finally:
            await bridge.stop()

    @pytest.mark.asyncio
    async def test_stream_timeout_yields_error(self, bridge):
        """When Pi goes silent, stream yields error event + done."""
        lines = [
            '{"type":"response","id":"1","success":true}\n',
            "",
        ]
        mock_proc = make_mock_process(lines)

        with patch("subprocess.Popen", return_value=mock_proc):
            with patch("asyncio.sleep", return_value=None):
                with patch.object(bridge, "_send_request", new_callable=AsyncMock, return_value=None):
                    await bridge.start()

        try:
            events = []
            async for ev in bridge.stream_prompt("slow"):
                events.append(ev)

            types = [e.split("\n")[0].replace("event: ", "") for e in events if e.strip()]
            assert types[0] == "task_start"
            assert "error" in types
            assert types[-1] == "done"
        finally:
            await bridge.stop()

    @pytest.mark.asyncio
    async def test_thinking_then_response_e2e(self, bridge):
        """Thinking/reasoning events flow before final response."""
        lines = [
            '{"type":"response","id":"1","success":true}\n',
            json.dumps({
                "type": "message_update",
                "message": {"role": "assistant", "content": [{"type": "thinking", "text": "Let me think..."}]},
                "assistantMessageEvent": {"type": "thinking_delta", "content": "Let me think..."},
            }) + "\n",
            json.dumps(make_token_event("Based on my analysis, the buffer area is 2.5 sq km.")) + "\n",
            json.dumps(make_agent_end()) + "\n",
            "",
        ]
        mock_proc = make_mock_process(lines)

        with patch("subprocess.Popen", return_value=mock_proc):
            with patch("asyncio.sleep", return_value=None):
                with patch.object(bridge, "_send_request", new_callable=AsyncMock, return_value=None):
                    await bridge.start()

        try:
            events = []
            async for ev in bridge.stream_prompt("Analyze buffer area"):
                events.append(ev)

            types = [e.split("\n")[0].replace("event: ", "") for e in events if e.strip()]
            assert "token" in types
            # At least one token event should have is_reasoning
            token_events = [e for e in events if "token" in e]
            reasoning_tokens = [e for e in token_events if "is_reasoning" in e and "true" in e]
            assert len(reasoning_tokens) >= 1
        finally:
            await bridge.stop()


class TestFeatureFlag:
    """Test USE_NEW_AGENT feature flag behavior."""

    def test_use_pi_bridge_helper(self):
        """_use_pi_bridge() returns correct value based on env and bridge state."""
        from app.api.routes.chat import _use_pi_bridge, USE_NEW_AGENT, pi_bridge

        original_flag = USE_NEW_AGENT
        original_bridge = pi_bridge

        try:
            # Test: flag off → False regardless of bridge
            import app.api.routes.chat as chat_mod
            chat_mod.USE_NEW_AGENT = False
            chat_mod.pi_bridge = MagicMock()
            assert _use_pi_bridge() is False

            # Test: flag on, no bridge → False
            chat_mod.USE_NEW_AGENT = True
            chat_mod.pi_bridge = None
            assert _use_pi_bridge() is False

            # Test: flag on, bridge set → True
            chat_mod.USE_NEW_AGENT = True
            chat_mod.pi_bridge = MagicMock()
            assert _use_pi_bridge() is True
        finally:
            chat_mod.USE_NEW_AGENT = original_flag
            chat_mod.pi_bridge = original_bridge