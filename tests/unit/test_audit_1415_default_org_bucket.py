"""#1415 residual: default-org bucket must not share spatial-events across users."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


@contextmanager
def _dummy_session():
    yield object()


@pytest.fixture()
def stub_db(monkeypatch):
    monkeypatch.setattr(
        "app.core.database.SessionLocal", lambda: _dummy_session()
    )
    monkeypatch.setattr(
        "app.core.tenancy.get_or_create_default_org_id_sync",
        lambda db: "default-org-id",
    )


def test_reject_default_org_bucket_blocks_unscoped_user(stub_db):
    from app.api.routes import spatial_events as se

    with pytest.raises(HTTPException) as ei:
        se._reject_default_org_bucket(
            {"user_id": "u1", "role": "viewer"}, "default-org-id"
        )
    assert ei.value.status_code == 403
    assert ei.value.detail == "org_membership_required"


def test_reject_default_org_bucket_allows_explicit_org(stub_db):
    from app.api.routes import spatial_events as se

    se._reject_default_org_bucket(
        {"user_id": "u1", "role": "viewer", "org_id": "default-org-id"},
        "default-org-id",
    )


def test_reject_default_org_bucket_allows_admin(stub_db):
    from app.api.routes import spatial_events as se

    se._reject_default_org_bucket(
        {"user_id": "root", "role": "admin"}, "default-org-id"
    )


def test_reject_default_org_bucket_allows_non_default_effective_org(stub_db):
    from app.api.routes import spatial_events as se

    # Integration tests mock effective org to org-a without JWT org_id
    se._reject_default_org_bucket({"id": "alice", "role": "viewer"}, "org-a")


def test_org_helper_rejects_default_bucket_end_to_end(stub_db, monkeypatch):
    from app.api.routes import spatial_events as se
    from app.core import tenancy

    monkeypatch.setattr(
        tenancy, "effective_org_in_thread", lambda user: "default-org-id"
    )
    with pytest.raises(HTTPException) as ei:
        se._org({"user_id": "anon-bucket", "role": "viewer"})
    assert ei.value.status_code == 403


def test_assert_create_bindings_rejects_foreign_session(monkeypatch):
    from app.api.routes import mission_runtime as mr
    from app.core.database import Base
    import app.models.db_model  # noqa: F401
    from app.models.db_model import Conversation

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr("app.core.database.SessionLocal", factory)
    with factory() as db:
        db.add(
            Conversation(
                id="sess-a",
                user_id="user-a",
                title="t",
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )
        )
        db.commit()

    mr._assert_create_bindings(
        {"user_id": "user-a", "role": "editor"},
        session_id="sess-a",
        project_id=None,
    )
    with pytest.raises(HTTPException) as ei:
        mr._assert_create_bindings(
            {"user_id": "user-b", "role": "editor"},
            session_id="sess-a",
            project_id=None,
        )
    assert ei.value.status_code == 404
