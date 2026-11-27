"""Unit tests for HistoryStoreProtocol and HistoryContext deepen seam."""
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

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


class _RecordingSession:
    def __init__(self, commit_error=None):
        self.messages = []
        self.rollback_calls = 0
        self.commit_error = commit_error

    async def get(self, model, session_id):
        return SimpleNamespace(id=session_id, user_id="owner")

    def add(self, message):
        self.messages.append(message)

    async def commit(self):
        if self.commit_error is not None:
            raise self.commit_error

    async def rollback(self):
        self.rollback_calls += 1


@pytest.mark.asyncio
async def test_commit_interaction_persists_metadata_as_user_context_receipt():
    db = _RecordingSession()
    receipt = {"turn_id": "turn-1", "status": "completed"}

    await AsyncHistoryService(db).commit_interaction(
        "session-1", "question", "answer", metadata=receipt, user_id="owner"
    )

    user_message = next(m for m in db.messages if m.role == "user")
    assert user_message.context_receipt == receipt


@pytest.mark.asyncio
async def test_save_message_rolls_back_non_locked_commit_error():
    db = _RecordingSession(commit_error=RuntimeError("write failed"))

    with pytest.raises(RuntimeError, match="write failed"):
        await AsyncHistoryService(db).save_message("session-1", "user", "question")

    assert db.rollback_calls == 1


@pytest.mark.asyncio
async def test_save_interaction_rolls_back_non_locked_commit_error():
    db = _RecordingSession(commit_error=RuntimeError("write failed"))

    with pytest.raises(RuntimeError, match="write failed"):
        await AsyncHistoryService(db).save_interaction(
            "session-1", "question", "answer"
        )

    assert db.rollback_calls == 1
