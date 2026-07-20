"""Chat API 测试

审计 T1：之前用 importlib bypass broken __init__.py。__init__.py 现在已健康
（循环 import 已修），改为直接 import。
"""
import pytest
from httpx import AsyncClient, ASGITransport
from unittest.mock import AsyncMock, patch, MagicMock
from fastapi import FastAPI

from app.api.routes import chat as _chat_mod
from app.api.routes.chat import router as _router


@pytest.fixture
def app():
    app = FastAPI()
    app.include_router(_router, prefix="/api")
    return app


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.mark.asyncio
async def test_list_tools(client):
    with patch.object(_chat_mod, "registry", MagicMock(get_schemas=lambda: [])):
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

    mock_registry = MagicMock()
    mock_registry.get_schemas.return_value = []
    mock_registry.tools = {}

    mock_engine = MagicMock()
    mock_engine.chat = AsyncMock(return_value={"session_id": "test-sid", "content": "你好！"})

    with patch.object(_chat_mod, "registry", mock_registry), \
         patch.object(_chat_mod, "engine", mock_engine):
        resp = await client.post("/api/chat/completions", json={"message": "你好"})
        assert resp.status_code == 200
        data = resp.json()
        assert "session_id" in data
        assert data["content"]


@pytest.mark.asyncio
async def test_clear_session(client):
    mock_engine = MagicMock()
    mock_engine.clear_session = AsyncMock(return_value=True)  # A2: clear_session 返回 bool
    with patch.object(_chat_mod, "engine", mock_engine):
        resp = await client.delete("/api/chat/sessions/test-session")
        assert resp.status_code == 200
