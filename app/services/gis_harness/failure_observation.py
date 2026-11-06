"""Bounded counters and debug tracebacks for optional harness degradation paths."""
from collections import OrderedDict
import logging
import threading

_counts: OrderedDict[str, int] = OrderedDict()
_lock = threading.Lock()


def observe_optional_failure(module: str) -> None:
    with _lock:
        count = _counts.pop(module, 0) + 1
        _counts[module] = count
        if len(_counts) > 256:
            _counts.popitem(last=False)
    logging.getLogger(module).debug('Optional harness path failed (count=%d)', count, exc_info=True)


def optional_failure_counts() -> dict[str, int]:
    with _lock:
        return dict(_counts)
