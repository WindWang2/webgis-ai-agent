"""统一 Span 观测树（Platform V4，ADR-0131 D1）。

把 Quality V3 W10 的"trace ID 传播"升级为**带阶段语义的 span 树**：

    User Request → Harness → Workflow → Tool/Algorithm → Data →
    GeoCompute → Model → Cartography → Artifact

设计契约：

- **不引入 OTel SDK**：字段名 OTel-shaped（trace_id/span_id/parent_span_id/
  start_time/end_time/status/attributes），vendor-neutral。接 OTel = 新增一个
  SpanExporter，事件源不改（与 events.py Sink 同纪律）。
- **挂靠 RuntimeContext**：trace_id 从当前 ContextVar 继承（无则生成新
  trace——独立脚本/worker 也能成树）；进入 span 时把 span_id 合并绑定进
  RuntimeContext，span 作用域内的日志自动带上 span 维度（与
  TraceContextMiddleware 的绑定语义一致，嵌套安全）。
- **与 GisTraceChain 的关系**：chain 是业务证据链（18 阶段、落注册表、
  完整性认证），span 是基础设施观测树（内存态、导出即弃）。两面各自
  独立，不互写——无第二事实源。
- **永不阻断业务**：导出/绑定/属性消毒的任何异常静默吞掉（与 emit_chain
  同门）；span 记录不落库、不外发，默认零持久化。
- **有界**：attributes 条目数/值长双上界（同 core.errors 纪律）；
  RingExporter 有界 deque（仅显式安装时存在）。

用法::

    with start_span(SpanStage.TOOL, "buffer_analysis", tool="buffer") as span:
        ...  # 业务执行；异常被分类后记录进 span，随后原样抛出
    # span.status == "error", span.error_category == "timeout" ...
"""
from __future__ import annotations

import asyncio
import contextlib
import contextvars
import json
import logging
import secrets
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Iterator, Optional

logger = logging.getLogger(__name__)


class SpanStage(str, Enum):
    """关键路径阶段（封闭词表；与 ADR-0131 的链路图逐项对应）。"""

    USER_REQUEST = "user_request"
    HARNESS = "harness"
    WORKFLOW = "workflow"
    TOOL = "tool"
    DATA = "data"
    GEOCOMPUTE = "geocompute"
    MODEL = "model"
    CARTOGRAPHY = "cartography"
    ARTIFACT = "artifact"
    EXTENSION = "extension"


class SpanStatus(str, Enum):
    OK = "ok"
    ERROR = "error"
    CANCELLED = "cancelled"


#: attributes 有界化（条目数/键长/值长三上界，core.errors 同款纪律）
_MAX_ATTR_ENTRIES = 16
_MAX_ATTR_KEY_CHARS = 64
_MAX_ATTR_VALUE_CHARS = 256


def _bounded_attrs(attrs: Optional[Dict[str, Any]]) -> Dict[str, str]:
    if not attrs:
        return {}
    out: Dict[str, str] = {}
    for k, v in list(attrs.items())[:_MAX_ATTR_ENTRIES]:
        try:
            out[str(k)[:_MAX_ATTR_KEY_CHARS]] = str(v)[:_MAX_ATTR_VALUE_CHARS]
        except Exception:  # noqa: BLE001 — __str__ 爆炸项跳过（review R1-m4）
            continue
    return out


def _new_span_id() -> str:
    return secrets.token_hex(8)


@dataclass(frozen=True)
class SpanRecord:
    """一条已结束的 span（不可变；导出面消费的唯一形态）。"""

    name: str
    stage: str
    trace_id: str
    span_id: str
    parent_span_id: Optional[str]
    started_at: float          # time.time()（墙钟，跨进程可对齐）
    duration_s: float          # time.perf_counter() 差值（单调）
    status: str
    error_category: Optional[str] = None
    attributes: Dict[str, str] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        """OTel-shaped 投影（无敏感字段；attributes 已消毒）。"""
        return {
            "name": self.name,
            "stage": self.stage,
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "parent_span_id": self.parent_span_id,
            "start_time": round(self.started_at, 6),
            "end_time": round(self.started_at + self.duration_s, 6),
            "duration_s": round(self.duration_s, 6),
            "status": self.status,
            "error_category": self.error_category,
            "attributes": dict(self.attributes),
        }


# ── Exporter 面（vendor-neutral；实现方只依赖 SpanRecord）──────────────────


class SpanExporter:
    """span 导出协议（OTel SpanExporter 的最小同构面）。"""

    def export(self, span: SpanRecord) -> None:  # pragma: no cover - 协议
        raise NotImplementedError


