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


class _FakeResult:
    def __init__(self, user):
        self._user = user

    def scalar_one_or_none(self):
        return self._user


class _FakeDb:
    """最小 AsyncSession 假件：execute 返回预置 User 行（同 test_auth_bypass）。"""

    def __init__(self, existing):
        self.existing = existing

    async def execute(self, _query):
        return _FakeResult(self.existing)


class TestGetCurrentUserWithVersion:
    """with_version 角色来源契约：role 以 DB 实时值为准（降级即时生效）。"""

    def _creds(self, claims: dict):
        from fastapi.security import HTTPAuthorizationCredentials

        token = create_access_token(claims)
        return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)

    @pytest.mark.asyncio
    async def test_role_downgrade_takes_effect_immediately(self):
        """深度评审 High：admin→viewer 降级后，旧 admin token 剩余 TTL 内不得再持 admin 权限。"""
        from types import SimpleNamespace

        from app.core.auth import get_current_user_with_version

        db = _FakeDb(existing=SimpleNamespace(
            id="u-demoted", role="viewer", org_id=None, is_active=True,
            token_version=0,
        ))
        info = await get_current_user_with_version(
            self._creds({"sub": "u-demoted", "role": "admin"}), db
        )
        assert info["role"] == "viewer"
        assert "admin:read" not in info["scopes"]

    @pytest.mark.asyncio
    async def test_stale_admin_scopes_claim_clamped_to_db_role(self):
        """降级后旧 token 的宽 scope claim 被角色基线钳制（scopes.py 交集语义）。"""
        from types import SimpleNamespace

        from app.core.auth import get_current_user_with_version

        db = _FakeDb(existing=SimpleNamespace(
            id="u-demoted", role="viewer", org_id=None, is_active=True,
            token_version=0,
        ))
        info = await get_current_user_with_version(
            self._creds({
                "sub": "u-demoted", "role": "admin",
                "scopes": "gis:read admin:write",
            }),
            db,
        )
        assert "gis:read" in info["scopes"]
        assert "admin:write" not in info["scopes"]

    @pytest.mark.asyncio
    async def test_db_role_wins_in_both_directions(self):
        """DB 优先与方向无关：DB 升为 admin 而 claim 仍是 viewer 时以 DB 为准。"""
        from types import SimpleNamespace

        from app.core.auth import get_current_user_with_version

        db = _FakeDb(existing=SimpleNamespace(
            id="u-promoted", role="admin", org_id=None, is_active=True,
            token_version=0,
        ))
        info = await get_current_user_with_version(
            self._creds({"sub": "u-promoted", "role": "viewer"}), db
        )
        assert info["role"] == "admin"


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
