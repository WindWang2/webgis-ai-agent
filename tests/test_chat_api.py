"""Chat API 测试

Uses importlib to bypass broken __init__.py (health.py issue).
Run with: python -m pytest tests/test_chat_api.py -v --noconftest
"""
import pytest
from httpx import AsyncClient, ASGITransport
from unittest.mock import AsyncMock, patch, MagicMock
from fastapi import FastAPI
import importlib.util
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Load chat module directly without triggering __init__.py
_spec = importlib.util.spec_from_file_location(
    "app.api.routes.chat",
    os.path.join(os.path.dirname(__file__), "..", "app", "api", "routes", "chat.py"),
    submodule_search_locations=[]
)
_chat_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_chat_mod)
router = _chat_mod.router


@pytest.fixture
def app():
    app = FastAPI()
    app.include_router(router, prefix="/api")
    return app


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.mark.asyncio
async def test_list_tools(client):
    resp = await client.get("/api/chat/tools")
    assert resp.status_code == 200
    data = resp.json()
    assert "tools" in data


@pytest.mark.asyncio
async def test_chat_completions(client):
    mock_msg = MagicMock()
    mock_msg.content = "你好！"
    mock_msg.tool_calls = None
    mock_response = MagicMock()
    mock_response.choices = [MagicMock(message=mock_msg)]

    with patch.object(_chat_mod.engine.client.chat.completions, "create", new_callable=AsyncMock, return_value=mock_response):
        resp = await client.post("/api/chat/completions", json={"message": "你好"})
        assert resp.status_code == 200
        data = resp.json()
        assert "session_id" in data
        assert data["content"]


@pytest.mark.asyncio
async def test_clear_session(client):
    resp = await client.delete("/api/chat/sessions/test-session")
    assert resp.status_code == 200
