"""Regression tests for #522: anonymous sessions must not share a single
NULL-user_id eviction bucket, and cap eviction must go through the
clear_session protocol (not a bare delete that silently swallows FK errors).

The pre-fix defect: `_enforce_cap` bucketed on `Conversation.user_id`, which is
NULL for every anonymous session — so the 1001st anonymous session evicted the
globally-oldest anonymous session (any user's), and the raw delete bypassed the
clear_session protocol, so an in-flight turn's message INSERT hit a Postgres FK
IntegrityError that `_save_msg_async`'s broad except silently swallowed.

The fix: anonymous sessions bucket by SEC-08 `owner_token` (each anonymous
session owns a fresh token → per-session buckets, no cross-user eviction at
all), legacy NULL-token sessions share one grandfather bucket, and every
eviction runs the clear_session protocol (clearing marker + in-flight turn
cancel/quiesce + session-data reclaim).
"""
import os
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

# 必须在 import app.* 之前设置
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-sec08-32-chars-okxxxxx")
os.environ.setdefault("ENV", "development")

from app.models.db_model import Base, Conversation  # noqa: E402
from app.services.chat.execution_engine import ChatExecutionEngine  # noqa: E402
from app.services.history_service_async import AsyncHistoryService, MAX_SESSIONS  # noqa: E402
from app.api.routes import chat as chat_mod  # noqa: E402


@pytest_asyncio.fixture
async def db(tmp_path):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'cap522.db'}",
        connect_args={"check_same_thread": False},
    )
    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False)

    # Turn SQLite FK enforcement ON so the "silent FK swallow" scenario is
    # actually observable (SQLite ignores FKs by default).
    @event.listens_for(engine.sync_engine, "connect")
    def _fk_on(dbapi_conn, _):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with session_factory() as s:
        yield s
    await engine.dispose()


def _conv(session_id: str, owner_token=None, user_id=None, age_seconds: int = 0) -> Conversation:
    base = datetime.now(timezone.utc) - timedelta(seconds=age_seconds)
    return Conversation(
        id=session_id,
        user_id=user_id,
        owner_token=owner_token,
        title="会话",
        created_at=base,
        updated_at=base,
    )


@pytest.mark.asyncio
async def test_unique_token_anonymous_sessions_never_evict_each_other(db):
    """Production shape: every new anonymous session mints a fresh SEC-08
    owner_token, so no two anonymous sessions share a bucket. Creating far
    more than MAX_SESSIONS anonymous sessions must evict NOTHING (the old
    code collapsed them all into one IS NULL bucket and evicted the 1001st)."""
    svc = AsyncHistoryService(db)
    created = []
    for i in range(MAX_SESSIONS + 400):
        conv, made = await svc.get_or_create_conversation_with_created(
            f"anon-{i}", user_id=None
        )
        assert made is True
        assert conv.owner_token  # every new anon session has its own token
        created.append(conv.id)

    # Existence check via db.get: get_session() without the owner_token would
    # return None for a token-bearing anonymous session (SEC-08) even when the
    # row exists — that is the ownership guard, not eviction.
    for sid in created:
        assert await db.get(Conversation, sid) is not None, (
            f"anonymous session {sid} was evicted by another anonymous session "
            "— buckets must be per owner_token"
        )


@pytest.mark.asyncio
async def test_same_owner_token_bucket_evicts_own_oldest_only(db):
    """Sessions sharing one owner_token share one 1000-slot bucket; overflow
    evicts ONLY the oldest same-token session, never another token's."""
    svc = AsyncHistoryService(db)
    for i in range(MAX_SESSIONS + 1):
        db.add(_conv(f"tokA-{i}", owner_token="tok-A", age_seconds=MAX_SESSIONS + 1 - i))
    db.add(_conv("tokB-0", owner_token="tok-B"))
    await db.commit()

    evicted = await svc._enforce_cap(None, owner_token="tok-A")
    assert evicted == ["tokA-0"], "overflow must evict the oldest same-token session"
    await db.commit()  # the create path commits right after _enforce_cap

    # The other token's session and all remaining tok-A sessions survive.
    assert await db.get(Conversation, "tokB-0") is not None
    for i in range(1, MAX_SESSIONS + 1):
        assert await db.get(Conversation, f"tokA-{i}") is not None


@pytest.mark.asyncio
async def test_legacy_null_token_bucket_isolated_from_token_sessions(db):
    """Grandfathered NULL-token sessions fall in their own legacy bucket: a
    token-bearing session's overflow must not evict them, and legacy overflow
    evicts only the oldest legacy session."""
    svc = AsyncHistoryService(db)
    for i in range(MAX_SESSIONS + 1):
        db.add(_conv(f"legacy-{i}", owner_token=None, age_seconds=MAX_SESSIONS + 1 - i))
    db.add(_conv("token-0", owner_token="tok-Z"))
    await db.commit()

    # Token-bearing bucket at 1 → no eviction, legacy bucket untouched.
    assert await svc._enforce_cap(None, owner_token="tok-Z") == []
    # Legacy bucket overflows → evicts oldest legacy only.
    evicted = await svc._enforce_cap(None, owner_token=None)
    assert evicted == ["legacy-0"]
    await db.commit()  # the create path commits right after _enforce_cap
    assert await db.get(Conversation, "token-0") is not None


