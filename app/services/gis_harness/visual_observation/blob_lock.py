"""Cross-process reference updates share the same per-content lock as GC."""
from __future__ import annotations

import hashlib
import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager

_STRIPES = [threading.Lock() for _ in range(64)]


@contextmanager
def blob_ref_lock(store: object, sha256: str) -> Iterator[None]:
    """按内容哈希的互斥（同进程分条锁 + 文件系统后端叠加 flock）。

    非文件系统后端（无 ``root``）没有可挂锁文件的目录 —— 退化为进程内
    分条锁（跨进程互斥缺席，与 sweep 对非 FS 后端直接跳过的口径一致），
    不得因属性缺席抛错（可插拔承诺）。
    """
    import fcntl
    from pathlib import Path

    digest = hashlib.sha256(sha256.encode()).hexdigest()
    root = getattr(store, "root", None)
    with _STRIPES[int(digest[:2], 16) % len(_STRIPES)]:
        if root is None:
            yield
            return
        directory = Path(root) / ".visual-ref-locks"
        directory.mkdir(parents=True, exist_ok=True)
        # Fixed stripes avoid retaining one lock-file inode for every expired image.
        fd = os.open(directory / digest[:2], os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)
