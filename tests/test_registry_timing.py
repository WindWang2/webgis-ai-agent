"""Registry timing wrapper tests — every dispatch records one metrics row."""
import json
import pytest

from app.services import tool_metrics
from app.tools.registry import ToolRegistry
from app.lib.tool_cache import cached_tool, _reset_redis_client_for_tests
from unittest.mock import patch, MagicMock


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    log_path = tmp_path / "tool_metrics.jsonl"
    monkeypatch.setattr(tool_metrics, "LOG_PATH", str(log_path))
    tool_metrics._reset_for_tests()
    _reset_redis_client_for_tests()
    yield log_path
    tool_metrics._reset_for_tests()
    _reset_redis_client_for_tests()


@pytest.mark.asyncio
async def test_dispatch_records_one_metrics_row(_isolated):
    reg = ToolRegistry()

    def fake_tool(x: int) -> dict:
        return {"r": x * 2}
    reg.register("fake_tool", "test", fake_tool)

    await reg.dispatch("fake_tool", {"x": 3}, session_id="s1")

    lines = _isolated.read_text().strip().splitlines()
    assert len(lines) == 1
    row = json.loads(lines[0])
    assert row["tool"] == "fake_tool"
    assert row["cache_hit"] is False
    assert row["session_id"] == "s1"
    assert row["duration_ms"] >= 0
    assert row["error"] is None


@pytest.mark.asyncio
async def test_dispatch_records_cache_hit_on_second_call(_isolated):
    reg = ToolRegistry()
    storage = {}

    @cached_tool(ttl=3600)
    def fake_tool(x: int) -> dict:
        return {"r": x * 2}
    reg.register("fake_tool", "test", fake_tool)

    with patch("app.lib.tool_cache._get_redis_client") as mock_client:
        mock_redis = MagicMock()
        mock_redis.get.side_effect = lambda k: storage.get(k)
        mock_redis.setex.side_effect = lambda k, ttl, v: storage.__setitem__(k, v)
        mock_client.return_value = mock_redis

        await reg.dispatch("fake_tool", {"x": 3}, session_id="s1")
        await reg.dispatch("fake_tool", {"x": 3}, session_id="s1")

    lines = _isolated.read_text().strip().splitlines()
    assert len(lines) == 2
    row1 = json.loads(lines[0])
    row2 = json.loads(lines[1])
    assert row1["cache_hit"] is False
    assert row2["cache_hit"] is True


@pytest.mark.asyncio
async def test_dispatch_records_error_class(_isolated):
    reg = ToolRegistry()

    def boom_tool() -> dict:
        raise RuntimeError("nope")
    reg.register("boom_tool", "test", boom_tool)

    # dispatch catches and returns std_error_response — we still expect a row.
    result = await reg.dispatch("boom_tool", {}, session_id=None)
    assert result.get("success") is False
    lines = _isolated.read_text().strip().splitlines()
    assert len(lines) == 1
    row = json.loads(lines[0])
    assert row["tool"] == "boom_tool"
    assert row["error"] == "RuntimeError"
