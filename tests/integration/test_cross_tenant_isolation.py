"""跨租户安全边界端到端测试。

TEST-03：三个单元测试文件 (test_task_api / test_layer_api / test_session_api)
都注释 "跨租户隔离由 test_cross_tenant_isolation 覆盖"，但该测试之前并不存在。
本文件补齐该契约 —— 真实 SQLite + 真路由 + 真 AsyncHistoryService，端到端验证
用户 A 无法访问用户 B 的任何资源（且 404 而非 403，避免存在性泄露）。

覆盖资源类型：
  1. Chat sessions 详情            GET    /api/v1/chat/sessions/{id}
  2. Session messages (随详情返回)  GET    /api/v1/chat/sessions/{id}  (内嵌 messages)
  3. Session map-state            GET    /api/v1/chat/sessions/{id}/map-state
  4. Session delete               DELETE /api/v1/chat/sessions/{id}
  5. Tasks list                   GET    /api/v1/tasks?session_id=...
  6. Uploads detail               GET    /api/v1/uploads/{id}
  7. Reports detail               GET    /api/v1/reports/{id}

正向 case：所有者能访问自己的资源。
反向 case：跨租户访问一律 404。
"""
import os
import uuid
from contextlib import asynccontextmanager

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

# 必须在 import app.* 之前设置（auth 模块在导入期即读 settings.JWT_SECRET_KEY）
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-cross-tenant-32-chars-ok")
os.environ.setdefault("ENV", "development")


# ── Fixture：独立 SQLite + 全部业务路由 + dep override ──────────────────

@pytest_asyncio.fixture
async def app_and_db(tmp_path, monkeypatch):
    """复用 test_critical_auth_hardening.py 的 fixture 思路，扩展覆盖更多路由。

    - 临时 aiosqlite DB，Base.metadata.create_all
    - override get_async_db 指向测试 DB
    - patch _utils.async_db_session 及 chat/upload/layer 各模块的拷贝引用，
      让不走 Depends 的间接 DB 访问（async_db_session）也连到测试 DB
    - 桩掉 rate_limiter，避免污染跨租户判定
    - 注册 chat / task / upload / report / layer 路由，与 main.py 一致
    """
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

    from app.models.db_model import Base
    from app.core.database import get_async_db
    from app.core import rate_limiter as rl_mod
    from app.tools import _utils
    from fastapi import FastAPI
    from app.api.routes import chat as chat_routes
    from app.api.routes import task as task_routes
    from app.api.routes import upload as upload_routes
    from app.api.routes import report as report_routes
    from app.api.routes import layer as layer_routes

    db_url = f"sqlite+aiosqlite:///{tmp_path / 'cross_tenant.db'}"
    test_engine = create_async_engine(db_url, connect_args={"check_same_thread": False})
    test_session = async_sessionmaker(bind=test_engine, expire_on_commit=False)

    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async def override_get_async_db():
        async with test_session() as s:
            yield s

    # 不走 Depends 的间接读库路径（_utils.async_db_session 读全局）必须 patch
    @asynccontextmanager
    async def override_async_db_session():
        async with test_session() as s:
            try:
                yield s
                await s.commit()
            except Exception:
                await s.rollback()
                raise
            finally:
                await s.close()

    monkeypatch.setattr(_utils, "async_db_session", override_async_db_session)
    # chat / upload / layer 都 `from app.tools._utils import async_db_session` 拷贝了名字
    monkeypatch.setattr("app.api.routes.chat.async_db_session", override_async_db_session)
    monkeypatch.setattr("app.api.routes.upload.async_db_session", override_async_db_session)
    monkeypatch.setattr("app.api.routes.layer.async_db_session", override_async_db_session)

    # 限速 stub：默认无限（限速由专门测试覆盖）
    class _NoOpLimiter:
        async def is_allowed(self, key, max_requests, window_seconds):
            return True

    async def _stub_get_rate_limiter():
        return _NoOpLimiter()

    monkeypatch.setattr(rl_mod, "get_rate_limiter", _stub_get_rate_limiter)
    monkeypatch.setattr("app.api.routes.auth.get_rate_limiter", _stub_get_rate_limiter)

    app = FastAPI()
    app.include_router(chat_routes.router, prefix="/api/v1")
    app.include_router(task_routes.router, prefix="/api/v1")
    app.include_router(upload_routes.router, prefix="/api/v1")
    app.include_router(report_routes.router, prefix="/api/v1")
    app.include_router(layer_routes.router, prefix="/api/v1")
    app.dependency_overrides[get_async_db] = override_get_async_db
    try:
        yield app, test_session
    finally:
        await test_engine.dispose()


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


