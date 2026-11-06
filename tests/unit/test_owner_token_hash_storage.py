"""The database contains verifiers; reading one cannot authenticate a caller."""
import hashlib
import importlib
import pytest
from types import SimpleNamespace

from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

from app.core.auth import owner_token_matches
from app.core.database import Base
from app.core.owner_tokens import OwnerTokenDigest
from app.models.db_model import Conversation, AnalysisTask


def test_session_and_job_verifiers_are_one_way_and_queryable():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    token = "fresh-anonymous-credential"
    expected = hashlib.sha256(token.encode()).hexdigest()
    try:
        with Session(engine) as db:
            db.add(Conversation(id="hashed-owner", owner_token=token))
            db.add(AnalysisTask(task_type="test", parameters={}, owner_token=token))
            db.commit()
            db.expunge_all()
            conversation = db.get(Conversation, "hashed-owner")
            assert isinstance(conversation.owner_token, OwnerTokenDigest)
            assert db.execute(text("SELECT owner_token FROM conversations")).scalar_one() == expected
            assert db.execute(text("SELECT owner_token FROM analysis_tasks")).scalar_one() == expected
            assert owner_token_matches(conversation.owner_token, token)
            assert not owner_token_matches(conversation.owner_token, expected)
            assert not owner_token_matches(conversation.owner_token, conversation.owner_token)
            assert db.execute(select(AnalysisTask).where(AnalysisTask.owner_token == token)).scalar_one().task_type == "test"
    finally:
        engine.dispose()


def test_migration_handles_string_and_integer_primary_keys_in_bounded_batches(monkeypatch):
    monkeypatch.setenv("OWNER_TOKEN_MIGRATION_QUIESCED", "1")
    engine = create_engine("sqlite://")
    module = importlib.import_module("migrations.versions.0102_owner_token_hashes")
    try:
        with engine.begin() as connection:
            connection.execute(text("CREATE TABLE conversations(id TEXT PRIMARY KEY, owner_token TEXT)"))
            connection.execute(text("CREATE TABLE analysis_tasks(id INTEGER PRIMARY KEY, owner_token TEXT)"))
            connection.execute(text("INSERT INTO conversations VALUES(:id,:token)"), [{"id": str(i).zfill(4), "token": "raw" + str(i)} for i in range(502)])
            connection.execute(text("INSERT INTO analysis_tasks VALUES(1,'job-raw')"))
            monkeypatch.setattr(module, "op", SimpleNamespace(get_bind=lambda: connection))
            module.upgrade()
            assert connection.execute(text("SELECT owner_token FROM conversations WHERE id='0501'")).scalar_one() == hashlib.sha256(b"raw501").hexdigest()
            assert connection.execute(text("SELECT owner_token FROM analysis_tasks")).scalar_one() == hashlib.sha256(b"job-raw").hexdigest()
            module.downgrade()
            assert connection.execute(text("SELECT COUNT(*) FROM conversations")).scalar_one() == 502
            assert connection.execute(text("SELECT COUNT(*) FROM conversations WHERE owner_token IS NOT NULL")).scalar_one() == 0
    finally:
        engine.dispose()


def test_only_creation_can_return_a_transport_credential():
    from app.api.routes.chat import _pi_stream_capability
    stored = OwnerTokenDigest("a" * 64)
    assert _pi_stream_capability(SimpleNamespace(owner_token=stored), True, None, None) is None
    assert _pi_stream_capability(SimpleNamespace(owner_token=stored, _issued_owner_token="new-token"), True, None, None) == "new-token"


def test_store_accepts_verified_server_verifier_but_not_a_transport_digest():
    from app.services.session_data_protocol import BaseSessionStore
    store = BaseSessionStore.__new__(BaseSessionStore)
    digest = hashlib.sha256(b"raw-token").hexdigest()
    metadata = {"owner_token_digest": digest}
    assert store._validate_owner_token(metadata, "raw-token") is None
    assert store._validate_owner_token(metadata, OwnerTokenDigest(digest)) is None
    assert store._validate_owner_token(metadata, digest).success is False


