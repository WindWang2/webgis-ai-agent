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

    async def _stub_get_rate_limiter():
        return _NoOpLimiter()

    # auth.py 用 from X import get_rate_limiter 拷贝了名字 —— 必须改它本身的引用
    monkeypatch.setattr(rl_mod, "get_rate_limiter", _stub_get_rate_limiter)
    monkeypatch.setattr("app.api.routes.auth.get_rate_limiter", _stub_get_rate_limiter)

    app = FastAPI()
    app.include_router(auth_routes.router, prefix="/api/v1")
    app.dependency_overrides[get_async_db] = override_get_async_db
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
    assert "username" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_register_rejects_bad_email(client):
    resp = await client.post("/api/v1/auth/register", json={
        "username": "carol",
        "email": "not-an-email",
        "password": "super-secret-1!",
    })
    assert resp.status_code == 400
    assert "email" in resp.json()["detail"]


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
    assert "用户名或密码" in resp.json()["detail"]


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
    monkeypatch.delenv("ALLOW_PUBLIC_REGISTER", raising=False)
    resp = await client.post("/api/v1/auth/register", json={
        "username": "attacker",
        "email": "attacker@example.com",
        "password": "super-secret-1!",
    })
    assert resp.status_code == 503
    assert "禁用" in resp.json()["detail"]
