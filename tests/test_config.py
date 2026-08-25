"""配置模块测试"""
from app.core.config import Settings


def test_default_settings(monkeypatch):
    # Clear DEBUG and ENV env vars to test default settings in isolation
    monkeypatch.delenv("DEBUG", raising=False)
    monkeypatch.delenv("ENV", raising=False)
    # 绕过 .env 文件读取真正的代码默认值
    s = Settings(_env_file=None)
    assert s.PROJECT_NAME == "WebGIS AI Agent"
    # 安全：默认禁用 DEBUG，避免 .env 缺失时生产端泄漏堆栈
    assert s.DEBUG is False
    # LLM_MODEL 默认值会随版本演进；只验证非空即可
    assert s.LLM_MODEL
    assert s.DATA_DIR == "./data"


def test_production_rejects_wildcard_cors():
    """生产环境严禁 CORS_ORIGINS=['*']"""
    import pytest
    with pytest.raises(RuntimeError, match="CORS_ORIGINS"):
        Settings(
            _env_file=None,
            ENV="production",
            JWT_SECRET_KEY="x" * 32,
            LLM_API_KEY="sk-test-key-for-cors-test",
            DATABASE_URL="postgresql://user:pass@localhost/db",
            CORS_ORIGINS=["*"],
        )


def test_llm_settings():
    s = Settings()
    assert s.LLM_BASE_URL
    assert s.LLM_MODEL


def test_osm_settings():
    s = Settings()
    assert s.OVERPASS_API_URL
    assert s.NOMINATIM_URL


def test_tiangodi_settings():
    s = Settings()
    assert hasattr(s, "TIANDITU_TOKEN")


def test_sentinel_settings():
    s = Settings()
    assert hasattr(s, "SENTINELHUB_CLIENT_ID")
    assert hasattr(s, "SENTINELHUB_CLIENT_SECRET")


def test_nasa_settings():
    s = Settings()
    assert hasattr(s, "NASA_EARTHDATA_USERNAME")
    assert hasattr(s, "NASA_EARTHDATA_PASSWORD")


def test_settings_does_not_read_db_host_components():
    """#618-32: compose '方式2' DB_HOST/DB_USER/… 是死键 —— Settings 只读 DATABASE_URL。"""
    fields = Settings.model_fields
    for name in ("DB_HOST", "DB_PORT", "DB_USER", "DB_PASSWORD", "DB_NAME", "DB_PWD"):
        assert name not in fields, (
            f"Settings.{name} would make decomposed DB_* keys real; "
            "the app only reads DATABASE_URL"
        )


def test_database_url():
    """DATABASE_URL must be a non-empty valid URL (sqlite or postgresql).

    CI runs against Postgres while local dev defaults to sqlite, so the
    assertion must accept both drivers instead of hard-coding sqlite.
    """
    from urllib.parse import urlparse
    s = Settings()
    assert s.DATABASE_URL, "DATABASE_URL must be set"
    parsed = urlparse(s.DATABASE_URL)
    assert parsed.scheme in {"sqlite", "postgresql", "postgres"}, (
        f"unsupported DATABASE_URL scheme: {parsed.scheme!r}"
    )


def test_production_rejects_auth_disabled():
    """#756: AUTH_DISABLED=true 是完整认证旁路（含 require_admin），生产必须 fail-loud。"""
    import pytest
    with pytest.raises(RuntimeError, match="AUTH_DISABLED"):
        Settings(
            _env_file=None,
            ENV="production",
            JWT_SECRET_KEY="x" * 32,
            LLM_API_KEY="sk-test-key-for-auth-disabled-test",
            DATABASE_URL="postgresql://user:pass@localhost/db",
            AUTH_DISABLED=True,
        )


def test_dev_allows_auth_disabled():
    """#756: 开发/测试模式仍允许免登录（本地测试便利）。"""
    s = Settings(
        _env_file=None,
        ENV="development",
        JWT_SECRET_KEY="x" * 32,
        AUTH_DISABLED=True,
    )
    assert s.AUTH_DISABLED is True


