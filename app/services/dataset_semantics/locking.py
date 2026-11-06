"""Bounded thread stripes plus stable cross-process dataset write locks."""
from collections.abc import Iterator
from contextlib import contextmanager
import os
from pathlib import Path
import threading

from app.lib.runtime.file_lock import exclusive_file_lock

_STRIPES = tuple(threading.RLock() for _ in range(64))


@contextmanager
def dataset_write_lock(path: Path) -> Iterator[None]:
    """Serialize complete read/modify/publish/prune transactions on a sidecar."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with _STRIPES[hash(str(path.resolve())) % len(_STRIPES)]:
        fd = os.open(path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            with exclusive_file_lock(fd):
                yield
        finally:
            os.close(fd)
