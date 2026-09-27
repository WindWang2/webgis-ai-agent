"""audit ISSUE-005（#1377）回归：legacy JWT（无 type/ver claim）sunset 机制。

- 默认（JWT_REJECT_LEGACY_TOKENS=False）：back-compat 接受，按 ver=0 处理，
  计 auth_jwt_legacy_accepted_total 指标 + 一次性告警
- 拒绝模式：legacy token 一律 401（get_current_user / get_current_user_with_version）
  /降级匿名（optional）；指标不递增
- 新格式 token（带 type+ver）不受开关影响（两种模式都放行）

sunset 执行步骤（观测期→通知期→强制期）与回滚方式见
docs/dev/jwt-legacy-token-sunset.md；本文件是各阶段的可执行验收锚点。
"""
import logging
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import jwt as pyjwt
import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from app.core import auth
from app.core.auth import (
    get_current_user,
    get_current_user_optional,
    get_current_user_with_version,
)
from app.core.config import settings


def _legacy_token(sub: str = "legacy-user") -> str:
    """模拟 ver/type claim 引入前签发的旧格式 token。"""
    payload = {
        "sub": sub,
        "username": sub,
        "exp": datetime.now(timezone.utc) + timedelta(minutes=10),
        "iat": datetime.now(timezone.utc),
    }
    return pyjwt.encode(payload, auth.SECRET_KEY, algorithm=auth.ALGORITHM)


def _creds(token: str) -> HTTPAuthorizationCredentials:
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


@pytest.fixture
def _reject_mode(monkeypatch):
    monkeypatch.setattr(settings, "JWT_REJECT_LEGACY_TOKENS", True)


@pytest.fixture(autouse=True)
def _clear_warned():
    auth._legacy_warned.clear()
    yield
    auth._legacy_warned.clear()


@pytest.mark.asyncio
async def test_legacy_token_accepted_by_default(monkeypatch):
    monkeypatch.setattr(settings, "JWT_REJECT_LEGACY_TOKENS", False)
    monkeypatch.setattr(auth, "auth_bypass_enabled", lambda: False)
    user = await get_current_user(_creds(_legacy_token()))
    assert user["user_id"] == "legacy-user"


@pytest.mark.asyncio
async def test_legacy_token_rejected_when_sunset(_reject_mode, monkeypatch):
    monkeypatch.setattr(auth, "auth_bypass_enabled", lambda: False)
    with pytest.raises(HTTPException) as ei:
        await get_current_user(_creds(_legacy_token()))
    assert ei.value.status_code == 401
    assert "legacy" in ei.value.detail.lower()


@pytest.mark.asyncio
async def test_new_token_unaffected_by_sunset(_reject_mode, monkeypatch):
    monkeypatch.setattr(auth, "auth_bypass_enabled", lambda: False)
    token = auth.create_access_token({"sub": "modern-user"})
    user = await get_current_user(_creds(token))
    assert user["user_id"] == "modern-user"


@pytest.mark.asyncio
async def test_optional_degrades_to_anon_when_sunset(_reject_mode, monkeypatch):
    monkeypatch.setattr(auth, "auth_bypass_enabled", lambda: False)
    user = await get_current_user_optional(_creds(_legacy_token()))
    assert user["user_id"] == "anonymous"


@pytest.mark.asyncio
async def test_legacy_accept_emits_metric_and_dedup_warning(monkeypatch, caplog):
    monkeypatch.setattr(settings, "JWT_REJECT_LEGACY_TOKENS", False)
    monkeypatch.setattr(auth, "auth_bypass_enabled", lambda: False)
    token = _legacy_token()
    with caplog.at_level(logging.WARNING, logger="app.core.auth"):
        await get_current_user(_creds(token))
        await get_current_user(_creds(token))
    warns = [r for r in caplog.records if "legacy JWT" in r.getMessage()]
    assert len(warns) == 1, "同一 token 的废弃告警应只发一次"


def test_check_legacy_rejects_missing_ver_even_with_type(_reject_mode):
    """type=access 但缺 ver claim 的畸形新 token 也算 legacy。"""
    payload = {"sub": "u", "type": "access"}
    with pytest.raises(HTTPException):
        auth._check_legacy_token(payload, "tok")


def test_check_legacy_passes_modern_payload(_reject_mode):
    auth._check_legacy_token({"sub": "u", "type": "access", "ver": 3}, "tok")


# ── 观测期证据链：指标断言（防重构静默丢失，sunset 翻开关的前置证据）────

