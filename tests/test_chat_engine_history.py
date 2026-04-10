# tests/test_chat_engine_history.py
import pytest
from unittest.mock import MagicMock, AsyncMock, patch


@pytest.mark.asyncio
async def test_chat_stream_persists_user_message():
    """ChatEngine should save user message to DB at stream start."""
    from app.services.chat_engine import ChatEngine
    from app.tools.registry import ToolRegistry

    registry = ToolRegistry()
    engine = ChatEngine(registry)

    mock_history = MagicMock()
    mock_conv = MagicMock(id="test-session")
    mock_history.get_or_create_conversation.return_value = mock_conv
    engine._history = mock_history

    # Patch _call_llm to return minimal assistant response
    async def fake_llm(messages, tools=None):
        return {
            "choices": [{
                "message": {"role": "assistant", "content": "done", "tool_calls": None},
                "finish_reason": "stop"
            }]
        }
    engine._call_llm = fake_llm

    events = []
    async for event in engine.chat_stream("hello", session_id="test-session"):
        events.append(event)

    mock_history.get_or_create_conversation.assert_called_with("test-session")
    assert mock_history.save_message.call_count >= 1
    first_call = mock_history.save_message.call_args_list[0]
    assert first_call[0][1] == "user"   # role
    assert first_call[0][2] == "hello"  # content
