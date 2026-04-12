import pytest
from unittest.mock import MagicMock, patch
from app.services.chat_engine import ChatEngine
from app.tools.registry import ToolRegistry
from app.models.db_model import Message

@pytest.fixture
def registry():
    return ToolRegistry()

@pytest.fixture
def chat_engine(registry):
    return ChatEngine(registry)

@pytest.mark.asyncio
async def test_chat_history_reloading(chat_engine):
    session_id = "test-session-persistence"
    
    # 1. Mock DB messages
    mock_messages = [
        Message(id=1, role="user", content="First message"),
        Message(id=2, role="assistant", content="First response")
    ]
    
    with patch("app.services.chat_engine.SessionLocal") as mock_session_local:
        mock_db = MagicMock()
        mock_session_local.return_value = mock_db
        
        with patch("app.services.chat_engine.HistoryService") as mock_history_service_cls:
            mock_history_service = MagicMock()
            mock_history_service_cls.return_value = mock_history_service
            
            mock_conv = MagicMock()
            mock_conv.messages = mock_messages
            mock_history_service.get_or_create_conversation.return_value = mock_conv
            
            # 2. Call _get_or_create_session (should load from mock DB)
            messages = await chat_engine._get_or_create_session(session_id)
            
            # 3. Verify
            # Should have: SYSTEM_PROMPT, user: First message, assistant: First response
            assert len(messages) == 3
            assert messages[0]["role"] == "system"
            assert messages[1]["role"] == "user"
            assert messages[1]["content"] == "First message"
            assert messages[2]["role"] == "assistant"
            assert messages[2]["content"] == "First response"
            
            # Verify it's in cache now
            assert session_id in chat_engine._sessions
            assert chat_engine._sessions[session_id] == messages

@pytest.mark.asyncio
async def test_db_msg_to_llm_conversion(chat_engine):
    # Test tool call conversion
    tool_calls = [{"id": "call1", "function": {"name": "test_tool", "arguments": "{}"}}]
    msg = Message(id=1, role="assistant", content="Thinking...", tool_calls=tool_calls)
    
    llm_msg = chat_engine._db_msg_to_llm(msg)
    assert llm_msg["role"] == "assistant"
    assert llm_msg["content"] == "Thinking..."
    assert llm_msg["tool_calls"] == tool_calls
    
    # Test tool result conversion
    msg_tool = Message(id=2, role="tool", content="Result", tool_call_id="call1")
    llm_msg_tool = chat_engine._db_msg_to_llm(msg_tool)
    assert llm_msg_tool["role"] == "tool"
    assert llm_msg_tool["tool_call_id"] == "call1"
    assert llm_msg_tool["content"] == "Result"
