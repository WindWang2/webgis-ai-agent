"""Tests for chat history API routes."""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from unittest.mock import MagicMock, patch, AsyncMock
from datetime import datetime

from app.api.routes import chat as _chat_mod


def make_conv(id_, title, updated):
    c = MagicMock()
    c.id = id_
    c.title = title
    c.created_at = datetime(2026, 1, 1)
    c.updated_at = updated
    c.messages = []
    return c


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(_chat_mod.router, prefix="/api/v1")
    return TestClient(app)


BASE = "/api/v1/chat"


def test_list_sessions_returns_json(client):
    conv = make_conv("s1", "Test", datetime(2026, 4, 10))
    with patch.object(_chat_mod, "AsyncHistoryService") as MockHS:
        mock_svc = MagicMock()
        MockHS.return_value = mock_svc
        mock_svc.list_sessions = AsyncMock(return_value=[conv])
        resp = client.get(f"{BASE}/sessions")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["sessions"]) == 1
    assert data["sessions"][0]["id"] == "s1"
    assert data["sessions"][0]["title"] == "Test"
    assert "updatedAt" in data["sessions"][0]


def test_get_session_detail(client):
    conv = make_conv("s1", "Test", datetime(2026, 4, 10))
    msg = MagicMock()
    msg.id = 1
    msg.role = "user"
    msg.content = "hello"
    msg.tool_calls = None
    msg.tool_result = None
    msg.created_at = datetime(2026, 4, 10)
    conv.messages = [msg]

    # #525: the guard returns a metadata-only Conversation; the route loads
    # messages explicitly via db.refresh — stub that refresh here.
    from app.core.database import get_async_db

    class _FakeDb:
        async def refresh(self, instance, attribute_names=None):
            return instance

    async def _override_db():
        yield _FakeDb()

    client.app.dependency_overrides[get_async_db] = _override_db
    with patch("app.core.auth.verify_session_owner", AsyncMock(return_value=conv)):
        resp = client.get(f"{BASE}/sessions/s1")
    assert resp.status_code == 200
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == "s1"
    assert len(data["messages"]) == 1
    assert data["messages"][0]["role"] == "user"


def test_get_session_detail_not_found(client):
    with patch("app.core.auth.verify_session_owner", AsyncMock(side_effect=_chat_mod.HTTPException(status_code=404, detail="Session not found"))):
        resp = client.get(f"{BASE}/sessions/nonexistent")
    assert resp.status_code == 404


def test_delete_session(client):
    mock_engine = MagicMock()
    mock_engine.clear_session = AsyncMock(return_value=True)  # A2: 返回 bool
    conv = make_conv("s1", "Test", datetime(2026, 4, 10))
    with patch.object(_chat_mod, "engine", mock_engine), patch("app.core.auth.verify_session_owner", AsyncMock(return_value=conv)):
        resp = client.delete(f"{BASE}/sessions/s1")
    assert resp.status_code == 200
    # A2: 带 user_id kwarg；SEC-08: 带 owner_token kwarg（无头时为 None）
    mock_engine.clear_session.assert_awaited_once_with("s1", user_id="anonymous", owner_token=None)
