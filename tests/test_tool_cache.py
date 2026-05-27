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
