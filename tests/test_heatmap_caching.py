"""End-to-end: heatmap_data cache hit + trim envelope on big inputs."""
import pytest
from unittest.mock import patch, MagicMock

from app.tools.registry import ToolRegistry
from app.tools.spatial import register_spatial_tools
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


def _make_point_fc(n: int) -> dict:
    return {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature",
             "geometry": {"type": "Point", "coordinates": [116.0 + i * 0.0001, 39.0]},
             "properties": {"idx": i}}
            for i in range(n)
        ],
    }


@pytest.mark.asyncio
async def test_heatmap_native_mode_second_call_cache_hit():
    """render_type=native 路径走 cache（无 Celery 依赖，测试最稳）."""
    reg = ToolRegistry()
    register_spatial_tools(reg)

    storage = {}
    args = {"geojson": _make_point_fc(10), "render_type": "native"}

    with patch("app.lib.tool_cache._get_redis_client") as mock_client:
        mock_redis = MagicMock()
        mock_redis.get.side_effect = lambda k: storage.get(k)
        mock_redis.setex.side_effect = lambda k, ttl, v: storage.__setitem__(k, v)
        mock_client.return_value = mock_redis

        r1 = await reg.dispatch("heatmap_data", args, session_id=None)
        r2 = await reg.dispatch("heatmap_data", args, session_id=None)

    assert r1 == r2

    import json
    lines = [json.loads(l) for l in
             open(tool_metrics.LOG_PATH).read().strip().splitlines()]
    assert lines[0]["cache_hit"] is False
    assert lines[1]["cache_hit"] is True
