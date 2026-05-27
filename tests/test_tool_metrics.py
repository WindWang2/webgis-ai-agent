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


def test_aggregator_counts_after_synthetic_calls(_isolated_metrics):
    for _ in range(3):
        tool_metrics.record_tool_call(
            tool="A", arg_bytes=0, result_bytes=0, duration_ms=100,
            cache_hit=False, error=None, session_id=None,
        )
    for _ in range(2):
        tool_metrics.record_tool_call(
            tool="A", arg_bytes=0, result_bytes=0, duration_ms=50,
            cache_hit=True, error=None, session_id=None,
        )
    tool_metrics.record_tool_call(
        tool="A", arg_bytes=0, result_bytes=0, duration_ms=200,
        cache_hit=False, error="ValueError", session_id=None,
    )
    snap = tool_metrics.aggregator_snapshot()
    assert snap["A"]["count"] == 6
    assert snap["A"]["total_ms"] == 3 * 100 + 2 * 50 + 200
    assert snap["A"]["max_ms"] == 200
    assert snap["A"]["hit_count"] == 2
    assert snap["A"]["error_count"] == 1


def test_emit_digest_writes_log_line(caplog, _isolated_metrics):
    for _ in range(5):
        tool_metrics.record_tool_call(
            tool="heatmap_data", arg_bytes=0, result_bytes=0, duration_ms=120,
            cache_hit=False, error=None, session_id=None,
        )
    with caplog.at_level("INFO", logger="app.services.tool_metrics"):
        tool_metrics.emit_digest()
    matching = [r for r in caplog.records if "TOOL_METRICS_DIGEST" in r.getMessage()]
    assert len(matching) == 1
    msg = matching[0].getMessage()
    assert "n=5" in msg
    assert "heatmap_data" in msg


def test_emit_digest_empty_aggregator_emits_nothing(caplog, _isolated_metrics):
    with caplog.at_level("INFO", logger="app.services.tool_metrics"):
        tool_metrics.emit_digest()
    matching = [r for r in caplog.records if "TOOL_METRICS_DIGEST" in r.getMessage()]
    assert len(matching) == 0


def test_auto_digest_at_100_calls(caplog, _isolated_metrics):
    with caplog.at_level("INFO", logger="app.services.tool_metrics"):
        for _ in range(100):
            tool_metrics.record_tool_call(
                tool="A", arg_bytes=0, result_bytes=0, duration_ms=1,
                cache_hit=False, error=None, session_id=None,
            )
    matching = [r for r in caplog.records if "TOOL_METRICS_DIGEST" in r.getMessage()]
    assert len(matching) == 1


def test_no_digest_at_99_calls(caplog, _isolated_metrics):
    with caplog.at_level("INFO", logger="app.services.tool_metrics"):
        for _ in range(99):
            tool_metrics.record_tool_call(
                tool="A", arg_bytes=0, result_bytes=0, duration_ms=1,
                cache_hit=False, error=None, session_id=None,
            )
    matching = [r for r in caplog.records if "TOOL_METRICS_DIGEST" in r.getMessage()]
    assert len(matching) == 0
