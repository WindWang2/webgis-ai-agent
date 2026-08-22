"""tool_metrics tests — queued JSONL writer + aggregator + percentiles + rotation."""
import json
import os
import time

import pytest

from app.services import tool_metrics


@pytest.fixture(autouse=True)
def _isolated_metrics(tmp_path, monkeypatch):
    """每个测试用临时日志文件 + 重置聚合器 + 清空待写队列。"""
    log_path = tmp_path / "tool_metrics.jsonl"
    # writer 必须先把上一个 LOG_PATH 的 batch 落盘，否则会写入新路径。
    tool_metrics._wait_idle()
    monkeypatch.setattr(tool_metrics, "LOG_PATH", str(log_path))
    tool_metrics._reset_for_tests()
    yield log_path
    tool_metrics._reset_for_tests()


def _wait_for_rows(log_path, min_rows=1, timeout=5.0):
    """Writes are async (writer thread) — poll until rows appear.

    Tolerates torn reads: the writer may be mid-append when we read.
    """
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
    rows = _wait_for_rows(_isolated_metrics)
    row = rows[0]
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
    row = _wait_for_rows(_isolated_metrics)[0]
    assert row["cache_hit"] is True
    assert row["session_id"] is None


def test_record_tool_call_error_records_class_name(_isolated_metrics):
    tool_metrics.record_tool_call(
        tool="osm_fetch", arg_bytes=100, result_bytes=0,
        duration_ms=2000, cache_hit=False, error="TimeoutError", session_id=None,
    )
    row = _wait_for_rows(_isolated_metrics)[0]
    assert row["error"] == "TimeoutError"


def test_record_tool_call_disk_failure_does_not_raise(monkeypatch, _isolated_metrics):
    """写盘失败不能阻塞工具调用。"""
    def boom(*a, **kw):
        raise OSError("disk full")
    monkeypatch.setattr(tool_metrics, "_append_batch", boom)
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


# ─── new: real percentiles, rotation, backpressure ───────────────────────────


def test_snapshot_reports_true_percentiles(_isolated_metrics):
    """Log2 histogram must yield p50/p90/p95/p99 (bounded bins, no raw retention)."""
    for _ in range(80):
        tool_metrics.record_tool_call(
            tool="B", arg_bytes=0, result_bytes=0, duration_ms=100,
            cache_hit=False, error=None, session_id=None,
        )
    for _ in range(19):
        tool_metrics.record_tool_call(
            tool="B", arg_bytes=0, result_bytes=0, duration_ms=1000,
            cache_hit=False, error=None, session_id=None,
        )
    tool_metrics.record_tool_call(
        tool="B", arg_bytes=0, result_bytes=0, duration_ms=10000,
        cache_hit=False, error=None, session_id=None,
    )
    snap = tool_metrics.aggregator_snapshot()["B"]
    assert snap["count"] == 100
    # p50 ≈ 100 (80% of samples at 100ms)
    assert 50 <= snap["p50"] <= 200
    # p90 ≈ 1000 (90% at ≤1000ms)
    assert 500 <= snap["p90"] <= 2000
    # p95 ≈ 1000 (95% at ≤1000ms)
    assert 500 <= snap["p95"] <= 2000
    # p99 ≈ 1000
    assert 500 <= snap["p99"] <= 5000
    assert snap["max_ms"] == 10000  # max is max, not a percentile


def test_snapshot_aggregates_result_bytes(_isolated_metrics):
    """Aggregator tracks total_result_bytes per tool (goal §9 result bytes)."""
    tool_metrics.record_tool_call(
        tool="C", arg_bytes=10, result_bytes=500, duration_ms=5,
        cache_hit=False, error=None, session_id=None,
    )
    tool_metrics.record_tool_call(
        tool="C", arg_bytes=10, result_bytes=1500, duration_ms=5,
        cache_hit=False, error=None, session_id=None,
    )
    snap = tool_metrics.aggregator_snapshot()["C"]
    assert snap["count"] == 2
    assert snap["total_result_bytes"] == 2000


def test_rotation_keeps_backups_bounded(_isolated_metrics):
    """Filling past MAX_LOG_BYTES must rotate to .1..5 and drop the oldest."""
    import app.services.tool_metrics as tm

    old = tm._MAX_LOG_BYTES
    tm._MAX_LOG_BYTES = 100  # each JSONL row is ~150B → every append rotates
    try:
        for _ in range(4):
            tool_metrics.record_tool_call(
                tool="rot", arg_bytes=0, result_bytes=0, duration_ms=1,
                cache_hit=False, error=None, session_id=None,
            )
            _wait_for_rows(_isolated_metrics)
        # #812: 固定 sleep 换有界轮询 —— 满载 runner 上 0.3s 可能不够 writer
        # 完成轮转，偶发红；等待 .1 备份出现（与 _wait_for_rows 同款纪律）。
        _deadline = time.monotonic() + 5.0
        while time.monotonic() < _deadline:
            backups = sorted(
                p for p in os.listdir(_isolated_metrics.parent)
                if p.startswith(_isolated_metrics.name) and p != _isolated_metrics.name
            )
            if any(p.endswith(".1") for p in backups):
                break
            time.sleep(0.02)
        assert len(backups) <= tool_metrics._MAX_ROTATIONS
        assert any(p.endswith(".1") for p in backups)
        # The live log is bounded by one writer batch (rotation caps growth),
        # not left to accumulate every row forever.
        assert os.path.getsize(_isolated_metrics) < 2000
    finally:
        tm._MAX_LOG_BYTES = old


def test_queue_full_drops_row_without_blocking(_isolated_metrics, monkeypatch):
    """Backpressure: a full queue must not block or raise in the caller."""
    monkeypatch.setattr(tool_metrics, "_MAX_QUEUE", 1)
    # Recreate a tiny queue to honor the patched bound.
    monkeypatch.setattr(tool_metrics, "_queue", __import__("queue").Queue(maxsize=1))
    tool_metrics.record_tool_call(
        tool="A", arg_bytes=0, result_bytes=0, duration_ms=1,
        cache_hit=False, error=None, session_id=None,
    )  # fills the queue (writer may not have drained yet)
    t0 = time.monotonic()
    tool_metrics.record_tool_call(
        tool="B", arg_bytes=0, result_bytes=0, duration_ms=1,
        cache_hit=False, error=None, session_id=None,
    )  # must drop fast, not block
    assert time.monotonic() - t0 < 1.0
    snap = tool_metrics.aggregator_snapshot()
    assert snap["A"]["count"] == 1
    assert snap["B"]["count"] == 1  # aggregator updated even if the row dropped
