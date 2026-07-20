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
    with patch.object(_chat_mod, "AsyncHistoryService") as MockHS:
        mock_svc = MagicMock()
        MockHS.return_value = mock_svc
        mock_svc.get_session = AsyncMock(return_value=conv)
        resp = client.get(f"{BASE}/sessions/s1")
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == "s1"
    assert len(data["messages"]) == 1
    assert data["messages"][0]["role"] == "user"


def test_get_session_detail_not_found(client):
    with patch.object(_chat_mod, "AsyncHistoryService") as MockHS:
        mock_svc = MagicMock()
        MockHS.return_value = mock_svc
        mock_svc.get_session = AsyncMock(return_value=None)
        resp = client.get(f"{BASE}/sessions/nonexistent")
    assert resp.status_code == 404


def test_delete_session(client):
    mock_engine = MagicMock()
    mock_engine.clear_session = AsyncMock(return_value=True)  # A2: 返回 bool
    with patch.object(_chat_mod, "engine", mock_engine):
        resp = client.delete(f"{BASE}/sessions/s1")
    assert resp.status_code == 200
    # A2: 现在带 user_id kwarg
    mock_engine.clear_session.assert_awaited_once_with("s1", user_id="anonymous")
