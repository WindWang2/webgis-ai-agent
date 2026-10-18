"""Deep-review carto-platform CP-09：CAS 去重命中刷新 mtime（GC grace 窗口）。"""

from __future__ import annotations

import os
import time

from app.services import durable_blob_store as dbs


def _store(tmp_path):
    return dbs.FilesystemBlobStore(tmp_path / "blobs")


def test_cp09_put_blob_dedup_hit_refreshes_mtime(tmp_path):
    store = _store(tmp_path)
    first = store.put_blob("k1", b"payload", "json")
    path = store.root / first.location
    old = time.time() - 30 * 86400
    os.utime(path, (old, old))
    again = store.put_blob("k1", b"payload", "json")
    assert again.put_new is False
    assert path.stat().st_mtime > time.time() - 60


def test_cp09_put_blob_from_path_dedup_hit_refreshes_mtime(tmp_path):
    store = _store(tmp_path)
    src = tmp_path / "src.bin"
    src.write_bytes(b"abc" * 100)
    first = store.put_blob_from_path("k2", src)
    path = store.root / first.location
    old = time.time() - 30 * 86400
    os.utime(path, (old, old))
    again = store.put_blob_from_path("k2", src)
    assert again.put_new is False
    assert path.stat().st_mtime > time.time() - 60
