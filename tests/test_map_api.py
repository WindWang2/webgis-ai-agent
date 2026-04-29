"""Map export API tests"""
import io
import os
import importlib.util
from unittest.mock import patch, MagicMock

import pytest
from fastapi import FastAPI
from httpx import AsyncClient, ASGITransport


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


# ─── PNG upload tests ─────────────────────────────────────────────────────────

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
        resp = await client.post(
            "/api/v1/export",
            files={"file": ("map.png", fake_content, "image/png")},
        )
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
        resp = await client.post(
            "/api/v1/export",
            files={"file": ("map.bmp", b"data", "image/bmp")},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["filename"].endswith(".png")


# ─── PDF export tests ─────────────────────────────────────────────────────────

def _make_tiny_png() -> bytes:
    """Return a minimal valid 1×1 white PNG for testing."""
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (1, 1), color=(255, 255, 255)).save(buf, format="PNG")
    return buf.getvalue()


@pytest.mark.asyncio
async def test_export_pdf_success(client):
    mod = _load_module()
    png_bytes = _make_tiny_png()
    with patch.object(mod, "EXPORT_DIR", "/tmp/test_exports"):
        resp = await client.post(
            "/api/v1/export/pdf",
            files={"file": ("map.png", png_bytes, "image/png")},
            data={"title": "测试专题图", "subtitle": "单元测试"},
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["filename"].endswith(".pdf")
    assert data["format"] == "pdf"
    assert "/api/v1/export/download/" in data["url"]


@pytest.mark.asyncio
async def test_export_pdf_invalid_image(client):
    """Non-image bytes should return 500."""
    mod = _load_module()
    with patch.object(mod, "EXPORT_DIR", "/tmp/test_exports"):
        resp = await client.post(
            "/api/v1/export/pdf",
            files={"file": ("bad.png", b"not-an-image", "image/png")},
        )
    assert resp.status_code == 500


@pytest.mark.asyncio
async def test_download_pdf_media_type(tmp_path):
    """Download endpoint returns application/pdf for .pdf files."""
    mod = _load_module()
    pdf_file = tmp_path / "test.pdf"
    pdf_file.write_bytes(b"%PDF-1.4 test")

    with patch.object(mod, "EXPORT_DIR", str(tmp_path)):
        app = FastAPI()
        app.include_router(mod.router, prefix="/api/v1")
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.get("/api/v1/export/download/test.pdf")
    assert resp.status_code == 200
    assert "pdf" in resp.headers["content-type"]
