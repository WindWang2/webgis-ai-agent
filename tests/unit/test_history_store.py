"""Unit tests for HistoryStoreProtocol and HistoryContext deepen seam."""
from unittest.mock import MagicMock
from app.services.history_store_protocol import HistoryContext, HistoryStoreProtocol
from app.services.history_service_async import AsyncHistoryService, _msg_to_llm_dict


def test_history_context_dataclass():
    ctx = HistoryContext(
        session_id="test-session-123",
        owner_token="token-abc",
        user_id="user-456",
        llm_messages=[{"role": "user", "content": "hello"}],
    )
    assert ctx.session_id == "test-session-123"
    assert ctx.owner_token == "token-abc"
    assert ctx.user_id == "user-456"
    assert len(ctx.llm_messages) == 1
    assert ctx.llm_messages[0]["role"] == "user"


def test_async_history_service_satisfies_protocol():
    svc = AsyncHistoryService()
    assert isinstance(svc, HistoryStoreProtocol)


def test_history_replay_normalizes_legacy_tool_names():
    m = MagicMock()
    m.role = "assistant"
    m.content = "Adding layer..."
    m.tool_calls = [
        {"id": "call_1", "type": "function", "function": {"name": "add_layer", "arguments": "{}"}},
        {"id": "call_2", "type": "function", "function": {"name": "set_view", "arguments": "{}"}},
    ]
    m.tool_call_id = None
    m.reasoning_content = None

    llm_dict = _msg_to_llm_dict(m)
    tcs = llm_dict["tool_calls"]
    assert tcs[0]["function"]["name"] == "webgis_layer_upsert"
    assert tcs[1]["function"]["name"] == "webgis_view_set"
