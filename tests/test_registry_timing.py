"""Registry timing wrapper tests — every dispatch records one metrics row."""
import json
import time
import pytest

from app.services import tool_metrics
from app.tools.registry import ToolRegistry
from app.lib.tool_cache import cached_tool, _reset_redis_client_for_tests
from unittest.mock import patch, MagicMock


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    log_path = tmp_path / "tool_metrics.jsonl"
    # writer 必须先把上一个 LOG_PATH 的 batch 落盘，否则会写入新路径。
    tool_metrics._wait_idle()
    monkeypatch.setattr(tool_metrics, "LOG_PATH", str(log_path))
    tool_metrics._reset_for_tests()
    _reset_redis_client_for_tests()
    yield log_path
    tool_metrics._reset_for_tests()
    _reset_redis_client_for_tests()


def _wait_rows(log_path, min_rows=1, timeout=5.0):
    """Metrics writes are async (queued writer thread) — poll for rows."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if log_path.exists():
            try:
                text = log_path.read_text(encoding="utf-8").strip()
            except (OSError, UnicodeDecodeError):
                time.sleep(0.02)
                continue
            rows = []
            for line in text.splitlines():
                if not line.strip():
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    pass  # torn line — retry
            if len(rows) >= min_rows:
                return rows
        time.sleep(0.02)
    raise AssertionError(f"log rows not written within {timeout}s: {log_path}")


@pytest.mark.asyncio
async def test_dispatch_records_one_metrics_row(_isolated):
    reg = ToolRegistry()

    def fake_tool(x: int) -> dict:
        return {"r": x * 2}
    reg.register("fake_tool", "test", fake_tool)

    await reg.dispatch("fake_tool", {"x": 3}, session_id="s1")

    rows = _wait_rows(_isolated)
    assert len(rows) == 1
    row = rows[0]
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

    rows = _wait_rows(_isolated, min_rows=2)
    assert len(rows) == 2
    row1 = rows[0]
    row2 = rows[1]
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
    rows = _wait_rows(_isolated)
    assert len(rows) == 1
    row = rows[0]
    assert row["tool"] == "boom_tool"
    assert row["error"] == "RuntimeError"