class LoggingSpanExporter:
    """结构化日志行导出（每 span 一行 JSON，走 logging 通道）。"""

    def __init__(self, logger_name: str = "app.observability.spans"):
        self._logger = logging.getLogger(logger_name)

    def export(self, span: SpanRecord) -> None:
        try:
            self._logger.info(json.dumps(span.as_dict(), ensure_ascii=False))
        except Exception:  # noqa: BLE001 — 导出永不阻断业务
            logger.debug("span log export failed", exc_info=True)


class RingSpanExporter:
    """有界环形缓冲（诊断/测试；显式安装，默认不存在）。"""

    def __init__(self, capacity: int = 256):
        self._spans: deque = deque(maxlen=max(1, int(capacity)))
        self._lock = threading.Lock()

    def export(self, span: SpanRecord) -> None:
        with self._lock:
            self._spans.append(span)

    def snapshot(self) -> tuple:
        with self._lock:
            return tuple(self._spans)

    def clear(self) -> None:
        with self._lock:
            self._spans.clear()


_EXPORTERS: list = []
_EXPORTERS_LOCK = threading.Lock()


def install_exporter(exporter: SpanExporter) -> None:
    with _EXPORTERS_LOCK:
        if exporter not in _EXPORTERS:
            _EXPORTERS.append(exporter)


def remove_exporter(exporter: SpanExporter) -> None:
    with _EXPORTERS_LOCK:
        if exporter in _EXPORTERS:
            _EXPORTERS.remove(exporter)


def reset_exporters_for_tests() -> None:
    with _EXPORTERS_LOCK:
        _EXPORTERS.clear()


def _export(span: SpanRecord) -> None:
    with _EXPORTERS_LOCK:
        exporters = tuple(_EXPORTERS)
    for exporter in exporters:
        try:
            exporter.export(span)
        except Exception:  # noqa: BLE001 — 单个导出器故障不拖累其他
            logger.debug("span exporter failed", exc_info=True)


# ── 当前 span（ContextVar；asyncio task/to_thread 免费继承）─────────────────

_CURRENT_SPAN: "contextvars.ContextVar[Optional[_ActiveSpan]]" = contextvars.ContextVar(
    "webgis_current_span", default=None
)


@dataclass
class _ActiveSpan:
    trace_id: str
    span_id: str
    stage: str
    name: str


def current_span_id() -> Optional[str]:
    active = _CURRENT_SPAN.get()
    return active.span_id if active is not None else None


# ── 跨进程传播辅助（Commit 4 的 submit/worker 共用此出口）──────────────────

TRACEPARENT_KEY = "traceparent"


def trace_headers() -> Dict[str, str]:
    """当前 RuntimeContext 的出站传播头（traceparent）。

    无 trace 上下文时返回 {}（调用方照常发送——传播是 best-effort，
    绝不为关联而阻塞派发）。
    """
    try:
        from app.lib.runtime.context import current_runtime_context

        ctx = current_runtime_context()
    except Exception:  # noqa: BLE001
        return {}
    if ctx is None or not ctx.trace_id:
        return {}
    span_id = current_span_id() or ctx.span_id or _new_span_id()
    from app.lib.observability.trace_context import TraceContext

    tp = TraceContext(version="00", trace_id=ctx.trace_id, span_id=span_id,
                      flags="01").traceparent()
    return {TRACEPARENT_KEY: tp}


def context_kwargs_from_headers(headers: Optional[dict]) -> Dict[str, str]:
    """从消息头恢复 trace 关联（worker 侧入口）。

    返回可直接传给 ``bind_runtime_context(**kwargs)`` 的子集；
    非法/缺失 traceparent 返回 {}（调用方保持既有绑定不变）。
    """
    if not headers:
        return {}
    raw = headers.get(TRACEPARENT_KEY)
    if not raw:
        return {}
    try:
        from app.lib.observability.trace_context import parse_traceparent

        parsed = parse_traceparent(raw if isinstance(raw, str) else str(raw))
    except Exception:  # noqa: BLE001
        return {}
    if parsed is None:
        return {}
    return {"trace_id": parsed.trace_id, "span_id": parsed.span_id}


# ── 主入口 ──────────────────────────────────────────────────────────────────


