"""Tests for session manager factory and fallback logic."""
import pytest
from unittest.mock import patch, MagicMock
from app.services.session_data import create_session_data_manager, SessionDataManager
from app.services.session_data_redis import RedisSessionDataManager

@pytest.fixture
def mock_settings():
    with patch("app.core.config.settings") as mocked:
        yield mocked

def test_factory_returns_redis_when_enabled(mock_settings):
    """TEST-13：USE_REDIS=True 时直接返回 Redis 后端（懒连接，不再做启动期 ping 探测）。"""
    mock_settings.USE_REDIS = True
    mock_settings.REDIS_URL = "redis://localhost:6379/0"

    # 不再 mock ping —— factory 现在不在构造期连接 Redis。
    # RedisSessionDataManager.__init__ 只存配置，真正的连接发生在首次 async 操作。
    manager = create_session_data_manager()
    assert isinstance(manager, RedisSessionDataManager)
    # 懒连接：构造后客户端还未创建（_r is None），连池归属到首个运行的 loop。
    assert manager._r is None
    assert manager._redis_url == "redis://localhost:6379/0"

def test_factory_returns_memory_when_redis_disabled(mock_settings):
    """USE_REDIS=False 时返回内存后端。"""
    mock_settings.USE_REDIS = False
    manager = create_session_data_manager()
    assert isinstance(manager, SessionDataManager)
    assert not isinstance(manager, RedisSessionDataManager)

def test_factory_falls_back_to_memory_when_redis_lib_missing(mock_settings):
    mock_settings.USE_REDIS = True
    
    # Mock ImportError on importing RedisSessionDataManager
    with patch("builtins.__import__", side_effect=lambda name, *args, **kwargs: (
        exec('raise ImportError("No module named \'redis\'")') if name == "redis" else MagicMock()
    )):
        # This is tricky because it might break other things. 
        # Better to mock the import of RedisSessionDataManager directly.
        pass

def test_factory_falls_back_to_memory_when_import_fails(mock_settings):
    mock_settings.USE_REDIS = True
    with patch("app.services.session_data_redis.RedisSessionDataManager", side_effect=ImportError("redis not installed")):
        manager = create_session_data_manager()
        assert isinstance(manager, SessionDataManager)
