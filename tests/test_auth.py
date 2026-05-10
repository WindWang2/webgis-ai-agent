"""认证模块测试"""
import pytest
from datetime import timedelta, timezone, datetime

from app.core.auth import (
    create_access_token,
    verify_token,
    get_current_user,
    get_current_user_optional,
)


class TestCreateAccessToken:
    def test_creates_token_with_default_expiry(self):
        token = create_access_token({"sub": "user123"})
        assert isinstance(token, str)
        assert len(token) > 0

    def test_creates_token_with_custom_expiry(self):
        token = create_access_token({"sub": "user123"}, expires_delta=timedelta(hours=2))
        payload = verify_token(token)
        assert payload["sub"] == "user123"

    def test_token_contains_exp_claim(self):
        token = create_access_token({"sub": "user123"})
        payload = verify_token(token)
        assert "exp" in payload
        assert payload["exp"] > datetime.now(timezone.utc).timestamp()


class TestVerifyToken:
    def test_valid_token(self):
        token = create_access_token({"sub": "user123", "role": "admin"})
        payload = verify_token(token)
        assert payload["sub"] == "user123"
        assert payload["role"] == "admin"

    def test_invalid_token_returns_none(self):
        assert verify_token("totally.invalid.token") is None

    def test_expired_token_returns_none(self):
        token = create_access_token({"sub": "user123"}, expires_delta=timedelta(seconds=-1))
        assert verify_token(token) is None

    def test_malformed_token_returns_none(self):
        assert verify_token("not-a-token") is None
        assert verify_token("") is None


class TestGetCurrentUser:
    @pytest.mark.asyncio
    async def test_missing_credentials_raises_401(self):
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc:
            await get_current_user(None)
        assert exc.value.status_code == 401

    @pytest.mark.asyncio
    async def test_invalid_token_raises_401(self):
        from fastapi import HTTPException
        from fastapi.security import HTTPAuthorizationCredentials
        creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="bad-token")
        with pytest.raises(HTTPException) as exc:
            await get_current_user(creds)
        assert exc.value.status_code == 401

    @pytest.mark.asyncio
    async def test_valid_token_returns_user(self):
        from fastapi.security import HTTPAuthorizationCredentials
        token = create_access_token({"sub": "user456"})
        creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
        user = await get_current_user(creds)
        assert user["user_id"] == "user456"

    @pytest.mark.asyncio
    async def test_token_without_sub_raises_401(self):
        from fastapi import HTTPException
        from fastapi.security import HTTPAuthorizationCredentials
        token = create_access_token({"role": "admin"})  # no sub
        creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
        with pytest.raises(HTTPException) as exc:
            await get_current_user(creds)
        assert exc.value.status_code == 401


class TestGetCurrentUserOptional:
    @pytest.mark.asyncio
    async def test_no_credentials_returns_anonymous(self):
        user = await get_current_user_optional(None)
        assert user["user_id"] == "anonymous"

    @pytest.mark.asyncio
    async def test_invalid_token_returns_anonymous(self):
        from fastapi.security import HTTPAuthorizationCredentials
        creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="bad")
        user = await get_current_user_optional(creds)
        assert user["user_id"] == "anonymous"

    @pytest.mark.asyncio
    async def test_valid_token_returns_user(self):
        from fastapi.security import HTTPAuthorizationCredentials
        token = create_access_token({"sub": "user789"})
        creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
        user = await get_current_user_optional(creds)
        assert user["user_id"] == "user789"
