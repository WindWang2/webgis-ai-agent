"""对话引擎测试"""
import pytest
from unittest.mock import AsyncMock, patch

from app.services.chat_engine import ChatEngine
from app.tools.registry import ToolRegistry, tool


@pytest.fixture
def registry():
    r = ToolRegistry()

    @tool(r, name="geocode", description="Geocode")
    def geocode(query: str) -> dict:
        return {"lat": 39.9042, "lon": 116.4074, "name": "北京"}

    return r


def test_engine_init(registry):
    engine = ChatEngine(registry)
    assert engine.model
    assert len(engine.registry.get_schemas()) == 1


@pytest.mark.asyncio
async def test_chat_simple_response(registry):
    """测试无工具调用的简单对话"""
    engine = ChatEngine(registry)
    mock_response = {"choices": [{"message": {"content": "你好！", "tool_calls": None}}]}

    with patch.object(engine, "_call_llm", new_callable=AsyncMock, return_value=mock_response):
        result = await engine.chat("你好")
        assert "session_id" in result
        assert result["content"]


@pytest.mark.asyncio
async def test_chat_with_tool_call(registry):
    """测试工具调用流程"""
    engine = ChatEngine(registry)

    resp1 = {"choices": [{"message": {
        "content": None,
        "tool_calls": [{"id": "call_1", "function": {"name": "geocode", "arguments": '{"query": "北京"}'}}]
    }}]}
    resp2 = {"choices": [{"message": {"content": "北京的坐标是 39.9042, 116.4074", "tool_calls": None}}]}

    with patch.object(engine, "_call_llm", new_callable=AsyncMock, side_effect=[resp1, resp2]):
        with patch.object(engine, "_save_msg_async", new_callable=AsyncMock):
            result = await engine.chat("北京的坐标在哪？")
            assert "39.9042" in result["content"]


@pytest.mark.asyncio
async def test_chat_stream(registry):
    """测试流式对话"""
    engine = ChatEngine(registry)
    msg = {"content": "你好", "tool_calls": None}

    async def fake_stream(*args, **kwargs):
        yield ("done", {"message": msg})

    with patch.object(engine, "_call_llm_stream", return_value=fake_stream()):
        with patch.object(engine, "_save_msg_async", new_callable=AsyncMock):
            events = []
            async for event in engine.chat_stream("你好"):
                events.append(event)
            assert len(events) >= 1
            assert any("content" in e for e in events)


@pytest.mark.asyncio
async def test_session_persistence(registry):
    """测试会话保持"""
    engine = ChatEngine(registry)
    mock_response = {"choices": [{"message": {"content": "OK", "tool_calls": None}}]}

    with patch.object(engine, "_call_llm", new_callable=AsyncMock, return_value=mock_response):
        with patch.object(engine, "_save_msg_async", new_callable=AsyncMock):
            r1 = await engine.chat("hello", session_id="s1")
            r2 = await engine.chat("world", session_id="s1")
            assert r1["session_id"] == "s1"
            assert r2["session_id"] == "s1"
            assert len(engine._sessions["s1"]) >= 4
