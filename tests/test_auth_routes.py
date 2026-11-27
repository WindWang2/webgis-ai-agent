"""/auth/register + /auth/login 集成测试。

用进程内 FastAPI + httpx.AsyncClient + 临时 sqlite（aiosqlite），
通过 dependency_overrides 注入测试 session — 不动全局模块状态。
"""
import os
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-for-auth-routes-32chars-ok")
os.environ.setdefault("ENV", "development")
# 多数测试需要公开注册；个别测试在 fixture 里覆盖此值
os.environ.setdefault("ALLOW_PUBLIC_REGISTER", "true")


@pytest_asyncio.fixture
async def app_and_db(tmp_path, monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "ALLOW_PUBLIC_REGISTER", True)
    """每个 test 用一个独立的 sqlite 文件 + 干净 schema，via dep override。

    同时把 rate limiter 替换为永远放行的 stub —— register/login 限速本身由
    test_auth_rate_limit.py 单独覆盖，这里只测业务逻辑，不应被 5/hour 限制干扰。
    """
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from app.models.db_model import Base
    from app.core.database import get_async_db
    from app.core import rate_limiter as rl_mod
    from fastapi import FastAPI
    from app.api.routes import auth as auth_routes

    db_url = f"sqlite+aiosqlite:///{tmp_path / 'auth_test.db'}"
    test_engine = create_async_engine(db_url, connect_args={"check_same_thread": False})
    test_session = async_sessionmaker(bind=test_engine, expire_on_commit=False)

    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async def override_get_async_db():
        async with test_session() as s:
            yield s

    class _NoOpLimiter:
        async def is_allowed(self, key, max_requests, window_seconds):
            return True

        async def count(self, key, window_seconds):
            return 0

        async def record(self, key, window_seconds):
            return None

    async def _stub_get_rate_limiter():
        return _NoOpLimiter()

    # auth.py 用 from X import get_rate_limiter 拷贝了名字 —— 必须改它本身的引用
    monkeypatch.setattr(rl_mod, "get_rate_limiter", _stub_get_rate_limiter)
    monkeypatch.setattr("app.api.routes.auth.get_rate_limiter", _stub_get_rate_limiter)

    app = FastAPI()
    # 镜像 app/main.py 的统一错误信封接线（ADR-0138）：裸 app 不挂 handler
    # 会让 HTTPException 走默认 {"detail"} 体，与生产行为不符。
    from starlette.exceptions import HTTPException as StarletteHTTPException
    from fastapi.exceptions import RequestValidationError
    from app.core.exception import (
        unified_http_exception_handler,
        unified_validation_exception_handler,
    )

    app.add_exception_handler(StarletteHTTPException, unified_http_exception_handler)
    app.add_exception_handler(RequestValidationError, unified_validation_exception_handler)
    app.include_router(auth_routes.router, prefix="/api/v1")
    app.dependency_overrides[get_async_db] = override_get_async_db
    # P3 回归测试需要直接操作同一 DB（identity map 竞态场景）。
    app.state.test_session = test_session
    try:
        yield app
    finally:
        await test_engine.dispose()


@pytest_asyncio.fixture
async def client(app_and_db):
    transport = ASGITransport(app=app_and_db)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.mark.asyncio
async def test_register_creates_user_and_returns_token(client):
    resp = await client.post("/api/v1/auth/register", json={
        "username": "alice",
        "email": "alice@example.com",
        "password": "super-secret-1!",
    })
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["token_type"] == "bearer"
    assert body["expires_in"] > 0
    assert body["user"]["username"] == "alice"
    assert body["user"]["email"] == "alice@example.com"
    assert len(body["access_token"]) > 20


@pytest.mark.asyncio
async def test_register_rejects_short_password(client):
    resp = await client.post("/api/v1/auth/register", json={
        "username": "bob",
        "email": "bob@example.com",
        "password": "short",
    })
    assert resp.status_code == 422  # Pydantic min_length 失败


@pytest.mark.asyncio
async def test_register_rejects_bad_username(client):
    resp = await client.post("/api/v1/auth/register", json={
        "username": "has spaces",
        "email": "x@y.com",
        "password": "super-secret-1!",
    })
    assert resp.status_code == 400
    assert "username" in resp.json()["message"]


@pytest.mark.asyncio
async def test_register_rejects_bad_email(client):
    resp = await client.post("/api/v1/auth/register", json={
        "username": "carol",
        "email": "not-an-email",
        "password": "super-secret-1!",
    })
    assert resp.status_code == 400
    assert "email" in resp.json()["message"]


@pytest.mark.asyncio
async def test_duplicate_register_returns_409(client):
    payload = {
        "username": "dave",
        "email": "dave@example.com",
        "password": "super-secret-1!",
    }
    r1 = await client.post("/api/v1/auth/register", json=payload)
    assert r1.status_code == 201
    r2 = await client.post("/api/v1/auth/register", json=payload)
    assert r2.status_code == 409


@pytest.mark.asyncio
async def test_login_by_username_succeeds(client):
    await client.post("/api/v1/auth/register", json={
        "username": "eve",
        "email": "eve@example.com",
        "password": "super-secret-1!",
    })
    resp = await client.post("/api/v1/auth/login", json={
        "identifier": "eve",
        "password": "super-secret-1!",
    })
    assert resp.status_code == 200
    assert resp.json()["user"]["username"] == "eve"


