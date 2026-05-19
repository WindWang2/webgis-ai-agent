"""配置模块测试"""
from app.core.config import Settings


def test_default_settings():
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


def test_database_url():
    s = Settings()
    assert "sqlite" in s.DATABASE_URL
