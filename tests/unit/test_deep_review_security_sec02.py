"""SEC-02 regression: template version API visibility/ownership (IDOR).

Deep-review swarm 2026-09-19 (SEC-02): the version endpoints had no
``_template_visible`` guard (reads were anonymous and cross-tenant) and
``create_version`` overwrote the base template payload without checking the
caller. Now: reads require built-in/own/org visibility, writes require
creator/admin, and inheritance parents must be visible too.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from app.api.routes import template_versions as tv
from app.core.database import Base


def _token(sub: str, role: str = "viewer", org_id=None) -> dict:
    from app.core.auth import create_access_token

    payload = {"sub": sub, "username": sub, "role": role}
    if org_id is not None:
        payload["org_id"] = org_id
    return {"Authorization": "Bearer " + create_access_token(payload)}


@pytest.fixture()
def session_factory(monkeypatch, tmp_path):
    import app.models.db_model  # noqa: F401 — CartographyTemplate
    import app.models.template_version  # noqa: F401 — TemplateVersion

    path = tmp_path / "template-security.db"
    engine = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    import app.core.database as db_mod
    from app.core.auth import invalidate_live_user_state
    from app.models.db_model import Organization, User

    monkeypatch.setattr(db_mod, "SessionLocal", factory)
    async_engine = create_async_engine(f"sqlite+aiosqlite:///{path}", poolclass=NullPool)
    monkeypatch.setattr(db_mod, "async_db_session", async_sessionmaker(async_engine))
    with factory() as db:
        for oid in (1, 2, 7):
            db.add(Organization(id=oid, name=f"Organization {oid}", slug=f"sec02-{oid}"))
        db.flush()
        for uid, role, oid in ((A, "viewer", 1), (B, "viewer", 2), ("root", "admin", 1)):
            db.add(User(id=uid, username=uid, email=f"{uid}@test.example",
                        role=role, org_id=oid, is_active=True, token_version=0))
        db.commit()
    invalidate_live_user_state()
    yield factory
    invalidate_live_user_state()
    engine.dispose()


@pytest.fixture()
def client(session_factory):
    app = FastAPI()
    app.include_router(tv.router, prefix="/api/v1")
    return TestClient(app)


def _make_template(session_factory, template_id: str, *, creator_id=None,
                   is_builtin: bool = False, org_id=None) -> None:
    from app.models.db_model import CartographyTemplate

    with session_factory() as db:
        db.add(CartographyTemplate(
            id=template_id, kind="layout", name=template_id, category="layout",
            keywords=[], description="", payload={"paperSize": "A4"},
            is_builtin=is_builtin, version=1, creator_id=creator_id,
            org_id=org_id,
        ))
        db.commit()


def _make_version(session_factory, template_id: str, payload: dict,
                  *, created_by=None, parent_version_id=None) -> str:
    from app.services.templates.versioning import create_version

    with session_factory() as db:
        row = create_version(
            db, template_id, payload,
            created_by=created_by, parent_version_id=parent_version_id,
        )
        return str(row.id)


A = "user-a"
B = "user-b"


# ── reads ─────────────────────────────────────────────────────────────────────


def test_cross_user_and_anonymous_reads_404(client, session_factory):
    _make_template(session_factory, "tmpl_a", creator_id=A)
    _make_version(session_factory, "tmpl_a", {"paperSize": "A4"}, created_by=A)

    assert client.get("/api/v1/templates/tmpl_a/versions").status_code == 404
    assert client.get(
        "/api/v1/templates/tmpl_a/versions/1", headers=_token(B)
    ).status_code == 404
    assert client.get("/api/v1/templates/tmpl_a/versions/1").status_code == 404


def test_owner_and_admin_reads_ok(client, session_factory):
    _make_template(session_factory, "tmpl_a", creator_id=A, org_id=1)
    _make_version(session_factory, "tmpl_a", {"paperSize": "A4"}, created_by=A)

    owner = client.get("/api/v1/templates/tmpl_a/versions", headers=_token(A))
    assert owner.status_code == 200
    assert len(owner.json()["items"]) == 1

    admin = client.get(
        "/api/v1/templates/tmpl_a/versions/1", headers=_token("root", "admin")
    )
    assert admin.status_code == 200


# ── writes ────────────────────────────────────────────────────────────────────


def test_cross_user_write_invisible_404_no_mutation(client, session_factory):
    _make_template(session_factory, "tmpl_a", creator_id=A)

    created = client.post(
        "/api/v1/templates/tmpl_a/versions",
        json={"payload": {"paperSize": "A3"}},
        headers=_token(B),
    )
    assert created.status_code == 404

    _make_version(session_factory, "tmpl_a", {"paperSize": "A4"}, created_by=A)
    dep = client.post(
        "/api/v1/templates/tmpl_a/versions/1/deprecate",
        json={"note": "nope"},
        headers=_token(B),
    )
    assert dep.status_code == 404

    from app.models.template_version import TemplateVersion

    with session_factory() as db:
        rows = db.query(TemplateVersion).filter_by(template_id="tmpl_a").all()
    assert len(rows) == 1
    assert rows[0].deprecated_at is None


def test_org_member_read_ok_but_write_403(client, session_factory):
    from app.models.db_model import User
    with session_factory() as db:
        db.get(User, A).org_id = 7
        db.get(User, B).org_id = 7
        db.commit()
    _make_template(session_factory, "tmpl_a", creator_id=A, org_id=7)
    _make_version(session_factory, "tmpl_a", {"paperSize": "A4"}, created_by=A)

    listed = client.get("/api/v1/templates/tmpl_a/versions",
                        headers=_token(B, org_id=7))
    assert listed.status_code == 200

    created = client.post(
        "/api/v1/templates/tmpl_a/versions",
        json={"payload": {"paperSize": "A3"}},
        headers=_token(B, org_id=7),
    )
    assert created.status_code == 403

    dep = client.post(
        "/api/v1/templates/tmpl_a/versions/1/deprecate",
        json={"note": "nope"},
        headers=_token(B, org_id=7),
    )
    assert dep.status_code == 403


def test_owner_write_roundtrip(client, session_factory):
    _make_template(session_factory, "tmpl_a", creator_id=A)

    created = client.post(
        "/api/v1/templates/tmpl_a/versions",
        json={"payload": {"paperSize": "A3"}},
        headers=_token(A),
    )
    assert created.status_code == 200
    assert created.json()["version"]["version"] == 1

    dep = client.post(
        "/api/v1/templates/tmpl_a/versions/1/deprecate",
        json={"note": "done"},
        headers=_token(A),
    )
    assert dep.status_code == 200
    assert dep.json()["deprecated"] is True


def test_admin_can_write_others_template(client, session_factory):
    _make_template(session_factory, "tmpl_a", creator_id=A, org_id=1)
    resp = client.post(
        "/api/v1/templates/tmpl_a/versions",
        json={"payload": {"paperSize": "A3"}},
        headers=_token("root", "admin"),
    )
    assert resp.status_code == 200


def test_admin_cannot_read_foreign_org_or_unscoped_private_template(client, session_factory):
    _make_template(session_factory, "foreign", creator_id=B, org_id=2)
    _make_template(session_factory, "private", creator_id=B)
    for tid in ("foreign", "private"):
        assert client.get(
            f"/api/v1/templates/{tid}/versions", headers=_token("root", "admin")
        ).status_code == 404


def test_stale_claims_cannot_grant_org_or_admin_access(client, session_factory):
    from app.models.db_model import User

    _make_template(session_factory, "tmpl_a", creator_id=A, org_id=1)
    # The signed org claim is not the live identity's organization.
    assert client.get(
        "/api/v1/templates/tmpl_a/versions", headers=_token(B, org_id=1)
    ).status_code == 404
    with session_factory() as db:
        db.get(User, "root").role = "viewer"
        db.commit()
    denied = client.post(
        "/api/v1/templates/tmpl_a/versions",
        json={"payload": {"paperSize": "A3"}},
        headers=_token("root", "admin"),
    )
    assert denied.status_code == 403


def test_org_admin_cannot_version_builtin_template(client, session_factory):
    _make_template(session_factory, "tmpl_builtin", is_builtin=True, org_id=1)
    denied = client.post(
        "/api/v1/templates/tmpl_builtin/versions",
        json={"payload": {"paperSize": "A3"}},
        headers=_token("root", "admin"),
    )
    assert denied.status_code == 403
    dep = client.post(
        "/api/v1/templates/tmpl_builtin/versions/1/deprecate",
        json={"note": "no"},
        headers=_token("root", "admin", org_id=1),
    )
    assert dep.status_code == 403


def test_builtin_readable_but_not_writable_by_non_admin(client, session_factory):
    _make_template(session_factory, "tmpl_builtin", is_builtin=True)
    _make_version(session_factory, "tmpl_builtin", {"paperSize": "A4"})

    listed = client.get("/api/v1/templates/tmpl_builtin/versions")
    assert listed.status_code == 200

    denied = client.post(
        "/api/v1/templates/tmpl_builtin/versions",
        json={"payload": {}},
        headers=_token(B),
    )
    assert denied.status_code == 403


# ── inheritance parent/chain visibility ───────────────────────────────────────


def test_parent_version_from_other_template_rejected(client, session_factory):
    """#1411: cross-template parent_version_id → 400 (same-template rule)."""
    _make_template(session_factory, "tmpl_a", creator_id=A)
    _make_template(session_factory, "tmpl_b", creator_id=B)
    parent_id = _make_version(session_factory, "tmpl_b", {"paperSize": "A4"},
                              created_by=B)

    resp = client.post(
        "/api/v1/templates/tmpl_a/versions",
        json={"payload": {}, "parent_version_id": parent_id},
        headers=_token(A),
    )
    assert resp.status_code == 400
    assert "parent" in str(resp.json().get("detail", "")).lower()


