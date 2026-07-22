"""Unit tests for ChatAgent bridge class."""
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from app.agent.chat import ChatAgent
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
        "result": {"success": True},
        "llm_payload": "tool result",
        "slim_event": {"success": True},
        "geojson_ref": None,
        "has_geojson": False,
        "repeated": False,
        "is_error": False,
        "error_msg": "",
    })
    return engine


class TestChatAgentInit:
    def test_default_init(self):
        """ChatAgent can be initialized with just an engine."""
        engine = _make_mock_engine()
        agent = ChatAgent(engine=engine)
        assert agent._engine is engine
        assert agent._registry is engine.registry
        assert agent._session_id == ""

    def test_init_with_state(self):
        """ChatAgent accepts a pre-built AgentState."""
        engine = _make_mock_engine()
        state = AgentState(systemPrompt="custom prompt")
        agent = ChatAgent(engine=engine, state=state)
        assert agent._state.systemPrompt == "custom prompt"


class TestChatAgentToolSelection:
    @pytest.mark.asyncio
    async def test_select_tools_without_catalog(self):
        """Without catalog, falls back to registry.get_schemas()."""
        engine = _make_mock_engine()
        engine.registry.get_schemas.return_value = [
            {"function": {"name": "tool_a"}, "executionMode": "parallel"},
            {"function": {"name": "tool_b"}, "executionMode": "parallel"},
        ]
        agent = ChatAgent(engine=engine)
        from app.agent._types import AgentContext
        ctx = AgentContext(messages=[{"role": "user", "content": "hello"}])
        tools = await agent._select_tools(ctx)
        assert len(tools) == 2
        # Each tool should have an execute key injected
        for t in tools:
            assert "execute" in t

    @pytest.mark.asyncio
    async def test_select_tools_with_catalog(self):
        """With catalog, uses catalog.select_schemas()."""
        engine = _make_mock_engine()
        mock_catalog = MagicMock()
        mock_catalog.select_schemas.return_value = [
            {"function": {"name": "catalog_tool"}},
        ]
        engine.catalog = mock_catalog
        agent = ChatAgent(engine=engine)
        from app.agent._types import AgentContext
        ctx = AgentContext(messages=[{"role": "user", "content": "hello"}])
        tools = await agent._select_tools(ctx)
        assert len(tools) == 1
        assert tools[0]["function"]["name"] == "catalog_tool"
        mock_catalog.select_schemas.assert_called_once_with("hello", session_id="")

    @pytest.mark.asyncio
    async def test_select_tools_injects_execute(self):
        """_inject_execute adds execute key to tool schemas."""
        engine = _make_mock_engine()
        agent = ChatAgent(engine=engine)
        schema = {"function": {"name": "my_tool"}}
        result = agent._inject_execute(schema)
        assert "execute" in result
        assert result["function"]["name"] == "my_tool"
        # Original not mutated
        assert "execute" not in schema


class TestChatAgentDispatch:
    @pytest.mark.asyncio
    async def test_dispatch_tool_delegates_to_engine(self):
        """_dispatch_tool delegates to engine._dispatch_tool."""
        engine = _make_mock_engine()
        agent = ChatAgent(engine=engine)
        agent._session_id = "sess-1"
        agent._executed_tools = set()
        agent._task_id = "task-1"

        outcome = await agent._dispatch_tool("test_tool", {"arg": "val"})
        engine._dispatch_tool.assert_called_once()
        assert "content" in outcome


class TestChatAgentStreamMapping:
    def test_map_message_update_with_content(self):
        """message_update event with content maps to token SSE."""
        from app.agent.chat._chat_agent import _map_event_to_sse
        event = {
            "type": "message_update",
            "message": {"role": "assistant", "content": "hello"},
        }
        result = _map_event_to_sse(event, "sess-1", "task-1")
        assert result is not None
        assert "token" in result
        assert "hello" in result

    def test_map_tool_execution_start(self):
        """tool_execution_start maps to step_start SSE."""
        from app.agent.chat._chat_agent import _map_event_to_sse
        event = {
            "type": "tool_execution_start",
            "toolCallId": "tc-1",
            "toolName": "my_tool",
            "args": {},
        }
        result = _map_event_to_sse(event, "sess-1", "task-1")
        assert result is not None
        assert "step_start" in result

    def test_map_tool_execution_end_success(self):
        """tool_execution_end (success) maps to step_result SSE."""
        from app.agent.chat._chat_agent import _map_event_to_sse
        event = {
            "type": "tool_execution_end",
            "toolCallId": "tc-1",
            "toolName": "my_tool",
            "result": {"content": [{"type": "text", "text": "ok"}]},
            "isError": False,
        }
        result = _map_event_to_sse(event, "sess-1", "task-1")
        assert result is not None
        assert "step_result" in result

    def test_map_tool_execution_end_error(self):
        """tool_execution_end (error) maps to step_error SSE."""
        from app.agent.chat._chat_agent import _map_event_to_sse
        event = {
            "type": "tool_execution_end",
            "toolCallId": "tc-1",
            "toolName": "my_tool",
            "result": {"content": [{"type": "text", "text": "error msg"}]},
            "isError": True,
        }
        result = _map_event_to_sse(event, "sess-1", "task-1")
        assert result is not None
        assert "step_error" in result

    def test_map_unknown_event(self):
        """Unknown event types return None."""
        from app.agent.chat._chat_agent import _map_event_to_sse
        event = {"type": "unknown_event"}
        result = _map_event_to_sse(event, "sess-1", "task-1")
        assert result is None
