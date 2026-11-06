"""Task-local creation evidence, independent of session-wide ref enumeration."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
import threading
from collections.abc import Iterator
from app.lib.runtime.ref_cursors import CONTEXT_OFFLOAD_REF_PREFIX


@dataclass
class CreatedRefs:
    _refs: set[str] = field(default_factory=set)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def note(self, ref: str) -> None:
        if ref.startswith(CONTEXT_OFFLOAD_REF_PREFIX):
            return
        if not ref.startswith("ref:") or ref.startswith(("ref:unavailable", "ref:sessionplan-", "ref:plan-", "ref:checkpoint-", "ref:artifacts-")):
            return
        with self._lock:
            if len(self._refs) < 128:
                self._refs.add(ref)

    def snapshot(self) -> list[str]:
        with self._lock:
            return sorted(self._refs)


_current: ContextVar[CreatedRefs | None] = ContextVar("created_refs", default=None)


def note_created_ref(ref: str) -> None:
    current = _current.get()
    if current is not None:
        current.note(ref)


@contextmanager
def capture_created_refs() -> Iterator[CreatedRefs]:
    current = CreatedRefs()
    token = _current.set(current)
    try:
        yield current
    finally:
        _current.reset(token)
