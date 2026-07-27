"""Tests for error message credential sanitization.

Part of P3-6: error_msg[:200] 可能泄露密钥.

unified-tool-dispatch 票据 03：legacy dispatch_tool 已删除，凭证脱敏行为现在
由 ToolDispatchService 拥有（它内部调用 sanitize_error_msg）。本测试改走服务
接口，断言 ToolDispatchResult 字段——但锁定的安全行为（密钥不出现在任何输出）不变。
"""
import pytest
from unittest.mock import AsyncMock, MagicMock

from app.services.tool_dispatch_service import ToolDispatchService


def _tc(name: str, args: str = "{}", tc_id: str = "call-1") -> dict:
    return {"id": tc_id, "type": "function", "function": {"name": name, "arguments": args}}


@pytest.mark.asyncio
async def test_dispatch_sanitizes_db_credentials():
    """If a tool raises an exception containing database connection passwords,

    the dispatch service must sanitize the credentials before returning or logging them.
    """
    sensitive_msg = "Could not connect: postgresql://postgres:my-super-secret-password-123@localhost:5432/gis_db"

    mock_registry = MagicMock()
    # Mock dispatch to raise ValueError with sensitive db URI
    mock_registry.dispatch = AsyncMock(side_effect=ValueError(sensitive_msg))

    service = ToolDispatchService(registry=mock_registry)
    result = await service.dispatch(
        tc=_tc("buffer_analysis", '{"radius": 100}', "call-1"),
        session_id="test-session",
        executed_tools=set(),
    )

    # The service must surface this as an error
    assert result.status == "error"

    # Assert that the sensitive password is fully masked and absent from all outputs
    secret_part = "my-super-secret-password-123"
    assert secret_part not in (result.error_msg or "")
    assert secret_part not in result.llm_payload
    assert secret_part not in result.slim_event["message"]
    assert secret_part not in result.raw_result["message"]

    # The output should contain the masked placeholder
    assert "******" in (result.error_msg or "")
    assert "******" in result.llm_payload


@pytest.mark.asyncio
async def test_dispatch_sanitizes_openai_keys():
    """If an exception contains an OpenAI or other API key, it must be masked."""
    sensitive_msg = "API Connection Error on key sk-proj-abcdefghijklmnopqrstuvwxyz1234567890"

    mock_registry = MagicMock()
    mock_registry.dispatch = AsyncMock(side_effect=ValueError(sensitive_msg))

    service = ToolDispatchService(registry=mock_registry)
    result = await service.dispatch(
        tc=_tc("heatmap_data", "{}", "call-2"),
        session_id="test-session",
        executed_tools=set(),
    )

    assert result.status == "error"

    # Assert OpenAI key is masked
    full_key = "sk-proj-abcdefghijklmnopqrstuvwxyz1234567890"
    assert full_key not in (result.error_msg or "")
    assert full_key not in result.llm_payload

    # Should contain masked key notation
    assert "sk-p***" in (result.error_msg or "") or "sk-p***" in result.llm_payload or "***" in (result.error_msg or "")
