"""W3C Trace Context 关联（Quality V3 W10，架构 §H / R3 修订）。

跨进程/跨服务的标准 trace 关联接缝：解析/生成 W3C ``traceparent`` 头
（Trace Context Propagation, v1），纯 stdlib，无 OTel SDK 依赖
（events.py 的 vendor-neutral Sink 协议是未来 OTel 接入点，follow-up）。

设计契约：
- **严格校验**：``00-{32hex}-{16hex}-{2hex}``；version/trace/span 的
  全 ff / 全 0 保留值非法（W3C 规范）；非法输入显式返回 None（调用方
  生成新 trace），绝不静默吞半截数据。
- **纯 ASGI 中间件（universal scope）**：``TraceContextMiddleware`` 同
  时覆盖 http 与 websocket（Subagent-A C-3：BaseHTTPMiddleware 对
  websocket 直接透传，chat WS 主链路会漏）；入站解析 → RuntimeContext
  合并绑定（trace_id/span_id 字段，additive）→ HTTP 响应回
  ``X-Trace-ID``（前端/外部 tracer 关联用）。
- 与既有 ``X-Request-ID`` 中间件正交：本模块不生成 request_id，只补
  trace 维度。
"""
from __future__ import annotations

import re
import secrets
from dataclasses import dataclass
from typing import Iterable, Optional, Tuple

TRACEPATTERN = re.compile(
    r"^([0-9a-f]{2})-([0-9a-f]{32})-([0-9a-f]{16})-([0-9a-f]{2})$")

#: W3C 保留值：version ff；id 全 0 / 全 f 非法
_TRACE_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_SPAN_ID_RE = re.compile(r"^[0-9a-f]{16}$")

RESPONSE_HEADER = "x-trace-id"
TRACEPARENT_HEADER = "traceparent"


@dataclass(frozen=True)
class TraceContext:
    version: str
    trace_id: str
    span_id: str
    flags: str

    def traceparent(self) -> str:
        return f"{self.version}-{self.trace_id}-{self.span_id}-{self.flags}"


def new_trace_id() -> str:
    return secrets.token_hex(16)


def new_span_id() -> str:
    return secrets.token_hex(8)


def generate_traceparent(trace_id: Optional[str] = None,
                         span_id: Optional[str] = None,
                         flags: str = "01") -> str:
    return TraceContext(
        version="00", trace_id=trace_id or new_trace_id(),
        span_id=span_id or new_span_id(), flags=flags,
    ).traceparent()


def parse_traceparent(header: Optional[str]) -> Optional[TraceContext]:
    """严格解析 traceparent；非法 → None（显式拒绝，不静默纠正）。"""
    if not header:
        return None
    value = header.strip().lower()
    m = TRACEPATTERN.match(value)
    if not m:
        return None
    version, trace_id, span_id, flags = m.groups()
    if version == "ff":
        return None
    if flags == "ff":  # W3C 保留值：all-f flags 与 version ff 同判非法
        return None
    if set(trace_id) in ({"0"}, {"f"}):
        return None
    if set(span_id) in ({"0"}, {"f"}):
        return None
    return TraceContext(version=version, trace_id=trace_id,
                        span_id=span_id, flags=flags)


def _header_value(raw_headers: Iterable[Tuple[bytes, bytes]],
                  name: bytes) -> Optional[str]:
    for key, value in raw_headers:
        if key.lower() == name:
            try:
                return value.decode("ascii")
            except UnicodeDecodeError:
                return None
    return None


class TraceContextMiddleware:
    """纯 ASGI 中间件：http + websocket 全 scope 覆盖（C-3）。

    - 入站 traceparent 解析失败/缺失 → 生成新 trace（照常服务，不拒请求）；
    - RuntimeContext 合并绑定（保留 request_id/session_id 等已有字段）；
    - http 响应回 X-Trace-ID（send 包装，流式响应同样覆盖）。
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        headers = scope.get("headers") or []
        incoming = parse_traceparent(
            _header_value(headers, TRACEPARENT_HEADER.encode()))
        if incoming is not None:
            ctx = incoming
        else:
            ctx = TraceContext(version="00", trace_id=new_trace_id(),
                               span_id=new_span_id(), flags="01")
        try:
            from app.lib.runtime.context import bind_runtime_context
            cm = bind_runtime_context(trace_id=ctx.trace_id,
                                      span_id=ctx.span_id)
        except Exception:  # noqa: BLE001 — 关联失败不阻断服务
            cm = None
        if scope["type"] == "http":
            send = _send_with_trace_header(send, ctx.trace_id)
        if cm is not None:
            with cm:
                await self.app(scope, receive, send)
        else:
            await self.app(scope, receive, send)


def _send_with_trace_header(send, trace_id: str):
    async def wrapped(message):
        if message["type"] == "http.response.start":
            headers = [(k, v) for k, v in message.get("headers") or []
                       if k.lower() != RESPONSE_HEADER.encode()]
            headers.append((RESPONSE_HEADER.encode(), trace_id.encode()))
            message = {**message, "headers": headers}
        await send(message)
    return wrapped