@contextlib.contextmanager
def start_span(
    stage: SpanStage,
    name: str,
    **attributes: Any,
) -> Iterator[SpanRecord]:
    """开启一个子 span（父子关系/trace 继承自动完成）。

    - 异常语义：业务异常分类（classify_exception）后记入 span 并**原样抛出**；
      asyncio.CancelledError 记 cancelled 后照常传播。
    - 返回的 SpanRecord 在 with 体内处于"进行中"（duration_s 为进入时刻的
      占位 0.0），退出时才完成并导出——体内不要持久化该对象。
    - setup 失败（属性/名字序列化爆炸等）绝不阻断 with 体内的业务代码：
      降级为一个零上下文的空 span（review R1-m4）。
    """
    setup = None
    try:
        setup = _span_setup(stage, name, attributes)
    except Exception:  # noqa: BLE001
        logger.debug("start_span setup failed; business runs without span",
                     exc_info=True)
    if setup is None:
        yield SpanRecord(
            name="span", stage="unknown", trace_id="0" * 32,
            span_id="0" * 16, parent_span_id=None,
            started_at=time.time(), duration_s=0.0,
            status=SpanStatus.OK.value,
        )
        return
    active, token, bind_cm, record, started_mono = setup
    exit_status: Optional[SpanStatus] = None
    error_category: Optional[str] = None
    try:
        yield record
    except asyncio.CancelledError:
        exit_status = SpanStatus.CANCELLED
        raise
    except BaseException as exc:
        exit_status = SpanStatus.ERROR
        try:
            from app.core.errors import classify_exception

            error_category = classify_exception(exc).category.value
        except Exception:  # noqa: BLE001
            error_category = None
        raise
    finally:
        duration = time.perf_counter() - started_mono
        if bind_cm is not None:
            with contextlib.suppress(Exception):
                bind_cm.__exit__(None, None, None)
        # review R1-m10：值被替换（跨 task 退出）时跳过 reset——此时原上下文
        # 的 token 已不可安全 reset，重置别人的值比保留陈旧值更危险。
        try:
            if _CURRENT_SPAN.get() is active:
                _CURRENT_SPAN.reset(token)
        except Exception:  # noqa: BLE001 — 跨上下文 reset 的 ValueError
            pass
        finished = SpanRecord(
            name=record.name,
            stage=record.stage,
            trace_id=record.trace_id,
            span_id=record.span_id,
            parent_span_id=record.parent_span_id,
            started_at=record.started_at,
            duration_s=duration,
            status=(exit_status or SpanStatus.OK).value,
            error_category=error_category,
            attributes=record.attributes,
        )
        with contextlib.suppress(Exception):
            _export(finished)


def _span_setup(
    stage: SpanStage,
    name: str,
    attributes: Dict[str, Any],
):
    """yield 之前的全部 setup（任何失败由 start_span 兜底，不阻断业务）。"""
    from app.lib.runtime.context import (
        bind_runtime_context,
        current_runtime_context,
    )

    try:
        ctx = current_runtime_context()
    except Exception:  # noqa: BLE001
        ctx = None
    trace_id = (ctx.trace_id if ctx is not None else None) or secrets.token_hex(16)
    parent = _CURRENT_SPAN.get()
    parent_span_id = parent.span_id if parent is not None else (
        ctx.span_id if ctx is not None else None
    )
    span_id = _new_span_id()

    try:
        safe_name = str(name)[:128]
    except Exception:  # noqa: BLE001（review R1-m4）
        safe_name = "span"
    active = _ActiveSpan(trace_id=trace_id, span_id=span_id,
                         stage=stage.value, name=safe_name)
    token = _CURRENT_SPAN.set(active)
    # span 作用域内日志带 span_id（与中间件的绑定语义一致；异常静默）
    bind_cm = None
    try:
        bind_cm = bind_runtime_context(trace_id=trace_id, span_id=span_id)
        bind_cm.__enter__()
    except Exception:  # noqa: BLE001
        bind_cm = None

    started_wall = time.time()
    started_mono = time.perf_counter()
    record = SpanRecord(
        name=safe_name,
        stage=stage.value,
        trace_id=trace_id,
        span_id=span_id,
        parent_span_id=parent_span_id,
        started_at=started_wall,
        duration_s=0.0,
        status=SpanStatus.OK.value,
        attributes=_bounded_attrs(attributes),
    )
    return active, token, bind_cm, record, started_mono


__all__ = [
    "SpanStage",
    "SpanStatus",
    "SpanRecord",
    "SpanExporter",
    "LoggingSpanExporter",
    "RingSpanExporter",
    "install_exporter",
    "remove_exporter",
    "reset_exporters_for_tests",
    "current_span_id",
    "trace_headers",
    "context_kwargs_from_headers",
    "TRACEPARENT_KEY",
    "start_span",
]
