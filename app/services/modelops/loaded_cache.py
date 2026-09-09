"""LoadedModelCache —— 进程内 loaded 模型缓存（ADR-0119 §3.6；R1-M4）。

契约（挑战修订后冻结）：

- key = sha256(descriptor.checksum + provider_ref + device + runtime fp)；
- **per-key single-flight**：并发首载只发生一次 ``provider.load``
  （其余等待者复用同一结果/失败）；
- **refcount**：acquire++ / release--；TTL/LRU 驱逐**仅限 refcount==0**；
  last-use 在 **release** 时更新；
- **负缓存分流**：permanent 失败（checksum/结构/描述符/load-unsupported）
  按 TTL 缓存；transient 失败（OOM/VRAM 预算/资源不可用）**不缓存**
  （驱逐其他模型后可成功——与降批/驱逐策略一致）；
- unload 失败 → 条目仍移除并标 poisoned（不重复投毒日志）；
- 无跨进程假设（单进程语义；多副本部署各自持有）。
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, Optional, Tuple

from app.lib.data.fingerprints import canonical_dumps, sha256_hex
from app.lib.modelops.descriptor import GeoModelDescriptor
from app.lib.modelops.errors import ProviderError, ProviderOOM, ResourceUnavailable
from app.services.modelops.providers.base import LoadedModel

logger = logging.getLogger(__name__)

#: 负缓存 TTL（秒）——仅 permanent 失败。
NEGATIVE_CACHE_TTL_S = 30.0
#: idle TTL（秒）——超时驱逐（refcount==0）。
DEFAULT_IDLE_TTL_S = 600.0

_PERMANENT_ERRORS = (ProviderError,)          # 含 ProviderLoadFailed / checksum 家族
_TRANSIENT_ERRORS = (ProviderOOM, ResourceUnavailable)


def load_key(descriptor: GeoModelDescriptor, *, provider_ref: str, device: str,
             runtime_fingerprint: str = "") -> str:
    return sha256_hex(
        canonical_dumps(
            {
                "checksum": descriptor.checksum,
                "model_id": descriptor.model_id,
                "model_version": descriptor.model_version,
                "provider_ref": provider_ref,
                "device": device,
                "runtime": runtime_fingerprint,
            }
        )
    )


@dataclass
class _Entry:
    model: LoadedModel
    refcount: int = 0
    last_used: float = field(default_factory=time.monotonic)
    poisoned: bool = False
    #: 驱逐时执行的安全卸载（cache 不持有 provider 引用——避免第二真相）。
    unload_cb: Optional[Callable[[LoadedModel], None]] = None


@dataclass
class _Negative:
    error: ProviderError
    expires_at: float


class LoadedModelCache:
    """有界 loaded-model 缓存（LRU by last-use + idle TTL + max entries）。"""

    def __init__(
        self,
        *,
        max_models: int = 4,
        idle_ttl_s: float = DEFAULT_IDLE_TTL_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._max_models = max(1, max_models)
        self._idle_ttl_s = idle_ttl_s
        self._clock = clock
        self._lock = threading.RLock()
        self._entries: Dict[str, _Entry] = {}
        self._negative: Dict[str, _Negative] = {}
        self._key_locks: Dict[str, threading.Lock] = {}
        self._stats = {"hits": 0, "misses": 0, "evictions": 0, "negative_hits": 0}

    # ── public ──────────────────────────────────────────────────────
    def acquire(
        self,
        key: str,
        descriptor: GeoModelDescriptor,
        *,
        provider_ref: str,
        device: str,
        load_fn: Callable[[], LoadedModel],
        unload_fn: Optional[Callable[[LoadedModel], None]] = None,
    ) -> Tuple[LoadedModel, float]:
        """取得（或加载）句柄；返回 (model, load_latency_s)。

        single-flight：同 key 并发首载时，只有第一个调用者执行 ``load_fn``。
        """
        with self._lock:
            entry = self._entries.get(key)
            if entry is not None and not entry.poisoned:
                entry.refcount += 1
                self._stats["hits"] += 1
                return entry.model, 0.0
            neg = self._negative.get(key)
            if neg is not None:
                if self._clock() < neg.expires_at:
                    self._stats["negative_hits"] += 1
                    raise neg.error
                del self._negative[key]
            self._stats["misses"] += 1
            key_lock = self._key_locks.setdefault(key, threading.Lock())
        # 锁外 load（不持全局锁做慢操作），per-key 串行。
        started = time.perf_counter()
        with key_lock:
            with self._lock:
                entry = self._entries.get(key)
                if entry is not None and not entry.poisoned:
                    entry.refcount += 1
                    self._stats["hits"] += 1
                    return entry.model, 0.0
            try:
                model = load_fn()
            except _TRANSIENT_ERRORS:
                # transient：不进负缓存（驱逐/降批后可重试）。
                with self._lock:
                    self._key_locks.pop(key, None)  # m-5：失败键锁不驻留
                raise
            except _PERMANENT_ERRORS as exc:
                with self._lock:
                    self._negative[key] = _Negative(
                        error=exc, expires_at=self._clock() + NEGATIVE_CACHE_TTL_S
                    )
                    self._key_locks.pop(key, None)  # m-5：失败键锁不驻留
                raise
            latency = time.perf_counter() - started
            with self._lock:
                # R2 m-6：load 期间若发生 invalidate（poisoned 回插）或负缓存
                # 写入，则本次 load 结果不回插（与失效语义同一临界区裁决）。
                if key in self._negative or (
                    key in self._entries and self._entries[key].poisoned
                ):
                    self._key_locks.pop(key, None)
                    # 等待者按 miss 处理（统计口径：真正触发 load 的进程计数）
                    return model, latency
                self._entries[key] = _Entry(model=model, refcount=1, unload_cb=unload_fn)
                self._key_locks.pop(key, None)
                self._evict_locked(keep=key)
            return model, latency

    def release(self, key: str) -> None:
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return
            entry.refcount = max(0, entry.refcount - 1)
            entry.last_used = self._clock()  # last-use 在 release 触点更新
            self._evict_locked()

    def invalidate(self, key: str) -> None:
        """显式失效（版本/provider 变化；poisoned 清理）。"""
        with self._lock:
            entry = self._entries.pop(key, None)
            self._negative.pop(key, None)
        if entry is not None and entry.refcount > 0:
            # 仍有 in-flight 使用者：先回插为 poisoned（release 后由 GC 移除）。
            with self._lock:
                entry.poisoned = True
                self._entries[key] = entry

    def stats(self) -> Dict[str, int]:
        with self._lock:
            return dict(self._stats)

    def size(self) -> int:
        with self._lock:
            return len(self._entries)

    # ── internal ────────────────────────────────────────────────────
    def _evict_locked(self, *, keep: Optional[str] = None) -> None:
        now = self._clock()
        for key in [k for k, e in self._entries.items() if e.poisoned and e.refcount == 0]:
            del self._entries[key]
        # idle TTL 驱逐（refcount==0 only）。
        for key, entry in list(self._entries.items()):
            if key == keep or entry.refcount > 0:
                continue
            if now - entry.last_used > self._idle_ttl_s:
                self._drop_locked(key)
                self._stats["evictions"] += 1
        # LRU 容量驱逐（refcount==0 only，last-use 最旧优先）。
        while len(self._entries) > self._max_models:
            candidates = [
                (e.last_used, k)
                for k, e in self._entries.items()
                if k != keep and e.refcount == 0
            ]
            if not candidates:
                break  # 全部 in-flight：有界让位于正确性（refcount 保护）
            candidates.sort()
            self._drop_locked(candidates[0][1])
            self._stats["evictions"] += 1

    def _drop_locked(self, key: str) -> None:
        entry = self._entries.pop(key, None)
        if entry is None:
            return
        if callable(entry.unload_cb):
            try:
                entry.unload_cb(entry.model)
            except Exception as exc:  # noqa: BLE001 — unload 失败：条目已移除，
                logger.warning(       # poisoned 语义由上层 invalidate 负责
                    "model unload failed (entry dropped): %s", exc
                )