@pytest.mark.asyncio
async def test_resolver_requires_transport_secret_not_database_digest(monkeypatch):
    from unittest.mock import AsyncMock
    from app.services.spatial_decision.target_resolver import TargetAreaResolver
    from app.services import session_ownership
    from app.core import runtime_context
    monkeypatch.setattr(runtime_context, "current_runtime_context", lambda: None)
    raw = "owner-check-raw"
    digest = hashlib.sha256(raw.encode()).hexdigest()
    monkeypatch.setattr(session_ownership, "lookup_session_owner_token", AsyncMock(return_value=OwnerTokenDigest(digest)))
    store = SimpleNamespace(get=AsyncMock(return_value={"type": "Point", "coordinates": [1, 2]}))
    resolver = TargetAreaResolver(session_store=store)
    assert await resolver._try_resolve_session_ref("ref:data-test", "session", owner_token=raw) is not None
    store.get.reset_mock()
    assert await resolver._try_resolve_session_ref("ref:data-test", "session", owner_token=digest) is None
    store.get.assert_not_awaited()


@pytest.mark.asyncio
async def test_concurrent_rotation_rechecks_old_capability_and_preserves_mirror(tmp_path, monkeypatch):
    import asyncio
    from contextlib import asynccontextmanager
    from fastapi import HTTPException
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from app.api.routes.chat import rotate_session_owner_token
    from app.api import owner_token_rotation as rotation
    from app.core.owner_tokens import owner_token_digest

    lock = asyncio.Lock()
    @asynccontextmanager
    async def serialized(*args, **kwargs):
        async with lock:
            yield SimpleNamespace(lost=False)

    mirror = {}
    async def write(sid, key, value):
        mirror[key] = value
        await asyncio.sleep(0.01)
        return True

    monkeypatch.setattr(rotation, "session_lock_registry", SimpleNamespace(lock=serialized))
    monkeypatch.setattr(rotation.session_data_manager, "set_map_state", write)
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'rotation.db'}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        async with factory() as db:
            db.add(Conversation(id="rotate-real", owner_token="old-token"))
            await db.commit()
        async with factory() as a, factory() as b:
            ca = await a.get(Conversation, "rotate-real")
            cb = await b.get(Conversation, "rotate-real")
            outcomes = await asyncio.gather(
                rotate_session_owner_token("rotate-real", conv=ca, db=a, owner_token="old-token"),
                rotate_session_owner_token("rotate-real", conv=cb, db=b, owner_token="old-token"),
                return_exceptions=True,
            )
        accepted = [value for value in outcomes if isinstance(value, dict)]
        rejected = [value for value in outcomes if isinstance(value, HTTPException)]
        assert len(accepted) == len(rejected) == 1
        assert rejected[0].status_code == 404
        async with factory() as db:
            persisted = await db.get(Conversation, "rotate-real")
            assert owner_token_matches(persisted.owner_token, accepted[0]["owner_token"])
            assert not owner_token_matches(persisted.owner_token, "old-token")
            assert mirror["owner_token_digest"] == owner_token_digest(persisted.owner_token)
    finally:
        await engine.dispose()


def test_failed_rotation_reconciles_fresh_database_verifier(monkeypatch):
    import asyncio
    from contextlib import asynccontextmanager
    from unittest.mock import AsyncMock
    from fastapi import HTTPException
    import pytest
    from app.api import owner_token_rotation as rotation
    from app.core.owner_tokens import owner_token_digest

    @asynccontextmanager
    async def locked(*args, **kwargs):
        yield SimpleNamespace(lost=False)

    old = OwnerTokenDigest(owner_token_digest("old"))
    current = SimpleNamespace(user_id=None, owner_token=old)
    committed = OwnerTokenDigest(owner_token_digest("uncertain-committed"))
    db = SimpleNamespace(rollback=AsyncMock(), commit=AsyncMock(side_effect=RuntimeError("commit reply lost")), execute=AsyncMock(side_effect=[SimpleNamespace(scalar_one_or_none=lambda: current), SimpleNamespace(scalar_one_or_none=lambda: committed)]))
    mirror = AsyncMock(return_value=True)
    monkeypatch.setattr(rotation, "session_lock_registry", SimpleNamespace(lock=locked))
    monkeypatch.setattr(rotation.session_data_manager, "set_map_state", mirror)
    with pytest.raises(HTTPException) as error:
        asyncio.run(rotation.rotate_anonymous_credential("sid", db, owner_token="old"))
    assert error.value.status_code == 503
    assert mirror.await_args_list[-1].args == ("sid", "owner_token_digest", str(committed))
