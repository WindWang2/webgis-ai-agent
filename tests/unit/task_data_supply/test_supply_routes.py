"""WP4 · 任务数据供给 REST 路由契约测试（/api/v1/task-data-supply）。

覆盖（与 agent 工具面共用 service 层，双入口一致性）：
- 覆盖对比：会话种子资产 → declared_only + 未核验披露（bbox 粗筛不宣称全覆盖）；
- 计划创建 → 计划查询（owner 隔离：跨会话 404 不泄漏存在性）；
- 执行：未批准 400 / 需确认 409（列 pending）/ 批准后 local 通道 completed；
- 鉴权先于查询：未认证 401/403 优先于业务逻辑。
"""
import pytest
from datetime import datetime, timezone
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.routes import task_data_supply as tds_routes
from app.core.database import Base, Engine, SessionLocal
from app.models.db_model import Conversation, User
from app.services.artifact_registry import register_artifact
from app.services.data_catalog.catalog import reset_data_catalog
from app.services.data_lifecycle.service import reset_lifecycle_service

_SID = "tds-route-session-01"
_OTHER = "tds-route-session-02"
_OWNER = "sup-owner"


@pytest.fixture(autouse=True)
def _seed_env():
    reset_data_catalog()
    reset_lifecycle_service()
    Base.metadata.create_all(bind=Engine)
    with SessionLocal() as db:
        if db.get(User, _OWNER) is None:
            db.add(User(
                id=_OWNER, username="sup-owner",
                email="sup-owner@example.com",
                password_hash="scrypt$16384$8$1$00$00",
                role="editor", is_active=True, token_version=0,
                created_at=datetime.now(timezone.utc)))
            db.commit()
        conv = db.get(Conversation, _SID)
        if conv is None:
            db.add(Conversation(
                id=_SID, user_id=_OWNER, title="tds route test",
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc)))
            db.commit()
    yield
    reset_data_catalog()
    reset_lifecycle_service()


async def _seed_asset() -> None:
    await register_artifact(
        _SID, artifact_id="ref:poi", producer_tool="query_osm_poi",
        artifact_type="poi_feature_set",
        descriptor={"bbox": [104.0, 30.0, 105.0, 31.0], "feature_count": 120,
                    "crs": "EPSG:4326"},
        metadata={"field_schema": {"name": {"type": "string"}},
                  "logical_role": "observation", "tags": ["osm", "poi"],
                  "display_name": "成都POI"})


def _app() -> FastAPI:
    application = FastAPI()
    application.include_router(tds_routes.router, prefix="/api/v1")
    return application


@pytest.fixture
async def client():
    application = _app()
    application.dependency_overrides[tds_routes.get_current_user] = (
        lambda: {"user_id": _OWNER})
    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def _scope_payload(**kw):
    base = {"session_id": _SID, "bbox": [104.0, 30.0, 105.0, 31.0],
            "roles": ["subject"]}
    base.update(kw)
    return base


@pytest.mark.asyncio
async def test_coverage_contract(client):
    await _seed_asset()
    resp = await client.post("/api/v1/task-data-supply/coverage",
                             json=_scope_payload())
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["success"] is True
    rows = body["coverage"]["roles"]
    assert rows and rows[0]["role"] == "subject"
    ev = rows[0]["evaluations"][0]
    assert ev["verdict"] == "declared_only"
    assert any("未核验" in d for d in ev["disclosures"])


@pytest.mark.asyncio
async def test_plan_create_and_get_with_owner_isolation(client):
    await _seed_asset()
    resp = await client.post("/api/v1/task-data-supply/plans",
                             json=_scope_payload(budget_max_bytes=10_000_000))
    assert resp.status_code == 200, resp.text
    plan = resp.json()["plan"]
    pid = plan["plan_id"]
    assert plan["state"] in ("requires_confirmation", "ready")

    got = await client.get(f"/api/v1/task-data-supply/plans/{pid}",
                           params={"session_id": _SID})
    assert got.status_code == 200
    assert got.json()["plan"]["plan_id"] == pid

    # 跨会话：不存在 + 归属不符 → 404（不泄漏存在性）
    other = await client.get(f"/api/v1/task-data-supply/plans/{pid}",
                             params={"session_id": _OTHER})
    assert other.status_code == 404


@pytest.mark.asyncio
async def test_execute_gate_and_completion(client):
    await _seed_asset()
    created = await client.post("/api/v1/task-data-supply/plans",
                                json=_scope_payload())
    plan = created.json()["plan"]
    pid = plan["plan_id"]

    denied = await client.post(
        f"/api/v1/task-data-supply/plans/{pid}/execute",
        params={"session_id": _SID}, json={"approved": False})
    assert denied.status_code == 400
    assert "TASK_SUPPLY_NOT_APPROVED" in denied.json()["detail"]

    needs = await client.post(
        f"/api/v1/task-data-supply/plans/{pid}/execute",
        params={"session_id": _SID}, json={"approved": True})
    assert needs.status_code == 409
    assert "TASK_SUPPLY_PLAN_NEEDS_CONFIRMATION" in needs.json()["detail"]

    rebuilt = await client.post(
        "/api/v1/task-data-supply/plans",
        json=_scope_payload(choices={"subject": "keep_gap"}))
    plan2 = rebuilt.json()["plan"]
    assert plan2["state"] == "ready"
    run = await client.post(
        f"/api/v1/task-data-supply/plans/{plan2['plan_id']}/execute",
        params={"session_id": _SID}, json={"approved": True})
    assert run.status_code == 200, run.text
    assert run.json()["results"][0]["outcome"] == "completed"


@pytest.mark.asyncio
async def test_unknown_role_rejected(client):
    resp = await client.post("/api/v1/task-data-supply/coverage",
                             json=_scope_payload(roles=["volcano"]))
    assert resp.status_code == 400
    assert "REQUIREMENT_ROLE_UNKNOWN" in resp.json()["detail"]


# ── R2-B/C 补齐：鉴权负例（此前 REVIEW.md 宣称但未兑现——现已真实存在） ──

@pytest.mark.asyncio
async def test_unauthenticated_rejected_before_business_logic():
    """不 override 鉴权依赖：未认证请求先于业务逻辑被拒（401）。"""
    application = _app()  # 不挂 dependency_overrides
    from app.core.auth import get_current_user as real_dep

    application.dependency_overrides[tds_routes.get_current_user] = (
        real_dep)
    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport,
                           base_url="http://test") as c:
        resp = await c.post("/api/v1/task-data-supply/coverage",
                            json=_scope_payload())
    assert resp.status_code in (401, 403), resp.text


@pytest.mark.asyncio
async def test_other_user_session_is_404():
    """会话存在但归属他人 → 404（不泄漏存在性；非「不存在」分支）。"""
    application = _app()
    application.dependency_overrides[tds_routes.get_current_user] = (
        lambda: {"user_id": "sup-intruder"})
    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport,
                           base_url="http://test") as c:
        resp = await c.post("/api/v1/task-data-supply/coverage",
                            json=_scope_payload())
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_anonymous_with_bad_owner_token_is_404():
    """匿名 + 错误 X-Session-Token → fail-closed 404（SEC-08 继承）。"""
    application = _app()
    application.dependency_overrides[tds_routes.get_current_user] = (
        lambda: {"user_id": "anonymous"})
    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport,
                           base_url="http://test") as c:
        resp = await c.post(
            "/api/v1/task-data-supply/coverage",
            json=_scope_payload(),
            headers={"X-Session-Token": "not-the-token"})
    assert resp.status_code == 404
