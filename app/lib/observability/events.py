"""Structured Observability（ADR-0104 Wave 6）——vendor-neutral 事件面。

设计契约：
- **事件词表收敛**：Harness / Data / Compute / Cartography 四个面的业务事件
  收进 ``EVENT_CATALOG``（category → event → 允许字段）。词表外的 event
  直接抛错（漂移即红）；词表内未声明的字段一律丢弃（allowlist 制，denylist
  挡不住近形键）。
- **vendor-neutral**：Sink 协议 + 有界 RingSink（诊断/测试）+ LoggingSink
  （stdlib logging，OTel-shaped 平面字段）。不绑定任何 SaaS；接 OTel SDK
  时新增一个 Sink 即可，事件源不改。
- **关联自动注入**：req/sess/turn/run/proj 取自 RuntimeContext（ContextVar），
  事件源不用手工传。
- **有界标签**：digest 按 (category, event, status) 聚合，三元组空间由
  词表 × 有界 status 词表笛卡尔限定，永不出现用户数据高基数标签。

与既有面的关系（无第二事实源）：
- geocompute 的 ``emit()``（ADR-0096）保持不动——它是 compute 面的执行
  trace，本模块的 compute 事件词表与其语义对齐；
- 日志关联主干（RuntimeCorrelationFilter）扩展 ``proj`` 字段（additive）。
"""
from __future__ import annotations

import hashlib
import json
import math
import logging
import threading
import time
from collections import deque
from typing import Any, Dict, List, Optional, Tuple

#: status 词表（有界；digest 标签空间的第三维）
STATUS_VOCABULARY = ("started", "completed", "failed", "cancelled", "rejected", None)

#: 事件词表：category → event → 允许的附加字段（有界元数据，绝无载荷）
EVENT_CATALOG: Dict[str, Dict[str, Tuple[str, ...]]] = {
    "harness": {
        "workflow_compiled": ("recipe_id", "task", "stage_count"),
        "replan": ("reason", "attempt"),
        "tool_retrieval": ("candidate_count",),
        "dispatch": ("tool", "policy"),
        "no_progress": ("step",),
        "completion": ("verdict",),
    },
    "data": {
        "ingest": ("role", "bytes"),
        "profile": ("rows", "fields"),
        "query": ("role",),
        "cache_hit": ("layer",),
        "cache_miss": ("layer",),
        "promotion": ("artifact_type", "bytes"),
    },
    "compute": {
        "queue": ("depth",),
        "dispatch": ("node", "backend"),
        "retry": ("node", "attempts"),
        "timeout": ("node",),
        "cancellation": ("node", "phase"),
        "resource_rejection": ("kind", "limit"),
    },
    "cartography": {
        "mapspec_compile": ("model", "components"),
        "render": ("layer_count",),
        "observe": ("verdict",),
        "export": ("format", "bytes"),
    },
}

logger = logging.getLogger("webgis.observability")


class Sink:
    """事件汇协议（vendor-neutral）。"""

    def write(self, record: Dict[str, Any]) -> None:  # pragma: no cover - 协议
        raise NotImplementedError


class RingSink(Sink):
    """进程内有界环形缓冲（诊断/测试观测用；绝不无限增长）。"""

    def __init__(self, capacity: int = 1024):
        self._capacity = capacity
        self._ring: deque = deque(maxlen=capacity)
        self._lock = threading.Lock()

    def write(self, record: Dict[str, Any]) -> None:
        with self._lock:
            self._ring.append(record)

    def snapshot(self, limit: int = 100) -> List[Dict[str, Any]]:
        with self._lock:
            items = list(self._ring)
        n = max(0, min(limit, self._capacity))
        return items[len(items) - n:] if n > 0 else []

    def clear(self) -> None:
        with self._lock:
            self._ring.clear()


class LoggingSink(Sink):
    """stdlib logging 汇：OTel-shaped 平面字段的 JSON 行。

    注意（R1 review MINOR-7，如实披露）：本模块当前是**词表+sink 基建**，
    app/ 内尚无 emit_event 调用点（数据面接线随各主线演进）；logger 挂接
    共享 handler 保证事件在接线后真实落盘而非被 lastResort 丢弃。
    """

    def __init__(self, logger_name: str = "webgis.observability"):
        # get_logger 复用共享 handler + 关联过滤器（R1 review MINOR-7：
        # 裸 getLogger 无 handler 时 INFO 会被 lastResort 丢弃）
        from app.core.logging_config import get_logger

        self._logger = get_logger(logger_name)

    def write(self, record: Dict[str, Any]) -> None:
        self._logger.info(json.dumps(record, ensure_ascii=False, default=str))


_sinks: List[Sink] = []
_sinks_lock = threading.Lock()


def register_sink(sink: Sink) -> None:
    with _sinks_lock:
        _sinks.append(sink)


def reset_sinks() -> None:
    with _sinks_lock:
        _sinks.clear()


def _default_sinks() -> List[Sink]:
    return [LoggingSink()]


def _ensure_default_sink() -> None:
    with _sinks_lock:
        if not _sinks:
            _sinks.extend(_default_sinks())


#: 兜底敏感键扫描（allowlist 之外的第二道）。正则**派生自**
#: ``app/services/jobs/redaction.py`` 的 SENSITIVE_KEY_PARTS（R1 review
#: MINOR-4：自绘词表比基线弱，近形键 access_key/private_key 等会漏）。
_SENSITIVE_KEY_RE = None


