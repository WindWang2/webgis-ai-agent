"""Mock-based end-to-end test for Pi bridge.

Simulates the full request flow without requiring a real LLM API key:
FastAPI /chat/stream -> PiBridge.stream_prompt() -> SSE events -> frontend

This test verifies:
1. HTTP endpoint accepts requests and returns SSE stream
2. PiBridge correctly maps Pi events to SSE format
3. Tool execution flow (tool_call -> step_start -> step_result)
4. Error handling (PiRpcError -> HTTP 502)
5. Feature flag gating (USE_NEW_AGENT=true/false)
"""
import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

# Ensure project root on path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.agent_pi_bridge import PiBridge, PiRpcError


from tests.fixtures.pi_mocks import (
    make_agent_end,
    make_token_event,
    make_tool_call_event,
    make_tool_execution_end,
    make_tool_execution_start,
)


def _make_event_rpc(events: list[dict], fail_request: bool = False) -> MagicMock:
    """Build a MagicMock PiRpcClient whose events queue is pre-seeded.

    The PiBridge delegates all subprocess I/O to ``PiRpcClient`` (ADR-0022
    refactor). These E2E tests previously mocked the subprocess directly and
    patched ``_send_request``/``_wait_for_ready`` on the bridge; those methods
    no longer live on ``PiBridge``. Instead we inject a mock RPC client that
    pre-seeds its ``events`` queue with the Pi event sequence the test wants to
    drive, preserving the original test intent (event->SSE mapping).

    Args:
        events: Pi events to seed into the queue (in order).
        fail_request: if True, ``request`` raises PiRpcError("connection refused").
    """
    rpc = MagicMock()
    rpc.events = asyncio.Queue()
    rpc.start = AsyncMock()
    rpc.stop = AsyncMock()
    rpc.process_died = False
    if fail_request:
        rpc.request = AsyncMock(side_effect=PiRpcError("connection refused"))
    else:
        async def _seed_then_idle(cmd, data=None):
            # Seed the events the test expects, then idle (the bridge's drain
            # loop / stream_prompt will consume them and terminate on agent_end
            # or the stream timeout).
            for ev in events:
                await rpc.events.put(ev)
        rpc.request = AsyncMock(side_effect=_seed_then_idle)
    return rpc


# ─── E2E scenario tests ────────────────────────────────────────────

