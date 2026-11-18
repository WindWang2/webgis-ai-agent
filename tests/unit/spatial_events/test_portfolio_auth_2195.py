"""#2195: org-wide portfolio is for live org members, not the default bucket."""
from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.routes import portfolio
from app.core import database, tenancy
from app.models.db_model import Base, Organization, User
from app.models.mission import GISMissionRow
from app.models.spatial_events import SpatialEventRow, SpatialWatchFireRow


@pytest.fixture()
def portfolio_env(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(database, "SessionLocal", factory)
    monkeypatch.setattr(portfolio, "_factory", lambda: factory)
    # Default-org cache must not refer to another test database.
    monkeypatch.setattr(tenancy, "get_or_create_default_org_id_sync", lambda db: 1)
    with factory() as db:
        db.add_all([Organization(id=1, name="Default", slug="default"), Organization(id=2, name="Other", slug="other")])
        for uid, org in [("unaffiliated-a", None), ("unaffiliated-b", None), ("member-a", 1), ("member-b", 2)]:
            db.add(User(id=uid, username=uid, email=f"{uid}@example.test", org_id=org, is_active=True))
        for org in (1, 2):
            db.add(GISMissionRow(mission_id=f"m{org}", org_id=str(org), project_id=f"p{org}", root_goal=f"secret-{org}"))
            db.add(SpatialEventRow(event_id=f"e{org}", org_id=str(org), project_id=f"p{org}", kind="changed", subject_type="dataset", subject_key="data", occurred_at=datetime.now(timezone.utc)))
            db.add(SpatialWatchFireRow(watch_id=f"w{org}", event_id=f"e{org}", org_id=str(org), action="notify", outcome="ok"))
        db.commit()
    app = FastAPI()
    app.include_router(portfolio.router, prefix="/api/v1")
    yield app, factory
    engine.dispose()


@pytest.mark.parametrize("uid", ["unaffiliated-a", "unaffiliated-b"])
@pytest.mark.parametrize("path", ["summary", "projects", "projects/p1"])
def test_default_bucket_is_not_shared_authority(portfolio_env, uid, path):
    app, _ = portfolio_env
    app.dependency_overrides[portfolio.get_current_user] = lambda: {"user_id": uid}
    res = TestClient(app).get(f"/api/v1/portfolio/{path}")
    assert res.status_code == 403
    assert "secret-" not in res.text


def test_live_membership_not_stale_jwt_or_admin_role(portfolio_env):
    app, factory = portfolio_env
    # A stale org claim (including default org) and admin role do not replace membership.
    app.dependency_overrides[portfolio.get_current_user] = lambda: {"user_id": "unaffiliated-a", "org_id": 1, "role": "admin"}
    assert TestClient(app).get("/api/v1/portfolio/summary").status_code == 403
    with factory() as db:
        db.get(User, "member-a").is_active = False
        db.commit()
    app.dependency_overrides[portfolio.get_current_user] = lambda: {"user_id": "member-a", "org_id": 1}
    assert TestClient(app).get("/api/v1/portfolio/summary").status_code == 403


def test_real_org_members_have_positive_and_cross_tenant_paths(portfolio_env):
    app, _ = portfolio_env
    for uid, own, other in [("member-a", 1, 2), ("member-b", 2, 1)]:
        app.dependency_overrides[portfolio.get_current_user] = lambda uid=uid: {"user_id": uid}
        client = TestClient(app)
        summary = client.get("/api/v1/portfolio/summary")
        assert summary.status_code == 200
        assert summary.json()["missions_by_state"] == {"created": 1}
        assert summary.json()["watch_fires_24h"] == 1
        assert client.get("/api/v1/portfolio/projects").json()["projects"][0]["project_id"] == f"p{own}"
        detail = client.get(f"/api/v1/portfolio/projects/p{own}")
        assert detail.status_code == 200
        assert detail.json()["missions"][0]["root_goal"] == f"secret-{own}"
        assert len(detail.json()["recent_watch_fires"]) == 1
        assert client.get(f"/api/v1/portfolio/projects/p{other}").status_code == 404
