"""#2198: canonical JWT identities survive session checks and durable submissions."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.api.routes import jobs, raster_sampling, skill_jobs
from app.core import database
from app.models.db_model import AnalysisTask, Base, Conversation
from app.services.data_operations import runner


@pytest.fixture()
def actor_db(tmp_path, monkeypatch):
    file = tmp_path / "actors.db"
    engine = create_engine(f"sqlite:///{file}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    ae = create_async_engine(f"sqlite+aiosqlite:///{file}")
    afactory = async_sessionmaker(bind=ae)
    monkeypatch.setattr(database, "SessionLocal", factory)
    with factory() as db:
        db.add(Conversation(id="session-a", user_id="a", title="A"))
        db.add(Conversation(id="anonymous-session", owner_token="test-owner-token", title="Anonymous"))
        db.commit()
    yield factory, afactory
    import asyncio
    asyncio.run(ae.dispose())
    engine.dispose()


@pytest.mark.asyncio
async def test_canonical_user_owns_session_and_foreign_user_does_not(actor_db):
    _, afactory = actor_db
    async with afactory() as db:
        await skill_jobs._authorize_session(db, "session-a", {"user_id": "a"}, None)
        with pytest.raises(HTTPException) as exc:
            await skill_jobs._authorize_session(db, "session-a", {"user_id": "b"}, None)
        assert exc.value.status_code == 404
        await skill_jobs._authorize_session(db, "anonymous-session", {}, "test-owner-token")
        with pytest.raises(HTTPException) as exc:
            await skill_jobs._authorize_session(db, "anonymous-session", {}, "wrong-token")
        assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_start_forwards_canonical_creator_to_skill_runner(actor_db, monkeypatch):
    from app.services.gis_harness.skill_jobs import runner as skill_runner
    from app.services.gis_harness.skill_jobs.store import prepared_store
    _, afactory = actor_db
    start = AsyncMock(return_value={"job_id": "123"})
    monkeypatch.setattr(skill_runner, "start_skill_job", start)
    monkeypatch.setattr(prepared_store, "pop", lambda *a, **kw: (SimpleNamespace(), {}))
    async with afactory() as db:
        await skill_jobs.start(skill_jobs.StartRequest(prepared_id="prepared-a", session_id="session-a"), user={"user_id": "a", "org_id": 2}, owner_token=None, db=db)
    assert start.await_args.kwargs["owner"].user_id == "a"
    assert start.await_args.kwargs["owner"].org_id == 2


@pytest.mark.parametrize("user,uid,token", [({"user_id": "a", "org_id": 2}, "a", None), (None, None, "test-owner-token")])
@pytest.mark.asyncio
async def test_sampling_creator_persists_through_real_durable_store_and_task_guard(actor_db, user, uid, token):
    factory, afactory = actor_db
    owner = raster_sampling._user_owner(user)
    job_id, _, _ = runner.submit_async_job(task_type="raster_sampling", display_name="Ownership regression", params={}, idempotency_key=None, owner_id=owner["user_id"], owner_token=token, org_id=owner["org_id"], session_id="session-a" if uid else "anonymous-session")
    with factory() as db:
        row = db.get(AnalysisTask, job_id)
        assert row.creator_id == uid
        assert row.org_id == (2 if uid else None)
    async with afactory() as db:
        assert (await jobs._durable_or_404(db, str(job_id), owner_id=uid, owner_token=token)).id == job_id
        with pytest.raises(HTTPException) as exc:
            await jobs._durable_or_404(db, str(job_id), owner_id="b", owner_token="wrong")
        assert exc.value.status_code == 404


@pytest.mark.parametrize("user", [None, {}, {"user_id": "anonymous"}, {"id": "anon"}])
def test_anonymous_identity_is_not_a_shared_durable_owner(user):
    assert raster_sampling._user_owner(user)["user_id"] is None
