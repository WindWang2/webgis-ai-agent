"""Per-source circuit breaker for remote Data Fabric sources (Section 36).

When a source is repeatedly failing, every user request would otherwise wait the
full timeout. The breaker short-circuits: after ``failure_threshold`` consecutive
failures it opens for ``cool_down`` seconds, during which calls fail fast
(``RESULT``-free) without touching the network; after cool-down it enters
half-open and lets one trial request through.

The clock is injectable so tests are deterministic (no real sleeps). State is
bounded: one entry per source id, evicted LRU beyond ``max_entries``.
"""
import logging
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Optional

from app.services.data_fabric.errors import SourceUnreachableError
from app.services.data_fabric.reliability import is_transient

logger = logging.getLogger(__name__)


class CircuitState(str, Enum):
    CLOSED = "closed"        # normal operation
    OPEN = "open"            # tripped: fail fast
    HALF_OPEN = "half_open"  # cool-down elapsed: one trial allowed


@dataclass
class _BreakerEntry:
    state: CircuitState = CircuitState.CLOSED
    consecutive_failures: int = 0
    opened_at: float = 0.0
    # Guards the single trial request in half-open.
    half_open_trial_inflight: bool = False


class CircuitBreaker:
    """Per-source breaker. Thread-compatible (callers serialize per source)."""

    def __init__(
        self,
        failure_threshold: int = 5,
        cool_down: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.failure_threshold = max(1, failure_threshold)
        self.cool_down = max(0.0, cool_down)
        self._clock = clock

    def _state(self, entry: _BreakerEntry) -> CircuitState:
        if entry.state == CircuitState.OPEN:
            if self._clock() - entry.opened_at >= self.cool_down:
                entry.state = CircuitState.HALF_OPEN
                entry.half_open_trial_inflight = False
        return entry.state

    def allow(self, entry: _BreakerEntry) -> bool:
        """Return True if a request may proceed; False → fail fast (open)."""
        state = self._state(entry)
        if state == CircuitState.OPEN:
            return False
        if state == CircuitState.HALF_OPEN:
            if entry.half_open_trial_inflight:
                return False  # only one trial at a time
            entry.half_open_trial_inflight = True
            return True
        return True  # CLOSED

    def record_success(self, entry: _BreakerEntry) -> None:
        entry.consecutive_failures = 0
        entry.state = CircuitState.CLOSED
        entry.half_open_trial_inflight = False

    def record_failure(self, entry: _BreakerEntry, exc: BaseException) -> None:
        # Permanent errors (bad request, security) do not trip the breaker —
        # they are not evidence the source is down.
        if not is_transient(exc):
            return
        entry.consecutive_failures += 1
        entry.half_open_trial_inflight = False
        if entry.state == CircuitState.HALF_OPEN:
            # Trial failed → reopen.
            entry.state = CircuitState.OPEN
            entry.opened_at = self._clock()
        elif entry.consecutive_failures >= self.failure_threshold:
            entry.state = CircuitState.OPEN
            entry.opened_at = self._clock()
            logger.warning(
                "circuit breaker opened after %d consecutive failures",
                entry.consecutive_failures,
            )


class CircuitBreakerRegistry:
    """Bounded registry of per-source breakers (LRU evict beyond max_entries).

    M1（ADR-0094 §10 / 审计）：registry 方法加 ``threading.Lock`` —— sync_catalog
    以 ≤16 线程并发 describe 同一 source（同 breaker entry），query 走
    ``asyncio.to_thread``；无锁的 ``consecutive_failures += 1`` 会丢失更新，
    OrderedDict move_to_end/逐出同样需要互斥。
    """

    def __init__(
        self,
        breaker: Optional[CircuitBreaker] = None,
        max_entries: int = 4096,
    ):
        self._breaker = breaker or CircuitBreaker()
        self._entries: "OrderedDict[str, _BreakerEntry]" = OrderedDict()
        self._max_entries = max_entries
        self._lock = threading.Lock()

    def _entry(self, source_key: str) -> _BreakerEntry:
        """调用方必须已持有 ``self._lock``。"""
        entry = self._entries.get(source_key)
        if entry is None:
            entry = _BreakerEntry()
            self._entries[source_key] = entry
            while len(self._entries) > self._max_entries:
                self._entries.popitem(last=False)
        else:
            self._entries.move_to_end(source_key)
        return entry

    def state(self, source_key: str) -> CircuitState:
        with self._lock:
            return self._breaker._state(self._entry(source_key))

    def allow(self, source_key: str) -> bool:
        with self._lock:
            return self._breaker.allow(self._entry(source_key))

    def release_trial(self, source_key: str) -> None:
        """M1：无条件释放 half-open trial 名额。

        ``allow()`` 在 HALF_OPEN 下占用唯一 trial 名额；调用方若此后未走到
        record_success/record_failure（如健康检查缓存命中提前返回），名额
        永久泄漏 → 熔断卡死 fail-fast。本方法用于该路径的确定性释放。
        """
        with self._lock:
            entry = self._entries.get(source_key)
            if entry is not None:
                entry.half_open_trial_inflight = False

    def record_success(self, source_key: str) -> None:
        with self._lock:
            self._breaker.record_success(self._entry(source_key))

    def record_failure(self, source_key: str, exc: BaseException) -> None:
        with self._lock:
            self._breaker.record_failure(self._entry(source_key), exc)

    def call(
        self,
        source_key: str,
        fn: Callable[..., object],
        *args,
        **kwargs,
    ) -> object:
        """Run ``fn`` under the breaker for ``source_key``.

        Fail-fast (open) raises ``SourceUnreachableError``. Otherwise the result
        (or exception) is recorded and propagated.
        """
        if not self.allow(source_key):
            raise SourceUnreachableError(
                f"source '{source_key}' circuit breaker is open (failing fast)",
                details={"circuit_state": self.state(source_key)},
            )
        try:
            result = fn(*args, **kwargs)
        except Exception as exc:
            self.record_failure(source_key, exc)
            raise
        self.record_success(source_key)
        return result


# Per-process default registry. Tests construct their own to inject fake clocks.
_breaker_registry: Optional[CircuitBreakerRegistry] = None


def get_breaker_registry() -> CircuitBreakerRegistry:
    global _breaker_registry
    if _breaker_registry is None:
        _breaker_registry = CircuitBreakerRegistry()
    return _breaker_registry


def set_breaker_registry(registry: CircuitBreakerRegistry) -> None:
    """Override the process registry (test seam)."""
    global _breaker_registry
    _breaker_registry = registry


__all__ = [
    "CircuitState",
    "CircuitBreaker",
    "CircuitBreakerRegistry",
    "get_breaker_registry",
    "set_breaker_registry",
]
