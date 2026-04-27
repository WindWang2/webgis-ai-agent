import pytest
import json
from unittest.mock import MagicMock, AsyncMock, patch
from app.services.chat_engine import ChatEngine
from app.tools.registry import ToolRegistry

@pytest.mark.asyncio
async def test_detect_suspicious_result():
    engine = ChatEngine(ToolRegistry())

    assert engine._detect_suspicious_result(None) is True
    assert engine._detect_suspicious_result([]) is True
    assert engine._detect_suspicious_result({}) is True
    assert engine._detect_suspicious_result({"type": "FeatureCollection", "features": []}) is True
    assert engine._detect_suspicious_result({"data": []}) is True
    assert engine._detect_suspicious_result({"poi_count": 0}) is True

    assert engine._detect_suspicious_result([1, 2]) is False
    assert engine._detect_suspicious_result({"type": "FeatureCollection", "features": [{"id": 1}]}) is False
    assert engine._detect_suspicious_result({"data": [1]}) is False
    assert engine._detect_suspicious_result({"poi_count": 5}) is False

@pytest.mark.asyncio
async def test_self_healing_hint_injection():
    registry = ToolRegistry()
    engine = ChatEngine(registry)

    mock_response = {
        "choices": [{
            "message": {
                "role": "assistant",
                "content": "Thinking...",
                "tool_calls": [{
                    "id": "call_1",
                    "function": {"name": "test_tool", "arguments": "{}"}
                }]
            }
        }]
    }

    mock_final_response = {
        "choices": [{
            "message": {"role": "assistant", "content": "Done."}
        }]
    }

    call_count = 0
    async def mock_call_llm(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        return [mock_response, mock_final_response][call_count - 1]

    with patch.object(engine, "_call_llm", new_callable=AsyncMock, side_effect=mock_call_llm):
        with patch.object(registry, "dispatch", new_callable=AsyncMock, return_value={"type": "FeatureCollection", "features": []}):
            with patch.object(engine, "_save_msg_async", new_callable=AsyncMock):
                with patch.object(engine, "_get_or_create_session", return_value=[]):

                    result = await engine.chat("test message", "session_1")

                    assert call_count == 2, f"Expected 2 _call_llm calls, got {call_count}"
                    messages_in_second_call = engine._call_llm.call_args_list[1][0][0]
                    tool_msg = next(m for m in messages_in_second_call if m["role"] == "tool")
                    assert "提示: 查询结果为空" in tool_msg["content"]
