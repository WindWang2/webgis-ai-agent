"""buffer_analysis perf opt-in: cache hit on identical call + trim envelope on big input."""
import pytest
from unittest.mock import patch, MagicMock

from app.tools.registry import ToolRegistry
from app.tools.spatial import register_spatial_tools
from app.lib.tool_cache import _reset_redis_client_for_tests
from app.services import tool_metrics


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    # writer 必须先把上一个 LOG_PATH 的 batch 落盘，否则会写入新路径。
    tool_metrics._wait_idle()
    monkeypatch.setattr(tool_metrics, "LOG_PATH", str(tmp_path / "tool_metrics.jsonl"))
    tool_metrics._reset_for_tests()
    _reset_redis_client_for_tests()
    yield
    tool_metrics._reset_for_tests()
    _reset_redis_client_for_tests()


@pytest.mark.asyncio
async def test_buffer_analysis_second_call_cache_hit():
    reg = ToolRegistry()
    register_spatial_tools(reg)

    storage = {}
    args = {
        "geojson": {"type": "FeatureCollection", "features": [
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [116.0, 39.0]}, "properties": {}}
        ]},
        "distance": 100.0,
        "unit": "m",
    }
    with patch("app.lib.tool_cache._get_redis_client") as mock_client:
        mock_redis = MagicMock()
        mock_redis.get.side_effect = lambda k: storage.get(k)
        mock_redis.setex.side_effect = lambda k, ttl, v: storage.__setitem__(k, v)
        mock_client.return_value = mock_redis

        r1 = await reg.dispatch("buffer_analysis", args, session_id=None)
        r2 = await reg.dispatch("buffer_analysis", args, session_id=None)

    assert r1 == r2
    # The second dispatch must have set cache_hit=True in its metrics row.
    # Metrics writes are async (queued writer thread) — poll for the rows.
    import json
    import time
    deadline = time.monotonic() + 5.0
    lines = []
    while time.monotonic() < deadline:
        try:
            lines = [json.loads(line) for line in
                     open(tool_metrics.LOG_PATH).read().strip().splitlines()]
        except (OSError, json.JSONDecodeError):
            lines = []
        if len(lines) >= 2:
            break
        time.sleep(0.05)
    assert len(lines) >= 2
    assert lines[0]["cache_hit"] is False
    assert lines[1]["cache_hit"] is True
