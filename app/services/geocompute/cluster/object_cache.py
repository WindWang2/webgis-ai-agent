"""GeoCompute V7 worker 本地载荷缓存（wave 8，01-architecture.md §2.3）。

worker 进程内的有界 LRU（entries + bytes 双界），缓存「按 ref 解析回的
上游载荷」—— fanout 多消费者 / 同进程重试（redelivery）不再重复经会话
存储取同一份字节。缓存身份 = (session_id, ref_id, owner_scope)：

- **跨 owner 不泄漏**：owner_scope 参与键 —— 即使 ref 冲突也绝不屈从
  （会话存储本身按 session 隔离，本缓存再加 owner 域，双保险）；
- **失败方向安全**：任何不确定（键冲突/超界/损坏）→ miss → 走会话存储
  重取（会话存储是真相，缓存只是加速）；命中不改变载荷语义（浅拷贝出缓存
  —— 防操作数改写腐蚀缓存条目，与 NodeResultStore 同纪律）；
- **有界**：entries ≤32 / bytes ≤256MB（env 可调小）；超出 LRU 逐出；
- **注册表接线**：put/hit 同步位置声明到 ``geocompute_worker_cache``
  （fail-open）—— placement 局部性打分的输入。
"""
from __future__ import annotations

import logging
import os
import threading
from collections import OrderedDict
from typing import Any, Optional

logger = logging.getLogger(__name__)

_DEFAULT_MAX_ENTRIES = 32
_DEFAULT_MAX_BYTES = 256 * 1024 * 1024


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


class PayloadCache:
    """(session, ref, owner) → 载荷的有界 LRU（worker 进程内）。"""

    def __init__(
        self,
        *,
        max_entries: Optional[int] = None,
        max_bytes: Optional[int] = None,
        registry: Optional[Any] = None,
        worker_id: Optional[str] = None,
    ):
        self._max_entries = max(1, int(
            max_entries or _env_int("WEBGIS_WORKER_CACHE_MAX_ENTRIES", _DEFAULT_MAX_ENTRIES)
        ))
        self._max_bytes = max(1, int(
            max_bytes or _env_int("WEBGIS_WORKER_CACHE_MAX_BYTES", _DEFAULT_MAX_BYTES)
        ))
        #: WorkerCacheRegistry（可选 —— 注册失败不影响缓存本身）
        self._registry = registry
        self._worker_id = worker_id
        self._entries: OrderedDict[tuple[str, str, str], dict[str, Any]] = OrderedDict()
        self._sizes: dict[tuple[str, str, str], int] = {}
        self._bytes = 0
        self._lock = threading.Lock()

    @staticmethod
    def _measure(payload: dict[str, Any]) -> int:
        """采样近似字节量（与 executor.NodeResultStore 同口径）。"""
        total = 0
        for key in ("features", "rows"):
            items = payload.get(key) or []
            if not items:
                continue
            sample = items[:64]
            avg = sum(len(str(f)) for f in sample) / len(sample)
            total += int(avg * len(items))
        return total

    def get(
        self, session_id: str, ref_id: str, owner_scope: str, *,
        locality_key: Optional[str] = None,
    ) -> Optional[dict[str, Any]]:
        key = (str(session_id), str(ref_id), str(owner_scope))
        with self._lock:
            payload = self._entries.get(key)
            if payload is None:
                return None
            self._entries.move_to_end(key)
        if self._registry is not None and locality_key:
            try:
                self._registry.record_hit(self._worker_id or "", owner_scope, locality_key)
            except Exception:  # noqa: BLE001 - 注册失败不影响命中
                pass
        # 浅拷贝出缓存：操作数约定不改写输入（拷贝兜底防缓存腐蚀）
        return {k: v for k, v in payload.items() if not k.startswith("__")}

    def put(
        self, session_id: str, ref_id: str, owner_scope: str,
        payload: dict[str, Any], *,
        locality_key: Optional[str] = None,
    ) -> None:
        key = (str(session_id), str(ref_id), str(owner_scope))
        size = min(self._measure(payload), self._max_bytes + 1)
        with self._lock:
            old = self._entries.pop(key, None)
            if old is not None:
                self._bytes -= self._sizes.pop(key, 0)
            if size > self._max_bytes:
                return  # 超预算的大载荷不入缓存（仍可直取）
            self._entries[key] = payload
            self._sizes[key] = size
            self._bytes += size
            while (
                len(self._entries) > self._max_entries
                or self._bytes > self._max_bytes
            ):
                oldest_key = next(iter(self._entries))
                self._entries.pop(oldest_key, None)
                self._bytes -= self._sizes.pop(oldest_key, 0)
                if not self._entries:
                    break
        # 注册表位置声明（成功缓存之后；fail-open）
        if self._registry is not None and locality_key and self._worker_id:
            try:
                self._registry.record_put(
                    self._worker_id, owner_scope, locality_key,
                    size_bytes=size,
                )
            except Exception:  # noqa: BLE001
                pass

    def stats(self) -> dict[str, int]:
        with self._lock:
            return {"entries": len(self._entries), "bytes": int(self._bytes)}


#: worker 进程级单例（懒构建；coordinator/REST 进程不使用）。
_default_cache: Optional[PayloadCache] = None
_cache_lock = threading.Lock()


def get_worker_cache(worker_id: Optional[str] = None) -> PayloadCache:
    global _default_cache
    if _default_cache is None:
        with _cache_lock:
            if _default_cache is None:
                registry = None
                try:
                    from app.services.geocompute.cluster.locality import (
                        WorkerCacheRegistry,
                    )

                    registry = WorkerCacheRegistry()
                except Exception:  # noqa: BLE001 - 注册表缺席 = 纯进程内缓存
                    registry = None
                _default_cache = PayloadCache(
                    registry=registry, worker_id=worker_id
                )
    return _default_cache
