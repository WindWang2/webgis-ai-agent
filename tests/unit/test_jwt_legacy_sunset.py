"""audit ISSUE-005（#1377）回归：legacy JWT（无 type/ver claim）sunset 机制。

- 默认（JWT_REJECT_LEGACY_TOKENS=False）：back-compat 接受，计指标+一次性告警
- 拒绝模式：legacy token 一律 401（get_current_user）/降级匿名（optional）
- 新格式 token（带 type+ver）不受开关影响
"""
import logging
from datetime import datetime, timedelta, timezone

import jwt as pyjwt
import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from app.core import auth
from app.core.auth import get_current_user, get_current_user_optional
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
