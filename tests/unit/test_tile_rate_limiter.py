"""G3D-V2-A A5：瓦片服务 quota 执行层 —— #1771 D11 留白清偿。

覆盖：滑动窗口超限/键隔离/窗口恢复/env 配置/禁用语义；enforce 的 429 +
Retry-After + typed code；terrain 路由接线（第 N+1 次 429）；出站
make_safe_session rate_limit 包装（SourceRateLimitedError）。
"""
from __future__ import annotations

import pytest
import requests as requests_lib

from app.services.data_fabric import tile_rate_limiter as trl
from app.services.data_fabric.tile_rate_limiter import (
    check_rate,
    enforce_tile_rate,
    reset_for_tests,
)


@pytest.fixture(autouse=True)
def _clean_windows():
    reset_for_tests()
    yield
    reset_for_tests()


class TestSlidingWindow:
    def test_over_limit_denied_with_retry_after(self):
        for _ in range(3):
            assert check_rate("k1", 3)[0] is True
        allowed, retry_after = check_rate("k1", 3)
        assert allowed is False
        assert retry_after >= 1

    def test_key_isolation(self):
        assert check_rate("tenant-a:item-1", 1)[0] is True
        assert check_rate("tenant-b:item-1", 1)[0] is True  # 不同租户不受影响
        assert check_rate("tenant-a:item-2", 1)[0] is True  # 不同源不受影响
        assert check_rate("tenant-a:item-1", 1)[0] is False

    def test_window_recovery(self, monkeypatch):
        fake_now = [1000.0]
        monkeypatch.setattr(trl.time, "monotonic", lambda: fake_now[0])
        assert check_rate("k2", 1)[0] is True
        assert check_rate("k2", 1)[0] is False
        fake_now[0] += 61.0  # 窗口滑过
        assert check_rate("k2", 1)[0] is True

    def test_disabled_when_rpm_nonpositive(self):
        for _ in range(100):
            assert check_rate("k3", 0)[0] is True

    def test_env_config(self, monkeypatch):
        monkeypatch.setenv("DATA_FABRIC_TILE_RPM", "2")
        monkeypatch.setenv("SESSION_TERRAIN_TILE_RPM", "5")
        assert trl.data_fabric_tile_rpm() == 2
        assert trl.session_terrain_tile_rpm() == 5
        monkeypatch.setenv("DATA_FABRIC_TILE_RPM", "not-a-number")
        assert trl.data_fabric_tile_rpm() == 600  # 非法值回落默认
        monkeypatch.delenv("DATA_FABRIC_TILE_RPM")
        assert trl.data_fabric_tile_rpm() == 600


class TestEnforceGuard:
    def test_429_with_headers_and_typed_code(self):
        from fastapi import HTTPException

        for _ in range(2):
            enforce_tile_rate("k4", 2, scope="test_scope")
        with pytest.raises(HTTPException) as ei:
            enforce_tile_rate("k4", 2, scope="test_scope")
        assert ei.value.status_code == 429
        assert int(ei.value.headers["Retry-After"]) >= 1
        detail = ei.value.detail
        assert detail["code"] == "TILE_RATE_LIMITED"
        assert detail["scope"] == "test_scope"
        assert detail["limit_rpm"] == 2
        # 不泄漏键本体（键含用户/会话标识）
        assert "k4" not in str(detail)


class TestTerrainRouteWiring:
    async def test_third_request_429(self, tmp_path, monkeypatch):
        from contextlib import asynccontextmanager

        from fastapi import FastAPI
        from httpx import ASGITransport, AsyncClient
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        from app.core.auth import get_current_user_optional
        from app.core.database import get_async_db
        from app.api.routes import layer as layer_routes
        from app.models.db_model import Base, Conversation, User

        db_url = f"sqlite+aiosqlite:///{tmp_path / 'rate.db'}"
        engine = create_async_engine(db_url, connect_args={"check_same_thread": False})
        db_factory = async_sessionmaker(bind=engine, expire_on_commit=False)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with db_factory() as db:
            db.add(User(
                id="rate-user", username="rate-user", email="rate-user@example.com",
                password_hash="scrypt$16384$8$1$00$00", role="editor",
                is_active=True, token_version=0,
            ))
            db.add(Conversation(id="sess-rate-00000001", user_id="rate-user"))
            await db.commit()

        async def override_db():
            async with db_factory() as s:
                try:
                    yield s
                    await s.commit()
                except Exception:
                    await s.rollback()
                    raise

        from app.services.session_data import SessionDataManager

        monkeypatch.setattr(layer_routes, "session_data_manager", SessionDataManager(capacity=4))
        monkeypatch.setenv("SESSION_TERRAIN_TILE_RPM", "2")

        app = FastAPI()
        app.include_router(layer_routes.router, prefix="/api/v1")
        app.dependency_overrides[get_async_db] = override_db
        app.dependency_overrides[get_current_user_optional] = lambda: {"user_id": "rate-user"}
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            url = (
                "/api/v1/layers/data/ref:terrain-deadbeef0000/terrain-tiles"
                "/10/500/300.png?session_id=sess-rate-00000001"
            )
            # 前两次：过限流后走到 ref 解析 → 404（ref 未注册），不是 429
            r1, r2 = await c.get(url), await c.get(url)
            assert r1.status_code == 404 and r2.status_code == 404
            # 第三次：限流命中 —— 429 + Retry-After
            r3 = await c.get(url)
            assert r3.status_code == 429
            assert int(r3.headers["Retry-After"]) >= 1
            assert r3.json()["detail"]["code"] == "TILE_RATE_LIMITED"
        await engine.dispose()


class TestOutboundSafeSession:
    def test_rate_limited_session_raises_typed_error(self):
        from app.services.data_fabric.errors import SourceRateLimitedError
        from app.services.data_fabric.security import make_safe_session

        session = make_safe_session(rate_limit=("src-x", 1))
        # 拦截真实网络：send 层替换为哑响应（request 包装层在 send 之前）
        session.send = lambda request, **kwargs: _dummy_response()  # noqa: ARG005
        r = session.get("https://example.com/api")
        assert r.status_code == 200
        with pytest.raises(SourceRateLimitedError):
            session.get("https://example.com/api")

    def test_no_rate_limit_keeps_legacy_behavior(self):
        from app.services.data_fabric.security import make_safe_session

        session = make_safe_session()
        session.send = lambda request, **kwargs: _dummy_response()  # noqa: ARG005
        for _ in range(5):
            assert session.get("https://example.com/api").status_code == 200


def _dummy_response():
    resp = requests_lib.models.Response()
    resp.status_code = 200
    resp._content = b"{}"  # noqa: SLF001 — 测试哑响应
    return resp
