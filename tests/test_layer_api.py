"""Layer & Task API tests"""
import pytest
from httpx import AsyncClient, ASGITransport
from unittest.mock import patch, MagicMock
from fastapi import FastAPI

from app.api.routes import layer as _mod


@pytest.fixture
def app(monkeypatch):
    """跨租户守卫 _verify_session_owner 在隔离测试里依赖真 DB；
    stub 成 always-pass（跨租户隔离由 test_cross_tenant_isolation 覆盖）。"""
    async def _noop_verify(session_id, user_id):
        return None
    monkeypatch.setattr(_mod, "_verify_session_owner", _noop_verify)

    app = FastAPI()
    app.include_router(_mod.router, prefix="/api/v1")
    return app


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


_VALID_SID = "session-aaaaaaaaaaaaaaaa"  # >= min_length=8


@pytest.mark.asyncio
async def test_get_session_layer_data_not_found(client):
    with patch.object(_mod.session_data_manager, "get", return_value=None):
        resp = await client.get("/api/v1/layers/data/ref-123", params={"session_id": _VALID_SID})
        assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_session_layer_data_success(client):
    mock_data = {"type": "FeatureCollection", "features": []}
    with patch.object(_mod.session_data_manager, "get", return_value=mock_data):
        resp = await client.get("/api/v1/layers/data/ref-123", params={"session_id": _VALID_SID})
        assert resp.status_code == 200
        assert resp.json()["type"] == "FeatureCollection"


@pytest.mark.asyncio
async def test_get_session_layer_data_rejects_short_session_id(client):
    """安全：session_id 过短应被 422 拒绝（能力令牌熵不足）。"""
    resp = await client.get("/api/v1/layers/data/ref-123", params={"session_id": "abc"})
    assert resp.status_code == 422


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
