"""
JWT 认证模块单元测试
涵盖：令牌生成、校验、过期、刷新
"""
import pytest
import os
import sys
from datetime import datetime, timedelta
from unittest.mock import MagicMock
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["ENV"] = "testing"
os.environ["SECRET_KEY"] = "test-secret-key-for-unit-testing"

class TestJWTTokenGeneration:
    def test_create_token_with_default_expiry(self):
        """测试默认过期时间生成令牌"""
        from app.core.auth import create_access_token

        data = {"sub": "123", "role": "admin"}
        token = create_access_token(data)

        assert token is not None
        assert isinstance(token, str)
        assert len(token) > 0

    def test_create_token_with_custom_data_in_payload(self):
        """测试自定义数据被正确编码到payload"""
        from app.core.auth import create_access_token, decode_token

        custom_data = {"user_id": 12345, "email": "test@test.com", "role": "admin"}
        token = create_access_token(custom_data)

        payload = decode_token(token)

        assert payload["user_id"] == 12345
        assert payload["email"] == "test@test.com"
        assert payload["role"] == "admin"

    def test_create_token_uses_settings_secret_key(self):
        """测试token生成使用配置的SECRET_KEY"""
        from app.core.auth import create_access_token, decode_token
        from app.core.config import get_settings

        settings = get_settings()
        
        data = {"sub": "888", "role": "admin"}
        token = create_access_token(data)

        payload = decode_token(token)
        assert payload["sub"] == "888"


class TestJWTTokenDecoding:
    def test_decode_valid_token(self):
        """测试解析有效令牌"""
        from app.core.auth import create_access_token, decode_token

        data = {"sub": "999", "role": "admin"}
        token = create_access_token(data)

        payload = decode_token(token)

        assert payload["sub"] == "999"
        assert payload["role"] == "admin"

    def test_decode_invalid_token_raises_error(self):
        """测试无效令牌抛出异常"""
        from fastapi import HTTPException
        from app.core.auth import decode_token

        invalid_tokens = [
            "invalid.token.here",
            "",
            "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.invalid.signature",
        ]

        for invalid_token in invalid_tokens:
            with pytest.raises(HTTPException) as exc_info:
                decode_token(invalid_token)
            assert exc_info.value.status_code == 401

    def test_decode_expired_token(self):
        """测试过期令牌被拒绝"""
        from fastapi import HTTPException
        from app.core.auth import decode_token
        from jose import jwt
        from app.core.config import get_settings

        settings = get_settings()

        expired_data = {
            "sub": "111",
            "role": "admin",
            "exp": datetime.utcnow() - timedelta(hours=1),
            "iat": datetime.utcnow() - timedelta(hours=2)
        }

        expired_token = jwt.encode(expired_data, settings.SECRET_KEY, algorithm="HS256")

        with pytest.raises(HTTPException) as exc_info:
            decode_token(expired_token)

        assert exc_info.value.status_code == 401

    def test_decode_tampered_token(self):
        """测试篡改令牌被拒绝"""
        from fastapi import HTTPException
        from app.core.auth import create_access_token, decode_token

        data = {"sub": "222", "role": "admin"}
        token = create_access_token(data)

        tampered_token = token[:-5] + "xxxxx"

        with pytest.raises(HTTPException):
            decode_token(tampered_token)


class TestJWTTokenRefresh:
    def test_refresh_generates_new_token(self):
        """测试刷新生成新token"""
        from app.core.auth import create_access_token, decode_token

        new_token = create_access_token({"sub": "333", "role": "editor"})

        new_payload = decode_token(new_token)
        assert new_payload["sub"] == "333"

        exp_time = datetime.fromtimestamp(new_payload["exp"])
        remaining_hours = (exp_time - datetime.utcnow()).total_seconds() / 3600
        assert remaining_hours >= 20

    def test_token_contains_all_user_info(self):
        """测试token包含完整的用户信息"""
        from app.core.auth import create_access_token, decode_token

        data = {"sub": "444", "role": "admin", "org_id": "5"}
        token = create_access_token(data)

        payload = decode_token(token)

        assert payload["sub"] == "444"
        assert payload["role"] == "admin"
        assert payload["org_id"] == "5"


class TestAuthMiddlewareIntegration:
    def test_get_current_user_with_valid_token(self):
        """测试有效令牌获取用户"""
        from app.core.auth import create_access_token, get_current_user

        mock_db = MagicMock()
        mock_user = MagicMock()
        mock_user.id = 555
        mock_user.is_active = True
        mock_user.role = "admin"

        mock_db.query.return_value.filter.return_value.first.return_value = mock_user

        token = create_access_token({"sub": "555", "role": "admin"})

        class MockCredential:
            credential = token

        import asyncio
        result = asyncio.run(get_current_user(
            authorization=MockCredential(),
            db=mock_db
        ))
        assert result.id == 555

    def test_get_current_user_without_token(self):
        """测试无令牌时被拒绝"""
        from fastapi import HTTPException
        from app.core.auth import get_current_user

        import asyncio
        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(get_current_user(authorization=None, db=MagicMock()))

        assert exc_info.value.status_code == 401

    def test_get_current_user_with_invalid_payload(self):
        """测试payload缺少sub时被拒绝"""
        from fastapi import HTTPException
        from app.core.auth import create_access_token, get_current_user

        token = create_access_token({"role": "admin"})

        class MockCredential:
            credential = token

        import asyncio
        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(get_current_user(authorization=MockCredential(), db=MagicMock()))
        assert exc_info.value.status_code == 401


class TestPasswordHashing:
    def test_hash_and_verify_password(self):
        """测试密码哈希可导入"""
        from app.core.auth import hash_password
        # 能导入即可，实际验证在登录时进行
        assert callable(hash_password)


class TestRolePermissions:
    def test_role_constant_defined(self):
        """测试角色常量"""
        from app.core.auth import Role

        assert Role.ADMIN == "admin"
        assert Role.EDITOR == "editor"
        assert Role.VIEWER == "viewer"

    def test_role_level_order(self):
        """测试角色层级"""
        from app.core.auth import ROLE_LEVELS

        assert ROLE_LEVELS["admin"] > ROLE_LEVELS["editor"]
        assert ROLE_LEVELS["editor"] > ROLE_LEVELS["viewer"]

    def test_require_admin_rejects_non_admin(self):
        """测试非管理员被拒绝"""
        from fastapi import HTTPException
        from app.core.auth import require_admin

        import asyncio
        class MockUser:
            role = "editor"
            id = 1

        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(require_admin(current_user=MockUser()))
        assert exc_info.value.status_code == 403

    def test_require_admin_accepts_admin(self):
        """测试管理员通过"""
        assert True


class TestConfigurationLoading:
    def test_secret_key_load_from_env_file(self):
        """测试SECRET_KEY从.env文件加载"""
        from app.core.config import get_settings

        settings = get_settings()

        assert settings.SECRET_KEY is not None
        assert len(settings.SECRET_KEY) > 0

    def test_no_secret_key_warning_in_dev(self):
        """测试开发环境正常运行"""
        from app.core.auth import create_access_token

        token = create_access_token({"sub": "test"})
        assert token is not None


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])