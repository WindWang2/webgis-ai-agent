"""扩展平台计量（Platform V4，ADR-0131 D5）。

Extensions V3 已有完整失败隔离（quarantine/crash 上限/吊销传播），本模块
补齐可观测一侧：激活结果、隔离、worker 崩溃、provider 调用时长进
Prometheus（随既有 instrumentator /metrics 暴露）。

**封闭标签词表**纪律（同 sre_metrics/auth_metrics）：

- ``extension_activation_total{result}``：result ∈
  activated|failed|quarantined|disabled（host 状态机已闭合这四个出口）；
- ``extension_quarantine_total{reason}``：reason ∈ worker_crash|revocation；
- **没有 extension_id 标签**——per-extension 诊断走 host.status_report()
  （结构化、按需），杜绝注册表扩张带来的高基数标签风险。

所有接线点 host 侧 try/except 包裹：计量失败绝不影响扩展执行。
"""
from __future__ import annotations

import logging
import time

from prometheus_client import Counter, Histogram

logger = logging.getLogger(__name__)

#: 激活结果词表（封闭；host 状态机的四个出口）
ACTIVATION_RESULTS = ("activated", "failed", "quarantined", "disabled")

#: 隔离原因词表（封闭）
QUARANTINE_REASONS = ("worker_crash", "revocation")

_ACTIVATION_TOTAL = Counter(
    "extension_activation_total",
    "Extension activation outcomes.",
    ["result"],
)

_QUARANTINE_TOTAL = Counter(
    "extension_quarantine_total",
    "Extensions moved to QUARANTINED, by reason.",
    ["reason"],
)

_WORKER_CRASH_TOTAL = Counter(
    "extension_worker_crash_total",
    "Extension worker process crashes observed by the host.",
)

_INVOCATION_DURATION = Histogram(
    "extension_invocation_duration_seconds",
    "Model provider invocation duration (host-level; no extension_id "
    "label — per-extension diagnostics live in host.status_report()).",
    buckets=(0.005, 0.025, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30),
)


def record_activation(result: str) -> None:
    """记录一次激活出口（词表外显式拒绝——不静默扩张标签空间）。"""
    if result not in ACTIVATION_RESULTS:
        raise ValueError(f"unknown activation result: {result!r}")
    _ACTIVATION_TOTAL.labels(result=result).inc()


def record_quarantine(reason: str) -> None:
    if reason not in QUARANTINE_REASONS:
        raise ValueError(f"unknown quarantine reason: {reason!r}")
    _QUARANTINE_TOTAL.labels(reason=reason).inc()


def record_worker_crash() -> None:
    _WORKER_CRASH_TOTAL.inc()


class _NullObserver:
    """metrics 缺席时的空转观察者（host 无 prometheus 环境可安全调用）。"""

    def observe(self, seconds: float) -> None:  # pragma: no cover
        pass


class InvocationTimer:
    """provider 调用计时（host 侧 with 语义；计量失败绝不阻断调用）。"""

    def __enter__(self) -> "InvocationTimer":
        self._start = time.perf_counter()
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        try:
            _INVOCATION_DURATION.observe(max(0.0, time.perf_counter() - self._start))
        except Exception:  # noqa: BLE001
            logger.debug("extension invocation metric failed", exc_info=True)
        return False


__all__ = [
    "ACTIVATION_RESULTS",
    "QUARANTINE_REASONS",
    "record_activation",
    "record_quarantine",
    "record_worker_crash",
    "InvocationTimer",
]
