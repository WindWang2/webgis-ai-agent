"""Layer & Task API tests"""
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
            "app.api.routes.layer",
            os.path.join(os.path.dirname(__file__), "..", "app", "api", "routes", "layer.py"),
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
async def test_get_session_layer_data_not_found(client):
    mod = _get_module()
    with patch.object(mod.session_data_manager, "get", return_value=None):
        resp = await client.get("/api/v1/layers/data/ref-123", params={"session_id": "sess-1"})
        assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_session_layer_data_success(client):
    mod = _get_module()
    mock_data = {"type": "FeatureCollection", "features": []}
    with patch.object(mod.session_data_manager, "get", return_value=mock_data):
        resp = await client.get("/api/v1/layers/data/ref-123", params={"session_id": "sess-1"})
        assert resp.status_code == 200
        assert resp.json()["type"] == "FeatureCollection"


@pytest.mark.asyncio
async def test_get_layer_types(client):
    resp = await client.get("/api/v1/layer-types")
    assert resp.status_code == 200
    data = resp.json()
    assert "layer_types" in data
    assert "analysis_types" in data
    assert len(data["layer_types"]) >= 3
    assert len(data["analysis_types"]) >= 4


@pytest.mark.asyncio
async def test_get_session_layer_data_missing_session_id(client):
    resp = await client.get("/api/v1/layers/data/ref-123")
    assert resp.status_code == 422