@pytest.mark.asyncio
async def test_authenticated_bucket_still_per_user(db):
    """Authenticated users keep the per-user_id bucket semantics (#498)."""
    from app.models.db_model import User

    db.add(User(id="user-1", username="u1", email="u1@example.com"))
    db.add(User(id="user-2", username="u2", email="u2@example.com"))
    # The model metadata has an insert-order cycle (users↔layers↔...), so the
    # unit of work falls back to alphabetical order and would INSERT
    # conversations before users → FK violation. Flush users first.
    await db.flush()
    svc = AsyncHistoryService(db)
    for i in range(MAX_SESSIONS + 1):
        db.add(_conv(f"u1-{i}", user_id="user-1", age_seconds=MAX_SESSIONS + 1 - i))
    db.add(_conv("u2-0", user_id="user-2"))
    await db.commit()

    assert await svc._enforce_cap("user-1") == ["u1-0"]
    await db.commit()  # the create path commits right after _enforce_cap
    assert await db.get(Conversation, "u2-0") is not None


class _ProtocolRecorder:
    """Stub engine that records protocol events and asserts the clearing
    marker is active while the in-flight turn is being cancelled."""

    def __init__(self):
        self.cancelled: list[str] = []
        self.marker_active_during_cancel: list[bool] = []

    async def cancel_inflight_turn(self, session_id: str):
        self.cancelled.append(session_id)
        self.marker_active_during_cancel.append(
            session_id in ChatExecutionEngine._clearing_sessions
        )


@pytest.mark.asyncio
async def test_eviction_runs_clear_session_protocol(db, monkeypatch):
    """Cap eviction must not be a bare delete: the evicted session goes
    through the clearing marker + in-flight-turn cancel + session-data
    reclaim, and the marker is cleared afterwards so a recreated session with
    the same id is not permanently write-suppressed. These are the exact
    methods the create path runs after committing the evicting transaction."""
    recorder = _ProtocolRecorder()
    monkeypatch.setattr(chat_mod, "engine", recorder)
    # E-2（#893）：engine 单例读取点已下沉 services/chat/engine_instance
    from app.services.chat import engine_instance as _holder
    monkeypatch.setattr(_holder, "_engine", recorder)

    svc = AsyncHistoryService(db)
    for i in range(MAX_SESSIONS + 1):
        db.add(_conv(f"proto-{i}", owner_token="tok-P", age_seconds=MAX_SESSIONS + 1 - i))
    await db.commit()

    # Same flow as get_or_create_conversation_with_created after commit:
    # enforce the cap, then run the protocol for each evicted session.
    evicted = await svc._enforce_cap(None, owner_token="tok-P")
    assert evicted == ["proto-0"]
    await db.commit()  # the create path commits right after _enforce_cap
    for evicted_id in evicted:
        await svc._run_eviction_protocol(evicted_id)

    assert recorder.cancelled == ["proto-0"]
    assert recorder.marker_active_during_cancel == [True], (
        "clearing marker must be active while the in-flight turn is quiesced "
        "(that is what suppresses the FK-violating writes)"
    )
    assert "proto-0" not in ChatExecutionEngine._clearing_sessions, (
        "clearing marker must be cleared after the protocol so a recreated "
        "session with the same id is not permanently write-suppressed"
    )
    # The DB row is gone; unrelated sessions untouched.
    assert await db.get(Conversation, "proto-0") is None
    assert await db.get(Conversation, "proto-1") is not None


@pytest.mark.asyncio
async def test_clearing_marker_suppresses_inflight_save(monkeypatch):
    """The write-suppression that the protocol relies on: while a session is
    marked as clearing, _save_msg_async must not open a DB session / write
    (this is what closes the silent FK-IntegrityError swallow window)."""
    from app.services.chat_engine import ChatEngine
    from app.tools.registry import ToolRegistry

    engine = ChatEngine(ToolRegistry())
    ChatExecutionEngine._clearing_sessions.add("evicted-session")
    try:
        async def _forbidden_db():
            raise AssertionError("DB write attempted while session is being cleared")

        monkeypatch.setattr("app.tools._utils.async_db_session", _forbidden_db)
        # Must return without raising and without touching the DB.
        await engine._save_msg_async("evicted-session", "user", "mid-turn content")
    finally:
        ChatExecutionEngine._clearing_sessions.discard("evicted-session")
