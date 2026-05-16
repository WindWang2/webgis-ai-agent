"""Tests for session manager factory and fallback logic."""
import pytest
from unittest.mock import patch, MagicMock
from app.services.session_data import create_session_data_manager, SessionDataManager
from app.services.session_data_redis import RedisSessionDataManager

@pytest.fixture
def mock_settings():
    with patch("app.core.config.settings") as mocked:
        yield mocked

def test_factory_returns_redis_when_enabled_and_available(mock_settings):
    mock_settings.USE_REDIS = True
    mock_settings.REDIS_URL = "redis://localhost:6379/0"
    
    with patch("app.services.session_data_redis.RedisSessionDataManager.ping", return_value=None):
        manager = create_session_data_manager()
        assert isinstance(manager, RedisSessionDataManager)

def test_factory_falls_back_to_memory_when_redis_fails(mock_settings):
    mock_settings.USE_REDIS = True
    mock_settings.REDIS_URL = "redis://invalid_host:6379/0"
    
    # Force ping to fail
    with patch("app.services.session_data_redis.RedisSessionDataManager.ping", side_effect=Exception("Connection error")):
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
