"""kde_contours perf opt-in: cache hit + trim on contour FC."""
import json
import pytest
from unittest.mock import patch, MagicMock

from app.tools.registry import ToolRegistry
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
async def test_kde_contours_second_call_cache_hit():
    # Check dependencies
    try:
        import matplotlib
        import scipy
    except ImportError:
        pytest.skip("matplotlib/scipy not installed")

    # Import registration function — try both naming conventions
    try:
        from app.tools.spatial_stats import register_spatial_stats_tools
        reg = ToolRegistry()
        register_spatial_stats_tools(reg)
    except ImportError:
        # Try alternative naming
        from app.tools.spatial_stats import register_stats_tools
        reg = ToolRegistry()
        register_stats_tools(reg)

    storage = {}
    args = {
        "geojson": {"type": "FeatureCollection", "features": [
            {"type": "Feature",
             "geometry": {"type": "Point", "coordinates": [116.0 + i * 0.01, 39.0]},
             "properties": {}}
            for i in range(20)
        ]},
    }
    with patch("app.lib.tool_cache._get_redis_client") as mock_client:
        mock_redis = MagicMock()
        mock_redis.get.side_effect = lambda k: storage.get(k)
        mock_redis.setex.side_effect = lambda k, ttl, v: storage.__setitem__(k, v)
        mock_client.return_value = mock_redis

        r1 = await reg.dispatch("kde_contours", args, session_id=None)
        r2 = await reg.dispatch("kde_contours", args, session_id=None)

    # Compare JSON serializations (cache returns lists instead of tuples for geometry coords)
    assert json.dumps(r1, sort_keys=True, default=str) == json.dumps(r2, sort_keys=True, default=str)

    lines = [json.loads(l) for l in
             open(tool_metrics.LOG_PATH).read().strip().splitlines()]
    assert lines[1]["cache_hit"] is True
