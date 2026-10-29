"""数据准备域 API V1 —— M5 单测（鉴权/越权负例 + 全端点正例 + typed 错误面）。

鉴权纪律镜像 uploads：会话不存在/不属于调用方一律 404（不泄露存在性）；
匿名会话带 X-Session-Token（SEC-08）；首写会话（无 Conversation 行）放行。
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from starlette.exceptions import HTTPException as StarletteHTTPException
from fastapi.exceptions import RequestValidationError

from app.api.routes import data_preparation as _mod
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
    app = FastAPI()
    app.add_exception_handler(StarletteHTTPException, unified_http_exception_handler)
    app.add_exception_handler(RequestValidationError, unified_validation_exception_handler)
    app.dependency_overrides[get_current_user_optional_with_version] = lambda: _mock_user
    app.include_router(_mod.router, prefix="/api/v1")
    yield app
    reset_dataset_semantic_store()


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def _school_payload(n=20, bad_every=0):
    features = []
    for i in range(n):
        lon, lat = 104.0 + (i % 10) * 0.01, 30.0 + (i % 5) * 0.01
        if bad_every and i % bad_every == 0:
            lon, lat = 999.0, 999.0
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": {"学校编号": f"S{i:03d}", "学校名称": f"第{i}小学",
                           "经度": lon, "纬度": lat, "备注": "x" * 300},
        })
    return {"geojson": {"type": "FeatureCollection", "features": features},
            "crs": "EPSG:4326"}


async def _seed_ref(session_id, payload=None):
    from app.services.session_data import session_data_manager
    payload = payload or _school_payload()
    return await session_data_manager.store(session_id, payload, prefix="test")


async def _seed_conversation(session_id, user_id="other-user", owner_token=None):
    from app.models.db_model import Conversation
    from app.tools._utils import async_db_session
    async with async_db_session() as db:
        db.add(Conversation(id=session_id, user_id=user_id,
                            owner_token=owner_token))
        await db.commit()


# ── 鉴权负例 ───────────────────────────────────────────────────────────────

class TestAuthNegatives:
    async def test_missing_session_id_rejected(self, client):
        resp = await client.post("/api/v1/data-preparation/inspect",
                                 json={"session_id": "", "source_ref": "ref:x"})
        assert resp.status_code == 400   # 路由守卫（空串在 pydantic 后被拦）

    async def test_foreign_session_404(self, client):
        await _seed_conversation("sess-foreign-1", user_id="other-user")
        resp = await client.post(
            "/api/v1/data-preparation/inspect",
            json={"session_id": "sess-foreign-1", "source_ref": "ref:any"})
        assert resp.status_code == 404

    async def test_forged_owner_token_404(self, client):
        await _seed_conversation("sess-anon-1", user_id=None,
                                 owner_token="real-token")
        resp = await client.post(
            "/api/v1/data-preparation/inspect",
            json={"session_id": "sess-anon-1", "source_ref": "ref:any"},
            headers={"X-Session-Token": "forged-token"})
        assert resp.status_code == 404

    async def test_anonymous_correct_token_ok(self, client):
        await _seed_conversation("sess-anon-2", user_id=None,
                                 owner_token="real-token")
        ref = await _seed_ref("sess-anon-2")
        resp = await client.post(
            "/api/v1/data-preparation/inspect",
            json={"session_id": "sess-anon-2", "source_ref": ref},
            headers={"X-Session-Token": "real-token"})
        assert resp.status_code == 200
        assert resp.json()["receipt"]["row_count"] == 20

    async def test_cross_session_ref_isolated(self, client):
        # ref 属于会话 A 的命名空间；会话 B（首写、同用户）也读不到它
        ref = await _seed_ref("sess-a-1")
        resp = await client.post(
            "/api/v1/data-preparation/inspect",
            json={"session_id": "sess-b-1", "source_ref": ref})
        assert resp.status_code == 404
        assert "REF_MISSING" in str(resp.json())


# ── inspect 正例与有界性 ──────────────────────────────────────────────────

class TestInspect:
    async def test_inspect_full_shape(self, client):
        sid = "sess-inspect-1"
        ref = await _seed_ref(sid, _school_payload(60))
        resp = await client.post(
            "/api/v1/data-preparation/inspect",
            json={"session_id": sid, "source_ref": ref})
        assert resp.status_code == 200
        body = resp.json()
        assert body["sample_truncated"] is True
        assert len(body["sample"]) == 50                    # 行预算
        # 字段预算：6 字段全在（≤64）；超长字符串截断到 200
        props = body["sample"][0]["properties"]
        assert len(props["备注"]) <= 200
        # 提案：学校名称 → name 高置信
        by_field = {p["field"]: p for p in body["proposals"]}
        assert by_field["学校名称"]["candidates"][0]["role"] == "name"
        # 收据：20 行、PK 候选唯一
        assert body["receipt"]["pk_field"] == "学校编号"
        assert body["receipt"]["pk_status"] == "unique"

    async def test_inspect_bounded_no_geometry_dump(self, client):
        sid = "sess-inspect-2"
        ref = await _seed_ref(sid)
        resp = await client.post(
            "/api/v1/data-preparation/inspect",
            json={"session_id": sid, "source_ref": ref})
        sample = resp.json()["sample"][0]
        assert sample["geometry_type"] == "Point"
        assert isinstance(sample["first_position"], list) and len(sample["first_position"]) == 2
        assert "coordinates" not in sample          # 绝不携带几何本体

    async def test_inspect_missing_ref_404(self, client):
        resp = await client.post(
            "/api/v1/data-preparation/inspect",
            json={"session_id": "sess-inspect-3", "source_ref": "ref:nope"})
        assert resp.status_code == 404


# ── plan / preview / execute / 生命周期 ───────────────────────────────────

class TestPlanLifecycle:
    async def test_plan_rejects_swap_without_evidence(self, client):
        sid = "sess-plan-1"
        ref = await _seed_ref(sid, _school_payload(10))   # 健康坐标 → 无嫌疑
        resp = await client.post(
            "/api/v1/data-preparation/plan",
            json={"session_id": sid, "source_ref": ref,
                  "steps": [{"type": "axis_swap", "lon_field": "经度",
                             "lat_field": "纬度"}]})
        assert resp.status_code == 422
        assert "SWAP_WITHOUT_EVIDENCE" in str(resp.json())

    async def test_full_lifecycle_filter_and_mapping(self, client):
        sid = "sess-plan-2"
        ref = await _seed_ref(sid, _school_payload(20, bad_every=10))
        # 1) plan：空步集且未开 auto → typed 拒绝（EMPTY_PLAN）
        resp = await client.post(
            "/api/v1/data-preparation/plan",
            json={"session_id": sid, "source_ref": ref, "steps": []})
        assert resp.status_code == 422
        assert "EMPTY_PLAN" in str(resp.json())
        # 显式给映射步 + auto 自动补过滤/高置信映射
        resp = await client.post(
            "/api/v1/data-preparation/plan",
            json={"session_id": sid, "source_ref": ref, "auto_steps": True,
                  "steps": [{"type": "field_mapping", "role": "name",
                             "field": "学校名称", "source": "user"}]})
        assert resp.status_code == 200
        plan = resp.json()["plan"]
        types = [s["type"] for s in plan["steps"]]
        assert "field_mapping" in types and "filter_rows" in types
        assert plan["mutates_data"] is True
        # 2) preview
        resp = await client.post(
            f"/api/v1/data-preparation/plan/{plan['plan_id']}/preview",
            params={"session_id": sid})
        assert resp.status_code == 200
        assert resp.json()["quality_delta_sample"].get("impossible_coordinates") == -1
        # 3) execute
        resp = await client.post(
            f"/api/v1/data-preparation/plan/{plan['plan_id']}/execute",
            params={"session_id": sid})
        assert resp.status_code == 200
        receipt = resp.json()["receipt"]
        assert receipt["row_before"] == 20 and receipt["row_after"] == 18
        assert receipt["output_ref"].startswith("ref:prep-")
        # 4) receipt 幂等查询
        resp = await client.get(
            f"/api/v1/data-preparation/plan/{plan['plan_id']}/receipt",
            params={"session_id": sid, "session_hash": plan["plan_hash"]})
        assert resp.status_code == 200
        assert resp.json()["output_ref"] == receipt["output_ref"]
        # 5) executed 不可 preview（状态回退缺口，R1 #5）也不可作废
        resp = await client.post(
            f"/api/v1/data-preparation/plan/{plan['plan_id']}/preview",
            params={"session_id": sid})
        assert resp.status_code == 409
        assert "PLAN_ALREADY_EXECUTED" in str(resp.json())
        resp = await client.delete(
            f"/api/v1/data-preparation/plan/{plan['plan_id']}",
            params={"session_id": sid})
        assert resp.status_code == 409

    async def test_discard_proposed_plan(self, client):
        sid = "sess-plan-3"
        ref = await _seed_ref(sid)
        resp = await client.post(
            "/api/v1/data-preparation/plan",
            json={"session_id": sid, "source_ref": ref,
                  "steps": [{"type": "field_mapping", "role": "id",
                             "field": "学校编号", "source": "user"}]})
        plan_id = resp.json()["plan"]["plan_id"]
        resp = await client.delete(
            f"/api/v1/data-preparation/plan/{plan_id}",
            params={"session_id": sid})
        assert resp.status_code == 200
        assert resp.json()["state"] == "discarded"
        # 作废后执行 → 409
        resp = await client.post(
            f"/api/v1/data-preparation/plan/{plan_id}/execute",
            params={"session_id": sid})
        assert resp.status_code == 409

    async def test_plans_listing(self, client):
        sid = "sess-plan-4"
        ref = await _seed_ref(sid)
        await client.post(
            "/api/v1/data-preparation/plan",
            json={"session_id": sid, "source_ref": ref,
                  "steps": [{"type": "field_mapping", "role": "name",
                             "field": "学校名称", "source": "user"}]})
        resp = await client.get("/api/v1/data-preparation/plans",
                                params={"session_id": sid})
        assert resp.status_code == 200
        assert len(resp.json()["plans"]) == 1


# ── 字段绑定写回 ───────────────────────────────────────────────────────────

class TestFieldBindings:
    async def test_bind_roles_versioned_writeback(self, client):
        sid = "sess-bind-1"
        ref = await _seed_ref(sid)
        resp = await client.post(
            "/api/v1/data-preparation/bindings/fields",
            json={"session_id": sid, "source_ref": ref,
                  "roles": {"学校编号": "id", "学校名称": "name",
                            "备注": "address"}})
        assert resp.status_code == 200
        body = resp.json()
        assert set(body["bound_fields"]) == {"学校编号", "学校名称"}
        # address 无 SemanticFieldRole 对应 → 诚实跳过披露
        assert ["备注", "NO_SEMANTIC_EQUIVALENT"] in body["skipped"]
        # descriptor 版本化：指纹非空
        assert body["descriptor_fingerprint"].startswith("dsd-v1:")

    async def test_bind_unknown_role_rejected(self, client):
        sid = "sess-bind-2"
        ref = await _seed_ref(sid)
        resp = await client.post(
            "/api/v1/data-preparation/bindings/fields",
            json={"session_id": sid, "source_ref": ref,
                  "roles": {"学校名称": "bogus_role"}})
        assert resp.status_code == 200
        assert ["学校名称", "ROLE_UNKNOWN"] in resp.json()["skipped"]