# ── Seed：两个不同租户的用户 + 各自的会话/上传/报告/任务 ─────────────

@pytest_asyncio.fixture
async def tenants(db):
    """Seed 两个 user (user_a, user_b)，各自一个 Conversation / UploadRecord / Report，
    并在 ChatEngine.tracker 里为 user_a 的 session 建一个任务。

    UploadRecord / Report 表没有 user_id 列 —— 归属通过 session_id → Conversation.user_id
    解析，所以必须把 upload/report 挂在各自 user 的 session 上。
    """
    from app.models.db_model import User, Conversation, Message
    from app.models.upload import UploadRecord
    from app.models.report import Report
    from app.core.auth import hash_password
    from app.services.chat_engine import ChatEngine
    from app.tools.registry import ToolRegistry
    from app.api.routes import chat as chat_mod

    user_a = User(
        id=str(uuid.uuid4()),
        username="alice",
        email="alice@example.com",
        password_hash=hash_password("pw-alice-12345"),
        role="viewer",
        is_active=True,
    )
    user_b = User(
        id=str(uuid.uuid4()),
        username="bob",
        email="bob@example.com",
        password_hash=hash_password("pw-bob-67890"),
        role="viewer",
        is_active=True,
    )
    db.add_all([user_a, user_b])
    await db.flush()

    conv_a = Conversation(id=str(uuid.uuid4()), user_id=user_a.id, title="Alice 的会话")
    conv_b = Conversation(id=str(uuid.uuid4()), user_id=user_b.id, title="Bob 的会话")
    db.add_all([conv_a, conv_b])
    await db.flush()

    # 给 Alice 的会话塞一条消息，便于正向断言 messages 非空
    msg_a = Message(conversation_id=conv_a.id, role="user", content="hello from alice")
    db.add(msg_a)

    # UploadRecord：挂到各自 session 上（归属经 session → conversation.user_id 解析）
    upload_a = UploadRecord(
        filename="/tmp/does-not-matter-a.geojson",
        original_name="a.geojson",
        file_type="vector",
        format="geojson",
        crs="EPSG:4326",
        geometry_type="Point",
        feature_count=1,
        bbox=[0.0, 0.0, 1.0, 1.0],
        file_size=42,
        session_id=conv_a.id,
    )
    upload_b = UploadRecord(
        filename="/tmp/does-not-matter-b.geojson",
        original_name="b.geojson",
        file_type="vector",
        format="geojson",
        crs="EPSG:4326",
        geometry_type="Point",
        feature_count=1,
        bbox=[2.0, 2.0, 3.0, 3.0],
        file_size=42,
        session_id=conv_b.id,
    )
    db.add_all([upload_a, upload_b])
    await db.flush()

    # Report：挂到各自 session 上
    report_a = Report(
        id=str(uuid.uuid4()),
        session_id=conv_a.id,
        title="Alice 的报告",
        format="html",
        status="completed",
        file_path="/tmp/does-not-matter-a.html",
    )
    report_b = Report(
        id=str(uuid.uuid4()),
        session_id=conv_b.id,
        title="Bob 的报告",
        format="html",
        status="completed",
        file_path="/tmp/does-not-matter-b.html",
    )
    db.add_all([report_a, report_b])

    await db.commit()

    # 在一个临时 ChatEngine 的 tracker 里给 Alice 的 session 建任务。
    # task 路由通过 chat.get_engine() 拿 engine，故把它挂到模块全局。
    engine = ChatEngine(ToolRegistry())
    task_a = engine.tracker.create(conv_a.id, "alice 的任务")
    chat_mod.engine = engine

    try:
        yield {
            "user_a": user_a,
            "user_b": user_b,
            "conv_a": conv_a,
            "conv_b": conv_b,
            "upload_a": upload_a,
            "upload_b": upload_b,
            "report_a": report_a,
            "report_b": report_b,
            "task_a": task_a,
        }
    finally:
        chat_mod.engine = None


