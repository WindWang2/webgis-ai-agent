"""Security test: error responses must not leak internal details to clients."""
import pytest
from unittest.mock import patch, MagicMock
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.api.routes.chat import router as chat_router


@pytest.fixture
def app():
    app = FastAPI()
    app.include_router(chat_router, prefix="/api/v1")
    return app


class TestChatErrorSanitization:
    """Chat endpoint must return generic error, not str(e)."""

    @patch("app.api.routes.chat.get_engine")
    def test_chat_exception_hides_internal_details(self, mock_get_engine, app):
        """When chat raises, 500 detail must NOT contain the exception message."""
        mock_engine = MagicMock()
        mock_engine.chat.side_effect = RuntimeError("secret DB connection string: postgres://admin:pw@db")
        mock_get_engine.return_value = mock_engine

        client = TestClient(app)
        resp = client.post("/api/v1/chat/completions", json={
            "message": "hello",
            "session_id": "test",
        })

        assert resp.status_code == 500
        detail = resp.json().get("detail", "")
        # Must NOT contain leaked internals
        assert "postgres://" not in detail
        assert "admin" not in detail
        assert "pw" not in detail

    @patch("app.api.routes.chat.get_engine")
    def test_stream_exception_hides_internal_details(self, mock_get_engine, app):
        """SSE stream error must NOT leak exception message."""
        async def failing_stream(*a, **kw):
            raise RuntimeError("secret API key: sk-abc123")
            yield  # make it async generator

        mock_engine = MagicMock()
        mock_engine.chat_stream = failing_stream
        mock_get_engine.return_value = mock_engine

        client = TestClient(app)
        with client.stream("POST", "/api/v1/chat/stream", json={
            "message": "hello",
            "session_id": "test",
        }) as resp:
            body = resp.read().decode()
            assert "sk-abc123" not in body
