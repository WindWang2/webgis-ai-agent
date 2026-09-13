"""Harness Kernel observability (K8, ADR-0180).

Process-local bounded counters in the ``tool_metrics`` spirit (ADR-0044):
in-memory aggregation + a structured log line per record so existing log
pipelines capture the stream without a new sink. No second metrics framework:
JSONL export rides the standard logger, and the counters are exposed via
``snapshot()`` for tests/digests.

Recorded dimensions (closed vocabulary, host-tagged):
- turn_started / turn_ended{status} / resume_detected / duplicate_turn_prevented
- plan_created / plan_replaced / plan_superseded / plan_patched{kind}
- step_succeeded / step_failed (optionally step=...)
- checkpoint_written / stale_write_refused / host_parity{host}
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()
_COUNTERS: Dict[str, int] = {}
_STARTED = time.time()

#: Hard cap on distinct counter keys (dims are closed vocabularies; a key
#: explosion would mean a caller is stuffing unbounded values into dims).
_MAX_KEYS = 512


def record(event: str, *, host: str = "", **dims: Any) -> None:
    """Count one kernel event; never raises, never blocks the plan path.

    ``host`` is a first-class dimension (host-parity KPI) and joins the other
    dims in the counter key: ``event|host=pi|kind=...``.
    """
    try:
        if host:
            dims = {**dims, "host": host}
        parts = [event]
        for key in sorted(dims):
            value = dims[key]
            if value in (None, ""):
                continue
            parts.append(f"{key}={value}")
        name = "|".join(parts)
        with _LOCK:
            if name not in _COUNTERS and len(_COUNTERS) >= _MAX_KEYS:
                return  # bounded: drop unknown-key explosions, keep hot counters
            _COUNTERS[name] = _COUNTERS.get(name, 0) + 1
        logger.info("HK_METRICS %s", name)
    except Exception:  # noqa: BLE001 — 观测绝不阻断计划路径
        pass


def snapshot() -> Dict[str, Any]:
    """Counters + uptime for tests/digest endpoints (read-only copy)."""
    with _LOCK:
        return {
            "counters": dict(sorted(_COUNTERS.items())),
            "uptime_s": round(time.time() - _STARTED, 3),
        }


def reset_for_tests() -> None:
    global _STARTED
    with _LOCK:
        _COUNTERS.clear()
    _STARTED = time.time()


def get_counter(name: str) -> int:
    with _LOCK:
        return _COUNTERS.get(name, 0)
