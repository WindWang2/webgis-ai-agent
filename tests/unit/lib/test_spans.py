"""统一 Span 观测树测试（ADR-0131 D1）。

覆盖：父子链、trace 继承/生成、异常分类落 status、attributes 有界化、
导出面隔离（单个 exporter 故障不拖累）、跨进程 header 出入口、
ContextVar 泄漏防护（span 结束后 current_span_id 复位）。
"""
from __future__ import annotations

import asyncio
import json
import logging

import pytest

from app.lib.observability.spans import (
    LoggingSpanExporter,
    RingSpanExporter,
    SpanStage,
    SpanStatus,
    context_kwargs_from_headers,
    current_span_id,
    install_exporter,
    remove_exporter,
    reset_exporters_for_tests,
    start_span,
    trace_headers,
)
from app.lib.observability.trace_context import generate_traceparent
from app.lib.runtime.context import bind_runtime_context


@pytest.fixture(autouse=True)
def _clean_span_state():
    reset_exporters_for_tests()
    yield
    reset_exporters_for_tests()


def _ring() -> RingSpanExporter:
    ring = RingSpanExporter(capacity=32)
    install_exporter(ring)
    return ring


# ── 基本树形 ────────────────────────────────────────────────────────────


def test_root_span_generates_trace_and_child_inherits():
    ring = _ring()
    with start_span(SpanStage.USER_REQUEST, "request") as root:
        root_trace = root.trace_id
        with start_span(SpanStage.TOOL, "buffer") as child:
            assert child.trace_id == root_trace
            assert child.parent_span_id == root.span_id
    spans = ring.snapshot()
    assert len(spans) == 2
    assert all(s.status == SpanStatus.OK.value for s in spans)
    # 子 span 先退出先导出；按身份而非顺序断言
    by_id = {s.span_id: s for s in spans}
    assert set(by_id) == {root.span_id, child.span_id}
    assert by_id[root.span_id].parent_span_id is None
    assert by_id[child.span_id].parent_span_id == root.span_id


def test_trace_inherited_from_runtime_context():
    ring = _ring()
    with bind_runtime_context(trace_id="a" * 32, span_id="b" * 16):
        with start_span(SpanStage.HARNESS, "plan") as span:
            assert span.trace_id == "a" * 32
            assert span.parent_span_id == "b" * 16
    (rec,) = ring.snapshot()
    assert rec.trace_id == "a" * 32


def test_span_binding_scoped_and_no_leak():
    """span 作用域内 current_span_id 可见，退出后复位、外层上下文不被污染。"""
    with bind_runtime_context(trace_id="c" * 32, span_id="d" * 16):
        assert current_span_id() is None
        with start_span(SpanStage.TOOL, "x"):
            inner = current_span_id()
            assert inner is not None and inner != "d" * 16
        assert current_span_id() is None
        # 外层 RuntimeContext.span_id 不被 span 绑定污染（bind 是作用域性的）
        from app.lib.runtime.context import current_runtime_context

        assert current_runtime_context().span_id == "d" * 16


def test_span_id_visible_in_thread_via_context_copy():
    """to_thread 继承 ContextVar（跨线程工具路径的 span 关联前提）。"""
    seen = {}

    async def main():
        with start_span(SpanStage.GEOCOMPUTE, "offloop"):
            seen["inner"] = current_span_id()
            await asyncio.to_thread(
                lambda: seen.setdefault("thread", current_span_id())
            )

    asyncio.run(main())
    assert seen["inner"] == seen["thread"]
    assert current_span_id() is None


# ── 异常/取消语义 ────────────────────────────────────────────────────────


def test_error_span_classifies_category_and_reraises():
    ring = _ring()
    with pytest.raises(TimeoutError):
        with start_span(SpanStage.MODEL, "llm_call") as span:
            raise TimeoutError("provider slow")
    (rec,) = ring.snapshot()
    assert rec.status == SpanStatus.ERROR.value
    assert rec.error_category == "timeout"
    assert span.span_id == rec.span_id


def test_cancelled_span_status():
    ring = _ring()

    async def run():
        with pytest.raises(asyncio.CancelledError):
            with start_span(SpanStage.TOOL, "cancelme"):
                raise asyncio.CancelledError()

    asyncio.run(run())
    (rec,) = ring.snapshot()
    assert rec.status == SpanStatus.CANCELLED.value


def test_base_exception_still_records_and_reraises():
    """BaseException（非 Cancelled）也必须先记录再传播（finally 语义）。"""
    ring = _ring()
    with pytest.raises(KeyboardInterrupt):
        with start_span(SpanStage.TOOL, "hard-exit"):
            raise KeyboardInterrupt()
    (rec,) = ring.snapshot()
    assert rec.status == SpanStatus.ERROR.value


def test_attributes_bounded():
    ring = _ring()
    big = {f"k{i}": "v" * 400 for i in range(40)}
    with start_span(SpanStage.TOOL, "attrs", **big):
        pass
    (rec,) = ring.snapshot()
    assert len(rec.attributes) == 16
    assert all(len(v) <= 256 for v in rec.attributes.values())


def test_single_exporter_failure_isolated():
    class _Boom:
        def export(self, span):
            raise RuntimeError("exporter down")

    boom = _Boom()
    install_exporter(boom)
    ring = _ring()
    with start_span(SpanStage.TOOL, "ok"):
        pass
    remove_exporter(boom)
    assert len(ring.snapshot()) == 1


# ── 跨进程 header 出入口 ─────────────────────────────────────────────────


def test_trace_headers_roundtrip():
    with bind_runtime_context(trace_id="e" * 32, span_id="f" * 16):
        with start_span(SpanStage.WORKFLOW, "dispatch"):
            headers = trace_headers()
            assert "traceparent" in headers
            restored = context_kwargs_from_headers(headers)
    assert restored["trace_id"] == "e" * 32
    assert len(restored["span_id"]) == 16
    assert restored["span_id"] != "f" * 16  # 是 span 自己的 id，不是外层的


def test_trace_headers_empty_without_context():
    assert trace_headers() == {}
    assert context_kwargs_from_headers(None) == {}
    assert context_kwargs_from_headers({}) == {}


def test_context_kwargs_rejects_garbage():
    assert context_kwargs_from_headers({"traceparent": "not-a-traceparent"}) == {}
    assert context_kwargs_from_headers({"traceparent": generate_traceparent()}) != {}


def test_traceparent_header_format():
    """出站头必须是合法 W3C traceparent（严格格式，可被外部 tracer 接受）。"""
    from app.lib.observability.trace_context import parse_traceparent

    with bind_runtime_context(trace_id="1" * 32, span_id="2" * 16):
        headers = trace_headers()
    parsed = parse_traceparent(headers["traceparent"])
    assert parsed is not None
    assert parsed.trace_id == "1" * 32


# ── LoggingExporter ──────────────────────────────────────────────────────


def test_logging_exporter_emits_json_line(caplog):
    exporter = LoggingSpanExporter()
    install_exporter(exporter)
    with caplog.at_level(logging.INFO, logger="app.observability.spans"):
        with start_span(SpanStage.ARTIFACT, "publish", kind="geotiff"):
            pass
    lines = [
        r for r in caplog.records if r.name == "app.observability.spans"
    ]
    assert lines
    payload = json.loads(lines[-1].getMessage())
    assert payload["stage"] == "artifact"
    assert payload["status"] == "ok"
    assert payload["attributes"] == {"kind": "geotiff"}
    assert "duration_s" in payload and "trace_id" in payload
