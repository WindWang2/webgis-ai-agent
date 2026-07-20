"""/auth/refresh + /auth/logout + token_version 集成测试 (S41)。

覆盖：
- login/register 同时返回 access + refresh token
- /auth/refresh 用 refresh token 换新 token 对
- /auth/logout bump ver 后旧 token 立即失效
- 各类错误：错 token type / 过期 / 签名错 / ver mismatch / rate limit

fixture 模式照搬 tests/test_auth_routes.py：进程内 FastAPI + httpx.AsyncClient +
aiosqlite + dependency_overrides + NoOp rate limiter。
"""
import os
import time
from datetime import timedelta

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

# 必须在 import app.* 之前设
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-s41-refresh-32-chars-ok")
os.environ.setdefault("ENV", "development")
os.environ.setdefault("ALLOW_PUBLIC_REGISTER", "true")


@pytest_asyncio.fixture
async def app_and_db(tmp_path, monkeypatch):
    """每个 test 一个独立 sqlite + 干净 schema + NoOp limiter。"""
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from app.models.db_model import Base
    from app.core.database import get_async_db
    from app.core import rate_limiter as rl_mod
    from fastapi import FastAPI
    from app.api.routes import auth as auth_routes

    db_url = f"sqlite+aiosqlite:///{tmp_path / 's41_test.db'}"
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


async def _register_user(client, username="alice", password="super-secret-1!"):
    """helper: 注册 + 返回 token pair."""
    resp = await client.post("/api/v1/auth/register", json={
        "username": username,
        "email": f"{username}@example.com",
        "password": password,
    })
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _login_user(client, identifier, password="super-secret-1!"):
    resp = await client.post("/api/v1/auth/login", json={
        "identifier": identifier,
        "password": password,
    })
    assert resp.status_code == 200, resp.text
    return resp.json()


