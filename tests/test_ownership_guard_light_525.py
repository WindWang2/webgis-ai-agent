"""Regression tests for #525: the ownership guard must not selectinload the
full message collection.

The pre-fix defect: `AsyncHistoryService.get_session` (used by
`verify_session_owner` / `require_owned_session` — ~30 guard sites incl. the
3s task-center poll) ran
`select(Conversation).where(...).options(selectinload(Conversation.messages))`,
so every 3s poll paid O(messages) full-row transfers + JSON decode, growing
linearly with conversation length.

The fix: guards use `get_session_meta` (Conversation row only, no message
collection); the one consumer that genuinely needs messages
(`GET /chat/sessions/{id}`) loads them explicitly.

Tests:
  1. measurable query-count regression: N ownership verifies emit no
     `SELECT ... FROM messages`.
  2. ownership contract: meta guard returns identical outcomes to the full
     get_session for owner/non-owner/anonymous/wrong-token/grandfather/miss.
  3. the detail endpoint still returns messages.
"""
import os

import pytest
import pytest_asyncio
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

# 必须在 import app.* 之前设置
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-sec08-32-chars-okxxxxx")
os.environ.setdefault("ENV", "development")

from app.models.db_model import Base, Conversation, Message, User  # noqa: E402
from app.services.history_service_async import AsyncHistoryService  # noqa: E402
from app.core.auth import verify_session_owner  # noqa: E402


@pytest_asyncio.fixture
async def db_factory(tmp_path):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'guard525.db'}",
        connect_args={"check_same_thread": False},
    )
    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine, session_factory
    await engine.dispose()


@pytest_asyncio.fixture
async def session_factory(db_factory):
    _, sf = db_factory
    return sf


async def _seed(db, *, messages: int = 0):
    """Seed user-1 + conversation c1 (user-1) with N messages, plus an
    anonymous token session and a grandfather (NULL-token) session."""
    # The model metadata has an insert-order cycle (users↔layers↔...), so the
    # unit of work falls back to alphabetical order and would INSERT
    # conversations before users → FK violation. Flush users first.
    db.add(User(id="user-1", username="u1", email="u1@example.com"))
    db.add(User(id="user-2", username="u2", email="u2@example.com"))
    await db.flush()
    db.add(Conversation(id="c1", user_id="user-1", title="会话"))
    db.add(Conversation(id="c2", user_id="user-2", title="会话"))
    db.add(Conversation(id="anon-tok", user_id=None, owner_token="secret-tok", title="anon"))
    db.add(Conversation(id="anon-legacy", user_id=None, owner_token=None, title="legacy"))
    await db.flush()
    for i in range(messages):
        db.add(
            Message(
                conversation_id="c1",
                role="user" if i % 2 == 0 else "assistant",
                content=f"message content {i} " + "x" * 200,
            )
        )
    await db.commit()


# ─── 1. measurable regression: guard emits no SELECT against messages ────────


@pytest.mark.asyncio
async def test_ownership_guard_emits_no_messages_select(db_factory):
    """N ownership verifies on a 500-message conversation must emit no
    `SELECT ... FROM messages` (the pre-fix guard loaded all 500 rows every
    call; the 3s task-center poll re-triggers it)."""
    engine, session_factory = db_factory
    async with session_factory() as db:
        await _seed(db, messages=500)

    statements: list[str] = []

    def _record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", _record)
    try:
        async with session_factory() as db:
            for _ in range(10):
                conv = await verify_session_owner(db, "c1", user_id="user-1")
                assert conv is not None and conv.id == "c1"
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", _record)

    msg_selects = [s for s in statements if "from messages" in s.lower()]
    assert msg_selects == [], (
        f"ownership guard emitted {len(msg_selects)} SELECTs against messages: "
        f"{msg_selects[:3]}"
    )
    # Sanity: the guard still ran real queries (not a no-op).
    conv_selects = [s for s in statements if "from conversations" in s.lower()]
    assert conv_selects, "guard emitted no conversations SELECT — test is vacuous"

    # Positive control: the same instrumentation must catch the full
    # get_session's message load — otherwise the harness itself is blind.
    statements.clear()
    event.listen(engine.sync_engine, "before_cursor_execute", _record)
    try:
        async with session_factory() as db:
            conv = await AsyncHistoryService(db).get_session("c1", user_id="user-1")
            assert conv is not None and len(conv.messages) == 500
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", _record)

    positive_msg_selects = [s for s in statements if "from messages" in s.lower()]
    assert positive_msg_selects, (
        "positive control failed: full get_session should load messages — "
        "the SQL recorder is not working"
    )


