"""Upload API tests"""
import pytest
from httpx import AsyncClient, ASGITransport
from unittest.mock import patch, MagicMock, AsyncMock
from fastapi import FastAPI
import importlib.util
import os


_mod = None


def _get_module():
    global _mod
    if _mod is None:
        spec = importlib.util.spec_from_file_location(
            "app.api.routes.upload",
            os.path.join(os.path.dirname(__file__), "..", "app", "api", "routes", "upload.py"),
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
async def test_upload_unsupported_format(client):
    resp = await client.post("/api/v1/upload", files={"files": ("test.txt", b"hello", "text/plain")})
    assert resp.status_code == 400
    assert "不支持" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_upload_raster_too_large(client):
    mod = _get_module()
    from app.services.data_parser import MAX_RASTER_SIZE
    # Create a fake file just over the limit
    big_content = b"x" * (MAX_RASTER_SIZE + 1)
    with patch("builtins.open", MagicMock()), \
         patch.object(mod, "get_upload_dir", return_value="/tmp/test"):
        resp = await client.post("/api/v1/upload",
                                files={"files": ("big.tif", big_content, "image/tiff")})
        assert resp.status_code == 400
        assert "超过限制" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_upload_no_files(client):
    resp = await client.post("/api/v1/upload")
    assert resp.status_code == 422
