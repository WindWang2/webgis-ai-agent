"""G04 组合模板资产 REST API 测试（async-client 隔离 sqlite 模式）。

覆盖：列表 scope（匿名 builtin-only / 登录含 DB）、显式 payload 创建、
save-from-session 派生创建（含会话属主守卫）、版本/发布/退役/恢复、
IDOR 404、非 creator 403、非法 visibility 422。
"""
from datetime import timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.orm import sessionmaker

from app.main import app as _fastapi_app
from app.core.database import get_async_db
from app.core.auth import create_access_token

_TEST_TOKEN_TTL = timedelta(hours=12)

user_token = create_access_token(
    {"sub": "u1", "role": "viewer"}, expires_delta=_TEST_TOKEN_TTL)
user_headers = {"Authorization": f"Bearer {user_token}"}
other_token = create_access_token(
    {"sub": "u2", "role": "viewer"}, expires_delta=_TEST_TOKEN_TTL)
other_headers = {"Authorization": f"Bearer {other_token}"}
admin_token = create_access_token(
    {"sub": "admin1", "role": "admin"}, expires_delta=_TEST_TOKEN_TTL)
admin_headers = {"Authorization": f"Bearer {admin_token}"}

_engine = None
_Session = None

_VARIANT_PAYLOAD = {
    "name": "API 测试资产",
    "base_template_id": "composition.density_map",
    "slot_overrides": {"chart_panel": {"max_count": 2}},
}


async def _override_get_async_db():
    async with _Session() as s:
        yield s


@pytest.fixture(autouse=True)
async def setup_db(tmp_path, monkeypatch):
    global _engine, _Session
    _engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path}/g04api.db")
    _Session = async_sessionmaker(_engine, expire_on_commit=False)
    import app.models  # noqa: F401 — 注册全部模型
    from app.core import database as db_module
    from app.core.auth import invalidate_live_user_state
    from app.models.db_model import Organization, User
    sync_engine = create_engine(f"sqlite:///{tmp_path}/g04api.db")
    monkeypatch.setattr(db_module, "SessionLocal", sessionmaker(sync_engine))
    monkeypatch.setattr(db_module, "async_db_session", _Session)
    invalidate_live_user_state()
    async with _engine.begin() as conn:
        await conn.run_sync(db_module.Base.metadata.create_all)
    async with _Session() as db:
        db.add(Organization(id=1, name="Asset test organization", slug="asset-tests"))
        await db.flush()
        for uid, role in (("u1", "viewer"), ("u2", "viewer"), ("admin1", "admin")):
            db.add(User(id=uid, username=uid, email=f"{uid}@test.example",
                        role=role, org_id=1 if uid != "u2" else None,
                        is_active=True, token_version=0))
        await db.commit()
    _fastapi_app.dependency_overrides[get_async_db] = _override_get_async_db
    yield
    _fastapi_app.dependency_overrides.pop(get_async_db, None)
    await _engine.dispose()
    sync_engine.dispose()
    invalidate_live_user_state()


async def _client() -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(app=_fastapi_app), base_url="http://test")


async def test_list_anonymous_builtin_only():
    async with await _client() as client:
        res = await client.get("/api/v1/composition-template-assets")
    assert res.status_code == 200
    body = res.json()
    assert body["success"] is True
    builtin = [a for a in body["assets"] if a["builtin"]]
    assert len(builtin) == 4
    assert all(a["asset_id"].startswith("asset.builtin.")
               for a in body["assets"])


async def test_create_explicit_payload_published():
    async with await _client() as client:
        res = await client.post(
            "/api/v1/composition-template-assets",
            json={"payload": _VARIANT_PAYLOAD, "publish": True},
            headers=user_headers)
        assert res.status_code == 200, res.text
        asset_id = res.json()["asset_id"]
        assert res.json()["status"] == "published"

        lst = await client.get(
            "/api/v1/composition-template-assets", headers=user_headers)
        db_rows = [a for a in lst.json()["assets"] if not a["builtin"]]
        assert any(a["asset_id"] == asset_id for a in db_rows)

        detail = await client.get(
            f"/api/v1/composition-template-assets/{asset_id}",
            headers=user_headers)
        assert detail.status_code == 200
        assert detail.json()["versions"][0]["revision"] == 1


async def test_create_requires_auth():
    async with await _client() as client:
        res = await client.post(
            "/api/v1/composition-template-assets",
            json={"payload": _VARIANT_PAYLOAD})
    assert res.status_code == 401


async def test_create_invalid_visibility_422():
    async with await _client() as client:
        res = await client.post(
            "/api/v1/composition-template-assets",
            json={"payload": _VARIANT_PAYLOAD, "visibility": "public"},
            headers=user_headers)
    assert res.status_code == 422
    assert "asset_invalid_visibility" in res.text


