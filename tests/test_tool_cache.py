"""Cache key generation tests for app.lib.tool_cache."""
import pytest

from app.lib.tool_cache import make_cache_key


def test_make_cache_key_deterministic():
    k1 = make_cache_key("heatmap_data", {"a": 1, "b": 2})
    k2 = make_cache_key("heatmap_data", {"a": 1, "b": 2})
    assert k1 == k2
    assert k1.startswith("tool_cache:v1:")
    # 16 hex chars after the prefix
    assert len(k1.split(":")[-1]) == 16


def test_make_cache_key_sorted_keys():
    # Same args in different insertion order must produce the same key.
    k1 = make_cache_key("heatmap_data", {"a": 1, "b": 2})
    k2 = make_cache_key("heatmap_data", {"b": 2, "a": 1})
    assert k1 == k2


def test_make_cache_key_tool_name_in_hash():
    k1 = make_cache_key("heatmap_data", {"a": 1})
    k2 = make_cache_key("h3_binning", {"a": 1})
    assert k1 != k2


def test_make_cache_key_nonjson_falls_back_to_str():
    from datetime import datetime
    # Should NOT raise — default=str handles datetime, set, etc.
    k = make_cache_key("x", {"t": datetime(2026, 5, 27)})
    assert k.startswith("tool_cache:v1:")


def test_make_cache_key_skips_ref_string():
    assert make_cache_key("x", {"geojson": "ref:abc123"}) is None


def test_make_cache_key_skips_ref_nested_in_list():
    assert make_cache_key("x", {"items": ["a", "ref:b"]}) is None


def test_make_cache_key_skips_ref_deep_nested():
    assert make_cache_key("x", {"a": {"b": {"c": "ref:x"}}}) is None


def test_make_cache_key_no_ref_returns_key():
    assert make_cache_key("x", {"a": "normal", "b": ["c", "d"]}) is not None


from unittest.mock import patch, MagicMock

from app.lib.tool_cache import get_cached, set_cached, _reset_redis_client_for_tests


@pytest.fixture(autouse=True)
def _reset_redis():
    """每个测试重置模块级 redis 单例，避免测试间污染。"""
    _reset_redis_client_for_tests()
    yield
    _reset_redis_client_for_tests()


def test_get_cached_miss_returns_none():
    with patch("app.lib.tool_cache._get_redis_client") as mock_client:
        mock_redis = MagicMock()
        mock_redis.get.return_value = None
        mock_client.return_value = mock_redis
        assert get_cached("tool_cache:v1:nope") is None


def test_get_cached_hit_decodes_json():
    with patch("app.lib.tool_cache._get_redis_client") as mock_client:
        mock_redis = MagicMock()
        mock_redis.get.return_value = b'{"success": true, "data": 42}'
        mock_client.return_value = mock_redis
        assert get_cached("tool_cache:v1:hit") == {"success": True, "data": 42}


def test_set_cached_writes_with_ttl():
    with patch("app.lib.tool_cache._get_redis_client") as mock_client:
        mock_redis = MagicMock()
        mock_client.return_value = mock_redis
        set_cached("tool_cache:v1:k", {"a": 1}, ttl=3600)
        mock_redis.setex.assert_called_once()
        call_args = mock_redis.setex.call_args
        assert call_args.args[0] == "tool_cache:v1:k"
        assert call_args.args[1] == 3600
        # value is JSON-encoded bytes
        import json as j
        assert j.loads(call_args.args[2]) == {"a": 1}


def test_get_cached_redis_down_returns_none_does_not_raise():
    """Redis 抛 ConnectionError → get_cached 返回 None，工具调用照常走未命中路径。"""
    import redis as redis_mod
    with patch("app.lib.tool_cache._get_redis_client") as mock_client:
        mock_redis = MagicMock()
        mock_redis.get.side_effect = redis_mod.ConnectionError("down")
        mock_client.return_value = mock_redis
        # MUST NOT raise
        assert get_cached("tool_cache:v1:k") is None


def test_set_cached_redis_down_swallows_error():
    """Redis SET 失败时工具调用必须照常返回结果。"""
    import redis as redis_mod
    with patch("app.lib.tool_cache._get_redis_client") as mock_client:
        mock_redis = MagicMock()
        mock_redis.setex.side_effect = redis_mod.ConnectionError("down")
        mock_client.return_value = mock_redis
        # MUST NOT raise
        set_cached("tool_cache:v1:k", {"a": 1}, ttl=3600)
