"""Knowledge API tests"""
import pytest
from httpx import AsyncClient, ASGITransport
from unittest.mock import patch, AsyncMock, MagicMock
from fastapi import FastAPI

from app.api.routes import knowledge as _mod
from app.core.auth import get_current_user

_mock_user = {"user_id": "test-user"}


@pytest.fixture
def app():
    app = FastAPI()
    app.dependency_overrides[get_current_user] = lambda: _mock_user
    app.include_router(_mod.router, prefix="/api/v1")
    return app


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.mark.asyncio
async def test_add_document_empty_content(client):
    resp = await client.post("/api/v1/knowledge/documents", json={
        "title": "Test", "content": "   "
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is False


@pytest.mark.asyncio
async def test_add_document_success(client):
    with patch.object(_mod.rag_service, "add_document", new_callable=AsyncMock,
                      return_value={"document_id": "doc-1", "chunk_count": 3, "status": "indexed"}):
        resp = await client.post("/api/v1/knowledge/documents", json={
            "title": "Test", "content": "Some content"
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["data"]["document_id"] == "doc-1"


@pytest.mark.asyncio
async def test_add_document_service_error(client):
    with patch.object(_mod.rag_service, "add_document", new_callable=AsyncMock,
                      return_value={"error": "FAISS not initialized"}):
        resp = await client.post("/api/v1/knowledge/documents", json={
            "title": "Test", "content": "Some content"
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is False


@pytest.mark.asyncio
async def test_semantic_search_success(client):
    mock_results = [{"text": "result 1", "score": 0.9}]
    with patch.object(_mod.rag_service, "semantic_search", new_callable=AsyncMock,
                      return_value=mock_results):
        resp = await client.get("/api/v1/knowledge/search", params={"q": "test query"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert len(data["data"]["results"]) == 1


@pytest.mark.asyncio
async def test_semantic_search_empty_query(client):
    resp = await client.get("/api/v1/knowledge/search", params={"q": "   "})
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is False
