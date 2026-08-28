"""GET /chat/sessions/{id}/plan — SessionPlan 面板水合端点（#1047）。

端到端验证（真路由 + 真 require_owned_session 守卫 + 临时 SQLite + 内存
session store，无 live LLM / Pi RPC）：
  - 信封存在 → 200 投影（字段拼写给死：envelope_id / user_goal / query /
    plan_id / recipe_id / progress(capability,status,bound_ref) / replaced /
    superseded / updated_at）
  - 无信封 → 204（显式空结果，绝不报错）
  - 槽位已开但 GIS 章节为空 → query/plan_id/recipe_id 为 null 的显式空投影
  - 会话隔离：信封只随所请求的 session 出现
  - 只读且只返回当前信封 —— 历史信封永不外泄
  - 所有权：无 token / 不存在的会话 → 404（复用现有守卫语义）
"""
import os

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

# 必须在 import app.* 之前设置（同 test_sec08_session_owner_token.py）
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-sec08-32-chars-okxxxxx")
os.environ.setdefault("ENV", "development")

from app.models.db_model import Base  # noqa: E402
from app.core.database import get_async_db  # noqa: E402
from app.tools import _utils  # noqa: E402
from app.api.routes import chat as chat_routes  # noqa: E402
from app.services.history_service_async import AsyncHistoryService  # noqa: E402
from app.services.session_data import session_data_manager  # noqa: E402
from app.services.session_plan import (  # noqa: E402
    CURRENT_ALIAS,
    HISTORY_ALIAS_PREFIX,
    STORE_PREFIX,
    CapabilityProgress,
    SessionPlan,
)


def _gis(query: str, scope: str, subject: str = "小学") -> dict:
    return {
        "plan_id": f"plan-{scope}",
        "query": query,
        "recipe_id": "poi_distribution_overview",
        "intent": {
            "query": query,
            "task": "distribution_overview",
            "scope": {"name": scope, "level": "city"},
            "subject": {"type": "poi", "category": subject},
        },
        "data_requirements": [
            {"capability": "poi_query", "status": "pending", "resolved_tool": "query_local_poi"},
        ],
        "analysis_steps": [],
        "status": "draft",
    }


async def _seed_current_envelope(plan: SessionPlan) -> None:
    """直接写 store + CURRENT_ALIAS（save_session_plan 的无锁落盘路径）。"""
    ref = await session_data_manager.store(plan.session_id, plan.model_dump(), prefix=STORE_PREFIX)
    await session_data_manager.set_alias(plan.session_id, ref, CURRENT_ALIAS)


@pytest_asyncio.fixture
async def app_and_db(tmp_path):
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'session-plan-route.db'}"
    test_engine = create_async_engine(db_url, connect_args={"check_same_thread": False})
    test_session = async_sessionmaker(bind=test_engine, expire_on_commit=False)

    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async def override_get_async_db():
        async with test_session() as s:
            yield s

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def override_async_db_session():
        async with test_session() as s:
            try:
                yield s
                await s.commit()
            except Exception:
                await s.rollback()
                raise

    from unittest.mock import patch

    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(chat_routes.router, prefix="/api/v1")
    app.dependency_overrides[get_async_db] = override_get_async_db
    # _utils.async_db_session 在这些路由不经过；override 挂上以防未来路由内使用。
    patcher = patch.object(_utils, "async_db_session", override_async_db_session)
    patcher.start()
    try:
        yield app, test_session
    finally:
        patcher.stop()
        await test_engine.dispose()
        await session_data_manager.clear_session("sess-plan-route-a")
        await session_data_manager.clear_session("sess-plan-route-b")


@pytest_asyncio.fixture
async def client(app_and_db):
    app, _ = app_and_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest_asyncio.fixture
async def db(app_and_db):
    _, session = app_and_db
    async with session() as s:
        yield s