@pytest.mark.asyncio
async def test_get_session_full_still_loads_messages(session_factory):
    """The full get_session (used by genuine history consumers) must still
    return the message collection — the fix must not have removed it."""
    async with session_factory() as db:
        await _seed(db, messages=500)
        conv = await AsyncHistoryService(db).get_session("c1", user_id="user-1")
        assert conv is not None
        assert len(conv.messages) == 500


# ─── 2. ownership contract: meta guard == full get_session outcomes ──────────


@pytest.mark.asyncio
async def test_guard_contract_matches_full_get_session(session_factory):
    async with session_factory() as db:
        await _seed(db)

        async def outcomes(use_meta: bool):
            svc = AsyncHistoryService(db)
            getter = svc.get_session_meta if use_meta else svc.get_session
            return {
                "owner": await getter("c1", user_id="user-1"),
                "non_owner": await getter("c1", user_id="user-2"),
                "anonymous_caller": await getter("c1", user_id=None),
                "wrong_user_str": await getter("c1", user_id="someone-else"),
                "anon_token_ok": await getter("anon-tok", owner_token="secret-tok"),
                "anon_token_wrong": await getter("anon-tok", owner_token="wrong"),
                "anon_token_missing": await getter("anon-tok"),
                "anon_legacy_ok": await getter("anon-legacy"),
                "miss": await getter("does-not-exist", user_id="user-1"),
            }

        meta = await outcomes(use_meta=True)
        full = await outcomes(use_meta=False)

    for key in meta:
        assert (meta[key] is not None) == (full[key] is not None), (
            f"guard outcome diverged from full get_session for {key}: "
            f"meta={meta[key] is not None}, full={full[key] is not None}"
        )
    # Sanity: the matrix exercises both allow and deny branches.
    assert meta["owner"] is not None
    assert meta["non_owner"] is None
    assert meta["anon_token_wrong"] is None
    # #1109: legacy NULL/NULL rows are fail-closed on BOTH paths (migration
    # g1109 mints random owner_tokens for existing rows).
    assert meta["anon_legacy_ok"] is None
    assert meta["miss"] is None


@pytest.mark.asyncio
async def test_verify_session_owner_uniform_404(session_factory):
    """not-found vs unauthorized → uniform 404 (no existence leak)."""
    async with session_factory() as db:
        await _seed(db)
        with pytest.raises(HTTPException) as e1:
            await verify_session_owner(db, "does-not-exist", user_id="user-1")
        with pytest.raises(HTTPException) as e2:
            await verify_session_owner(db, "c1", user_id="user-2")
        assert e1.value.status_code == 404
        assert e2.value.status_code == 404


# ─── 3. GET /chat/sessions/{id} still returns messages ──────────────────────


@pytest_asyncio.fixture
async def app_and_db(db_factory, monkeypatch):
    engine, session_factory = db_factory

    from app.core.database import get_async_db
    from app.tools import _utils
    from app.api.routes import chat as chat_routes

    async def override_get_async_db():
        async with session_factory() as s:
            yield s

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def override_async_db_session():
        async with session_factory() as s:
            try:
                yield s
                await s.commit()
            except Exception:
                await s.rollback()
                raise

    monkeypatch.setattr(_utils, "async_db_session", override_async_db_session)
    monkeypatch.setattr("app.api.routes.chat.async_db_session", override_async_db_session)

    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(chat_routes.router, prefix="/api/v1")
    app.dependency_overrides[get_async_db] = override_get_async_db
    yield app, session_factory


@pytest.mark.asyncio
async def test_session_detail_route_returns_messages(app_and_db):
    """The route that genuinely needs messages (GET /chat/sessions/{id}) must
    still return them after the guard went metadata-only."""
    app, session_factory = app_and_db
    async with session_factory() as db:
        await _seed(db, messages=10)

    from app.core.auth import create_access_token

    token = create_access_token({"sub": "user-1", "username": "u1", "role": "viewer"})

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/api/v1/chat/sessions/c1",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["messages"]) == 10, "session detail must still return messages"
    assert body["messages"][0]["role"] in ("user", "assistant")


