"""REST 路由契约测试（T10）——挂载面 + 鉴权先于业务 + 上传白名单。"""
from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.routes import project_exchange as routes
from app.core.database import Base, Engine, SessionLocal
from app.models.db_model import User
from app.services.project_exchange import contracts as pc

_OWNER = "ex-route-owner"


@pytest.fixture(autouse=True)
def _seed():
    Base.metadata.create_all(bind=Engine)
    with SessionLocal() as db:
        if db.get(User, _OWNER) is None:
            db.add(User(
                id=_OWNER, username="ex-route",
                email="ex-route@example.com",
                password_hash="scrypt$16384$8$1$00$00",
                role="editor", is_active=True, token_version=0,
                created_at=datetime.now(timezone.utc)))
            db.commit()
    yield


@pytest.fixture()
async def client():
    app = FastAPI()
    app.include_router(routes.router, prefix="/api/v1")
    app.include_router(routes.exchange_sub, prefix="/api/v1")
    async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


class TestAuthFirst:
    @pytest.mark.asyncio
    async def test_inspect_requires_auth(self, client):
        r = await client.post(
            "/api/v1/projects/p/exchange/inspect",
            json={"selection": {"kind": "project"}})
        assert r.status_code in (401, 403)

    @pytest.mark.asyncio
    async def test_export_requires_auth(self, client):
        r = await client.post(
            "/api/v1/projects/p/exchange/export", json={})
        assert r.status_code in (401, 403)

    @pytest.mark.asyncio
    async def test_import_submit_requires_auth(self, client):
        r = await client.post("/api/v1/project-exchange/imports", json={})
        assert r.status_code in (401, 403)

    @pytest.mark.asyncio
    async def test_receipt_hidden_without_auth(self, client):
        r = await client.get(
            "/api/v1/project-exchange/exports/pkg-"
            + "a" * 32 + "/receipt")
        assert r.status_code in (401, 403, 404)


class TestUploadGuard:
    @pytest.mark.asyncio
    async def test_upload_rejects_non_zip(self, client, monkeypatch):
        from app.core.auth import get_current_user

        app = FastAPI()
        app.include_router(routes.router, prefix="/api/v1")
        app.dependency_overrides[get_current_user] = lambda: {
            "user_id": _OWNER, "id": _OWNER, "org_id": None}
        async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test") as ac:
            r = await ac.post(
                "/api/v1/project-exchange/packages/upload",
                files={"file": ("evil.sh", b"#!/bin/sh\nrm -rf /",
                                "text/x-sh")})
        assert r.status_code == 422
        body = r.json()
        assert body["detail"]["code"] == pc.ERR_ENTRY_TYPE

    @pytest.mark.asyncio
    async def test_upload_accepts_zip(self, client):
        from app.core.auth import get_current_user

        app = FastAPI()
        app.include_router(routes.router, prefix="/api/v1")
        app.dependency_overrides[get_current_user] = lambda: {
            "user_id": _OWNER, "id": _OWNER, "org_id": None}
        async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test") as ac:
            r = await ac.post(
                "/api/v1/project-exchange/packages/upload",
                files={"file": ("pkg.wgproject.zip", b"PK\x03\x04stub",
                                "application/zip")})
        assert r.status_code == 200
        body = r.json()
        assert body["package_upload_id"].startswith("pkgnz-")
        assert len(body["sha256"]) == 64
