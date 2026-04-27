"""Tests for chat history API routes."""
import os
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from unittest.mock import MagicMock, patch
from datetime import datetime
import importlib.util


def _load_chat_module():
    spec = importlib.util.spec_from_file_location(
        "app.api.routes.chat",
        os.path.join(os.path.dirname(__file__), "..", "app", "api", "routes", "chat.py"),
        submodule_search_locations=[]
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_chat_mod = None


def _get_chat_mod():
    global _chat_mod
    if _chat_mod is None:
        _chat_mod = _load_chat_module()
    return _chat_mod


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
    mod = _get_chat_mod()
    app = FastAPI()
    app.include_router(mod.router, prefix="/api/v1")
    return TestClient(app)


BASE = "/api/v1/chat"


def test_list_sessions_returns_json(client):
    conv = make_conv("s1", "Test", datetime(2026, 4, 10))
    mod = _get_chat_mod()
    with patch.object(mod, "HistoryService") as MockHS:
        mock_svc = MagicMock()
        MockHS.return_value = mock_svc
        mock_svc.list_sessions.return_value = [conv]
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
    mod = _get_chat_mod()
    with patch.object(mod, "HistoryService") as MockHS:
        mock_svc = MagicMock()
        MockHS.return_value = mock_svc
        mock_svc.get_session.return_value = conv
        resp = client.get(f"{BASE}/sessions/s1")
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == "s1"
    assert len(data["messages"]) == 1
    assert data["messages"][0]["role"] == "user"


def test_get_session_detail_not_found(client):
    mod = _get_chat_mod()
    with patch.object(mod, "HistoryService") as MockHS:
        mock_svc = MagicMock()
        MockHS.return_value = mock_svc
        mock_svc.get_session.return_value = None
        resp = client.get(f"{BASE}/sessions/nonexistent")
    assert resp.status_code == 404


def test_delete_session(client):
    mod = _get_chat_mod()
    with patch.object(mod.engine, "clear_session") as mock_clear:
        resp = client.delete(f"{BASE}/sessions/s1")
    assert resp.status_code == 200
    mock_clear.assert_called_once_with("s1")
