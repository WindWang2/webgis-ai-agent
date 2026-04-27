"""Knowledge API tests"""
import pytest
from httpx import AsyncClient, ASGITransport
from unittest.mock import patch, AsyncMock, MagicMock
from fastapi import FastAPI
import importlib.util
import os


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "app.api.routes.knowledge",
        os.path.join(os.path.dirname(__file__), "..", "app", "api", "routes", "knowledge.py"),
        submodule_search_locations=[]
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def app():
    mod = _load_module()
    app = FastAPI()
    app.include_router(mod.router, prefix="/api/v1")
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
    mod = _load_module()
    with patch.object(mod.rag_service, "add_document", new_callable=AsyncMock,
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
    mod = _load_module()
    with patch.object(mod.rag_service, "add_document", new_callable=AsyncMock,
                      return_value={"error": "FAISS not initialized"}):
        resp = await client.post("/api/v1/knowledge/documents", json={
            "title": "Test", "content": "Some content"
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is False


@pytest.mark.asyncio
async def test_semantic_search_success(client):
    mod = _load_module()
    mock_results = [{"text": "result 1", "score": 0.9}]
    with patch.object(mod.rag_service, "semantic_search", new_callable=AsyncMock,
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
