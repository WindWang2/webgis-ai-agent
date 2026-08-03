# tests/test_chat_engine_history.py
import pytest
from unittest.mock import AsyncMock, patch


@pytest.mark.asyncio
async def test_chat_stream_persists_user_message(monkeypatch):
    """ChatEngine should save user message via _save_msg_async at stream start."""
    from app.services.chat_engine import ChatEngine
    from app.tools.registry import ToolRegistry

    registry = ToolRegistry()
    engine = ChatEngine(registry)

    msg = {"content": "done", "tool_calls": None}

    async def fake_stream(*args, **kwargs):
        yield ("done", {"message": msg})

    # Patch the session/plan/title side effects so chat_stream does not touch
    # the DB / Redis / a real LLM planner (which would hang the test). Mirrors
    # the fixture pattern in test_chat_engine_planning.py.
    async def fake_get_or_create_session(session_id, user_id=None):
        return []

    async def fake_maybe_plan(*a, **kw):
        return None

    async def fake_generate_title(*a, **kw):
        return None

    monkeypatch.setattr(engine, "_get_or_create_session", fake_get_or_create_session)
    monkeypatch.setattr(engine, "_maybe_plan", fake_maybe_plan)
    monkeypatch.setattr(engine, "_generate_title", fake_generate_title)

    with patch.object(engine, "_call_llm_stream", return_value=fake_stream()):
        with patch.object(engine, "_save_msg_async", new_callable=AsyncMock) as mock_save:
            events = []
            async for event in engine.chat_stream("hello", session_id="test-session"):
                events.append(event)

    assert mock_save.call_count >= 1
    first_call = mock_save.call_args_list[0]
    assert first_call[0][1] == "user"
    assert first_call[0][2] == "hello"