def _auth_headers(user) -> dict:
    """为 user 签发一个 access token（与生产 create_access_token 同算法）。

    task/upload/report 路由用 get_current_user（不查 DB、不校验 ver），故只要
    sub 与 seeded User.id 一致即可；role 用实际值保持真实语义。
    """
    from app.core.auth import create_access_token

    token = create_access_token(
        {"sub": user.id, "username": user.username, "role": user.role}
    )
    return {"Authorization": f"Bearer {token}"}


# ────────────────────────────────────────────────────────────────────
# 1. Chat session 详情 + 内嵌 messages
# ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_owner_can_read_own_session(client, tenants):
    """正向：Alice 能读自己的 session（含 messages）。"""
    resp = await client.get(
        f"/api/v1/chat/sessions/{tenants['conv_a'].id}",
        headers=_auth_headers(tenants["user_a"]),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == tenants["conv_a"].id
    # messages 随详情返回，验证至少能看到自己种的那条
    roles = [m["role"] for m in body["messages"]]
    assert "user" in roles


@pytest.mark.asyncio
async def test_cross_tenant_session_detail_404(client, tenants):
    """反向：Bob 读 Alice 的 session → 404（不是 403）。"""
    resp = await client.get(
        f"/api/v1/chat/sessions/{tenants['conv_a'].id}",
        headers=_auth_headers(tenants["user_b"]),
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_cross_tenant_session_messages_404(client, tenants):
    """反向：会话没有独立 messages 端点 —— messages 随 GET /sessions/{id} 返回。
    Bob 取 Alice 的会话时拿不到任何消息（整体 404）。"""
    resp = await client.get(
        f"/api/v1/chat/sessions/{tenants['conv_a'].id}",
        headers=_auth_headers(tenants["user_b"]),
    )
    assert resp.status_code == 404


# ────────────────────────────────────────────────────────────────────
# 2. Session map-state
# ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_owner_can_read_own_map_state(client, tenants):
    """正向：Alice 读自己 session 的 map-state → 200（state 为空也合法）。"""
    from unittest.mock import patch, AsyncMock

    with patch(
        "app.services.session_data.session_data_manager.get_map_state",
        AsyncMock(return_value={}),
    ):
        resp = await client.get(
            f"/api/v1/chat/sessions/{tenants['conv_a'].id}/map-state",
            headers=_auth_headers(tenants["user_a"]),
        )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_cross_tenant_map_state_404(client, tenants):
    """反向：Bob 读 Alice 的 map-state → 404。"""
    resp = await client.get(
        f"/api/v1/chat/sessions/{tenants['conv_a'].id}/map-state",
        headers=_auth_headers(tenants["user_b"]),
    )
    assert resp.status_code == 404


# ────────────────────────────────────────────────────────────────────
# 3. Session delete
# ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_cross_tenant_delete_session_404(client, tenants):
    """反向：Bob 删 Alice 的 session → 404，且会话仍然存在。

    DELETE 走 ChatEngine.clear_session → AsyncHistoryService.delete_session，
    越权返回 False → 路由 404。验证 Alice 仍能读到自己的会话。
    """
    # Bob 尝试删 Alice 的会话
    resp = await client.delete(
        f"/api/v1/chat/sessions/{tenants['conv_a'].id}",
        headers=_auth_headers(tenants["user_b"]),
    )
    assert resp.status_code == 404

    # Alice 仍能读到 → 证明删除未生效
    resp = await client.get(
        f"/api/v1/chat/sessions/{tenants['conv_a'].id}",
        headers=_auth_headers(tenants["user_a"]),
    )
    assert resp.status_code == 200


# ────────────────────────────────────────────────────────────────────
# 4. Tasks list（/tasks?session_id=...）
# ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_owner_can_list_own_tasks(client, tenants):
    """正向：Alice 列出自己 session 下的任务。"""
    resp = await client.get(
        "/api/v1/tasks",
        params={"session_id": tenants["conv_a"].id},
        headers=_auth_headers(tenants["user_a"]),
    )
    assert resp.status_code == 200
    task_ids = [t["task_id"] for t in resp.json()["tasks"]]
    assert tenants["task_a"].id in task_ids


@pytest.mark.asyncio
async def test_cross_tenant_tasks_list_404(client, tenants):
    """反向：Bob 列 Alice 的 session 任务 → 404（_verify_session_owner 越权拒绝）。

    注意不是「返回空列表」—— 空列表会让 Bob 知道这个 session_id 存在。
    越权访问他人 session 一律 404。
    """
    resp = await client.get(
        "/api/v1/tasks",
        params={"session_id": tenants["conv_a"].id},
        headers=_auth_headers(tenants["user_b"]),
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_cross_tenant_task_detail_404(client, tenants):
    """反向：Bob 直接查 Alice 的 task_id → 404（task 经 session 解析归属）。"""
    resp = await client.get(
        f"/api/v1/tasks/{tenants['task_a'].id}",
        headers=_auth_headers(tenants["user_b"]),
    )
    assert resp.status_code == 404


# ────────────────────────────────────────────────────────────────────
# 5. Uploads detail
# ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_owner_can_read_own_upload(client, tenants):
    """正向：Alice 读自己 session 下的 upload。"""
    resp = await client.get(
        f"/api/v1/uploads/{tenants['upload_a'].id}",
        headers=_auth_headers(tenants["user_a"]),
    )
    assert resp.status_code == 200
    assert resp.json()["id"] == tenants["upload_a"].id


@pytest.mark.asyncio
async def test_cross_tenant_upload_404(client, tenants):
    """反向：Bob 读 Alice 的 upload → 404。

    UploadRecord 无 user_id 列；归属经 record.session_id → Conversation.user_id。
    """
    resp = await client.get(
        f"/api/v1/uploads/{tenants['upload_a'].id}",
        headers=_auth_headers(tenants["user_b"]),
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_cross_tenant_uploads_list_404(client, tenants):
    """反向：Bob 按 Alice 的 session_id 列 uploads → 404（归属校验在前）。"""
    resp = await client.get(
        "/api/v1/uploads",
        params={"session_id": tenants["conv_a"].id},
        headers=_auth_headers(tenants["user_b"]),
    )
    assert resp.status_code == 404


# ────────────────────────────────────────────────────────────────────
# 6. Reports detail
# ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_owner_can_read_own_report(client, tenants):
    """正向：Alice 读自己的 report。

    report 路由返回 ApiResponse（200，body code=SUCCESS）。
    """
    resp = await client.get(
        f"/api/v1/reports/{tenants['report_a'].id}",
        headers=_auth_headers(tenants["user_a"]),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == "SUCCESS"
    assert body["data"]["id"] == tenants["report_a"].id


@pytest.mark.asyncio
async def test_cross_tenant_report_404(client, tenants):
    """反向：Bob 读 Alice 的 report → 404（_check_report_owner 越权 404）。"""
    resp = await client.get(
        f"/api/v1/reports/{tenants['report_a'].id}",
        headers=_auth_headers(tenants["user_b"]),
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_cross_tenant_reports_list_404(client, tenants):
    """反向：Bob 按 Alice 的 session_id 列 reports → 404。"""
    resp = await client.get(
        "/api/v1/reports",
        params={"session_id": tenants["conv_a"].id},
        headers=_auth_headers(tenants["user_b"]),
    )
    assert resp.status_code == 404


# ────────────────────────────────────────────────────────────────────
# 8. Chat body session_id 所有权 (REVIEW-P0-2)
#
# ADR-0030 给 /chat/sessions/* 全部挂上了 require_owned_session，但
# /chat/completions 与 /chat/stream 漏掉了 —— 这两个端点的 session_id 来自
# request body，只有 get_current_user_optional，下游 load_context 又对
# owner_token 不匹配只打 warning，导致任何人拿到别人的 session UUID 就能读取
# 全部历史并往里追加消息。这里补齐该契约。
#
# 说明：越权时守卫在触达 ChatEngine 之前就 404，所以这些用例不需要可用的 LLM。
# 放行路径只断言"不是 404"（后续引擎调用可能因无 LLM 而失败），这足以证明守卫
# 没有误拦。
# ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_cross_tenant_chat_stream_404(client, tenants):
    """反向：Bob 拿 Alice 的 session_id 发 /chat/stream → 404。"""
    resp = await client.post(
        "/api/v1/chat/stream",
        json={"message": "leak alice's history", "session_id": tenants["conv_a"].id},
        headers=_auth_headers(tenants["user_b"]),
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_cross_tenant_chat_completions_404(client, tenants):
    """反向：Bob 拿 Alice 的 session_id 发 /chat/completions → 404。"""
    resp = await client.post(
        "/api/v1/chat/completions",
        json={"message": "leak alice's history", "session_id": tenants["conv_a"].id},
        headers=_auth_headers(tenants["user_b"]),
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_anonymous_cannot_post_to_owned_session(client, tenants):
    """反向：完全未认证的调用者拿 Alice 的 session_id 发消息 → 404。"""
    resp = await client.post(
        "/api/v1/chat/stream",
        json={"message": "no auth at all", "session_id": tenants["conv_a"].id},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_owner_not_blocked_on_own_session(db, tenants):
    """正向：Alice 用自己的 session_id 不被守卫拦截。

    放行路径直接断言守卫本身，不走整条路由 —— 一旦放行，真实 ChatEngine 会去
    调 LLM 并挂住整个用例（守卫之后就没有可断言的边界了）。
    """
    from app.api.routes.chat import _guard_body_session

    await _guard_body_session(db, tenants["conv_a"].id, tenants["user_a"].id, None)


@pytest.mark.asyncio
async def test_new_session_id_not_blocked(db, tenants):
    """正向：库中不存在的 session_id 必须放行（首条消息会新建该会话），
    否则守卫会把"开新对话"这条正常路径打成 404。"""
    from app.api.routes.chat import _guard_body_session

    await _guard_body_session(db, str(uuid.uuid4()), tenants["user_a"].id, None)


@pytest.mark.asyncio
async def test_omitted_session_id_not_blocked(db, tenants):
    """正向：不带 session_id（前端首次进入）必须放行。"""
    from app.api.routes.chat import _guard_body_session

    await _guard_body_session(db, None, tenants["user_a"].id, None)


@pytest.mark.asyncio
async def test_guard_rejects_other_tenants_session(db, tenants):
    """反向（守卫层）：Bob 传 Alice 的 session_id → HTTPException 404。

    与上面的路由级用例互补：这里锁定守卫自身的返回码，确保未来重构守卫时
    不会退化成 403（会泄漏会话存在性）或静默放行。
    """
    from fastapi import HTTPException

    from app.api.routes.chat import _guard_body_session

    with pytest.raises(HTTPException) as exc:
        await _guard_body_session(db, tenants["conv_a"].id, tenants["user_b"].id, None)
    assert exc.value.status_code == 404
