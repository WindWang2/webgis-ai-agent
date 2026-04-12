import pytest
import json
from unittest.mock import MagicMock, patch
from app.services.chat_engine import ChatEngine
from app.tools.registry import ToolRegistry

@pytest.mark.asyncio
async def test_detect_suspicious_result():
    engine = ChatEngine(ToolRegistry())
    
    # Positive cases (suspicious)
    assert engine._detect_suspicious_result(None) is True
    assert engine._detect_suspicious_result([]) is True
    assert engine._detect_suspicious_result({}) is True
    assert engine._detect_suspicious_result({"type": "FeatureCollection", "features": []}) is True
    assert engine._detect_suspicious_result({"data": []}) is True
    assert engine._detect_suspicious_result({"poi_count": 0}) is True
    
    # Negative cases (not suspicious)
    assert engine._detect_suspicious_result([1, 2]) is False
    assert engine._detect_suspicious_result({"type": "FeatureCollection", "features": [{"id": 1}]}) is False
    assert engine._detect_suspicious_result({"data": [1]}) is False
    assert engine._detect_suspicious_result({"poi_count": 5}) is False

@pytest.mark.asyncio
async def test_self_healing_hint_injection():
    registry = ToolRegistry()
    engine = ChatEngine(registry)
    
    # Mock LLM response to trigger a tool call
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
    
    # Second response to end the loop
    mock_final_response = {
        "choices": [{
            "message": {"role": "assistant", "content": "Done."}
        }]
    }
    
    with patch.object(engine, "_call_llm") as mock_call:
        mock_call.side_effect = [mock_response, mock_final_response]
        
        # Mock tool registry to return an empty result (suspicious)
        with patch.object(registry, "dispatch", return_value={"type": "FeatureCollection", "features": []}):
            with patch.object(engine, "_save_msg_async", MagicMock()):
                with patch.object(engine, "_get_or_create_session", return_value=[]):
                    
                    result = await engine.chat("test message", "session_1")
                    
                    # Verify that the hint was injected into the messages passed back to the LLM
                    # The second call to _call_llm should contain the tool result WITH the hint
                    messages_in_second_call = mock_call.call_args_list[1][0][0]
                    tool_msg = next(m for m in messages_in_second_call if m["role"] == "tool")
                    
                    assert "提示: 查询结果为空" in tool_msg["content"]
                    assert "(提示: 查询结果为空" in tool_msg["content"]
