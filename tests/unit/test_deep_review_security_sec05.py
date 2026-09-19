"""SEC-05 + DATA-08 regression tests for /api/v1/data-quality.

Deep-review swarm 2026-09-19:
- SEC-05: ``POST /data-quality/evaluate`` was anonymously writable with an
  arbitrary ``project_id`` (cross-tenant report pollution) and ignored the
  ``_MAX_INLINE_BYTES_ESTIMATE`` cap.
- DATA-08: report pagination materialized the full table to count rows.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.routes import data_quality as dq
from app.core.auth import get_current_user, get_current_user_optional, get_owner_token
from app.core.database import Base

ANON = {"user_id": "anonymous", "role": "anonymous"}
USER_A = {"user_id": "user-a", "role": "viewer", "org_id": None}
USER_B = {"user_id": "user-b", "role": "viewer", "org_id": None}
ADMIN = {"user_id": "admin", "role": "admin", "org_id": None}

FEATURES = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [1.0, 2.0]},
            "properties": {"name": "x"},
        },
    ],
}


@pytest.fixture()
def session_factory(monkeypatch):
    import app.models  # noqa: F401 — register all tables
    import app.models.data_quality  # noqa: F401 — not re-exported by app.models

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    import app.core.database as db_mod

    monkeypatch.setattr(db_mod, "SessionLocal", factory)
    return factory


def _client(optional_user):
    app = FastAPI()
    app.include_router(dq.router, prefix="/api/v1")
    app.dependency_overrides[get_current_user_optional] = lambda: optional_user
    app.dependency_overrides[get_current_user] = lambda: optional_user
    app.dependency_overrides[get_owner_token] = lambda: None
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _make_project(session_factory, owner_id: str) -> str:
    from app.services.project_service import ProjectService

    with session_factory() as db:
        project = ProjectService.create_project(db, name="p", owner_id=owner_id)
    return str(project.id)


async def _post_evaluate(user, payload):
    async with _client(user) as client:
        return await client.post("/api/v1/data-quality/evaluate", json=payload)


# ── SEC-05: auth requirement on persist / project_id / session_id ─────────────


@pytest.mark.asyncio
async def test_anonymous_stateless_evaluate_still_allowed(session_factory):
    resp = await _post_evaluate(ANON, {"geojson": FEATURES, "persist": False})
    assert resp.status_code == 200
    assert resp.json()["report_id"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {"geojson": FEATURES, "persist": True},
        {"geojson": FEATURES, "project_id": "proj_x"},
        {"geojson": FEATURES, "session_id": "sess_x"},
    ],
)
async def test_anonymous_persist_scope_requires_auth(session_factory, payload):
    resp = await _post_evaluate(ANON, payload)
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_foreign_project_persist_404(session_factory):
    foreign = _make_project(session_factory, owner_id="user-b")
    resp = await _post_evaluate(
        USER_A, {"geojson": FEATURES, "persist": True, "project_id": foreign}
    )
    assert resp.status_code == 404

    from app.models.data_quality import QualityReport
    from sqlalchemy import select

    with session_factory() as db:
        rows = db.execute(select(QualityReport)).scalars().all()
    assert rows == [], "foreign-project evaluation must not persist"


@pytest.mark.asyncio
async def test_own_project_persist_succeeds(session_factory):
    own = _make_project(session_factory, owner_id="user-a")
    resp = await _post_evaluate(
        USER_A, {"geojson": FEATURES, "persist": True, "project_id": own}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["report_id"]

    from app.models.data_quality import QualityReport
    from sqlalchemy import select

    with session_factory() as db:
        row = db.execute(select(QualityReport)).scalars().one()
    assert row.created_by == "user-a"
    assert row.project_id == own


@pytest.mark.asyncio
async def test_foreign_session_persist_404(session_factory, monkeypatch):
    def _deny(session_id, user, owner_token):
        raise HTTPException(status_code=404, detail="Session not found")

    monkeypatch.setattr(dq, "_verify_session_access", _deny)
    resp = await _post_evaluate(
        USER_A, {"geojson": FEATURES, "persist": True, "session_id": "sess-b"}
    )
    assert resp.status_code == 404


# ── SEC-05: inline byte cap ───────────────────────────────────────────────────


def test_inline_byte_cap_enforced():
    blob = "x" * (dq._MAX_INLINE_BYTES_ESTIMATE + 1)
    body = dq.EvaluateRequest(
        geojson={
            "type": "FeatureCollection",
            "features": [
                {"type": "Feature", "geometry": None, "properties": {"blob": blob}},
            ],
        }
    )
    with pytest.raises(HTTPException) as exc:
        dq.evaluate_quality(body, user=ANON, owner_token=None)
    assert exc.value.status_code == 413


# ── DATA-08: count query + tenant scoping ────────────────────────────────────


def test_list_reports_total_is_filtered_count(session_factory):
    from app.models.data_quality import QualityReport

    with session_factory() as db:
        db.add_all([
            QualityReport(created_by="user-a", status="completed"),
            QualityReport(created_by="user-a", status="completed"),
            QualityReport(created_by="user-b", status="completed"),
        ])
        db.commit()

    page = dq.list_quality_reports(
        limit=None, offset=None, project_id=None, session_id=None, user=USER_A
    )
    assert page.total == 2
    assert len(page.items) == 2
    assert page.has_more is False

    admin_page = dq.list_quality_reports(
        limit=None, offset=None, project_id=None, session_id=None, user=ADMIN
    )
    assert admin_page.total == 3
