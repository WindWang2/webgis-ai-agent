"""Deep-review API/DATA batch — pagination regressions.

API-06：/uploads limit/offset 必须有界（Query ge/le）。
API-07/DATA-08：data-lifecycle 列表 total 走 COUNT 聚合且过滤集一致。
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.models.data_lifecycle  # noqa: F401
from app.api.routes import data_lifecycle as lifecycle_routes
from app.api.routes import upload as upload_routes
from app.core.database import Base, Engine, SessionLocal
from app.models.data_lifecycle import GcPlan, LifecycleObject
from app.models.upload import UploadRecord


@pytest.fixture(autouse=True)
def _schema():
    Base.metadata.create_all(bind=Engine)
    with SessionLocal() as db:
        db.query(UploadRecord).filter(UploadRecord.session_id == "sess-pg-1").delete()
        db.query(LifecycleObject).delete()
        db.query(GcPlan).delete()
        db.commit()
    yield


def _lifecycle_client(user_id: str = "pg-user") -> TestClient:
    application = FastAPI()
    application.include_router(lifecycle_routes.router, prefix="/api/v1")
    application.dependency_overrides[lifecycle_routes.get_current_user] = (
        lambda: {"user_id": user_id, "role": "viewer"}
    )
    return TestClient(application)


def _upload_client(monkeypatch) -> TestClient:
    application = FastAPI()
    application.include_router(upload_routes.router, prefix="/api/v1")
    application.dependency_overrides[upload_routes.get_current_user_with_version] = (
        lambda: {"user_id": "pg-user", "token_version": 0}
    )

    async def _owned(db, session_id, user_id, owner_token=None):
        return None

    monkeypatch.setattr(upload_routes, "_verify_session_owner", _owned)
    return TestClient(application)


def test_uploads_list_clamps_limit_and_offset(monkeypatch):
    client = _upload_client(monkeypatch)
    base = "/api/v1/uploads?session_id=sess-pg-1"

    assert client.get(f"{base}&limit=200&offset=0").status_code == 200
    assert client.get(f"{base}&limit=0").status_code == 422
    assert client.get(f"{base}&limit=201").status_code == 422
    assert client.get(f"{base}&limit=100&offset=-1").status_code == 422


def test_lifecycle_objects_total_respects_kind_and_owner_filters(monkeypatch):
    with SessionLocal() as db:
        db.add_all([
            LifecycleObject(kind="cog_output", object_id="pg-a.tif",
                            owner_scope="pg-user", byte_size=1, tier="hot"),
            LifecycleObject(kind="cog_output", object_id="pg-b.tif",
                            owner_scope="pg-user", byte_size=1, tier="hot"),
            LifecycleObject(kind="artifact_cache", object_id="pg-c.tif",
                            owner_scope="pg-user", byte_size=1, tier="hot"),
            LifecycleObject(kind="cog_output", object_id="pg-theirs.tif",
                            owner_scope="other-user", byte_size=1, tier="hot"),
        ])
        db.commit()

    client = _lifecycle_client()
    r = client.get("/api/v1/data-lifecycle/objects")
    body = r.json()
    assert body["total"] == 3, "非 admin 仅见本 owner scope"
    assert all(i["owner_scope"] == "pg-user" for i in body["items"])
    assert "pg-theirs.tif" not in {i["object_id"] for i in body["items"]}

    r = client.get("/api/v1/data-lifecycle/objects?kind=artifact_cache")
    assert r.json()["total"] == 1
    assert r.json()["items"][0]["object_id"] == "pg-c.tif"


def test_gc_plans_total_respects_status_and_creator_filters(monkeypatch):
    with SessionLocal() as db:
        db.add_all([
            GcPlan(id="plan-pg-1", status="pending_approval", created_by="pg-user",
                   scope={}, plan_tree={}),
            GcPlan(id="plan-pg-2", status="approved", created_by="pg-user",
                   scope={}, plan_tree={}),
            GcPlan(id="plan-pg-3", status="pending_approval", created_by="other",
                   scope={}, plan_tree={}),
        ])
        db.commit()

    client = _lifecycle_client()
    r = client.get("/api/v1/data-lifecycle/gc/plans")
    assert r.json()["total"] == 2
    r = client.get("/api/v1/data-lifecycle/gc/plans?status=approved")
    assert r.json()["total"] == 1
    assert r.json()["items"][0]["id"] == "plan-pg-2"
