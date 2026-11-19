"""#2197: NULL-org is not a shared preset bucket; every API enforces ownership."""
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.api.routes import skill_jobs as routes
from app.core import database
from app.models.db_model import Base, Conversation, SkillJobPreset
from app.services.gis_harness.skill_jobs import presets


@pytest.fixture()
def preset_env(tmp_path, monkeypatch):
    url = str(tmp_path / "presets.db")
    engine = create_engine(f"sqlite:///{url}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    async_engine = create_async_engine(f"sqlite+aiosqlite:///{url}")
    afactory = async_sessionmaker(bind=async_engine)
    monkeypatch.setattr(database, "SessionLocal", factory)
    skill = SimpleNamespace(version="1.0", fingerprint=lambda: "digest")
    monkeypatch.setattr("app.services.gis_harness.skills.loader.get_skill_library", lambda: SimpleNamespace(get=lambda sid: skill))
    with factory() as db:
        db.add(Conversation(id="session-b", user_id="b", title="Owned by B"))
        db.add(SkillJobPreset(id="private-a", org_id=None, creator_id="a", name="Private", skill_id="test", skill_version="1.0", params={"secret": "a-only"}))
        db.add(SkillJobPreset(id="org-a", org_id=1, creator_id="a", name="Shared org config", skill_id="test", skill_version="1.0"))
        db.commit()
    app = FastAPI()
    app.include_router(routes.router, prefix="/api/v1")

    async def get_db():
        async with afactory() as db:
            yield db

    app.dependency_overrides[routes.get_async_db] = get_db
    yield app, factory
    import asyncio
    asyncio.run(async_engine.dispose())
    engine.dispose()


@pytest.mark.parametrize("uid,org", [("b", None), ("b", 2)])
def test_all_private_preset_routes_reject_other_user(preset_env, uid, org):
    app, factory = preset_env
    app.dependency_overrides[routes.get_current_user] = lambda: {"id": uid, "org_id": org}
    client = TestClient(app)
    base = "/api/v1/skill-jobs/presets"
    assert client.get(base).json()["presets"] == []
    assert client.get(f"{base}/private-a").status_code == 404
    assert client.put(f"{base}/private-a", json={"name": "stolen", "skill_id": "test"}).status_code == 404
    assert client.post(f"{base}/private-a/prepare", json={"session_id": "session-b"}).status_code == 404
    assert client.delete(f"{base}/private-a").status_code == 404
    with factory() as db:
        assert db.get(SkillJobPreset, "private-a").name == "Private"


def test_owner_can_save_read_update_and_delete_without_org(preset_env):
    app, _ = preset_env
    app.dependency_overrides[routes.get_current_user] = lambda: {"id": "a", "org_id": None}
    client = TestClient(app)
    base = "/api/v1/skill-jobs/presets"
    assert [p["id"] for p in client.get(base).json()["presets"]] == ["private-a"]
    assert client.get(f"{base}/private-a").status_code == 200
    assert presets.get_preset_raw("private-a", org_id=None, creator_id="a")["params"] == {"secret": "a-only"}
    assert presets.get_preset_raw("private-a", org_id=None, creator_id="b") is None
    assert client.put(f"{base}/private-a", json={"name": "mine", "skill_id": "test"}).status_code == 200
    created = client.post(base, json={"name": "new", "skill_id": "test"})
    assert created.status_code == 200
    assert created.json()["creator_id"] == "a"
    assert client.delete(f"{base}/private-a").status_code == 200


def test_real_org_configuration_sharing_stays_org_scoped(preset_env):
    app, _ = preset_env
    for org, status in [(1, 200), (2, 404), (None, 404)]:
        app.dependency_overrides[routes.get_current_user] = lambda org=org: {"id": "b", "org_id": org}
        assert TestClient(app).get("/api/v1/skill-jobs/presets/org-a").status_code == status


def test_no_identity_is_not_a_null_creator_wildcard(preset_env):
    app, _ = preset_env
    app.dependency_overrides[routes.get_current_user] = lambda: {}
    client = TestClient(app)
    assert client.get("/api/v1/skill-jobs/presets").json()["presets"] == []
    assert client.get("/api/v1/skill-jobs/presets/private-a").status_code == 404
    assert client.post("/api/v1/skill-jobs/presets", json={"name": "no owner", "skill_id": "test"}).status_code == 422
