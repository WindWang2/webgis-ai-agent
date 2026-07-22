"""Integration tests for the new Agent system.

Tests the full stack: AgentRuntime → ChatAgent → AgentLoop → tool dispatch.
"""
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from app.agent.chat import ChatAgent
from app.agent._runtime import AgentRuntime
from app.agent._types import AgentState, ModelInfo


def _make_mock_engine():
    """Create a mock ChatEngine with all required attributes."""
    engine = MagicMock()
    engine.model = "deepseek-v4"
    engine.base_url = "https://api.deepseek.com"
    engine.api_key = "test-key"
    engine.use_prompt_caching = False
    engine.registry = MagicMock()
    engine.registry.get_schemas.return_value = []
    engine.registry.metadata.return_value = {"tier": 1}
    engine.catalog = None
    engine.tracker = MagicMock()
    engine.tracker.create.return_value = MagicMock(id="task-1")
    engine.tracker.start_step.return_value = MagicMock(id="step-1")
    engine._llm_config.return_value = MagicMock(model="deepseek-v4", base_url="https://api.deepseek.com", api_key="test-key")
    engine._call_llm_stream = AsyncMock()
    engine._dispatch_tool = AsyncMock(return_value={
        "result": {"success": True, "content": "result"},
        "llm_payload": "tool result",
        "slim_event": {"success": True},
        "geojson_ref": None,
        "has_geojson": False,
        "repeated": False,
        "is_error": False,
        "error_msg": "",
    })
    engine.clear_session = AsyncMock(return_value=True)
    return engine


class TestAgentRuntimeIntegration:
    @pytest.mark.asyncio
    async def test_runtime_creates_chat_agent(self):
        """AgentRuntime creates a ChatAgent for a session."""
        engine = _make_mock_engine()
        runtime = AgentRuntime(chat_engine=engine)
        agent = await runtime.get_or_create_agent("session-1")
        assert isinstance(agent, ChatAgent)
        assert agent._session_id == "session-1"

    @pytest.mark.asyncio
    async def test_runtime_reuses_agent(self):
        """AgentRuntime returns same agent for same session."""
        engine = _make_mock_engine()
        runtime = AgentRuntime(chat_engine=engine)
        agent1 = await runtime.get_or_create_agent("session-1")
        agent2 = await runtime.get_or_create_agent("session-1")
        assert agent1 is agent2

    @pytest.mark.asyncio
    async def test_runtime_clear_session(self):
        """AgentRuntime clears session correctly."""
        engine = _make_mock_engine()
        runtime = AgentRuntime(chat_engine=engine)
        await runtime.get_or_create_agent("session-1")
        result = await runtime.clear_session("session-1")
        assert result is True
        engine.clear_session.assert_called_once_with("session-1")

    @pytest.mark.asyncio
    async def test_stream_request_yields_events(self):
        """handle_stream_request yields SSE events."""
        engine = _make_mock_engine()
        runtime = AgentRuntime(chat_engine=engine)
        events = []
        async for event in runtime.handle_stream_request(
            message="hello",
            session_id="session-1",
        ):
            events.append(event)
        # Should yield at least task_start, done events
        assert len(events) >= 2
        assert any("task_start" in e for e in events)
        assert any("done" in e for e in events)

    @pytest.mark.asyncio
    async def test_use_new_agent_flag(self):
        """USE_NEW_AGENT flag is read from environment."""
        from app.agent._runtime import USE_NEW_AGENT
        # Default is False (no env var set in test)
        assert isinstance(USE_NEW_AGENT, bool)
