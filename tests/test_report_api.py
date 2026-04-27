"""Report API tests"""
import pytest
from httpx import AsyncClient, ASGITransport
from unittest.mock import patch, MagicMock, AsyncMock
from fastapi import FastAPI
import importlib.util
import os


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "app.api.routes.report",
        os.path.join(os.path.dirname(__file__), "..", "app", "api", "routes", "report.py"),
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
async def test_create_report_unsupported_format(client):
    resp = await client.post("/api/v1/reports", json={
        "session_id": "sess-1", "format": "docx"
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is False
    assert "不支持的格式" in data["message"]


def test_allowed_formats():
    """Verify ALLOWED_FORMATS contains expected formats"""
    mod = _load_module()
    assert "pdf" in mod.ALLOWED_FORMATS
    assert "html" in mod.ALLOWED_FORMATS
    assert "markdown" in mod.ALLOWED_FORMATS
    assert "md" in mod.ALLOWED_FORMATS
    assert "docx" not in mod.ALLOWED_FORMATS


def test_serialize_report():
    """Test _serialize_report helper"""
    mod = _load_module()
    mock_report = MagicMock()
    mock_report.id = "r-1"
    mock_report.session_id = "s-1"
    mock_report.title = "Test"
    mock_report.format = "pdf"
    mock_report.status = "completed"
    mock_report.file_size = 1024
    mock_report.share_code = None
    mock_report.share_expires_at = None
    mock_report.error_message = None
    mock_report.created_at = MagicMock()
    mock_report.created_at.isoformat.return_value = "2026-01-01T00:00:00"

    result = mod._serialize_report(mock_report)
    assert result["id"] == "r-1"
    assert result["status"] == "completed"
    assert result["download_url"] == "/api/v1/reports/r-1/download"


def test_media_type():
    mod = _load_module()
    assert mod._media_type("pdf") == "application/pdf"
    assert mod._media_type("html") == "text/html"
    assert mod._media_type("markdown") == "text/markdown"


def test_file_ext():
    mod = _load_module()
    assert mod._file_ext("pdf") == "pdf"
    assert mod._file_ext("html") == "html"
    assert mod._file_ext("markdown") == "md"
    assert mod._file_ext("md") == "md"