def test_effective_read_denied_when_legacy_cross_template_ancestor(client, session_factory):
    """Read path still 404s if a legacy dirty cross-template chain exists in DB."""
    from app.models.template_version import TemplateVersion

    _make_template(session_factory, "tmpl_a", creator_id=A)
    _make_template(session_factory, "tmpl_b", creator_id=B)
    parent_id = _make_version(session_factory, "tmpl_a", {"paperSize": "A4", "secret": 1},
                              created_by=A)
    # Bypass create_version same-template guard to simulate pre-#1411 dirty data.
    with session_factory() as db:
        row = TemplateVersion(
            template_id="tmpl_b",
            version=1,
            payload={"style": {"font": "x"}},
            parent_version_id=parent_id,
            created_by=B,
        )
        db.add(row)
        db.commit()
        child_id = row.id

    resp = client.get("/api/v1/templates/tmpl_b/versions/1", headers=_token(B))
    assert resp.status_code == 404
    assert child_id  # the row exists; chain visibility is what blocks the read


def test_cross_template_parent_version_rejected(client, session_factory):
    """#1411: parent_version_id from another template must not merge into effective."""
    _make_template(session_factory, "tmpl_victim", creator_id=A, org_id=1)
    _make_template(session_factory, "tmpl_attacker", creator_id=B, org_id=2)
    victim_vid = _make_version(
        session_factory, "tmpl_victim",
        {"secret_style": "#deadbeef", "paperSize": "A0"},
        created_by=A,
    )

    # Even if attacker somehow knows victim's version id, POST must fail.
    resp = client.post(
        "/api/v1/templates/tmpl_attacker/versions",
        json={"payload": {"paperSize": "A4"}, "parent_version_id": victim_vid},
        headers=_token(B, org_id=2),
    )
    assert resp.status_code in (400, 403), resp.text
    body = resp.json()
    detail = str(body.get("detail") or body)
    assert "parent" in detail.lower() or "same template" in detail.lower() or "belong" in detail.lower()

    # Attacker's own latest effective must not contain victim secrets.
    listed = client.get(
        "/api/v1/templates/tmpl_attacker/versions/latest",
        headers=_token(B, org_id=2),
    )
    # No version created → 404, or if somehow created without parent, no secret.
    if listed.status_code == 200:
        effective = listed.json().get("effective_payload") or {}
        assert "secret_style" not in effective
