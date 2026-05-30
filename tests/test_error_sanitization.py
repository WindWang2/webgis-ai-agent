"""Tests for error message credential sanitization.

Part of P3-6: error_msg[:200] 可能泄露密钥.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.chat.dispatcher import dispatch_tool


@pytest.mark.asyncio
async def test_dispatch_tool_sanitizes_db_credentials():
    """If a tool raises an exception containing database connection passwords,

    the dispatcher must sanitize the credentials before returning or logging them.
    """
    sensitive_msg = "Could not connect: postgresql://postgres:my-super-secret-password-123@localhost:5432/gis_db"
    
    mock_registry = MagicMock()
    # Mock dispatch to raise ValueError with sensitive db URI
    mock_registry.dispatch = AsyncMock(side_effect=ValueError(sensitive_msg))
    
    tc = {
        "id": "call-1",
        "type": "function",
        "function": {
            "name": "buffer_analysis",
            "arguments": '{"radius": 100}'
        }
    }
    
    out = await dispatch_tool(
        tc=tc,
        session_id="test-session",
        executed_tools=set(),
        registry=mock_registry
    )
    
    # The dispatcher must return success=False
    assert out["is_error"] is True
    
    # Assert that the sensitive password is fully masked and absent from all outputs
    secret_part = "my-super-secret-password-123"
    assert secret_part not in out["error_msg"]
    assert secret_part not in out["llm_payload"]
    assert secret_part not in out["slim_event"]["message"]
    assert secret_part not in out["result"]["message"]
    
    # The output should contain the masked placeholder
    assert "******" in out["error_msg"]
    assert "******" in out["llm_payload"]


@pytest.mark.asyncio
async def test_dispatch_tool_sanitizes_openai_keys():
    """If an exception contains an OpenAI or other API key, it must be masked."""
    sensitive_msg = "API Connection Error on key sk-proj-abcdefghijklmnopqrstuvwxyz1234567890"
    
    mock_registry = MagicMock()
    mock_registry.dispatch = AsyncMock(side_effect=ValueError(sensitive_msg))
    
    tc = {
        "id": "call-2",
        "type": "function",
        "function": {
            "name": "heatmap_data",
            "arguments": "{}"
        }
    }
    
    out = await dispatch_tool(
        tc=tc,
        session_id="test-session",
        executed_tools=set(),
        registry=mock_registry
    )
    
    assert out["is_error"] is True
    
    # Assert OpenAI key is masked
    full_key = "sk-proj-abcdefghijklmnopqrstuvwxyz1234567890"
    assert full_key not in out["error_msg"]
    assert full_key not in out["llm_payload"]
    
    # Should contain masked key notation
    assert "sk-p***" in out["error_msg"] or "sk-p***" in out["llm_payload"] or "***" in out["error_msg"]
