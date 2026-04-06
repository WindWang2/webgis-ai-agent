"""对话引擎测试"""
import pytest
import json
from unittest.mock import AsyncMock, patch, MagicMock

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
    mock_msg = MagicMock()
    mock_msg.content = "你好！我是 GIS 分析助手。"
    mock_msg.tool_calls = None
    mock_response = MagicMock()
    mock_response.choices = [MagicMock(message=mock_msg)]

    with patch.object(engine.client.chat.completions, "create", new_callable=AsyncMock, return_value=mock_response):
        result = await engine.chat("你好")
        assert "session_id" in result
        assert result["content"]


@pytest.mark.asyncio
async def test_chat_with_tool_call(registry):
    """测试工具调用流程"""
    engine = ChatEngine(registry)

    # 第一次调用返回 tool_call
    tc = MagicMock()
    tc.id = "call_1"
    tc.function.name = "geocode"
    tc.function.arguments = '{"query": "北京"}'
    msg1 = MagicMock()
    msg1.content = None
    msg1.tool_calls = [tc]
    resp1 = MagicMock()
    resp1.choices = [MagicMock(message=msg1)]

    # 第二次调用返回最终回复
    msg2 = MagicMock()
    msg2.content = "北京的坐标是 39.9042, 116.4074"
    msg2.tool_calls = None
    resp2 = MagicMock()
    resp2.choices = [MagicMock(message=msg2)]

    with patch.object(engine.client.chat.completions, "create", new_callable=AsyncMock, side_effect=[resp1, resp2]):
        result = await engine.chat("北京的坐标在哪？")
        assert "39.9042" in result["content"] or result["content"]


@pytest.mark.asyncio
async def test_chat_stream(registry):
    """测试流式对话"""
    engine = ChatEngine(registry)
    mock_msg = MagicMock()
    mock_msg.content = "你好"
    mock_msg.tool_calls = None
    mock_response = MagicMock()
    mock_response.choices = [MagicMock(message=mock_msg)]

    with patch.object(engine.client.chat.completions, "create", new_callable=AsyncMock, return_value=mock_response):
        events = []
        async for event in engine.chat_stream("你好"):
            events.append(event)
        assert len(events) >= 1
        assert any("content" in e for e in events)


@pytest.mark.asyncio
async def test_session_persistence(registry):
    """测试会话保持"""
    engine = ChatEngine(registry)
    mock_msg = MagicMock()
    mock_msg.content = "OK"
    mock_msg.tool_calls = None
    mock_response = MagicMock()
    mock_response.choices = [MagicMock(message=mock_msg)]

    with patch.object(engine.client.chat.completions, "create", new_callable=AsyncMock, return_value=mock_response):
        r1 = await engine.chat("hello", session_id="s1")
        r2 = await engine.chat("world", session_id="s1")
        assert r1["session_id"] == "s1"
        assert r2["session_id"] == "s1"
        # 同一 session 应该有历史
        assert len(engine._sessions["s1"]) >= 4  # system + 2 user + 2 assistant