@pytest.mark.asyncio
async def test_plan_projection_shape(client, db):
    """信封存在 → 200，投影字段拼写给死（含四种状态行与 bound_ref）。"""
    sid = "sess-plan-route-a"
    async with db as session:
        conv = await AsyncHistoryService(session).get_or_create_conversation(sid, user_id=None)
        token = conv.owner_token

    await _seed_current_envelope(
        SessionPlan(
            envelope_id="sp-shape-1",
            session_id=sid,
            user_goal="成都市小学分布情况",
            gis_chapter=_gis("成都市小学分布情况", "成都市"),
            progress=[
                CapabilityProgress(capability="poi_query", status="complete", bound_ref="ref:geojson-poi"),
                CapabilityProgress(capability="admin_boundary", status="pending"),
                CapabilityProgress(capability="heatmap", status="voided"),
                CapabilityProgress(capability="buffer", status="unavailable"),
            ],
            replaced=True,
        )
    )

    resp = await client.get(
        f"/api/v1/chat/sessions/{sid}/plan",
        headers={"X-Session-Token": token},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == {
        "session_id",
        "envelope_id",
        "user_goal",
        "query",
        "plan_id",
        "recipe_id",
        "progress",
        "replaced",
        "superseded",
        "updated_at",
    }
    assert body["session_id"] == sid
    assert body["envelope_id"] == "sp-shape-1"
    assert body["user_goal"] == "成都市小学分布情况"
    assert body["query"] == "成都市小学分布情况"
    assert body["plan_id"] == "plan-成都市"
    assert body["recipe_id"] == "poi_distribution_overview"
    assert body["replaced"] is True
    assert body["superseded"] is False
    assert isinstance(body["updated_at"], float)
    assert body["progress"] == [
        {"capability": "poi_query", "status": "complete", "bound_ref": "ref:geojson-poi"},
        {"capability": "admin_boundary", "status": "pending", "bound_ref": ""},
        {"capability": "heatmap", "status": "voided", "bound_ref": ""},
        {"capability": "buffer", "status": "unavailable", "bound_ref": ""},
    ]


@pytest.mark.asyncio
async def test_plan_route_204_when_no_envelope(client, db):
    """无信封 → 显式 204 空结果，绝不是错误。"""
    sid = "sess-plan-route-b"
    async with db as session:
        conv = await AsyncHistoryService(session).get_or_create_conversation(sid, user_id=None)
        token = conv.owner_token

    resp = await client.get(
        f"/api/v1/chat/sessions/{sid}/plan",
        headers={"X-Session-Token": token},
    )
    assert resp.status_code == 204
    assert resp.content == b""


@pytest.mark.asyncio
async def test_plan_route_slot_without_chapter_is_explicit_empty(client, db):
    """槽位已开（intent 未跑）→ 200 空投影：章节三字段为 null、progress 空。"""
    sid = "sess-plan-route-a"
    async with db as session:
        conv = await AsyncHistoryService(session).get_or_create_conversation(sid, user_id=None)
        token = conv.owner_token

    await _seed_current_envelope(
        SessionPlan(envelope_id="sp-empty-slot", session_id=sid)
    )

    resp = await client.get(
        f"/api/v1/chat/sessions/{sid}/plan",
        headers={"X-Session-Token": token},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["envelope_id"] == "sp-empty-slot"
    assert body["query"] is None
    assert body["plan_id"] is None
    assert body["recipe_id"] is None
    assert body["progress"] == []
    assert body["user_goal"] == ""


@pytest.mark.asyncio
async def test_plan_route_is_session_scoped(client, db):
    """信封只随所请求的 session 出现：B 会话读不到 A 的信封。"""
    sid_a = "sess-plan-route-a"
    sid_b = "sess-plan-route-b"
    async with db as session:
        conv_a = await AsyncHistoryService(session).get_or_create_conversation(sid_a, user_id=None)
        conv_b = await AsyncHistoryService(session).get_or_create_conversation(sid_b, user_id=None)

    await _seed_current_envelope(
        SessionPlan(
            envelope_id="sp-scoped-a",
            session_id=sid_a,
            user_goal="成都市小学分布情况",
            gis_chapter=_gis("成都市小学分布情况", "成都市"),
        )
    )

    resp_a = await client.get(
        f"/api/v1/chat/sessions/{sid_a}/plan",
        headers={"X-Session-Token": conv_a.owner_token},
    )
    assert resp_a.status_code == 200
    assert resp_a.json()["envelope_id"] == "sp-scoped-a"

    resp_b = await client.get(
        f"/api/v1/chat/sessions/{sid_b}/plan",
        headers={"X-Session-Token": conv_b.owner_token},
    )
    assert resp_b.status_code == 204


@pytest.mark.asyncio
async def test_plan_route_returns_current_envelope_only(client, db):
    """只读、永不外泄历史：supersede 后只返回当前信封，无历史列表字段。"""
    sid = "sess-plan-route-a"
    async with db as session:
        conv = await AsyncHistoryService(session).get_or_create_conversation(sid, user_id=None)
        token = conv.owner_token

    # 模拟一次真实 supersede 的落盘结果：旧信封归档在 history alias 下，
    # CURRENT_ALIAS 指向新信封。
    old = SessionPlan(
        envelope_id="sp-old-chengdu",
        session_id=sid,
        user_goal="成都市小学分布情况",
        gis_chapter=_gis("成都市小学分布情况", "成都市"),
    )
    old.superseded = True
    old_ref = await session_data_manager.store(sid, old.model_dump(), prefix=STORE_PREFIX)
    await session_data_manager.set_alias(sid, old_ref, f"{HISTORY_ALIAS_PREFIX}{old.envelope_id}")
    new = SessionPlan(
        envelope_id="sp-new-beijing",
        session_id=sid,
        user_goal="分析北京学校",
        gis_chapter=_gis("分析北京学校", "北京市", subject="学校"),
        previous_goal=old.user_goal,
    )
    await _seed_current_envelope(new)

    resp = await client.get(
        f"/api/v1/chat/sessions/{sid}/plan",
        headers={"X-Session-Token": token},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["envelope_id"] == "sp-new-beijing"
    assert body["user_goal"] == "分析北京学校"
    # 旧信封（即使仍存在于 store 的 history alias 下）绝不出现。
    assert "sp-old-chengdu" not in resp.text
    assert "成都市小学分布情况" not in resp.text
    assert "history" not in body


@pytest.mark.asyncio
async def test_plan_route_requires_owned_session(client, db):
    """复用 SEC-08 守卫语义：无 token 的匿名会话 / 不存在的会话 → 404。"""
    sid = "sess-plan-route-a"
    async with db as session:
        await AsyncHistoryService(session).get_or_create_conversation(sid, user_id=None)

    resp = await client.get(f"/api/v1/chat/sessions/{sid}/plan")
    assert resp.status_code == 404

    resp_missing = await client.get(
        "/api/v1/chat/sessions/sess-plan-never-existed/plan",
        headers={"X-Session-Token": "whatever"},
    )
    assert resp_missing.status_code == 404
