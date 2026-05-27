"""tool_metrics tests — JSONL writer + in-process aggregator + digest emission."""
import json
import os
import pytest

from app.services import tool_metrics


@pytest.fixture(autouse=True)
def _isolated_metrics(tmp_path, monkeypatch):
    """每个测试用临时日志文件 + 重置聚合器。"""
    log_path = tmp_path / "tool_metrics.jsonl"
    monkeypatch.setattr(tool_metrics, "LOG_PATH", str(log_path))
    tool_metrics._reset_for_tests()
    yield log_path
    tool_metrics._reset_for_tests()


def test_record_tool_call_writes_one_jsonl_line(_isolated_metrics):
    tool_metrics.record_tool_call(
        tool="heatmap_data",
        arg_bytes=1234,
        result_bytes=56789,
        duration_ms=312,
        cache_hit=False,
        error=None,
        session_id="sess1",
    )
    text = _isolated_metrics.read_text().strip()
    assert text.count("\n") == 0  # exactly one line
    row = json.loads(text)
    assert row["tool"] == "heatmap_data"
    assert row["arg_bytes"] == 1234
    assert row["result_bytes"] == 56789
    assert row["duration_ms"] == 312
    assert row["cache_hit"] is False
    assert row["error"] is None
    assert row["session_id"] == "sess1"
    assert "ts" in row and row["ts"].endswith("Z")


def test_record_tool_call_cache_hit_true(_isolated_metrics):
    tool_metrics.record_tool_call(
        tool="heatmap_data", arg_bytes=10, result_bytes=20,
        duration_ms=1, cache_hit=True, error=None, session_id=None,
    )
    row = json.loads(_isolated_metrics.read_text().strip())
    assert row["cache_hit"] is True
    assert row["session_id"] is None


def test_record_tool_call_error_records_class_name(_isolated_metrics):
    tool_metrics.record_tool_call(
        tool="osm_fetch", arg_bytes=100, result_bytes=0,
        duration_ms=2000, cache_hit=False, error="TimeoutError", session_id=None,
    )
    row = json.loads(_isolated_metrics.read_text().strip())
    assert row["error"] == "TimeoutError"


def test_record_tool_call_disk_failure_does_not_raise(monkeypatch, _isolated_metrics):
    """写盘失败不能阻塞工具调用。"""
    def boom(*a, **kw):
        raise OSError("disk full")
    monkeypatch.setattr(tool_metrics, "_write_jsonl_line", boom)
    # MUST NOT raise
    tool_metrics.record_tool_call(
        tool="x", arg_bytes=0, result_bytes=0, duration_ms=0,
        cache_hit=False, error=None, session_id=None,
    )
