"""#2229 回归：raster_authoring 源/修订缓存的跨线程读写必须加锁。

旧实现：解析在 asyncio.to_thread 工作线程执行、ref 失效钩子来自其它线程，
无锁的 cache.clear()/pop 与另一线程的迭代并发 → RuntimeError
（dictionary changed size during iteration），瓦片请求 500。
"""
from __future__ import annotations

import threading

import app.services.raster_authoring.source as src


def test_cache_concurrent_put_get_invalidate_no_crash():
    errors: list = []

    def worker(n: int) -> None:
        try:
            sid = f"thr-{n}"
            for i in range(400):
                ref = f"ref:r-{i % 8}"
                key = (sid, ref, "/tmp/x", i, 0)
                src._cache_put(src._REVISION_CACHE, key, f"fp-{i}")
                src._cache_get(src._REVISION_CACHE, key)
                if i % 5 == 0:
                    src.invalidate_source_cache(sid, ref)
        except Exception as exc:  # noqa: BLE001 — 任何并发损坏都视为失败
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
