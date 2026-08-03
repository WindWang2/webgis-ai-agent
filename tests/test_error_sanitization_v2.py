"""Security test: error responses must not leak internal details to clients."""
import pytest
from unittest.mock import patch, MagicMock
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.api.routes.chat import router as chat_router
from app.core.database import get_async_db


@pytest.fixture
def app(tmp_path):
    """Bare chat app with an isolated SQLite session.

    chat_completions / chat_stream take `Depends(get_async_db)` for the
    session-ownership guard (S31/S32/SEC-08). The global async engine must NOT
    be used here: it is module-level with a Postgres QueuePool, and asyncpg
    connections are bound to the loop that created them — TestClient runs its
    own loop, so a pooled connection left by an earlier test's loop raises
    "another operation is in progress". Bind a per-test aiosqlite engine
    instead; these tests are about error sanitization, not DB behaviour.
    """
    from sqlalchemy import create_engine
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    from app.models.db_model import Base

    db_file = tmp_path / "sanitization.db"

    # Create the schema synchronously: these tests build TestClient without a
    # context manager, so startup events never fire.
    sync_engine = create_engine(f"sqlite:///{db_file}")
    Base.metadata.create_all(bind=sync_engine)
    sync_engine.dispose()

    engine = create_async_engine(
        f"sqlite+aiosqlite:///{db_file}",
        poolclass=NullPool,
        connect_args={"check_same_thread": False},
    )
    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False)

    async def override_get_async_db():
        async with session_factory() as db:
            yield db

    app = FastAPI()
    app.include_router(chat_router, prefix="/api/v1")
    app.dependency_overrides[get_async_db] = override_get_async_db
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
