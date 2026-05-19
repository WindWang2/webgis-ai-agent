"""认证模块测试"""
import pytest
from datetime import timedelta, timezone, datetime

from app.core.auth import (
    create_access_token,
    verify_token,
    get_current_user,
    get_current_user_optional,
    hash_password,
    verify_password,
)


class TestPasswordHashing:
    def test_round_trip_match(self):
        h = hash_password("secret-passphrase-1!")
        assert verify_password("secret-passphrase-1!", h) is True

    def test_wrong_password_fails(self):
        h = hash_password("a")
        assert verify_password("b", h) is False

    def test_garbage_stored_returns_false(self):
        # 不抛异常，只返回 False；时序侧信道安全前提
        assert verify_password("x", "") is False
        assert verify_password("x", "garbage-no-dollar") is False
        assert verify_password("x", "scrypt$bad$format") is False

    def test_empty_plaintext_rejected(self):
        import pytest
        with pytest.raises(ValueError):
            hash_password("")

    def test_two_hashes_of_same_password_differ(self):
        # 不同 salt → 不同 hash（确认确实在用 salt）
        assert hash_password("same") != hash_password("same")

    def test_rejects_pathological_scrypt_params(self):
        # 防御：攻击者构造 N 巨大值想做 CPU DoS
        bogus = "scrypt$999999999$8$1$" + "00" * 16 + "$" + "00" * 32
        assert verify_password("x", bogus) is False


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
