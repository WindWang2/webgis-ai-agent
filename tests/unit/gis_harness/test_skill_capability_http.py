"""Real JWT/session/durable HTTP guards; only database infrastructure is injected."""
from contextlib import asynccontextmanager
from types import SimpleNamespace
import uuid

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.api.routes import skill_jobs
from app.core import auth, database
from app.core.config import settings
from app.models.db_model import AnalysisTask, Base, Conversation, Organization, User


@pytest.fixture()
async def http_env(tmp_path, monkeypatch):
    path = tmp_path / "capability.db"
    engine = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine)
    ae = create_async_engine(f"sqlite+aiosqlite:///{path}")
    af = async_sessionmaker(ae, expire_on_commit=False)

    @asynccontextmanager
    async def sessions():
        async with af() as db:
            yield db
            await db.commit()

    async def dep():
        async with sessions() as db:
            yield db

    monkeypatch.setattr(database, "SessionLocal", factory)
    monkeypatch.setattr(database, "async_db_session", sessions)
    monkeypatch.setattr(settings, "AUTH_DISABLED", False)
    auth.invalidate_live_user_state()
    uid, other = str(uuid.uuid4()), str(uuid.uuid4())
    with factory() as db:
        db.add_all([Organization(id=81, name="A", slug="a"), Organization(id=82, name="B", slug="b")])
        for user, org in [(uid, 81), (other, 82)]:
            db.add(User(id=user, username=user, email=f"{user}@example.test", org_id=org, role="editor", is_active=True, token_version=0))
        db.add_all([
            Conversation(id="owned-session", user_id=uid, owner_token="not-a-user-bypass", title="Owned"),
            Conversation(id="anon-session", owner_token="capability-a", title="Anonymous"),
            Conversation(id="other-anon-session", owner_token="capability-b", title="Other"),
        ])
        for jid, creator, org, project, token, session, kind in [
            (991, uid, 81, None, None, "owned-session", "skill_job"),
            (992, None, None, None, "capability-a", "anon-session", "skill_job"),
            (993, other, 82, None, "capability-a", "anon-session", "skill_job"),
            (994, None, None, "foreign-project", "capability-a", "anon-session", "skill_job"),
            (995, None, 82, None, "capability-a", "anon-session", "skill_job"),
            (996, None, None, None, "capability-a", "anon-session", "analysis"),
            (997, None, None, None, "different-job-capability", "anon-session", "skill_job"),
            (998, None, None, None, "capability-b", "other-anon-session", "skill_job"),
        ]:
            db.add(AnalysisTask(id=jid, creator_id=creator, org_id=org, project_id=project, owner_token=token, session_id=session, task_type="skill_job:review", job_kind=kind, status="pending", parameters={"skill_id": "review"}))
        db.commit()
    jwt = auth.create_access_token({"sub": uid}, token_version=0)
    foreign = auth.create_access_token({"sub": other}, token_version=0)
    app = FastAPI()
    app.include_router(skill_jobs.router, prefix="/api/v1")
    # Infrastructure seam only: actual auth, session, job guards are never overridden.
    app.dependency_overrides[database.get_async_db] = dep
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield SimpleNamespace(client=client, uid=uid, factory=factory, sessions=sessions, headers={"Authorization": f"Bearer {jwt}"}, foreign={"Authorization": f"Bearer {foreign}"}, capability={"X-Session-Token": "capability-a"})
    auth.invalidate_live_user_state()
    await ae.dispose()
    engine.dispose()


async def test_anonymous_capability_lists_reads_cancels_only_anonymous_owned_jobs(http_env):
    env = http_env
    base = "/api/v1/skill-jobs"
    listed = await env.client.get(base + "/runs?session_id=anon-session", headers=env.capability)
    assert listed.status_code == 200, listed.text
    assert [r["job_id"] for r in listed.json()["runs"]] == ["992"]
    assert (await env.client.get(base + "/runs/992", headers=env.capability)).status_code == 200
    cancel = await env.client.post(base + "/runs/992/cancel?session_id=anon-session", headers=env.capability)
    assert cancel.status_code == 200, cancel.text
    with env.factory() as db:
        assert db.get(AnalysisTask, 992).cancel_requested_at is not None