@pytest.mark.asyncio
async def test_login_by_email_succeeds(client):
    await client.post("/api/v1/auth/register", json={
        "username": "frank",
        "email": "frank@example.com",
        "password": "super-secret-1!",
    })
    resp = await client.post("/api/v1/auth/login", json={
        "identifier": "frank@example.com",
        "password": "super-secret-1!",
    })
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_login_wrong_password_returns_401(client):
    await client.post("/api/v1/auth/register", json={
        "username": "grace",
        "email": "grace@example.com",
        "password": "super-secret-1!",
    })
    resp = await client.post("/api/v1/auth/login", json={
        "identifier": "grace",
        "password": "wrong-password",
    })
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_login_unknown_user_returns_401(client):
    resp = await client.post("/api/v1/auth/login", json={
        "identifier": "no-such-user",
        "password": "any",
    })
    assert resp.status_code == 401
    # 信息上不区分『用户不存在』vs『密码错』
    assert "用户名或密码" in resp.json()["message"]


@pytest.mark.asyncio
async def test_me_requires_token(client):
    resp = await client.get("/api/v1/auth/me")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_me_with_valid_token_returns_user(client):
    reg = await client.post("/api/v1/auth/register", json={
        "username": "henry",
        "email": "henry@example.com",
        "password": "super-secret-1!",
    })
    token = reg.json()["access_token"]
    resp = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json()["user_id"]


@pytest.mark.asyncio
async def test_register_disabled_by_default(client, monkeypatch):
    """审计 S28：默认关闭公开注册 —— 防止匿名铸造合法 JWT 后访问所有 admin 端点。"""
    from app.core.config import settings
    monkeypatch.setattr(settings, "ALLOW_PUBLIC_REGISTER", False)
    resp = await client.post("/api/v1/auth/register", json={
        "username": "attacker",
        "email": "attacker@example.com",
        "password": "super-secret-1!",
    })
    assert resp.status_code == 503
    assert "禁用" in resp.json()["message"]


# ── P3 review: delay ledger keyed by IP, atomic login counters ──────────────


@pytest.mark.asyncio
async def test_login_failure_delay_is_keyed_by_client_ip(client, monkeypatch):
    """P3 auth.py:284：渐进延迟不得按客户端可控 identifier 记账。"""
    from app.api.routes import auth as auth_routes
    from app.core.password_policy import (
        pending_failure_count,
        reset_login_failures,
    )

    victim = "delay-victim-p3@example.com"
    ip = "203.0.113.77"
    reset_login_failures(ip)
    reset_login_failures(victim)
    monkeypatch.setattr(auth_routes, "_get_client_ip", lambda _request: ip)

    for _ in range(3):
        resp = await client.post(
            "/api/v1/auth/login",
            json={"identifier": victim, "password": "wrong-password"},
        )
        assert resp.status_code == 401

    assert pending_failure_count(ip) == 3
    # 受害者用户名不得进入进程内延迟台账（否则攻击者可拖慢其登录）。
    assert pending_failure_count(victim) == 0
    reset_login_failures(ip)


@pytest.mark.asyncio
async def test_login_count_increment_is_atomic(app_and_db, monkeypatch):
    """P3 auth.py:292：并发登录不得丢失 login_count 自增。

    模拟：请求 Session 的 identity map 里是旧值 0，期间另一事务把 DB
    计数提交到 5。修复前 ORM 回写 stale+1=1；修复后 DB 侧 +1 → 6。
    """
    import asyncio

    from sqlalchemy import select, update
    from starlette.requests import Request

    from app.api.routes import auth as auth_routes
    from app.core.auth import hash_password
    from app.models.db_model import User
    from app.schemas.auth_schema import LoginRequest

    session_maker = app_and_db.state.test_session
    password = "super-secret-1!"
    async with session_maker() as seed:
        seed.add(User(
            id="u-atomic-p3",
            username="atomicp3",
            email="atomicp3@example.com",
            password_hash=await asyncio.to_thread(hash_password, password),
            role="viewer",
            is_active=True,
            token_version=0,
            login_count=0,
        ))
        await seed.commit()

    stale = session_maker()
    user_stale = (
        await stale.execute(select(User).where(User.id == "u-atomic-p3"))
    ).scalar_one()
    assert user_stale.login_count == 0
    # 结束读事务（identity map 仍持有旧值），让并发写可以提交。
    await stale.commit()

    async with session_maker() as other:
        await other.execute(
            update(User).where(User.id == "u-atomic-p3").values(login_count=5))
        await other.commit()

    request = Request({
        "type": "http", "method": "POST", "path": "/api/v1/auth/login",
        "headers": [], "client": ("198.51.100.9", 1234),
        "query_string": b"", "server": ("testserver", 80), "scheme": "http",
    })
    origin = auth_routes._get_client_ip(request)
    from app.core.password_policy import reset_login_failures
    reset_login_failures(origin)

    token = await auth_routes.login(
        LoginRequest(identifier="atomicp3", password=password),
        request, db=stale)
    assert token.access_token

    async with session_maker() as check:
        count = (
            await check.execute(
                select(User.login_count).where(User.id == "u-atomic-p3"))
        ).scalar_one()
    assert count == 6
    await stale.close()
