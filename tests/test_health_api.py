"""Health & Readiness API tests"""
import pytest
from httpx import AsyncClient, ASGITransport
from unittest.mock import patch, MagicMock
from fastapi import FastAPI
import importlib.util
import os

_mod = None


def _get_module():
    global _mod
    if _mod is None:
        spec = importlib.util.spec_from_file_location(
            "app.api.routes.health",
            os.path.join(os.path.dirname(__file__), "..", "app", "api", "routes", "health.py"),
            submodule_search_locations=[]
        )
        _mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_mod)
    return _mod


@pytest.fixture
def app():
    mod = _get_module()
    app = FastAPI()
    app.include_router(mod.router, prefix="/api/v1")
    return app


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.mark.asyncio
async def test_health_check(client):
    resp = await client.get("/api/v1/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "healthy"
    assert "timestamp" in data
    assert data["service"] == "WebGIS AI Agent"


@pytest.mark.asyncio
async def test_readiness_check_healthy(client):
    mod = _get_module()
    with patch.object(mod, "_check_db", return_value=True), \
         patch.object(mod, "_check_llm", return_value=True):
        resp = await client.get("/api/v1/ready")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ready"] is True
        assert data["database"] == "connected"
        assert data["llm"] == "reachable"


@pytest.mark.asyncio
async def test_readiness_check_unhealthy(client):
    mod = _get_module()
    with patch.object(mod, "_check_db", return_value=False), \
         patch.object(mod, "_check_llm", return_value=False):
        resp = await client.get("/api/v1/ready")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ready"] is False
        assert data["database"] == "disconnected"
        assert data["llm"] == "unreachable"
