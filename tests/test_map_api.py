"""Map export API tests"""
import pytest
from httpx import AsyncClient, ASGITransport
from unittest.mock import patch, MagicMock
from fastapi import FastAPI
import importlib.util
import os
import io


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "app.api.routes.map",
        os.path.join(os.path.dirname(__file__), "..", "app", "api", "routes", "map.py"),
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
async def test_upload_map_export_no_filename(client):
    resp = await client.post("/api/v1/export", files={"file": ("", b"data")})
    assert resp.status_code in (400, 422)


@pytest.mark.asyncio
async def test_upload_map_export_success(client):
    mod = _load_module()
    fake_content = b"fake-png-data"
    with patch("builtins.open", MagicMock()), \
         patch.object(mod, "EXPORT_DIR", "/tmp/test_exports"):
        resp = await client.post("/api/v1/export",
                                files={"file": ("map.png", fake_content, "image/png")})
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["filename"].endswith(".png")
        assert "url" in data


@pytest.mark.asyncio
async def test_download_map_export_not_found(client):
    mod = _load_module()
    with patch.object(mod, "EXPORT_DIR", "/tmp/nonexistent_exports"):
        resp = await client.get("/api/v1/export/download/nonexistent.png")
        assert resp.status_code == 404


@pytest.mark.asyncio
async def test_upload_map_export_invalid_ext_becomes_png(client):
    mod = _load_module()
    with patch("builtins.open", MagicMock()), \
         patch.object(mod, "EXPORT_DIR", "/tmp/test_exports"):
        resp = await client.post("/api/v1/export",
                                files={"file": ("map.bmp", b"data", "image/bmp")})
        assert resp.status_code == 200
        data = resp.json()
        assert data["filename"].endswith(".png")