@pytest.mark.parametrize("jid", [991, 993, 994, 995, 996, 997, 998])
async def test_capability_never_bypasses_principal_tenant_project_or_job_kind(http_env, jid):
    env = http_env
    base = "/api/v1/skill-jobs"
    assert (await env.client.get(f"{base}/runs/{jid}", headers=env.capability)).status_code == 404
    assert (await env.client.post(f"{base}/runs/{jid}/cancel?session_id=anon-session", headers=env.capability)).status_code == 404
    # Even an authenticated user cannot use an anonymous capability for another creator.
    if jid == 993:
        assert (await env.client.get(f"{base}/runs/{jid}", headers=env.headers | env.capability)).status_code == 404


@pytest.mark.parametrize("headers", [{}, {"X-Session-Token": "wrong"}, {"X-Session-Token": "capability-b"}])
async def test_missing_wrong_or_other_session_capability_is_denied_for_every_session_operation(http_env, headers):
    client = http_env.client
    base = "/api/v1/skill-jobs"
    for path in ["/runs?session_id=anon-session", "/runs/992"]:
        assert (await client.get(base + path, headers=headers)).status_code == 404
    for path, body in [
        ("/prepare", {"session_id": "anon-session", "skill_id": "validate_spatial_data"}),
        ("/start", {"session_id": "anon-session", "prepared_id": "anything"}),
        ("/runs/992/cancel?session_id=anon-session", {}),
    ]:
        assert (await client.post(base + path, json=body, headers=headers)).status_code == 404


async def test_authenticated_owner_unchanged_and_user_session_token_is_not_a_bypass(http_env):
    env = http_env
    base = "/api/v1/skill-jobs"
    # Historical rows affected by #2198 remain visible only through their real
    # authenticated session owner, not through any presented capability.
    with env.factory() as db:
        db.add(AnalysisTask(id=999, creator_id=None, org_id=81, session_id="owned-session", task_type="skill_job:legacy", job_kind="skill_job", status="pending", parameters={}))
        db.commit()
    assert (await env.client.get(base + "/runs/999", headers=env.headers)).status_code == 200
    assert (await env.client.get(base + "/runs/999", headers=env.foreign | {"X-Session-Token": "not-a-user-bypass"})).status_code == 404
    assert (await env.client.get(base + "/runs/999", headers=env.capability)).status_code == 404
    assert (await env.client.get(base + "/runs?session_id=owned-session", headers=env.headers)).status_code == 200
    assert (await env.client.get(base + "/runs/991", headers=env.headers)).status_code == 200
    for headers in [env.foreign, {"X-Session-Token": "not-a-user-bypass"}, env.foreign | {"X-Session-Token": "not-a-user-bypass"}]:
        assert (await env.client.get(base + "/runs?session_id=owned-session", headers=headers)).status_code == 404
        assert (await env.client.get(base + "/runs/991", headers=headers)).status_code == 404
    assert (await env.client.post(base + "/runs/991/cancel?session_id=owned-session", headers=env.headers)).status_code == 200


@pytest.mark.parametrize("authorization", ["Bearer invalid", "Basic invalid"])
async def test_invalid_auth_is_not_downgraded_to_capability_caller(http_env, authorization):
    response = await http_env.client.get("/api/v1/skill-jobs/runs?session_id=anon-session", headers=http_env.capability | {"Authorization": authorization})
    assert response.status_code == 401


async def test_account_presets_stay_authenticated_despite_valid_capability(http_env):
    client = http_env.client
    base = "/api/v1/skill-jobs/presets"
    assert (await client.get(base, headers=http_env.capability)).status_code == 401
    assert (await client.post(base, headers=http_env.capability, json={"name": "x", "skill_id": "validate_spatial_data"})).status_code == 401
    assert (await client.post(base + "/unknown/prepare", headers=http_env.capability, json={"session_id": "anon-session"})).status_code == 401


@pytest.mark.parametrize("revoke", ["inactive", "token_version"])
async def test_revoked_authenticated_principal_cannot_fall_back_to_valid_capability(http_env, revoke):
    env = http_env
    with env.factory() as db:
        user = db.get(User, env.uid)
        if revoke == "inactive":
            user.is_active = False
        else:
            user.token_version += 1
        db.commit()
    auth.invalidate_live_user_state(env.uid)
    response = await env.client.get("/api/v1/skill-jobs/runs?session_id=anon-session", headers=env.headers | env.capability)
    assert response.status_code == (403 if revoke == "inactive" else 401)
