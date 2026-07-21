"""网络请求工具模块测试"""
import ssl
import pytest

from app.core.network import (
    get_ssl_context,
    get_base_headers,
    create_client_session,
    get_shared_client,
    close_shared_client,
)


class TestGetSSLContext:
    def test_verify_mode_returns_default_context(self):
        ctx = get_ssl_context(verify=True)
        assert isinstance(ctx, ssl.SSLContext)
        assert ctx.check_hostname is True
        assert ctx.verify_mode == ssl.CERT_REQUIRED

    def test_no_verify_requires_explicit_env_flag(self):
        """Refuse to create insecure SSL context unless ALLOW_INSECURE_SSL is set."""
        import os
        # Ensure env is clean
        os.environ.pop("ALLOW_INSECURE_SSL", None)
        with pytest.raises(ValueError, match="ALLOW_INSECURE_SSL"):
            get_ssl_context(verify=False)

    def test_no_verify_with_env_flag_returns_insecure_context(self):
        """ALLOW_INSECURE_SSL=true permits insecure context (for dev/testing only)."""
        import os
        os.environ["ALLOW_INSECURE_SSL"] = "true"
        try:
            ctx = get_ssl_context(verify=False)
            assert isinstance(ctx, ssl.SSLContext)
            assert ctx.check_hostname is False
            assert ctx.verify_mode == ssl.CERT_NONE
        finally:
            os.environ.pop("ALLOW_INSECURE_SSL", None)


class TestGetBaseHeaders:
    def test_contains_user_agent(self):
        headers = get_base_headers()
        assert "User-Agent" in headers
        assert "WebGIS-AI-Agent" in headers["User-Agent"]

    def test_contains_accept_language(self):
        headers = get_base_headers()
        assert "Accept-Language" in headers
        assert "zh-CN" in headers["Accept-Language"]

    def test_contains_accept_json(self):
        headers = get_base_headers()
        assert headers.get("Accept") == "application/json"


class TestSharedClient:
    @pytest.mark.asyncio
    async def test_get_shared_client_returns_session(self):
        session = await get_shared_client()
        assert session is not None
        assert not session.closed

    @pytest.mark.asyncio
    async def test_close_shared_client(self):
        await get_shared_client()
        await close_shared_client()
        # Calling again after close should create a new session
        session = await get_shared_client()
        assert session is not None
        assert not session.closed
        await close_shared_client()

    @pytest.mark.asyncio
    async def test_shared_client_is_reused(self):
        s1 = await get_shared_client()
        s2 = await get_shared_client()
        assert s1 is s2
        await close_shared_client()
