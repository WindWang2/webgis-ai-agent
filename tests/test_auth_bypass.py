"""测试阶段免登录（AUTH_DISABLED=true）契约。

开关打开时：受保护依赖不校验 Bearer token，统一放行为 test-admin(admin)；
关闭时：行为与原先完全一致（401 / 匿名哨兵）。bypass 身份必须是绑定身份
（非 anonymous 哨兵），使会话归属与所有权守卫和受保护端点一致。
"""
import asyncio
from types import SimpleNamespace

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.core.auth import (
    AUTH_BYPASS_PROFILE,
    AUTH_BYPASS_USER_ID,
    auth_bypass_enabled,
    get_current_user,
    get_current_user_optional,
    get_current_user_with_version,
    require_admin,
)
from app.core.config import settings


@pytest.fixture
def bypass_on(monkeypatch):
    monkeypatch.setattr(settings, "AUTH_DISABLED", True, raising=False)
    assert auth_bypass_enabled() is True


@pytest.fixture
def bypass_off(monkeypatch):
    # 显式关闭：本地 .env 可能开着 AUTH_DISABLED=true，关闭态契约不能依赖
    # 环境默认值。
    monkeypatch.setattr(settings, "AUTH_DISABLED", False, raising=False)
    assert auth_bypass_enabled() is False


# ─── 依赖级：无凭证放行为 test-admin ─────────────────────────────────────


@pytest.mark.asyncio
async def test_get_current_user_bypass_returns_admin_without_token(bypass_on):
    user = await get_current_user(None)  # type: ignore[arg-type]
    assert user["user_id"] == AUTH_BYPASS_USER_ID
    assert user["role"] == "admin"


@pytest.mark.asyncio
async def test_get_current_user_optional_bypass_is_bound_identity(bypass_on):
    user = await get_current_user_optional(None)  # type: ignore[arg-type]
    # 不是匿名哨兵 —— 否则同一请求在受保护端点是 test-admin、在可选端点
    # 是 anonymous，会话归属互相矛盾。
    assert user["user_id"] == AUTH_BYPASS_USER_ID
    assert user["user_id"] not in {"anonymous", "anon"}


@pytest.mark.asyncio
async def test_get_current_user_without_bypass_still_401(bypass_off):
    assert auth_bypass_enabled() is False
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        await get_current_user(None)  # type: ignore[arg-type]
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_optional_without_bypass_still_anonymous(bypass_off):
    assert auth_bypass_enabled() is False
    user = await get_current_user_optional(None)  # type: ignore[arg-type]
    assert user["user_id"] == "anonymous"


# ─── with_version：惰性建 test-admin 行 / DB 不可用降级 ─────────────────


class _FakeResult:
    def __init__(self, user):
        self._user = user

    def scalar_one_or_none(self):
        return self._user


class _FakeDb:
    def __init__(self, existing):
        self.existing = existing
        self.added = []

    async def execute(self, _query):
        return _FakeResult(self.existing)

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        pass

    async def refresh(self, _obj):
        pass


@pytest.mark.asyncio
async def test_with_version_bypass_creates_test_admin_row(bypass_on):
    db = _FakeDb(existing=None)
    profile = await get_current_user_with_version(None, db)  # type: ignore[arg-type]
    assert profile["user_id"] == AUTH_BYPASS_USER_ID
    assert profile["role"] == "admin"
    assert len(db.added) == 1
    created = db.added[0]
    assert created.id == AUTH_BYPASS_USER_ID
    assert created.role == "admin"
    assert created.is_active is True
    # 随机密码：/auth/login 无法用已知口令登录该账号。
    assert created.password_hash and created.password_hash != ""


@pytest.mark.asyncio
async def test_with_version_bypass_reuses_existing_row(bypass_on):
    existing = SimpleNamespace(
        id=AUTH_BYPASS_USER_ID, role="admin", is_active=True, token_version=0
    )
    db = _FakeDb(existing=existing)
    profile = await get_current_user_with_version(None, db)  # type: ignore[arg-type]
    assert profile["user"] is existing
    assert len(db.added) == 0  # 不重复建行


@pytest.mark.asyncio
async def test_with_version_bypass_degrades_when_db_down(bypass_on):
    class _BrokenDb:
        async def execute(self, _query):
            raise RuntimeError("db down")

    profile = await get_current_user_with_version(None, _BrokenDb())  # type: ignore[arg-type]
    # 降级而非 500：dict 仍可用，仅缺 ORM user 键。
    assert profile["user_id"] == AUTH_BYPASS_USER_ID
    assert "user" not in profile


# ─── 路由级：require_admin 链在 bypass 下免登录通过 ───────────────────────


def _guarded_app(db) -> FastAPI:
    from app.core.database import get_async_db

    app = FastAPI()
    app.dependency_overrides[get_async_db] = lambda: db

    @app.get("/guarded")
    async def guarded(_user: dict = Depends(require_admin)):
        return {"user_id": _user["user_id"], "role": _user["role"]}

    return app


def test_guarded_route_passes_without_token_when_bypass_on(bypass_on):
    client = TestClient(_guarded_app(_FakeDb(existing=None)))
    resp = client.get("/guarded")  # 无 Authorization 头
    assert resp.status_code == 200
    assert resp.json() == {"user_id": AUTH_BYPASS_USER_ID, "role": "admin"}


def test_guarded_route_still_401_when_bypass_off(bypass_off):
    client = TestClient(_guarded_app(_FakeDb(existing=None)))
    resp = client.get("/guarded")
    assert resp.status_code == 401
