"""G44 REST 面测试 —— owner API 同一 validator（F26）+ 会话 ACL 边界。

鉴权依赖直通（跨会话负例在服务层 F29 覆盖；这里验证路由正确挂载服务层
判定与 400/404/409 映射）。
"""
from __future__ import annotations

import uuid
from pathlib import Path
from unittest.mock import MagicMock
from urllib.parse import quote

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.services.feature_edit import executor as fe_executor
from app.services.feature_edit import service as fe_service
from app.services.session_data import MemorySessionStore

FACILITIES = {
    "type": "FeatureCollection",
    "features": [
        {"type": "Feature", "id": f"fac-{i}",
         "geometry": {"type": "Point", "coordinates": [116.0 + 0.01 * i, 39.0]},
         "properties": {"asset_no": f"A-{i:03d}",
                        "status": "planned" if i % 2 == 0 else "active",
                        "capacity": 10.0 + i}}
        for i in range(3)
    ],
}

DOC = {
    "version": 1, "title": "设施合同",
    "fields": [
        {"field": "asset_no", "type": "string", "required": True},
        {"field": "status", "type": "string", "enum": ["planned", "active", "retired"]},
        {"field": "capacity", "type": "number", "min": 0},
    ],
    "cross_rules": [
        {"rule_id": "u", "kind": "unique", "fields": ["asset_no"]},
    ],
}


@pytest.fixture
def app():
    a = FastAPI()
    from app.api.routes import field_contracts as fc_routes
    from app.api.routes import feature_edits as fe_routes

    a.include_router(fc_routes.router, prefix="/api/v1")
    a.include_router(fe_routes.router, prefix="/api/v1")
    return a


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture(autouse=True)
def _auth(app):
    from app.services.auth_history_bridge import require_owned_session

    owner = MagicMock()
    owner.owner_user_id = "owner-route-1"
    app.dependency_overrides[require_owned_session] = lambda: owner


@pytest.fixture(autouse=True)
def _storage(tmp_path: Path, monkeypatch):
    store = MemorySessionStore()
    import app.services.session_data as sd

    monkeypatch.setattr(sd, "session_data_manager", store)
    for mod in (fe_service, fe_executor):
        monkeypatch.setattr(mod, "session_data_manager", store)
    from app.services.field_contracts import service as fc_service

    monkeypatch.setattr(fc_service, "_findings_dir",
                        lambda session_id: tmp_path / "fc" / session_id)
    yield store


def _url(session_id: str, suffix: str = "") -> str:
    return f"/api/v1/chat/sessions/{quote(session_id)}/field-contracts{suffix}"


async def _seed(store, session_id: str) -> str:
    return await store.store(session_id, {"geojson": FACILITIES}, prefix="data")


class TestContractLifecycleRoutes:
    async def test_create_assess_activate_retire_roundtrip(self, client, _storage):
        s = f"sess-g44r-{uuid.uuid4().hex[:8]}"
        ref = await _seed(_storage, s)
        resp = await client.post(_url(s), json={"ref_id": ref, "document": DOC})
        assert resp.status_code == 200, resp.text
        cid = resp.json()["contract_id"]

        resp2 = await client.post(_url(s, f"/{cid}/assess"))
        assert resp2.status_code == 200, resp2.text
        aid = resp2.json()["assessment"]["id"]
        # 后台评估任务在同一 event loop —— 显式等待其终态（有界）
        from app.services.field_contracts import service as fc_service

        task = fc_service._RUNNERS.get(aid)
        if task is not None:
            await task
        else:
            assessment = await fc_service.get_assessment(s, aid)
            assert assessment["status"] == "completed", assessment
        resp3 = await client.post(_url(s, f"/{cid}/activate"))
        assert resp3.status_code == 200, resp3.text
        assert resp3.json()["activated"] is True
        resp4 = await client.post(_url(s, f"/{cid}/retire"))
        assert resp4.status_code == 200, resp4.text

    async def test_f26_direct_owner_api_blocked_by_same_validator(self, client, _storage):
        """直接 REST 面提交非法属性（越 number 区间）→ 同一 central validator 拒绝。"""
        s = f"sess-g44r-{uuid.uuid4().hex[:8]}"
        ref = await _seed(_storage, s)
        from app.services.field_contracts import service as fc_service

        created = await fc_service.create_contract(s, ref_id=ref, document=DOC)
        await fc_service.start_assessment(s, created["contract_id"], background=False)
        await fc_service.activate_contract(s, created["contract_id"])
        # owner REST preview：capacity=-1 违反 min=0 → 服务端同一 central validator
        # 拒绝（plan committable=false + contract_violation；与 G14 既有语义一致，
        # 前端不是校验边界）
        resp = await client.post(
            f"/api/v1/chat/sessions/{quote(s)}/feature-edits/preview",
            json={
                "target": {"ref_id": ref, "feature_ids": ["fac-0"]},
                "ops": [{"op": "set_fields", "fields": {"capacity": -1}}],
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["committable"] is False
        codes = [f.get("code") for f in body.get("validation", [])]
        assert "contract_violation" in codes
        # 无 plan_id 可决策（invalid plan 不入 store）→ 无写路径
        assert body.get("plan_id") is None or body.get("status") == "invalid"
        payload = await _storage.get(s, ref)
        assert payload["geojson"]["features"][0]["properties"]["capacity"] == 10.0

    async def test_invalid_document_400_with_errors(self, client, _storage):
        s = f"sess-g44r-{uuid.uuid4().hex[:8]}"
        ref = await _seed(_storage, s)
        bad = {"version": 1, "fields": [{"field": "x", "type": "string"}],
               "cross_rules": [{"rule_id": "u", "kind": "unique", "fields": ["nope"]}]}
        resp = await client.post(_url(s), json={"ref_id": ref, "document": bad})
        assert resp.status_code == 400
        assert resp.json()["detail"]["code"] == "invalid_document"

    async def test_binding_and_observe(self, client, _storage):
        s = f"sess-g44r-{uuid.uuid4().hex[:8]}"
        ref = await _seed(_storage, s)
        resp = await client.get(
            _url(s, f"/datasets/{quote(ref, safe='')}/binding")
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "missing"
        resp2 = await client.get(
            _url(s, f"/datasets/{quote(ref, safe='')}/observe")
        )
        assert resp2.status_code == 200
        assert "asset_no" in resp2.json()["observed"]
