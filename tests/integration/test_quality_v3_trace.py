"""W3C trace 关联测试（Quality V3 W10）。

parse/generate 单元（正/负/边界）+ 纯 ASGI 中间件（http/websocket 全
scope）+ RuntimeContext 合并绑定 + events envelope trace 键。
"""
from __future__ import annotations

import asyncio

import pytest

from app.lib.observability.trace_context import (
    TraceContext,
    TraceContextMiddleware,
    generate_traceparent,
    new_span_id,
    new_trace_id,
    parse_traceparent,
)
from app.lib.runtime.context import (
    bind_runtime_context,
    current_runtime_context,
)


# ── parse/generate ──────────────────────────────────────────────────────

VALID = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"


def test_parse_valid():
    ctx = parse_traceparent(VALID)
    assert ctx == TraceContext(version="00",
                               trace_id="4bf92f3577b34da6a3ce929d0e0e4736",
                               span_id="00f067aa0ba902b7", flags="01")


def test_parse_uppercase_and_whitespace():
    ctx = parse_traceparent(f"  {VALID.upper()} ")
    assert ctx is not None and ctx.trace_id.startswith("4bf9")


@pytest.mark.parametrize("bad", [
    "", None,
    "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7",  # 缺 flags
    "0-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01",  # version 短
    "ff-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01",  # version ff
    "00-ffffffffffffffffffffffffffffffff-00f067aa0ba902b7-01",  # trace 全 f
    "00-00000000000000000000000000000000-00f067aa0ba902b7-01",  # trace 全 0
    "00-4bf92f3577b34da6a3ce929d0e0e4736-0000000000000000-01",  # span 全 0
    "00-4bf92f3577b34da6a3ce929d0e0e473g-00f067aa0ba902b7-01",  # 非 hex
    "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-zz",  # flags 非法
    "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-ff",  # flags ff 保留
])
def test_parse_invalid_explicit_reject(bad):
    assert parse_traceparent(bad) is None


def test_generate_roundtrip_and_uniqueness():
    tp = generate_traceparent()
    ctx = parse_traceparent(tp)
    assert ctx is not None
    assert generate_traceparent(trace_id=ctx.trace_id,
                                span_id=new_span_id()) != tp
    assert len({new_trace_id() for _ in range(50)}) == 50
    assert len({new_span_id() for _ in range(50)}) == 50


# ── ASGI 中间件（http + websocket 全 scope） ────────────────────────────

def _capture_app(captured: dict):
    async def app(scope, receive, send):
        captured["scope"] = scope
        captured["ctx"] = current_runtime_context()
        if scope["type"] == "http":
            await send({"type": "http.response.start", "status": 200,
                        "headers": [(b"content-type", b"text/plain")]})
            await send({"type": "http.response.body", "body": b"ok"})
    return app


def _receive(messages):
    idx = {"i": 0}

    async def receive():
        i = idx["i"]
        idx["i"] += 1
        return messages[min(i, len(messages) - 1)]
    return receive


def _collect(sent: list):
    async def send(message):
        sent.append(message)
    return send


def test_http_inbound_traceparent_bound_and_echoed():
    captured: dict = {}
    app = TraceContextMiddleware(_capture_app(captured))
    sent: list = []
    scope = {"type": "http", "headers": [
        (b"traceparent", VALID.encode()),
        (b"x-request-id", b"req-42"),
    ]}
    asyncio.run(app(scope, _receive([{"type": "http.request"}]),
                    _collect(sent)))
    # 断言：下游可见 trace 上下文（request_id 绑定是
    # RequestCorrelationMiddleware 的职责，不在本中间件契约内）
    assert captured["ctx"].trace_id == "4bf92f3577b34da6a3ce929d0e0e4736"
    assert captured["ctx"].span_id == "00f067aa0ba902b7"
    # 响应头回显 X-Trace-ID
    start = next(m for m in sent if m["type"] == "http.response.start")
    headers = {k.lower(): v for k, v in start["headers"]}
    assert headers[b"x-trace-id"] == b"4bf92f3577b34da6a3ce929d0e0e4736"


def test_http_missing_traceparent_generates_valid():
    captured: dict = {}
    app = TraceContextMiddleware(_capture_app(captured))
    sent: list = []
    scope = {"type": "http", "headers": []}
    asyncio.run(app(scope, _receive([{"type": "http.request"}]),
                    _collect(sent)))
    ctx = captured["ctx"]
    assert ctx.trace_id and len(ctx.trace_id) == 32
    assert parse_traceparent(
        f"00-{ctx.trace_id}-{ctx.span_id}-01") is not None
    start = next(m for m in sent if m["type"] == "http.response.start")
    headers = {k.lower(): v for k, v in start["headers"]}
    assert headers[b"x-trace-id"] == ctx.trace_id.encode()


def test_http_invalid_traceparent_falls_back_to_generated():
    captured: dict = {}
    app = TraceContextMiddleware(_capture_app(captured))
    sent: list = []
    scope = {"type": "http", "headers": [
        (b"traceparent", b"garbage-value")]}

    asyncio.run(app(scope, _receive([{"type": "http.request"}]),
                    _collect(sent)))
    assert captured["ctx"].trace_id
    assert captured["ctx"].trace_id != "garbage-value"


def test_websocket_scope_covered():
    """C-3：chat WS 主链路的 trace 绑定（BaseHTTPMiddleware 盲区）。"""
    captured: dict = {}
    app = TraceContextMiddleware(_capture_app(captured))
    scope = {"type": "websocket", "headers": [(b"traceparent",
                                               VALID.encode())]}
    asyncio.run(app(scope, _receive([{"type": "websocket.connect"}]),
                    _collect([])))
    assert captured["ctx"].trace_id == "4bf92f3577b34da6a3ce929d0e0e4736"


def test_non_http_scope_passthrough():
    captured: dict = {}
    app = TraceContextMiddleware(_capture_app(captured))
    scope = {"type": "lifespan"}
    asyncio.run(app(scope, _receive([{"type": "lifespan.startup"}]),
                    _collect([])))
    assert "ctx" not in captured or captured.get("ctx") is None


# ── RuntimeContext / events envelope ────────────────────────────────────

def test_runtime_context_trace_fields_additive():
    with bind_runtime_context(request_id="rq", trace_id="tr", span_id="sp"):
        ctx = current_runtime_context()
        assert ctx.request_id == "rq" and ctx.trace_id == "tr"
        # 嵌套合并：内层只覆盖 turn，trace/request 保留
        with bind_runtime_context(turn_id="t1"):
            inner = current_runtime_context()
            assert inner.trace_id == "tr" and inner.turn_id == "t1"
        assert current_runtime_context().turn_id is None


def test_events_envelope_carries_trace():
    from app.lib.observability.events import RingSink, emit_event, register_sink

    sink = RingSink()
    register_sink(sink)
    with bind_runtime_context(trace_id="trace-abc"):
        emit_event("cartography", "render", status="completed", layer_count=1)
    rec = sink.snapshot(1)[0]
    assert rec["trace"] == "trace-abc"


def test_real_app_has_trace_middleware_mounted():
    from app.main import app

    middleware_types = [
        m.cls.__name__ if isinstance(getattr(m, "cls", None), type)
        else type(getattr(m, "cls", m)).__name__
        for m in app.user_middleware
    ]
    assert any("Trace" in name for name in middleware_types), \
        f"trace 中间件未挂载: {middleware_types}"
