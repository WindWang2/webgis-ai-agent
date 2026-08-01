"""
Unit tests for ChatContextAssembler deep module.
"""
import pytest
from app.services.chat.context_assembler import ChatContextAssembler, ContextAssemblyResult
from app.services.chat.context_builder import compose_request_messages
from app.services.session_data import SessionDataManager


@pytest.mark.asyncio
async def test_assemble_empty_messages():
    assembler = ChatContextAssembler()
    result = await assembler.assemble("test_session", [])
    assert result.messages == []
    assert result.estimated_tokens == 0
    assert result.history_turns_included == 0
    assert result.layer_count == 0
    assert result.to_messages() == []


@pytest.mark.asyncio
async def test_assemble_basic_prompt():
    assembler = ChatContextAssembler()
    messages = [
        {"role": "system", "content": "You are a WebGIS AI agent."},
        {"role": "user", "content": "Hello!"},
    ]
    result = await assembler.assemble("test_session_1", messages)
    assert isinstance(result, ContextAssemblyResult)
    assert len(result.messages) >= 2
    assert result.messages[0]["role"] == "system"
    assert "WebGIS AI agent" in result.messages[0]["content"]
    assert "环境感知" in result.messages[0]["content"]
    assert result.history_turns_included == 1
    assert result.estimated_tokens > 0
    assert result.to_messages() == result.messages


@pytest.mark.asyncio
async def test_dependency_injection():
    custom_store = SessionDataManager()
    assembler = ChatContextAssembler(store=custom_store)
    messages = [
        {"role": "system", "content": "System prompt."},
        {"role": "user", "content": "Show me points."},
    ]
    result = await assembler.assemble("test_session_di", messages)
    assert result.layer_count == 0
    assert isinstance(result.estimated_tokens, int)


@pytest.mark.asyncio
async def test_adapter_parity():
    session_id = "test_session_parity"
    messages = [
        {"role": "system", "content": "System prompt."},
        {"role": "user", "content": "Show me hospital buffer zone."},
    ]
    legacy_res = await compose_request_messages(session_id, messages)
    assembler = ChatContextAssembler()
    deep_res = await assembler.assemble(session_id, messages)
    assert legacy_res == deep_res.to_messages()
