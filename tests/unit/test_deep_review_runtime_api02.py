"""API-02: non-streaming anonymous chat must return (and persist) owner_token.

The Pi branch of POST /chat/completions persisted the transcript with
``get_or_create_conversation`` and dropped the minted capability, so a second
anonymous turn on the returned session_id failed the ownership guard
(404). Streaming-parity fix: get-or-create WITH ``created``, mint capability
via ``_pi_stream_capability``, write the one-way digest, return the token.

Test drives the route function directly against in-memory SQLite: two
sequential anonymous requests must both succeed, the second presenting the
token returned by the first.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.api.routes.chat as chat_mod
from app.api.routes.chat import ChatRequest, chat_completions
from app.models.db_model import Base


class _FakeBridge:
    async def prompt(self, message, session_id=None, **kwargs):
        return {"sessionId": session_id, "content": f"echo:{message}"}


@pytest.fixture()
async def session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


@pytest.fixture()
def patched_route(monkeypatch, session_factory):
    @asynccontextmanager
    async def fake_async_db_session():
        async with session_factory() as db:
            try:
                yield db
                await db.commit()
            except Exception:
                await db.rollback()
                raise

    bridge = _FakeBridge()
    monkeypatch.setattr(chat_mod, "USE_NEW_AGENT", True)
    monkeypatch.setattr(
        chat_mod, "_ensure_pi_bridge_available", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(chat_mod, "_pi_turn_bridge", lambda _sid: bridge)
    monkeypatch.setattr(
        chat_mod, "_record_frontend_cartographic_observation", AsyncMock()
    )
    monkeypatch.setattr(
        chat_mod, "_build_cartography_turn_context", AsyncMock(return_value="")
    )
    monkeypatch.setattr(
        chat_mod, "_build_situation_env_block", AsyncMock(return_value="")
    )
    monkeypatch.setattr(chat_mod, "harvest_project_memory", AsyncMock())
    monkeypatch.setattr(chat_mod, "async_db_session", fake_async_db_session)
    monkeypatch.setattr(
        "app.services.gis_harness.map_completion.maybe_finalize_map_product",
        AsyncMock(),
    )
    return session_factory


@pytest.mark.asyncio
async def test_sequential_anonymous_turns_keep_owner_token(patched_route):
    session_factory = patched_route

    async with session_factory() as db:
        resp1 = await chat_completions(
            ChatRequest(message="first turn", session_id=None),
            request=MagicMock(),
            _user={},
            owner_token=None,
            db=db,
        )

    assert resp1.session_id
    assert resp1.owner_token, "API-02: minted owner_token must be returned"
    assert resp1.content == "echo:first turn"

    async with session_factory() as db:
        resp2 = await chat_completions(
            ChatRequest(message="second turn", session_id=resp1.session_id),
            request=MagicMock(),
            _user={},
            owner_token=resp1.owner_token,
            db=db,
        )

    assert resp2.session_id == resp1.session_id
    assert resp2.content == "echo:second turn"
