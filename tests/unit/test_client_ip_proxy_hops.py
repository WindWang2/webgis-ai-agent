"""#1675: client_ip_from must gate X-Real-IP / X-Forwarded-For on trusted hops.

``TRUSTED_PROXY_HOPS`` declares how many trusted proxy layers sit in front of
the app (default 1 = production nginx single hop, which sets
``X-Real-IP $remote_addr``). When the app is reached directly — exposed
uvicorn port, internal service mesh, or an ingress that passes
``$http_x_real_ip`` through — every forwarded header is attacker-controlled:
pre-fix, sending a fresh ``X-Real-IP`` per request rotated every IP-keyed
limiter bucket (auth_login_fail / ws_connect / ws_collab / rate_limit),
giving unbounded online password brute-force. hops<=0 keys the limiter on the
TCP peer instead.

Regression (issue verification): 40 failed logins from a directly-exposed app,
each carrying a different forged ``X-Real-IP`` — the 6th must already be 429.
"""
import os

import pytest
from httpx import ASGITransport, AsyncClient

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-client-ip-hops-32chars")
os.environ.setdefault("ENV", "development")


def _request(headers: dict, client=("10.0.0.5", 1234)):
    from starlette.requests import Request

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [(k.encode(), v.encode()) for k, v in headers.items()],
        "client": client,
    }
    return Request(scope)


# ── unit: hops semantics ─────────────────────────────────────────────────


def test_default_single_hop_trusts_x_real_ip(monkeypatch):
    """Default (production nginx): X-Real-IP is set from $remote_addr."""
    monkeypatch.delenv("TRUSTED_PROXY_HOPS", raising=False)
    from app.core.client_ip import client_ip_from

    req = _request({
        "x-real-ip": "203.0.113.9",
        "x-forwarded-for": "198.51.100.7, 203.0.113.9",
    })
    assert client_ip_from(req) == "203.0.113.9"


def test_default_single_hop_uses_last_xff_hop(monkeypatch):
    """No X-Real-IP behind nginx: the appended (last) XFF hop is the peer."""
    monkeypatch.delenv("TRUSTED_PROXY_HOPS", raising=False)
    from app.core.client_ip import client_ip_from

    req = _request({"x-forwarded-for": "1.2.3.4, 203.0.113.9"})
    assert client_ip_from(req) == "203.0.113.9"


def test_direct_exposure_ignores_forged_headers(monkeypatch):
    """#1675: hops=0 (direct exposure) — forged headers cannot key a limiter."""
    monkeypatch.setenv("TRUSTED_PROXY_HOPS", "0")
    from app.core.client_ip import client_ip_from

    req = _request({
        "x-real-ip": "1.2.3.4",
        "x-forwarded-for": "9.9.9.9, 1.2.3.4",
    })
    assert client_ip_from(req) == "10.0.0.5"


def test_multi_hop_uses_nth_from_last_xff_hop(monkeypatch):
    """hops=3: X-Real-IP semantics are unreliable across hops; XFF[-3] wins."""
    monkeypatch.setenv("TRUSTED_PROXY_HOPS", "3")
    from app.core.client_ip import client_ip_from

    req = _request({
        "x-real-ip": "6.6.6.6",
        "x-forwarded-for": "1.1.1.1, 2.2.2.2, 3.3.3.3, 4.4.4.4",
    })
    assert client_ip_from(req) == "2.2.2.2"


def test_multi_hop_short_xff_chain_falls_back_to_peer(monkeypatch):
    """hops=3 but only one XFF hop: topology mismatch → peer, never the
    attacker-controlled leftmost value."""
    monkeypatch.setenv("TRUSTED_PROXY_HOPS", "3")
    from app.core.client_ip import client_ip_from

    req = _request({"x-forwarded-for": "9.9.9.9"})
    assert client_ip_from(req) == "10.0.0.5"


def test_invalid_env_value_falls_back_to_default_single_hop(monkeypatch):
    """Typo'd TRUSTED_PROXY_HOPS keeps the production default (1) rather than
    silently collapsing everyone into one shared bucket (0)."""
    monkeypatch.setenv("TRUSTED_PROXY_HOPS", "not-a-number")
    from app.core.client_ip import client_ip_from

    req = _request({"x-real-ip": "203.0.113.9"})
    assert client_ip_from(req) == "203.0.113.9"


def test_no_client_returns_unknown(monkeypatch):
    monkeypatch.delenv("TRUSTED_PROXY_HOPS", raising=False)
    from app.core.client_ip import client_ip_from

    assert client_ip_from(_request({}, client=None)) == "unknown"


# ── regression: forged X-Real-IP cannot rotate the login-failure bucket ──


@pytest.mark.asyncio
async def test_direct_exposure_forged_real_ip_cannot_rotate_login_bucket(
    tmp_path, monkeypatch
):
    """40 failed logins, each with a fresh forged X-Real-IP, against a
    directly-exposed app (TRUSTED_PROXY_HOPS=0): the fail ledger must key on
    the TCP peer, so the 6th attempt is already 429.

    Pre-fix every request got a fresh bucket → 40x 401 (unbounded brute-force).
    """
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from fastapi import FastAPI
    from starlette.exceptions import HTTPException as StarletteHTTPException
    from fastapi.exceptions import RequestValidationError

    from app.core.rate_limiter import MemoryRateLimiter
    from app.core.database import get_async_db
    from app.models.db_model import Base
    from app.core.exception import (
        unified_http_exception_handler,
        unified_validation_exception_handler,
    )
    from app.api.routes import auth as auth_routes

    # 直连暴露：无受信代理，转发头全部不可信（#1675）
    monkeypatch.setenv("TRUSTED_PROXY_HOPS", "0")

    db_url = f"sqlite+aiosqlite:///{tmp_path / 'client_ip_hops.db'}"
    engine = create_async_engine(db_url, connect_args={"check_same_thread": False})
    session_maker = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async def override_get_async_db():
        async with session_maker() as s:
            yield s

    limiter = MemoryRateLimiter()

    async def _get_test_limiter():
        return limiter

    monkeypatch.setattr(auth_routes, "get_rate_limiter", _get_test_limiter)

    # 渐进延迟是 per-account 的独立机制（password_policy，#ticket-009 背景），
    # 与本测试的主题（IP 键限流桶）无关；中和掉以免 40 次失败累积 ~5min 睡眠。
    monkeypatch.setattr(auth_routes, "record_login_failure",
                        lambda identifier: 0.0)

    app = FastAPI()
    app.add_exception_handler(
        StarletteHTTPException, unified_http_exception_handler)
    app.add_exception_handler(
        RequestValidationError, unified_validation_exception_handler)
    app.include_router(auth_routes.router, prefix="/api/v1")
    app.dependency_overrides[get_async_db] = override_get_async_db

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport,
                               base_url="http://test") as client:
            statuses = []
            for i in range(40):
                resp = await client.post(
                    "/api/v1/auth/login",
                    json={"identifier": "ghost", "password": "wrong-password"},
                    headers={
                        # 每请求换一个伪造 X-Real-IP —— 修复前即可轮换限流桶
                        "X-Real-IP": f"1.2.3.{i % 256}",
                        "X-Forwarded-For": f"9.9.9.{i % 256}",
                    },
                )
                statuses.append(resp.status_code)

        assert statuses[:5] == [401] * 5, statuses[:5]
        assert statuses[5:] == [429] * 35, (
            "forged X-Real-IP must not rotate the login-failure bucket "
            f"(#1675): got {statuses[5:10]}..."
        )
    finally:
        await engine.dispose()