def test_llm_private_endpoints_allowed_in_production():
    """#925: LLM_BASE_URL 允许内网/集群内私网地址，仅做轻量校验。"""
    # direct private IPs and localhost should be allowed for LLM
    for url in [
        "http://10.244.1.25:8000/v1",
        "http://192.168.1.50:11434/v1",
        "http://172.16.0.10:8000/v1",
        "http://127.0.0.1:8000/v1",
        "http://localhost:11434/v1",
    ]:
        s = Settings(
            _env_file=None,
            ENV="production",
            JWT_SECRET_KEY="x" * 32,
            LLM_API_KEY="sk-test-key",
            DATABASE_URL="postgresql://user:pass@localhost/db",
            LLM_BASE_URL=url,
            OVERPASS_API_URL="https://overpass.openstreetmap.fr/api/interpreter",
            NOMINATIM_URL="https://nominatim.openstreetmap.org/search",
        )
        assert s.LLM_BASE_URL == url

    # cluster DNS resolving to private IP should also be allowed for LLM
    import unittest.mock as mock

    def _fake_llm_private(host, *a, **kw):
        if host == "vllm-service.webgis-prod.svc.cluster.local":
            return [(2, 1, 6, "", ("10.244.2.15", 8000))]
        return [(2, 1, 6, "", ("1.1.1.1", 80))]

    with mock.patch("socket.getaddrinfo", side_effect=_fake_llm_private):
        s = Settings(
            _env_file=None,
            ENV="production",
            JWT_SECRET_KEY="x" * 32,
            LLM_API_KEY="sk-test-key",
            DATABASE_URL="postgresql://user:pass@localhost/db",
            LLM_BASE_URL="http://vllm-service.webgis-prod.svc.cluster.local:8000/v1",
            OVERPASS_API_URL="https://overpass.openstreetmap.fr/api/interpreter",
            NOMINATIM_URL="https://nominatim.openstreetmap.org/search",
        )
        assert "vllm-service" in s.LLM_BASE_URL


def test_llm_still_rejects_invalid_scheme_and_no_hostname():
    """#925: LLM 轻量校验仍需拒绝非法 scheme 和缺失 hostname。"""
    import pytest
    with pytest.raises(Exception, match="disallowed scheme"):
        Settings(
            _env_file=None,
            ENV="production",
            JWT_SECRET_KEY="x" * 32,
            LLM_API_KEY="sk-test-key",
            DATABASE_URL="postgresql://user:pass@localhost/db",
            LLM_BASE_URL="ftp://10.0.0.1/v1",
        )
    with pytest.raises(Exception, match="has no hostname"):
        Settings(
            _env_file=None,
            ENV="production",
            JWT_SECRET_KEY="x" * 32,
            LLM_API_KEY="sk-test-key",
            DATABASE_URL="postgresql://user:pass@localhost/db",
            LLM_BASE_URL="http:///no-host",
        )


def test_overpass_and_nominatim_still_block_private():
    """#925 对照: Overpass/Nominatim 保持严格 SSRF，私网仍被拒。"""
    import pytest
    with pytest.raises(Exception, match="private|blocked domain|Blocked"):
        Settings(
            _env_file=None,
            ENV="production",
            JWT_SECRET_KEY="x" * 32,
            LLM_API_KEY="sk-test-key",
            DATABASE_URL="postgresql://user:pass@localhost/db",
            OVERPASS_API_URL="http://10.1.1.1/api",
        )
    with pytest.raises(Exception, match="private|blocked domain|Blocked"):
        Settings(
            _env_file=None,
            ENV="production",
            JWT_SECRET_KEY="x" * 32,
            LLM_API_KEY="sk-test-key",
            DATABASE_URL="postgresql://user:pass@localhost/db",
            NOMINATIM_URL="http://192.168.1.10/search",
        )
    # DNS 解析到私网也应被拒
    import unittest.mock as mock
    with mock.patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("10.0.0.5", 80))]):
        with pytest.raises(Exception, match="private.*Blocked|Blocked"):
            Settings(
                _env_file=None,
                ENV="production",
                JWT_SECRET_KEY="x" * 32,
                LLM_API_KEY="sk-test-key",
                DATABASE_URL="postgresql://user:pass@localhost/db",
                OVERPASS_API_URL="http://evil.example.com/api",
            )
