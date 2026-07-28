"""Unit tests for HistoryStoreProtocol and HistoryContext deepen seam."""
import pytest
from app.services.history_store_protocol import HistoryContext, HistoryStoreProtocol
from app.services.history_service_async import AsyncHistoryService


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
