# tests/test_chat_engine_history.py
import pytest
from unittest.mock import AsyncMock, patch


@pytest.mark.asyncio
async def test_chat_stream_persists_user_message():
    """ChatEngine should save user message via _save_msg_async at stream start."""
    from app.services.chat_engine import ChatEngine
    from app.tools.registry import ToolRegistry

    registry = ToolRegistry()
    engine = ChatEngine(registry)

    msg = {"content": "done", "tool_calls": None}

    async def fake_stream(*args, **kwargs):
        yield ("done", {"message": msg})

    with patch.object(engine, "_call_llm_stream", return_value=fake_stream()):
        with patch.object(engine, "_save_msg_async", new_callable=AsyncMock) as mock_save:
            events = []
            async for event in engine.chat_stream("hello", session_id="test-session"):
                events.append(event)

    assert mock_save.call_count >= 1
    first_call = mock_save.call_args_list[0]
    assert first_call[0][1] == "user"
    assert first_call[0][2] == "hello"