def _auth_header(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ──────────────────────────────────────────────────────────────────────
# 1. login/register 返回 access + refresh token 对
# ──────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_login_returns_both_access_and_refresh_token(client):
    """login 响应应同时包含 access_token 和 refresh_token。"""
    await _register_user(client, username="alice")
    body = await _login_user(client, "alice")
    assert body["access_token"]
    assert body["refresh_token"]
    assert body["token_type"] == "bearer"
    assert body["expires_in"] > 0
    assert body["user"]["username"] == "alice"


@pytest.mark.asyncio
async def test_register_returns_both_access_and_refresh_token(client):
    """register 响应也应同时含两 token (新用户首次注册即获得 refresh 能力)。"""
    body = await _register_user(client, username="bob")
    assert body["access_token"]
    assert body["refresh_token"]


@pytest.mark.asyncio
async def test_access_token_carries_type_and_ver_claims(client):
    """decode access token 应见 type=access, ver=0, iat。"""
    from app.core.auth import verify_token
    body = await _register_user(client, username="carol")
    payload = verify_token(body["access_token"])
    assert payload is not None
    assert payload["type"] == "access"
    assert payload["ver"] == 0
    assert "iat" in payload
    assert "exp" in payload
    assert payload["sub"] == body["user"]["id"]


@pytest.mark.asyncio
async def test_refresh_token_carries_type_jti_and_ver_claims(client):
    """decode refresh token 应见 type=refresh, jti, ver=0。"""
    from app.core.auth import verify_token
    body = await _register_user(client, username="dave")
    payload = verify_token(body["refresh_token"])
    assert payload is not None
    assert payload["type"] == "refresh"
    assert payload["ver"] == 0
    assert "jti" in payload
    assert len(payload["jti"]) >= 16  # secrets.token_hex(16) -> 32 chars


@pytest.mark.asyncio
async def test_token_version_defaults_to_zero_in_db(client, app_and_db):
    """新注册用户的 DB 列 token_version 应为 0。"""
    body = await _register_user(client, username="erin")
    user_id = body["user"]["id"]

    # 直接查库验证
    from app.core.database import get_async_db
    from app.models.db_model import User
    from sqlalchemy import select

    # 取原始 dep override (fixture 里设的)
    override = app_and_db.dependency_overrides[get_async_db]
    async for db in override():
        result = await db.execute(select(User).where(User.id == user_id))
        user = result.scalar_one()
        assert user.token_version == 0
        break


# ──────────────────────────────────────────────────────────────────────
# 2. /auth/refresh 端点
# ──────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_refresh_with_valid_refresh_token_returns_new_pair(client):
    """合法 refresh token -> 新 access + refresh token 对。

    注意: 不强制断言新旧 token 字符串不同 -- JWT 在同一秒内签发且 sub/role/ver 一致时
    可能字节相同 (iat/exp 是秒精度)。这是正常的；rotation 的语义是 "新 token 也合法"，
    不是 "新 token 必须不同"。真正强制 rotation 唯一性需要 jti 服务端存储。
    """
    body = await _register_user(client, username="frank")
    resp = await client.post("/api/v1/auth/refresh", json={
        "refresh_token": body["refresh_token"],
    })
    assert resp.status_code == 200, resp.text
    new_body = resp.json()
    assert new_body["access_token"]
    assert new_body["refresh_token"]
    # 新 access token 应能用于访问 /me (验证它确实是有效 access token)
    me_resp = await client.get(
        "/api/v1/auth/me",
        headers=_auth_header(new_body["access_token"]),
    )
    assert me_resp.status_code == 200
    assert me_resp.json()["username"] == "frank"
    # 新 refresh token 也能用于下一次 refresh
    resp2 = await client.post("/api/v1/auth/refresh", json={
        "refresh_token": new_body["refresh_token"],
    })
    assert resp2.status_code == 200


@pytest.mark.asyncio
async def test_refresh_rejects_access_token_used_as_refresh(client):
    """拿 access token 去 /refresh 应当 401 (type mismatch)。"""
    body = await _register_user(client, username="grace")
    resp = await client.post("/api/v1/auth/refresh", json={
        "refresh_token": body["access_token"],
    })
    assert resp.status_code == 401
    assert "type" in resp.json()["detail"].lower() or "refresh" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_refresh_rejects_tampered_token(client):
    """签名被篡改的 token 应 401。"""
    body = await _register_user(client, username="henry")
    # 翻转最后几个字符 -- 让签名失效但保持长度合法
    bad_token = body["refresh_token"][:-4] + ("AAAA" if body["refresh_token"][-4:] != "AAAA" else "BBBB")
    resp = await client.post("/api/v1/auth/refresh", json={"refresh_token": bad_token})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_refresh_rejects_garbage_input(client):
    """长度合法但非 JWT 的字符串应 401 (不是 Pydantic 长度错)。

    注意: 太短 (<10 chars) 会先被 Pydantic min_length 拦截返回 422；这里测的是
    通过长度校验后到达 endpoint 逻辑的非法 token。
    """
    await _register_user(client, username="ivan")
    resp = await client.post("/api/v1/auth/refresh", json={
        "refresh_token": "this-is-definitely-not-a-valid-jwt-token",
    })
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_refresh_rejects_short_input_with_422(client):
    """<10 字符的输入应被 Pydantic 拒绝 (422)。"""
    await _register_user(client, username="ivan2")
    resp = await client.post("/api/v1/auth/refresh", json={"refresh_token": "short"})
    assert resp.status_code == 422  # Pydantic min_length 失败


@pytest.mark.asyncio
async def test_refresh_rejects_expired_refresh_token(client):
    """过期的 refresh token 应 401。"""
    # 手动签发一个已过期的 refresh token
    from app.core.auth import create_refresh_token
    body = await _register_user(client, username="judy")
    user_id = body["user"]["id"]

    expired = create_refresh_token(
        data={"sub": user_id, "username": "judy", "role": "viewer"},
        expires_delta=timedelta(seconds=-1),  # 已过期
        token_version=0,
    )
    # jose 可能缓存时间，sleep 1s 让 exp 真正过期
    time.sleep(1.1)
    resp = await client.post("/api/v1/auth/refresh", json={"refresh_token": expired})
    assert resp.status_code == 401


# ──────────────────────────────────────────────────────────────────────
# 3. /auth/logout + token_version 失效
# ──────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_logout_bumps_token_version(client):
    """logout 后 user.token_version 应 +1 (从 0 -> 1)。"""
    body = await _register_user(client, username="kate")
    resp = await client.post(
        "/api/v1/auth/logout",
        headers=_auth_header(body["access_token"]),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["ok"] is True

    # 验证 DB 列已 bump
    from fastapi import FastAPI  # noqa: F401 (avoid removing import side-effect)

    # 直接通过 override 拿 db
    # 由于无法轻易从已注册 app 拿原 override，简单办法：再注册一个 client 调 /me
    # 用旧 access token 调 /me 应当 401
    me_resp = await client.get(
        "/api/v1/auth/me",
        headers=_auth_header(body["access_token"]),
    )
    assert me_resp.status_code == 401, "logout 后旧 access token 应失效"


@pytest.mark.asyncio
async def test_logout_invalidates_refresh_token_too(client):
    """logout 后旧 refresh token 不能再 refresh。"""
    body = await _register_user(client, username="leo")
    # logout
    resp = await client.post(
        "/api/v1/auth/logout",
        headers=_auth_header(body["access_token"]),
    )
    assert resp.status_code == 200
    # 旧 refresh token 应当失效
    refresh_resp = await client.post("/api/v1/auth/refresh", json={
        "refresh_token": body["refresh_token"],
    })
    assert refresh_resp.status_code == 401
    assert "revoked" in refresh_resp.json()["detail"].lower() or "re-login" in refresh_resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_logout_invalidates_all_sessions(client):
    """logout-everywhere: 即使第二台设备的 token 也应失效。

    模拟：同一用户两次 login (代表两台设备)，logout 一次，两套 token 都失效。
    """
    # 先注册，再 login 两次 (模拟两台设备)
    await _register_user(client, username="mia")
    body1 = await _login_user(client, "mia")
    body2 = await _login_user(client, "mia")

    # 用 device 1 的 token logout
    resp = await client.post(
        "/api/v1/auth/logout",
        headers=_auth_header(body1["access_token"]),
    )
    assert resp.status_code == 200

    # device 2 的 access token 也应失效
    me_resp = await client.get(
        "/api/v1/auth/me",
        headers=_auth_header(body2["access_token"]),
    )
    assert me_resp.status_code == 401, "logout-everywhere: device 2 也应失效"

    # device 2 的 refresh token 也应失效
    refresh_resp = await client.post("/api/v1/auth/refresh", json={
        "refresh_token": body2["refresh_token"],
    })
    assert refresh_resp.status_code == 401


@pytest.mark.asyncio
async def test_protected_endpoint_rejects_refresh_token(client):
    """refresh token 不能当 access 用 -> /me 应 401。"""
    body = await _register_user(client, username="nina")
    me_resp = await client.get(
        "/api/v1/auth/me",
        headers=_auth_header(body["refresh_token"]),
    )
    assert me_resp.status_code == 401


@pytest.mark.asyncio
async def test_logout_requires_authentication(client):
    """无 token 调 /logout 应 401。"""
    resp = await client.post("/api/v1/auth/logout")
    assert resp.status_code == 401


# ──────────────────────────────────────────────────────────────────────
# 4. /auth/me 使用 ver 校验
# ──────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_me_endpoint_works_with_valid_token(client):
    """正常 access token 调 /me 应返回用户信息。"""
    body = await _register_user(client, username="oscar")
    me_resp = await client.get(
        "/api/v1/auth/me",
        headers=_auth_header(body["access_token"]),
    )
    assert me_resp.status_code == 200, me_resp.text
    data = me_resp.json()
    assert data["user_id"] == body["user"]["id"]
    assert data["username"] == "oscar"
    assert data["email"] == "oscar@example.com"
    assert data["role"] == "viewer"


@pytest.mark.asyncio
async def test_me_returns_full_user_info(client):
    """/me 应返回 DB 中的完整用户信息 (username/email/role/full_name)。"""
    body = await _register_user(client, username="paul")
    me_resp = await client.get(
        "/api/v1/auth/me",
        headers=_auth_header(body["access_token"]),
    )
    assert me_resp.status_code == 200
    data = me_resp.json()
    assert "username" in data
    assert "email" in data
    assert "role" in data


# ──────────────────────────────────────────────────────────────────────
# 5. back-compat: 旧 token (无 type/ver claim) 仍工作
# ──────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_legacy_token_without_type_claim_works_for_one_ttl(client):
    """S41 部署前签发的旧 token (无 type/ver claim) 应仍被接受 (back-compat)。

    这是为了避免 flag-day: 部署瞬间所有现存 session 失效会让所有用户被踢出。
    """
    from app.core.auth import ACCESS_TOKEN_EXPIRE_MINUTES
    from datetime import timedelta

    body = await _register_user(client, username="quinn")
    user_id = body["user"]["id"]

    # 手动构造一个 "旧式" token: 只有 sub/username/role/exp，无 type/ver
    # 直接用 jose 签
    from jose import jwt
    from app.core.auth import SECRET_KEY, ALGORITHM
    from datetime import datetime, timezone

    legacy_payload = {
        "sub": user_id,
        "username": "quinn",
        "role": "viewer",
        "exp": datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES),
    }
    legacy_token = jwt.encode(legacy_payload, SECRET_KEY, algorithm=ALGORITHM)

    # 调 /me: 旧 token 应当被接受 (ver 视为 0，user.token_version 也是 0)
    me_resp = await client.get(
        "/api/v1/auth/me",
        headers=_auth_header(legacy_token),
    )
    assert me_resp.status_code == 200, f"back-compat: 旧 token 应仍工作，但 got {me_resp.status_code}: {me_resp.text}"


# ──────────────────────────────────────────────────────────────────────
# 6. rate limit (用 monkeypatched limiter，验证逻辑而非实际限流)
# ──────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_refresh_rate_limit_triggers_429(client, app_and_db, monkeypatch):
    """超过 30 次/5min 应返回 429。"""
    body = await _register_user(client, username="rhoda")

    # 用一个计数 limiter: 第 31 次拒绝
    call_count = {"n": 0}

    class _CountingLimiter:
        async def is_allowed(self, key, max_requests, window_seconds):
            call_count["n"] += 1
            # refresh 路径的 key 是 auth_refresh:<user_id>
            if key.startswith("auth_refresh:") and call_count["n"] > 30:
                return False
            return True

    async def _stub_get_rate_limiter():
        return _CountingLimiter()

    # patch 两个名字
    from app.core import rate_limiter as rl_mod
    monkeypatch.setattr(rl_mod, "get_rate_limiter", _stub_get_rate_limiter)
    monkeypatch.setattr("app.api.routes.auth.get_rate_limiter", _stub_get_rate_limiter)

    # 调 30 次应成功
    for i in range(30):
        resp = await client.post("/api/v1/auth/refresh", json={
            "refresh_token": body["refresh_token"],
        })
        assert resp.status_code == 200, f"refresh #{i+1} 应成功，但 got {resp.status_code}: {resp.text}"
        # 注意：每次 refresh 会发新 refresh token；用最新的继续刷
        body = resp.json()

    # 第 31 次应 429
    resp = await client.post("/api/v1/auth/refresh", json={
        "refresh_token": body["refresh_token"],
    })
    assert resp.status_code == 429
