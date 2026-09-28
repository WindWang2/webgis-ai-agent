"""Process-wide MVT tile cache for Data Fabric catalog items (#1545).

Owns the catalog MVT tile cache so both consumers import it from the service
layer (correct dependency direction): the route module
(``app.api.routes.data_fabric``) memoizes built tiles via get/put, and
``app.services.data_fabric.manager`` invalidates a catalog item's tiles on
sync/fingerprint change via ``invalidate_item``. Previously the cache lived
inside the route module and the manager reached into that private symbol.

缓存键契约（调用方负责）：``(item_id, tenant_scope, fingerprint, z, x, y)`` ——
tenant scope 与 dataset fingerprint 必须入键，防止跨租户共享条目与旧版本瓦片。

W12 字节记账契约：值统一为 :class:`TileCacheEntry`（NamedTuple，兼容裸二元
tuple）。条目内存按 **gzip 字节长度** 计 —— 此前 ``len(value)`` 数的是 tuple
的元素个数（恒为 2），256MB 字节预算从未真正生效，进程可无界驻留瓦片字节。
超单条上限的瓦片不入缓存（诚实降级：仍然服务该响应，只是不复用）。

线程模型：``DfTileCache`` 是 threading.Lock 保护的同步结构（df-manager-loop
协程与 manager 线程共用同一实例）；协程内的 single-flight 收敛见
``TileBuildCoalescer``（必须在 df-manager-loop 上使用，asyncio 语义）。
"""
import asyncio
import threading
from collections import OrderedDict
from typing import Any, Awaitable, Callable, Dict, Generic, Hashable, NamedTuple, Optional, TypeVar

_T = TypeVar("_T")


class TileCacheEntry(NamedTuple):
    """缓存条目：gzip 瓦片字节 + 构建时的 dataset fingerprint。

    NamedTuple 保留既有裸 tuple ``put(key, (gz, fp))`` / ``get(...)==(gz, fp)``
    的兼容性（test_deep_review_api_data_tiles / test_data_fabric_postgis_v2
    依赖该语义）；字节记账只看 ``gz``。
    """

    gz: bytes
    fingerprint: str


def _entry_bytes(value: Any) -> int:
    """条目驻留字节的唯一真源：gzip 载荷长度（防御历史裸 tuple / bytes 值）。"""
    if isinstance(value, TileCacheEntry):
        return len(value.gz)
    if isinstance(value, (bytes, bytearray)):
        return len(value)
    if isinstance(value, tuple) and value and isinstance(value[0], (bytes, bytearray)):
        return len(value[0])
    return len(value)


class DfTileCache:
    """条目 + 字节双上限 LRU（R3-m9/R4：单瓦片可达 MB 级，仅条目上限会
    累积到 GB 级驻留内存）。"""

    def __init__(
        self,
        max_entries: int = 2048,
        max_bytes: int = 256 * 1024 * 1024,
        max_entry_bytes: int = 4 * 1024 * 1024,
    ):
        self._cache: "OrderedDict[tuple, Any]" = OrderedDict()
        self._lock = threading.Lock()
        self._max = max_entries
        self._max_bytes = max_bytes
        self._max_entry_bytes = max_entry_bytes
        self._bytes = 0
        # 观测计数（hit/miss/驱逐/超限拒绝）——tile 命中率与缓存驻留的
        # 唯一进程内事实源；stats() 返回快照，绝不返回内部可变结构。
        self._hits = 0
        self._misses = 0
        self._puts = 0
        self._evictions = 0
        self._oversize_rejects = 0

    def get(self, key):
        with self._lock:
            v = self._cache.get(key)
            if v is not None:
                self._cache.move_to_end(key)
                self._hits += 1
            else:
                self._misses += 1
            return v

    def put(self, key, value) -> bool:
        """插入条目。返回是否真正入库（超单条上限 → False，调用方可据此
        标注诚实降级，无需再 get 探测污染 hit/miss 计数）。"""
        size = _entry_bytes(value)
        if size > self._max_entry_bytes:
            with self._lock:
                self._oversize_rejects += 1
            return False
        with self._lock:
            old = self._cache.pop(key, None)
            if old is not None:
                self._bytes -= _entry_bytes(old)
            self._cache[key] = value
            self._bytes += size
            self._puts += 1
            while (len(self._cache) > self._max) or (
                self._bytes > self._max_bytes and len(self._cache) > 1
            ):
                _, evicted = self._cache.popitem(last=False)
                self._bytes -= _entry_bytes(evicted)
                self._evictions += 1
        return True

    def invalidate_item(self, item_id: str) -> None:
        with self._lock:
            for k in [k for k in self._cache if k[0] == item_id]:
                self._bytes -= _entry_bytes(self._cache.pop(k))

    def stats(self) -> Dict[str, Any]:
        """计数快照（O(1)，锁内一致读）。"""
        with self._lock:
            return {
                "entries": len(self._cache),
                "bytes": self._bytes,
                "max_entries": self._max,
                "max_bytes": self._max_bytes,
                "max_entry_bytes": self._max_entry_bytes,
                "hits": self._hits,
                "misses": self._misses,
                "puts": self._puts,
                "evictions": self._evictions,
                "oversize_rejects": self._oversize_rejects,
            }


class TileBuildCoalescer(Generic[_T]):
    """df-manager-loop 上的瓦片构建 single-flight。

    并发同键请求合并为一次构建：首个请求执行 ``builder()``，其余 await 同一
    future（ADR-0047 同族纪律：昂贵构建绝不惊群）。异常按请求逐份传播——
    失败不缓存、不污染后续请求。

    诚实降级（与会话路径 SingleFlightManager 同语义）：在飞键数达上限或等
    待超时后，后到请求 **自己执行构建**，绝不永久悬挂，也绝不静默吞结果。

    仅限单事件循环内使用（future 属于创建它的 loop；df-manager-loop 常驻，
    满足该前提）。
    """

    def __init__(self, max_inflight: int = 512, wait_timeout_s: float = 30.0):
        self._inflight: "Dict[Hashable, asyncio.Future]" = {}
        self._max_inflight = max_inflight
        self._wait_timeout_s = wait_timeout_s

    async def run(self, key: Hashable, builder: Callable[[], Awaitable[_T]]) -> _T:
        leader = key not in self._inflight
        if leader and len(self._inflight) >= self._max_inflight:
            # 有界降级：不打进 in-flight 表，直接自己构建（dict 操作在同一
            # loop 上无竞态）。
            return await builder()
        if leader:
            fut: "asyncio.Future" = asyncio.get_running_loop().create_future()
            self._inflight[key] = fut
            try:
                result = await builder()
                fut.set_result(result)
                return result
            except BaseException as exc:
                fut.set_exception(exc)
                raise
            finally:
                self._inflight.pop(key, None)
        follower_fut = self._inflight[key]
        try:
            return await asyncio.wait_for(
                asyncio.shield(follower_fut), timeout=self._wait_timeout_s
            )
        except (asyncio.TimeoutError, TimeoutError):
            # 领导者构建超过等待上限：后到者自行构建（诚实降级，不悬挂）。
            return await builder()

    def inflight_keys(self) -> int:
        return len(self._inflight)


TILE_CACHE = DfTileCache()

__all__ = ["DfTileCache", "TileCacheEntry", "TileBuildCoalescer", "TILE_CACHE"]
