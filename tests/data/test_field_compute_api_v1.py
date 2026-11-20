"""G19 字段计算 API V1 —— 真实 ASGI 路由层回归（WP09）。

与 `test_preparation_api_v1.py` 同款鉴权纪律：会话不存在/不属于调用方 404
（不泄露存在性）；用 FastAPI + httpx ASGITransport 走真实路由、依赖覆盖与
typed 错误映射，不绕过 `_require_session`。
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from httpx import ASGITransport, AsyncClient
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.routes import field_compute as _mod
from app.core.auth import get_current_user_optional_with_version
from app.core.exception import (
    unified_http_exception_handler,
    unified_validation_exception_handler,
)

_mock_user = {"user_id": "test-user"}


@pytest.fixture
def app(monkeypatch, tmp_path):
    monkeypatch.setattr("app.services.mapspec.store.BASE_STORAGE_DIR", tmp_path)
    from app.services.dataset_semantics import reset_dataset_semantic_store
    reset_dataset_semantic_store()
    a = FastAPI()
    a.add_exception_handler(StarletteHTTPException, unified_http_exception_handler)
    a.add_exception_handler(RequestValidationError, unified_validation_exception_handler)
    a.dependency_overrides[get_current_user_optional_with_version] = lambda: _mock_user
    a.include_router(_mod.router, prefix="/api/v1")
    yield a
    reset_dataset_semantic_store()


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _seed_conversation(session_id, user_id="test-user", owner_token=None):
    from app.models.db_model import Conversation, User
    from app.tools._utils import async_db_session
    async with async_db_session() as db:
        if user_id and await db.get(User, user_id) is None:
            db.add(User(id=user_id, username=user_id,
                        email=f"{user_id}@example.com", role="viewer",
                        is_active=True))
            await db.flush()
        if await db.get(Conversation, session_id) is None:
            db.add(Conversation(id=session_id, user_id=user_id,
                                owner_token=owner_token))
            await db.commit()


def _payload():
    return {"geojson": {"type": "FeatureCollection", "features": [
        {"type": "Feature",
         "geometry": {"type": "Point", "coordinates": [104.0 + i * 0.01, 30.0]},
         "properties": {"人口": 1000 + i * 100, "面积 平方千米": 10 + i,
                        "名称": f"区{i}"}}
        for i in range(4)]},
        "crs": "EPSG:4326", "crs_source": "declared"}


async def _seed_ref(session_id):
    from app.services.session_data import session_data_manager
    await _seed_conversation(session_id)
    return await session_data_manager.store(session_id, _payload(), prefix="test")


class TestCatalogAndAuth:
    async def test_catalog_is_public_read_only_contract(self, client):
        resp = await client.get("/api/v1/field-compute/catalog")
        assert resp.status_code == 200
        body = resp.json()
        assert body["function_set_version"] == "g19-fc-1.0"
        assert 0 < len(body["functions"]) <= 40
        assert body["limits"]["max_rows"] > 0

    async def test_foreign_session_is_404(self, client):
        await _seed_conversation("sess-fc-api-foreign", user_id="other-user")
        resp = await client.post(
            "/api/v1/field-compute/describe",
            json={"session_id": "sess-fc-api-foreign", "source_ref": "ref:any"})
        assert resp.status_code == 404

    async def test_missing_session_rejected(self, client):
        resp = await client.post(
            "/api/v1/field-compute/describe",
            json={"session_id": "", "source_ref": "ref:any"})
        assert resp.status_code in (400, 404)


class TestApiJourney:
    async def test_describe_plan_preview_execute_result(self, client):
        sid = "sess-fc-api-journey"
        ref = await _seed_ref(sid)
        d = await client.post("/api/v1/field-compute/describe",
                              json={"session_id": sid, "source_ref": ref})
        assert d.status_code == 200
        assert d.json()["data_shape"] == "geojson"
        assert d.json()["row_count"] == 4
        assert "features" not in d.json()

        p = await client.post("/api/v1/field-compute/plan", json={
            "session_id": sid, "source_ref": ref,
            "columns": [
                {"name": "人口密度", "expression": "{人口} / {面积 平方千米}"},
                {"name": "分级", "expression": "if({人口密度} > 100, '高', '低')"},
            ],
            "field_units": {"人口": "persons",
                            "面积 平方千米": "square_kilometers"},
            "timezone": "UTC", "error_policy": "null",
        })
        assert p.status_code == 200, p.text
        plan = p.json()["plan"]
        assert plan["compute_fingerprint"] and plan["content_hash"]
        assert p.json()["column_order"] == ["人口密度", "分级"]

        pv = await client.post(
            f"/api/v1/field-compute/plan/{plan['plan_id']}/preview",
            params={"session_id": sid, "generation": 1})
        assert pv.status_code == 200, pv.text
        assert pv.json()["kind"] == "sample"
        assert pv.json()["confirmation"]["params_fingerprint"] == plan["compute_fingerprint"]
        assert pv.json()["columns"][0]["measurement_kind"] == "density"

        ex = await client.post(
            f"/api/v1/field-compute/plan/{plan['plan_id']}/execute",
            params={"session_id": sid},
            json={"expected_fingerprint": plan["compute_fingerprint"],
                  "project_id": "proj-api"})
        assert ex.status_code == 200, ex.text
        receipt = ex.json()["receipt"]
        assert receipt["output_ref"].startswith("ref:fc-")
        assert receipt["row_count"] == 4
        assert receipt["persistence"] == "project_artifact"

        got = await client.get("/api/v1/field-compute/result",
                               params={"session_id": sid,
                                       "output_ref": receipt["output_ref"]})
        assert got.status_code == 200
        assert "人口密度" in got.json()["fields"]
        assert got.json()["sample_rows"][0]["人口密度"] == 100.0

        pr = await client.get("/api/v1/field-compute/project-results",
                              params={"session_id": sid, "project_id": "proj-api"})
        assert pr.status_code == 200 and len(pr.json()["items"]) == 1

    async def test_compile_error_is_typed_422(self, client):
        sid = "sess-fc-api-error"
        ref = await _seed_ref(sid)
        resp = await client.post("/api/v1/field-compute/plan", json={
            "session_id": sid, "source_ref": ref,
            "columns": [{"name": "坏", "expression": "__import__('os').system('x')"}],
        })
        assert resp.status_code == 422
        body = resp.json()
        # 统一错误信封把结构化 detail 放在 data；legacy 信封直接放 detail。
        detail = body.get("detail") if isinstance(body.get("detail"), dict) \
            else body.get("data")
        assert isinstance(detail, dict), body
        assert detail["code"] in ("UNKNOWN_NODE", "UNKNOWN_FUNCTION",
                                  "UNKNOWN_FIELD", "UNKNOWN_OPERATOR")
        assert "hint" in detail

    async def test_stale_fingerprint_is_409(self, client):
        sid = "sess-fc-api-stale"
        ref = await _seed_ref(sid)
        p = await client.post("/api/v1/field-compute/plan", json={
            "session_id": sid, "source_ref": ref,
            "columns": [{"name": "密度", "expression": "{人口} / {面积 平方千米}"}],
        })
        plan = p.json()["plan"]
        resp = await client.post(
            f"/api/v1/field-compute/plan/{plan['plan_id']}/execute",
            params={"session_id": sid}, json={"expected_fingerprint": "outdated"})
        assert resp.status_code == 409
        body = resp.json()
        detail = body.get("detail") if isinstance(body.get("detail"), dict) \
            else body.get("data")
        assert detail["code"] == "STALE_PLAN"


class TestCancelEndpoint:
    """cancel 端点真实 ASGI 覆盖（复审：不能只用服务层直调冒充端点验证）。"""

    async def test_unknown_plan_is_typed_404(self, client):
        sid = "sess-fc-api-cancel-404"
        await _seed_ref(sid)
        resp = await client.post(
            "/api/v1/field-compute/plan/fc-does-not-exist/cancel",
            params={"session_id": sid})
        assert resp.status_code == 404, resp.text
        body = resp.json()
        detail = body.get("detail") if isinstance(body.get("detail"), dict) \
            else body.get("data")
        assert detail["code"] == "PLAN_NOT_FOUND"

    async def test_idle_plan_cancel_is_false_and_not_poisoned(self, client):
        sid = "sess-fc-api-cancel-idle"
        ref = await _seed_ref(sid)
        p = await client.post("/api/v1/field-compute/plan", json={
            "session_id": sid, "source_ref": ref,
            "columns": [{"name": "密度", "expression": "{人口} / 1"}],
        })
        plan = p.json()["plan"]

        before = await client.post(
            f"/api/v1/field-compute/plan/{plan['plan_id']}/cancel",
            params={"session_id": sid})
        assert before.status_code == 200
        assert before.json()["cancelled"] is False

        ex = await client.post(
            f"/api/v1/field-compute/plan/{plan['plan_id']}/execute",
            params={"session_id": sid}, json={})
        assert ex.status_code == 200, ex.text
        assert ex.json()["receipt"]["output_ref"].startswith("ref:fc-")

        after = await client.post(
            f"/api/v1/field-compute/plan/{plan['plan_id']}/cancel",
            params={"session_id": sid})
        assert after.status_code == 200
        assert after.json()["cancelled"] is False
