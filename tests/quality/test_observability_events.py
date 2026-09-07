"""Structured Observability 红线（ADR-0104 Wave 6）。

- 词表锁：EVENT_CATALOG 覆盖 Harness/Data/Compute/Cartography 四面的
  业务事件；词表外 event 抛错（漂移即红）；
- allowlist 制：未声明字段丢弃，敏感键双重防线；
- 关联自动注入（req/sess/turn/run/proj ← RuntimeContext）；
- 有界标签 digest（计数 + 时长分位）；
- 日志关联主干 proj 字段补齐（RuntimeCorrelationFilter + 格式）。
"""
from __future__ import annotations

import json
import logging

import pytest

from app.lib.observability import (
    EVENT_CATALOG,
    STATUS_VOCABULARY,
    RingSink,
    emit_event,
    event_digest,
    register_sink,
    reset_sinks,
)
from app.lib.runtime.context import bind_runtime_context


@pytest.fixture(autouse=True)
def _clean_sinks():
    reset_sinks()
    yield
    reset_sinks()


def test_catalog_covers_four_planes():
    assert set(EVENT_CATALOG) == {"harness", "data", "compute", "cartography"}
    # 目标词表抽查：无进度/晋升/资源拒绝/观测必须可发射
    assert "no_progress" in EVENT_CATALOG["harness"]
    assert "promotion" in EVENT_CATALOG["data"]
    assert "resource_rejection" in EVENT_CATALOG["compute"]
    assert "observe" in EVENT_CATALOG["cartography"]


def test_unknown_event_rejected():
    with pytest.raises(ValueError):
        emit_event("harness", "definitely_not_in_catalog")
    with pytest.raises(ValueError):
        emit_event("no_such_plane", "dispatch")


def test_unknown_status_rejected():
    with pytest.raises(ValueError):
        emit_event("harness", "dispatch", status="super_completed")


def test_allowlist_drops_undeclared_fields():
    sink = RingSink()
    register_sink(sink)
    emit_event("harness", "dispatch", tool="buffer_analysis", policy="thread",
               sql_text="DROP TABLE users")  # sql_text 未声明 → 丢弃
    rec = sink.snapshot(1)[0]
    assert rec["tool"] == "buffer_analysis"
    assert "sql_text" not in rec


def test_sensitive_key_never_enters_even_if_declared():
    """双防线：假设未来词表误声明敏感键，发射时仍被兜底扫描拦截。"""
    from app.lib.observability import events as ev

    sink = RingSink()
    register_sink(sink)
    # 直接构造恶意词表（模拟词表编辑回退），发射路径必须仍拦截
    ev.EVENT_CATALOG["harness"]["dispatch"] = ("tool", "api_key", "password")
    try:
        emit_event("harness", "dispatch", tool="t", api_key="sk-x", password="p")
    finally:
        ev.EVENT_CATALOG["harness"]["dispatch"] = ("tool", "policy")
    rec = sink.snapshot(1)[0]
    assert rec["tool"] == "t"
    assert "api_key" not in rec and "password" not in rec


def test_correlation_auto_injected():
    sink = RingSink()
    register_sink(sink)
    with bind_runtime_context(request_id="rq1", session_id="s1",
                              turn_id="t1", run_id="r1", project_id="p1"):
        emit_event("cartography", "render", status="completed", layer_count=3)
    rec = sink.snapshot(1)[0]
    assert rec["req"] == "rq1"
    assert rec["sess"] == "s1"
    assert rec["turn"] == "t1"
    assert rec["run"] == "r1"
    assert rec["proj"] == "p1"


def test_digest_bounded_and_quantiled():
    sink = RingSink()
    register_sink(sink)
    for i in range(10):
        emit_event("compute", "dispatch", status="completed",
                   duration_s=0.01 * (i + 1), node="n1", backend="local")
    emit_event("compute", "resource_rejection", status="rejected",
               kind="rows", limit=100000)
    digest = event_digest()
    assert digest["counts"]["compute|dispatch|completed"] == 10
    assert digest["counts"]["compute|resource_rejection|rejected"] == 1
    assert digest["duration_p50_s"]["compute|dispatch"] > 0
    assert digest["duration_p95_s"]["compute|dispatch"] >= (
        digest["duration_p50_s"]["compute|dispatch"])
    # 标签空间有界：digest 键数 ≤ 词表事件数 × status 词表
    max_keys = sum(len(ev) for ev in EVENT_CATALOG.values()) * len(STATUS_VOCABULARY)
    assert len(digest["counts"]) <= max_keys


def test_logging_sink_writes_json(caplog):
    from app.lib.observability import LoggingSink

    with caplog.at_level(logging.INFO, logger="webgis.observability"):
        LoggingSink().write({
            "category": "data", "event": "promotion", "status": "completed",
            "artifact_type": "geojson_fc", "bytes": 128,
        })
    line = caplog.records[-1].message
    payload = json.loads(line)
    assert payload["event"] == "promotion"
    assert payload["artifact_type"] == "geojson_fc"


def test_logging_filter_injects_project_id():
    from app.core.logging_config import RuntimeCorrelationFilter
    from app.lib.runtime.context import bind_runtime_context

    record = logging.LogRecord(
        "test", logging.INFO, __file__, 1, "msg", None, None)
    with bind_runtime_context(project_id="proj-42"):
        assert RuntimeCorrelationFilter().filter(record) is True
    assert record.project_id == "proj-42"  # type: ignore[attr-defined]


# ── R2 review MINOR-5/6：修复项回归锁 ────────────────────────────────────


def test_string_values_capped_and_nonfinite_float_normalized():
    sink = RingSink()
    register_sink(sink)
    long_reason = "x" * 500
    emit_event("harness", "replan", reason=long_reason)
    emit_event("compute", "queue", depth=float("nan"))
    recs = sink.snapshot(10)
    assert len(recs[0]["reason"]) == 257  # 256 + "…"
    assert recs[1]["depth"] == "nan"  # 非 strict-JSON 字面量不得入记录


def test_snapshot_zero_returns_empty():
    sink = RingSink()
    register_sink(sink)
    emit_event("harness", "dispatch", tool="t")
    assert sink.snapshot(0) == []
    assert sink.snapshot(-5) == []


def test_sink_write_exception_isolated():
    class _Boom:
        def write(self, record):
            raise RuntimeError("sink down")

    register_sink(_Boom())
    sink = RingSink()
    register_sink(sink)
    emit_event("harness", "dispatch", tool="t")  # 不得抛
    assert len(sink.snapshot(10)) == 1  # 健康汇仍收到
