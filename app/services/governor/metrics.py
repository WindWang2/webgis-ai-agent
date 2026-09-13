"""Governor 观测面（R16；ADR-0182 D11）。

prometheus-client 原语进默认 REGISTRY（与 ``app/lib/observability/metrics.py``
同惯例，随既有 instrumentator /metrics 暴露）。**封闭标签词表**纪律：

- ``decision``：五值准入词表（contract.AdmissionDecision）；
- ``subsystem``：五个背压通道（raster/browser/export/external/llm）；
- ``retry_class``：contract.RetryClass 封闭词表；
- **没有任何 session-id / tool-name / tenant 标签**（高基数 → 结构化日志
  ``[resource-governor]`` 前缀行）。

观测面绝不抛、绝不阻断业务路径（与 BudgetRegistry.observe 同纪律）。
"""
from __future__ import annotations

import logging

from prometheus_client import Counter, Gauge, Histogram

logger = logging.getLogger(__name__)

_ADMISSION_TOTAL = Counter(
    "governor_admission_total",
    "Admission decisions by outcome.",
    ["decision", "mode"],
)

_QUEUE_WAIT_SECONDS = Histogram(
    "governor_queue_wait_seconds",
    "Time a deferred demand spent waiting before execution.",
    ["subsystem"],
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60),
)

_EXECUTION_SECONDS = Histogram(
    "governor_execution_seconds",
    "Observed execution duration of a reserved demand.",
    ["subsystem"],
    buckets=(0.05, 0.25, 1, 2.5, 5, 10, 30, 60, 120, 300),
)

_ESTIMATE_ERROR_RATIO = Histogram(
    "governor_estimate_error_ratio",
    "|actual/expected - 1| for memory and wall-time estimates (bounded view).",
    ["dimension"],
    buckets=(0.1, 0.25, 0.5, 1.0, 2.0, 4.0, 8.0, 16.0),
)

_RETRY_TOTAL = Counter(
    "governor_retry_total",
    "Retry attempts charged against the retry budget.",
    ["retry_class", "outcome"],
)

_FALLBACK_TOTAL = Counter(
    "governor_fallback_total",
    "Fallback/degrade executions (R7 ladder taken).",
    ["subsystem", "semantics"],
)

_CANCELLATION_LATENCY = Histogram(
    "governor_cancellation_latency_seconds",
    "Time from cancel signal to reservation release.",
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10),
)

_INFLIGHT_RESERVATIONS = Gauge(
    "governor_inflight_reservations",
    "Live reservations by subsystem channel.",
    ["subsystem"],
)

_HEAVY_INFLIGHT = Gauge(
    "governor_heavy_inflight",
    "Live heavy-class reservations (global heavy concurrency view).",
)

_SESSION_ACTIVE = Gauge(
    "governor_sessions_active",
    "Sessions with live reservations or queue entries (bounded by process).",
)

_GOVERNOR_ERRORS = Counter(
    "governor_internal_errors_total",
    "Governor internal failures (fail-open counter; admission unaffected).",
    ["operation"],
)

_EXHAUSTION_AVOIDED = Counter(
    "governor_resource_exhaustion_avoided_total",
    "Rejects/limits that prevented projected over-commit (hard-limit hits).",
    ["dimension"],
)


def observe_admission(decision: str, mode: str) -> None:
    try:
        _ADMISSION_TOTAL.labels(decision=decision, mode=mode).inc()
    except Exception:  # noqa: BLE001
        logger.debug("governor admission metric failed", exc_info=True)


def observe_queue_wait(subsystem: str, seconds: float) -> None:
    try:
        _QUEUE_WAIT_SECONDS.labels(subsystem=_safe_label(subsystem)).observe(max(0.0, seconds))
    except Exception:  # noqa: BLE001
        logger.debug("governor queue-wait metric failed", exc_info=True)


def observe_execution(subsystem: str, seconds: float) -> None:
    try:
        _EXECUTION_SECONDS.labels(subsystem=_safe_label(subsystem)).observe(max(0.0, seconds))
    except Exception:  # noqa: BLE001
        logger.debug("governor execution metric failed", exc_info=True)


def observe_estimate_error(dimension: str, expected: float, actual: float) -> None:
    """|actual/expected - 1|；expected ≤ 0 或任一侧缺失时不产出序列。"""
    try:
        if expected <= 0 or actual is None:
            return
        _ESTIMATE_ERROR_RATIO.labels(dimension=dimension).observe(
            abs(float(actual) / float(expected) - 1.0)
        )
    except Exception:  # noqa: BLE001
        logger.debug("governor estimate-error metric failed", exc_info=True)


def observe_retry(retry_class: str, outcome: str) -> None:
    try:
        _RETRY_TOTAL.labels(retry_class=_safe_label(retry_class), outcome=outcome).inc()
    except Exception:  # noqa: BLE001
        logger.debug("governor retry metric failed", exc_info=True)


def observe_fallback(subsystem: str, semantics: str) -> None:
    try:
        _FALLBACK_TOTAL.labels(subsystem=_safe_label(subsystem), semantics=semantics).inc()
    except Exception:  # noqa: BLE001
        logger.debug("governor fallback metric failed", exc_info=True)


def observe_cancellation_latency(seconds: float) -> None:
    try:
        _CANCELLATION_LATENCY.observe(max(0.0, seconds))
    except Exception:  # noqa: BLE001
        logger.debug("governor cancel-latency metric failed", exc_info=True)


def set_inflight(subsystem: str, count: int) -> None:
    try:
        _INFLIGHT_RESERVATIONS.labels(subsystem=_safe_label(subsystem)).set(max(0, count))
    except Exception:  # noqa: BLE001
        logger.debug("governor inflight gauge failed", exc_info=True)


def set_heavy_inflight(count: int) -> None:
    try:
        _HEAVY_INFLIGHT.set(max(0, count))
    except Exception:  # noqa: BLE001
        logger.debug("governor heavy gauge failed", exc_info=True)


def set_sessions_active(count: int) -> None:
    try:
        _SESSION_ACTIVE.set(max(0, count))
    except Exception:  # noqa: BLE001
        logger.debug("governor sessions gauge failed", exc_info=True)


def inc_internal_error(operation: str) -> None:
    try:
        _GOVERNOR_ERRORS.labels(operation=_safe_label(operation)).inc()
    except Exception:  # noqa: BLE001
        logger.debug("governor error metric failed", exc_info=True)


def inc_exhaustion_avoided(dimension: str) -> None:
    try:
        _EXHAUSTION_AVOIDED.labels(dimension=_safe_label(dimension)).inc()
    except Exception:  # noqa: BLE001
        logger.debug("governor exhaustion metric failed", exc_info=True)


def _safe_label(value: str) -> str:
    """标签兜底：未知词表值折算为 ``other``（封闭词表的最后防线）。"""
    return value if value else "other"


__all__ = [
    "observe_admission",
    "observe_queue_wait",
    "observe_execution",
    "observe_estimate_error",
    "observe_retry",
    "observe_fallback",
    "observe_cancellation_latency",
    "set_inflight",
    "set_heavy_inflight",
    "set_sessions_active",
    "inc_internal_error",
    "inc_exhaustion_avoided",
]
