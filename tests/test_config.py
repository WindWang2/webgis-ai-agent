"""配置模块测试"""
from app.core.config import Settings


def test_default_settings():
    s = Settings()
    assert s.PROJECT_NAME == "WebGIS AI Agent"
    assert s.DEBUG is True
    assert s.LLM_MODEL == "MiniMax-M2.5"
    assert s.DATA_DIR == "./data"


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