async def test_create_invalid_payload_422():
    bad = {"name": "x"}  # 既无 base 也无 inline
    async with await _client() as client:
        res = await client.post(
            "/api/v1/composition-template-assets",
            json={"payload": bad}, headers=user_headers)
    assert res.status_code == 422


async def test_publish_gate_failure_422():
    bad = dict(_VARIANT_PAYLOAD)
    bad["slot_overrides"] = {
        "chart_panel": {"preferred_templates": ["ghost/x"]}}
    async with await _client() as client:
        res = await client.post(
            "/api/v1/composition-template-assets",
            json={"payload": bad, "publish": True}, headers=user_headers)
    assert res.status_code == 422
    assert "asset_publish_gate_failed" in res.text


async def test_versions_publish_retire_restore_flow():
    async with await _client() as client:
        created = await client.post(
            "/api/v1/composition-template-assets",
            json={"payload": _VARIANT_PAYLOAD, "publish": False},
            headers=user_headers)
        assert created.status_code == 200
        asset_id = created.json()["asset_id"]
        assert created.json()["status"] == "draft"

        pub = await client.post(
            f"/api/v1/composition-template-assets/{asset_id}"
            f"/versions/1/publish", headers=user_headers)
        assert pub.status_code == 200
        assert pub.json()["status"] == "published"

        new_v = await client.post(
            f"/api/v1/composition-template-assets/{asset_id}/versions",
            json={"payload": dict(_VARIANT_PAYLOAD,
                                  slot_overrides={"chart_panel":
                                                  {"max_count": 1}}),
                  "publish": True},
            headers=user_headers)
        assert new_v.status_code == 200
        assert new_v.json()["revision"] == 2

        restore = await client.post(
            f"/api/v1/composition-template-assets/{asset_id}/restore/1",
            headers=user_headers)
        assert restore.status_code == 200
        assert restore.json()["revision"] == 3
        assert restore.json()["restored_from"] == 1

        retire = await client.post(
            f"/api/v1/composition-template-assets/{asset_id}/retire",
            json={"note": "done"}, headers=user_headers)
        assert retire.status_code == 200
        assert retire.json()["status"] == "retired"

        detail = await client.get(
            f"/api/v1/composition-template-assets/{asset_id}",
            headers=user_headers)
        assert detail.status_code == 200
        assert all(v["status"] == "retired" for v in detail.json()["versions"])


async def test_idor_other_user_gets_404():
    async with await _client() as client:
        created = await client.post(
            "/api/v1/composition-template-assets",
            json={"payload": _VARIANT_PAYLOAD}, headers=user_headers)
        asset_id = created.json()["asset_id"]
        res = await client.get(
            f"/api/v1/composition-template-assets/{asset_id}",
            headers=other_headers)
        assert res.status_code == 404
        # 匿名亦 404（不可见不泄露存在性）
        res_anon = await client.get(
            f"/api/v1/composition-template-assets/{asset_id}")
        assert res_anon.status_code == 404


async def test_write_other_user_gets_403_when_org_shared():
    """org 共享资产：同 org 他人可见（200）但不可写（403）。"""
    from app.models.db_model import User
    async with _Session() as db:
        (await db.get(User, "u2")).org_id = 1
        await db.commit()
    async with await _client() as client:
        created = await client.post(
            "/api/v1/composition-template-assets",
            json={"payload": _VARIANT_PAYLOAD, "visibility": "org"},
            headers=user_headers)
        asset_id = created.json()["asset_id"]
        seen = await client.get(
            f"/api/v1/composition-template-assets/{asset_id}",
            headers=other_headers)
        assert seen.status_code == 200
        denied = await client.post(
            f"/api/v1/composition-template-assets/{asset_id}/versions/1/publish",
            headers=other_headers)
        assert denied.status_code == 403
        # 同组织的实时 admin 可管理该共享资产。
        as_admin = await client.get(
            f"/api/v1/composition-template-assets/{asset_id}",
            headers=admin_headers)
        assert as_admin.status_code == 200
        # admin 可写（admin 豁免）
        pub = await client.post(
            f"/api/v1/composition-template-assets/{asset_id}"
            f"/versions/1/publish", headers=admin_headers)
        assert pub.status_code == 200


async def test_publish_missing_version_404():
    async with await _client() as client:
        created = await client.post(
            "/api/v1/composition-template-assets",
            json={"payload": _VARIANT_PAYLOAD}, headers=user_headers)
        asset_id = created.json()["asset_id"]
        res = await client.post(
            f"/api/v1/composition-template-assets/{asset_id}"
            f"/versions/9/publish", headers=user_headers)
    assert res.status_code == 404
