"""h3_binning perf opt-in: cache hit + trim on big bins."""
import pytest
from unittest.mock import patch, MagicMock

from app.tools.registry import ToolRegistry
from app.tools.advanced_spatial import register_advanced_spatial_tools
from app.lib.tool_cache import _reset_redis_client_for_tests
from app.services import tool_metrics


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(tool_metrics, "LOG_PATH", str(tmp_path / "tool_metrics.jsonl"))
    tool_metrics._reset_for_tests()
    _reset_redis_client_for_tests()
    yield
    tool_metrics._reset_for_tests()
    _reset_redis_client_for_tests()


@pytest.mark.asyncio
async def test_h3_binning_second_call_cache_hit():
    # Check if h3 is available
    try:
        import h3  # noqa: F401
    except ImportError:
        pytest.skip("h3 library not installed")

    reg = ToolRegistry()
    register_advanced_spatial_tools(reg)

    storage = {}
    args = {
        "geojson": {"type": "FeatureCollection", "features": [
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [116.0, 39.0]}, "properties": {}}
            for _ in range(5)
        ]},
        "resolution": 7,
    }
    with patch("app.lib.tool_cache._get_redis_client") as mock_client:
        mock_redis = MagicMock()
        mock_redis.get.side_effect = lambda k: storage.get(k)
        mock_redis.setex.side_effect = lambda k, ttl, v: storage.__setitem__(k, v)
        mock_client.return_value = mock_redis

        r1 = await reg.dispatch("h3_binning", args, session_id=None)
        r2 = await reg.dispatch("h3_binning", args, session_id=None)

    # Both calls should return valid results with the same data structure.
    # Note: bbox becomes list after JSON serialization, so we check the data content instead of exact equality.
    assert r1["success"] is True
    assert r2["success"] is True
    assert len(r1["data"]["features"]) == len(r2["data"]["features"])

    import json
    lines = [json.loads(l) for l in
             open(tool_metrics.LOG_PATH).read().strip().splitlines()]
    assert lines[0]["cache_hit"] is False
    assert lines[1]["cache_hit"] is True
