"""Cross-process reference updates share the same per-content lock as GC."""
from __future__ import annotations

import hashlib
import os
import threading
from contextlib import contextmanager
from collections.abc import Iterator

_STRIPES = [threading.Lock() for _ in range(64)]


@contextmanager
def blob_ref_lock(store: object, sha256: str) -> Iterator[None]:
    import fcntl
    from pathlib import Path

    digest = hashlib.sha256(sha256.encode()).hexdigest()
    directory = Path(getattr(store, "root")) / ".visual-ref-locks"
    directory.mkdir(parents=True, exist_ok=True)
    with _STRIPES[int(digest[:2], 16) % len(_STRIPES)]:
        # Fixed stripes avoid retaining one lock-file inode for every expired image.
        fd = os.open(directory / digest[:2], os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)
