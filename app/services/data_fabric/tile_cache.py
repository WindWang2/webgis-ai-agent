"""Process-wide MVT tile cache for Data Fabric catalog items (#1545).

Owns the catalog MVT tile cache so both consumers import it from the service
layer (correct dependency direction): the route module
(``app.api.routes.data_fabric``) memoizes built tiles via get/put, and
``app.services.data_fabric.manager`` invalidates a catalog item's tiles on
sync/fingerprint change via ``invalidate_item``. Previously the cache lived
inside the route module and the manager reached into that private symbol.

缓存键契约（调用方负责）：``(item_id, tenant_scope, fingerprint, z, x, y)`` ——
tenant scope 与 dataset fingerprint 必须入键，防止跨租户共享条目与旧版本瓦片。
"""
import threading
from collections import OrderedDict
from typing import Optional, Tuple


class DfTileCache:
    """条目 + 字节双上限 LRU（R3-m9/R4：单瓦片可达 MB 级，仅条目上限会
    累积到 GB 级驻留内存）。"""

    def __init__(
        self, max_entries: int = 2048, max_bytes: int = 256 * 1024 * 1024
    ) -> None:
        self._cache: "OrderedDict[tuple, bytes]" = OrderedDict()
        self._lock = threading.Lock()
        self._max = max_entries
        self._max_bytes = max_bytes
        self._bytes = 0

    def get(self, key: Tuple[str, str, str, int, int, int]) -> Optional[bytes]:
        with self._lock:
            v = self._cache.get(key)
            if v is not None:
                self._cache.move_to_end(key)
            return v

    def put(
        self,
        key: Tuple[str, str, str, int, int, int],
        value: bytes,
    ) -> None:
        with self._lock:
            old = self._cache.pop(key, None)
            if old is not None:
                self._bytes -= len(old)
            self._cache[key] = value
            self._bytes += len(value)
            while (len(self._cache) > self._max) or (
                self._bytes > self._max_bytes and len(self._cache) > 1
            ):
                _, evicted = self._cache.popitem(last=False)
                self._bytes -= len(evicted)

    def invalidate_item(self, item_id: str) -> None:
        with self._lock:
            for k in [k for k in self._cache if k[0] == item_id]:
                self._bytes -= len(self._cache.pop(k))


TILE_CACHE = DfTileCache()

__all__ = ["DfTileCache", "TILE_CACHE"]