def _legacy_metric_value() -> float:
    """从 prometheus 默认 REGISTRY 读取 accepted 计数器当前值。"""
    from prometheus_client import REGISTRY

    value = REGISTRY.get_sample_value("auth_jwt_legacy_accepted_total")
    return float(value or 0.0)


@pytest.mark.asyncio
async def test_legacy_accept_increments_metric(monkeypatch):
    """兼容模式：每接受一个 legacy token，accepted 指标 +1。"""
    monkeypatch.setattr(settings, "JWT_REJECT_LEGACY_TOKENS", False)
    monkeypatch.setattr(auth, "auth_bypass_enabled", lambda: False)
    before = _legacy_metric_value()
    await get_current_user(_creds(_legacy_token()))
    assert _legacy_metric_value() == pytest.approx(before + 1)


@pytest.mark.asyncio
async def test_legacy_reject_does_not_touch_accept_metric(_reject_mode, monkeypatch):
    """强制模式：拒绝路径不得递增 accepted 指标（指标语义只属于兼容接受）。"""
    monkeypatch.setattr(auth, "auth_bypass_enabled", lambda: False)
    before = _legacy_metric_value()
    with pytest.raises(HTTPException):
        await get_current_user(_creds(_legacy_token()))
    assert _legacy_metric_value() == pytest.approx(before)


# ── ver=0 语义固化：legacy token 的 back-compat 等价类 ─────────────────

@pytest.mark.asyncio
async def test_optional_returns_ver_zero_for_legacy(monkeypatch):
    """兼容模式把 legacy token 按 ver=0 处理（optional 依赖透传 ver=0）。"""
    monkeypatch.setattr(settings, "JWT_REJECT_LEGACY_TOKENS", False)
    monkeypatch.setattr(auth, "auth_bypass_enabled", lambda: False)
    user = await get_current_user_optional(_creds(_legacy_token()))
    assert user["user_id"] == "legacy-user"
    assert user["ver"] == 0


@pytest.mark.asyncio
async def test_new_token_accepted_in_compat_mode(monkeypatch):
    """开关关闭时新格式 token 照常通过（双状态对照面的另一半）。"""
    monkeypatch.setattr(settings, "JWT_REJECT_LEGACY_TOKENS", False)
    monkeypatch.setattr(auth, "auth_bypass_enabled", lambda: False)
    token = auth.create_access_token({"sub": "modern-user"})
    user = await get_current_user(_creds(token))
    assert user["user_id"] == "modern-user"


# ── with_version 依赖（查库路径）的双状态 ──────────────────────────────

class _FakeResult:
    def __init__(self, row: object):
        self._row = row

    def scalar_one_or_none(self) -> object:
        return self._row


class _FakeDB:
    def __init__(self, user: object):
        self._user = user

    async def execute(self, _query: object) -> _FakeResult:
        return _FakeResult(self._user)


def _fake_user(token_version: int = 0) -> object:
    return SimpleNamespace(
        token_version=token_version, is_active=True, role="viewer", org_id=None
    )


@pytest.mark.asyncio
async def test_with_version_accepts_legacy_as_ver0(monkeypatch):
    """兼容模式：with_version 依赖把 legacy token 视为 ver=0，与 DB 匹配即放行。"""
    monkeypatch.setattr(settings, "JWT_REJECT_LEGACY_TOKENS", False)
    monkeypatch.setattr(auth, "auth_bypass_enabled", lambda: False)
    user = await get_current_user_with_version(
        _creds(_legacy_token()), db=_FakeDB(_fake_user(token_version=0))
    )
    assert user["user_id"] == "legacy-user"
    assert user["user"].token_version == 0


@pytest.mark.asyncio
async def test_with_version_rejects_legacy_before_db_lookup(_reject_mode, monkeypatch):
    """强制模式：with_version 依赖在查库之前就 401（fail fast，不打 DB）。"""
    monkeypatch.setattr(auth, "auth_bypass_enabled", lambda: False)

    class _ProbingDB(_FakeDB):
        async def execute(self, _query: object) -> _FakeResult:
            raise AssertionError("sunset 拒绝必须发生在 DB 查询之前")

    with pytest.raises(HTTPException) as ei:
        await get_current_user_with_version(
            _creds(_legacy_token()), db=_ProbingDB(_fake_user())
        )
    assert ei.value.status_code == 401
    assert "legacy" in ei.value.detail.lower()
