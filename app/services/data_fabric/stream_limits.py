"""Bounded process-local admission for authenticated NDJSON pump lifetimes.

Budgets are per user, per upstream source, and global. Multi-worker deployments
have independent windows (the same boundary as the existing tile limiter).
"""
from __future__ import annotations

import hashlib
import threading
import time
import uuid
from collections import OrderedDict, deque
from dataclasses import dataclass, field

USER_CONCURRENCY = 2
SOURCE_CONCURRENCY = 4
GLOBAL_CONCURRENCY = 16
USER_RPM = 12
SOURCE_RPM = 60
_MAX_KEYS = 4096
_LOCK = threading.RLock()


class StreamLimitExceeded(RuntimeError):
    def __init__(self, reason: str, retry_after: int = 1) -> None:
        self.reason = reason
        self.retry_after = retry_after
        super().__init__(reason)


@dataclass
class _Window:
    active: int = 0
    starts: deque[float] = field(default_factory=deque)


_WINDOWS: OrderedDict[str, _Window] = OrderedDict()
_ACTIVE: set[str] = set()


def _reserve_key(key: str, *, concurrency: int, rpm: int) -> None:
    now = time.monotonic()
    while key not in _WINDOWS and len(_WINDOWS) >= _MAX_KEYS:
        victim = next((name for name, window in _WINDOWS.items() if not window.active), None)
        if victim is None:
            raise StreamLimitExceeded("admission_registry_full")
        _WINDOWS.pop(victim)
    window = _WINDOWS.setdefault(key, _Window())
    _WINDOWS.move_to_end(key)
    while window.starts and window.starts[0] <= now - 60:
        window.starts.popleft()
    if window.active >= concurrency:
        raise StreamLimitExceeded("concurrency_exceeded")
    if len(window.starts) >= rpm:
        raise StreamLimitExceeded("rate_exceeded", max(1, int(60 - now + window.starts[0]) + 1))
    window.active += 1
    window.starts.append(now)


def _key(scope: str, identity: str) -> str:
    return scope + ":" + hashlib.sha256(identity.encode()).hexdigest()


class StreamLease:
    def __init__(self, user_id: str) -> None:
        self._id = uuid.uuid4().hex
        self._keys: list[str] = []
        if not user_id:
            raise StreamLimitExceeded("missing_user")
        with _LOCK:
            if len(_ACTIVE) >= GLOBAL_CONCURRENCY:
                raise StreamLimitExceeded("global_concurrency_exceeded")
            key = _key("user", user_id)
            _reserve_key(key, concurrency=USER_CONCURRENCY, rpm=USER_RPM)
            self._keys.append(key)
            _ACTIVE.add(self._id)

    def bind_source(self, source_id: str) -> None:
        with _LOCK:
            if self._id not in _ACTIVE or len(self._keys) != 1 or not source_id:
                raise StreamLimitExceeded("invalid_source_reservation")
            key = _key("source", source_id)
            _reserve_key(key, concurrency=SOURCE_CONCURRENCY, rpm=SOURCE_RPM)
            self._keys.append(key)

    def close(self) -> None:
        with _LOCK:
            if self._id not in _ACTIVE:
                return
            _ACTIVE.remove(self._id)
            for key in self._keys:
                _WINDOWS[key].active = max(0, _WINDOWS[key].active - 1)


def reset_for_tests() -> None:
    with _LOCK:
        _WINDOWS.clear()
        _ACTIVE.clear()