def _sensitive_re():
    global _SENSITIVE_KEY_RE
    if _SENSITIVE_KEY_RE is None:
        import re

        try:
            from app.services.jobs.redaction import SENSITIVE_KEY_PARTS
            parts = sorted(SENSITIVE_KEY_PARTS)
        except Exception:  # noqa: BLE001 —— 循环导入兜底（保持历史子集）
            parts = ["secret", "token", "password", "api_key", "apikey",
                     "authorization", "cookie", "credential"]
        _SENSITIVE_KEY_RE = re.compile(
            "(?i)(" + "|".join(re.escape(p) for p in parts) + ")")
    return _SENSITIVE_KEY_RE


def emit_event(
    category: str,
    event: str,
    *,
    status: Optional[str] = None,
    duration_s: Optional[float] = None,
    error_code: Optional[str] = None,
    **fields: Any,
) -> None:
    """发射一条业务事件（词表外 event 抛错；词表外字段丢弃）。"""
    catalog = EVENT_CATALOG.get(category)
    if catalog is None:
        raise ValueError(f"unknown event category: {category!r}")
    if event not in catalog:
        raise ValueError(f"unknown event {event!r} in category {category!r}")
    if status not in STATUS_VOCABULARY:
        raise ValueError(f"status {status!r} outside bounded vocabulary")

    from app.lib.runtime.context import current_runtime_context

    ctx = current_runtime_context()
    record: Dict[str, Any] = {
        "ts": time.time(),
        "category": category,
        "event": event,
        "req": getattr(ctx, "request_id", None),
        "sess": getattr(ctx, "session_id", None),
        "turn": getattr(ctx, "turn_id", None),
        "run": getattr(ctx, "run_id", None),
        "proj": getattr(ctx, "project_id", None),
        # Quality V3 W10（additive envelope 键，词表外）：W3C trace 关联
        "trace": getattr(ctx, "trace_id", None),
        "status": status,
        "duration_s": round(duration_s, 6) if duration_s is not None else None,
        "error_code": error_code,
    }
    sensitive = _sensitive_re()
    allowed = catalog[event]
    for key, value in fields.items():
        if key not in allowed or value is None:
            continue  # allowlist 制：未声明/None 一律丢弃
        if sensitive.search(key):
            continue  # 第二道防线：词表误编辑也不会泄漏
        # R1 review MINOR-5：值侧有界化 —— 自由文本字段（reason/verdict/tool…）
        # 若放行任意字符串，str(exc) 之类的载荷会绕过键防线。
        if isinstance(value, str):
            record[key] = value[:256] + ("…" if len(value) > 256 else "")
        elif isinstance(value, bool) or isinstance(value, (int, float)):
            # R2 review MINOR-6：非有限 float（NaN/Inf）会让 LoggingSink 吐出
            # 非 strict-JSON 的字面量 —— 归一为字符串。
            if isinstance(value, float) and not math.isfinite(value):
                record[key] = str(value)
            else:
                record[key] = value
        else:
            record[key] = str(value)[:256]

    _ensure_default_sink()
    with _sinks_lock:
        sinks = list(_sinks)
    for sink in sinks:
        try:
            sink.write(record)
        except Exception:  # noqa: BLE001 —— 观测面故障不得传染业务路径
            logger.warning("observability sink %r write failed", sink,
                           exc_info=True)


def event_digest(limit: int = 500) -> Dict[str, Any]:
    """有界 digest：按 (category, event, status) 聚合计数 + 时长分位。

    标签空间 = EVENT_CATALOG 笛卡尔 × STATUS_VOCABULARY，天然有界。
    """
    counts: Dict[Tuple[str, str, Optional[str]], int] = {}
    durations: Dict[Tuple[str, str], List[float]] = {}
    with _sinks_lock:
        sinks = list(_sinks)
    records: List[Dict[str, Any]] = []
    for sink in sinks:
        snapshot = getattr(sink, "snapshot", None)
        if callable(snapshot):
            records.extend(snapshot(limit))
    for rec in records:
        key = (rec.get("category"), rec.get("event"), rec.get("status"))
        counts[key] = counts.get(key, 0) + 1
        if rec.get("duration_s") is not None:
            dkey = (rec.get("category"), rec.get("event"))
            durations.setdefault(dkey, []).append(float(rec["duration_s"]))

    def _quantile(values: List[float], q: float) -> float:
        ordered = sorted(values)
        if not ordered:
            return 0.0
        idx = min(len(ordered) - 1, int(q * (len(ordered) - 1)))
        return round(ordered[idx], 6)

    return {
        "counts": {
            f"{c}|{e}|{s}": n for (c, e, s), n in sorted(
                counts.items(), key=lambda kv: (str(kv[0][0]), str(kv[0][1]), str(kv[0][2])))
        },
        "duration_p50_s": {
            f"{c}|{e}": _quantile(v, 0.5) for (c, e), v in sorted(
                durations.items(), key=lambda kv: (str(kv[0][0]), str(kv[0][1])))
        },
        "duration_p95_s": {
            f"{c}|{e}": _quantile(v, 0.95) for (c, e), v in sorted(
                durations.items(), key=lambda kv: (str(kv[0][0]), str(kv[0][1])))
        },
        "catalog_events": sum(len(ev) for ev in EVENT_CATALOG.values()),
        "fingerprint": hashlib.sha256(
            json.dumps(sorted(EVENT_CATALOG.keys()), sort_keys=True).encode()
        ).hexdigest()[:16],
    }