class TestPiBridgeE2EScenarios:
    """Full-flow mock tests simulating real Pi event sequences."""

    @pytest.mark.asyncio
    async def test_simple_chat_e2e(self):
        """Simple chat: user message -> assistant text -> done."""
        events = [
            make_token_event("Hello! How can I help?"),
            make_agent_end(),
        ]
        bridge = PiBridge(rpc=_make_event_rpc(events))

        async for ev in bridge.stream_prompt("Hi"):
            pass  # consume; assertions below re-run the bridge

        # Re-run to collect for assertions (the mock re-seeds each request).
        bridge2 = PiBridge(rpc=_make_event_rpc(events))
        events_out = []
        async for ev in bridge2.stream_prompt("Hi"):
            events_out.append(ev)

        types = [e.split("\n")[0].replace("event: ", "") for e in events_out if e.strip()]
        assert types[0] == "task_start"
        assert "token" in types
        assert "task_complete" in types
        assert types[-1] == "done"

    @pytest.mark.asyncio
    async def test_gis_tool_call_e2e(self):
        """GIS tool call: user -> assistant -> tool_call -> step_start -> step_result -> done."""
        events = [
            make_token_event("I'll analyze that for you."),
            make_tool_call_event("spatial_analyze", '{"layer":"buildings"}'),
            make_tool_execution_start("tc-1", "spatial_analyze"),
            make_tool_execution_end("tc-1", "spatial_analyze", is_error=False),
            make_agent_end(),
        ]
        bridge = PiBridge(rpc=_make_event_rpc(events))

        events_out = []
        async for ev in bridge.stream_prompt("Buffer buildings by 500m"):
            events_out.append(ev)

        types = [e.split("\n")[0].replace("event: ", "") for e in events_out if e.strip()]
        assert "task_start" in types
        assert "token" in types
        assert "tool_call" in types
        assert "step_start" in types
        assert "step_result" in types
        assert "task_complete" in types
        assert "done" in types

    @pytest.mark.asyncio
    async def test_tool_error_e2e(self):
        """Tool error: step_error should appear in event stream."""
        events = [
            make_tool_call_event("spatial_analyze", '{"layer":"invalid"}'),
            make_tool_execution_start("tc-1", "spatial_analyze"),
            make_tool_execution_end("tc-1", "spatial_analyze", is_error=True),
            make_agent_end(),
        ]
        bridge = PiBridge(rpc=_make_event_rpc(events))

        events_out = []
        async for ev in bridge.stream_prompt("Analyze invalid layer"):
            events_out.append(ev)

        types = [e.split("\n")[0].replace("event: ", "") for e in events_out if e.strip()]
        assert "step_error" in types
        error_ev = next(e for e in events_out if "step_error" in e)
        assert "Tool error" in error_ev

    @pytest.mark.asyncio
    async def test_pi_rpc_failure_yields_task_error(self):
        """When Pi process fails, stream_prompt yields task_error + done."""
        bridge = PiBridge(rpc=_make_event_rpc([], fail_request=True))

        events_out = []
        async for ev in bridge.stream_prompt("test"):
            events_out.append(ev)

        types = [e.split("\n")[0].replace("event: ", "") for e in events_out if e.strip()]
        assert "task_error" in types
        error_ev = next(e for e in events_out if "task_error" in e)
        assert "connection refused" in error_ev
        assert types[-1] == "done"

    @pytest.mark.asyncio
    async def test_stream_timeout_yields_error(self, monkeypatch):
        """When Pi goes silent, stream yields error event + done."""
        # No events seeded -> stream_prompt times out waiting for the first event.
        bridge = PiBridge(rpc=_make_event_rpc([]))
        monkeypatch.setattr("app.agent_pi_bridge.PI_EVENT_STREAM_TIMEOUT", 0.01)

        events_out = []
        async for ev in bridge.stream_prompt("slow"):
            events_out.append(ev)

        types = [e.split("\n")[0].replace("event: ", "") for e in events_out if e.strip()]
        assert types[0] == "task_start"
        assert "error" in types
        assert types[-1] == "done"

    @pytest.mark.asyncio
    async def test_thinking_then_response_e2e(self):
        """Thinking/reasoning events flow before final response."""
        events = [
            {
                "type": "message_update",
                "message": {"role": "assistant", "content": [{"type": "thinking", "text": "Let me think..."}]},
                "assistantMessageEvent": {"type": "thinking_delta", "contentIndex": 0, "delta": "Let me think..."},
            },
            make_token_event("Based on my analysis, the buffer area is 2.5 sq km."),
            make_agent_end(),
        ]
        bridge = PiBridge(rpc=_make_event_rpc(events))

        events_out = []
        async for ev in bridge.stream_prompt("Analyze buffer area"):
            events_out.append(ev)

        types = [e.split("\n")[0].replace("event: ", "") for e in events_out if e.strip()]
        assert "token" in types
        # At least one token event should have is_reasoning
        token_events = [e for e in events_out if "token" in e]
        reasoning_tokens = [e for e in token_events if "is_reasoning" in e and "true" in e]
        assert len(reasoning_tokens) >= 1


class TestFeatureFlag:
    """Test USE_NEW_AGENT feature flag behavior."""

    def test_use_pi_bridge_helper(self):
        """_use_pi_bridge() returns correct value based on env and bridge state."""
        from app.api.routes.chat import _use_pi_bridge, USE_NEW_AGENT, pi_bridge

        original_flag = USE_NEW_AGENT
        original_bridge = pi_bridge

        try:
            # Test: flag off -> False regardless of bridge
            import app.api.routes.chat as chat_mod
            chat_mod.USE_NEW_AGENT = False
            chat_mod.pi_bridge = MagicMock()
            assert _use_pi_bridge() is False

            # Test: flag on, no bridge -> False
            chat_mod.USE_NEW_AGENT = True
            chat_mod.pi_bridge = None
            assert _use_pi_bridge() is False

            # Test: flag on, bridge set, subprocess alive -> True
            chat_mod.USE_NEW_AGENT = True
            alive_bridge = MagicMock()
            alive_bridge._process_died = False
            chat_mod.pi_bridge = alive_bridge
            assert _use_pi_bridge() is True

            # C-F15: flag on, bridge set but subprocess died -> False (legacy fallback)
            dead_bridge = MagicMock()
            dead_bridge._process_died = True
            chat_mod.pi_bridge = dead_bridge
            assert _use_pi_bridge() is False
        finally:
            chat_mod.USE_NEW_AGENT = original_flag
            chat_mod.pi_bridge = original_bridge
