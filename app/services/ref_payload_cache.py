"""进程内已解析 ref payload 缓存（P-1 / #874）。

`ref:` 提货券的解引用是 Agent 链式分析的最高频路径（buffer(ref:A) →
intersect(ref:B) → dissolve(ref:C)），此前每次都执行完整 Redis GET
（11MB 级）+ json.loads（50k 要素 ≈ 171ms/次）。本缓存以 (session_id,
ref_id) 为键保存**已解析对象**，TTL + 条目/字节双上限 LRU，命中零
Redis 流量、零解析成本。

失效协议（与 spatial_index_cache / tile_lru_cache 同点位挂钩）：
- ``overwrite`` / ``delete_ref`` / ``store`` 内 LRU 淘汰 / ``clear_session``
  时就地失效；
- 跨副本写入由 TTL 兜底（与 map_state L1 同策略，取 5s）。

只读约定：缓存命中返回**同一对象**（不拷贝）——调用方（registry 解引用、
数据面序列化）不得就地修改 payload；需要可变副本的路径继续走 ``get()``
（deepcopy 语义不变，#701-2 契约保持）。
"""
from __future__ import annotations

import threading
import time
from collections import OrderedDict
from typing import Any, Optional, Tuple

# TTL 故意取短：每个 worker 进程私有缓存，长 TTL 会把其他副本的 overwrite
# 服务成过期对象太久。5s 足以折叠一个 turn 内的链式解引用突发。
PAYLOAD_TTL_SECONDS = 5.0
MAX_ENTRIES = 256
MAX_BYTES = 128 * 1024 * 1024  # 128MB，约为 spatial_index_cache 预算的一半


class RefPayloadCache:
    """Thread-safe TTL + count + byte bounded LRU of parsed ref payloads."""

    def __init__(
        self,
        ttl: float = PAYLOAD_TTL_SECONDS,
        max_entries: int = MAX_ENTRIES,
        max_bytes: int = MAX_BYTES,
    ) -> None:
        self._ttl = ttl
        self._max_entries = max_entries
        self._max_bytes = max_bytes
        # key -> (obj, expire_monotonic, approx_bytes)
        self._entries: "OrderedDict[Tuple[str, str], Tuple[Any, float, int]]" = OrderedDict()
        self._total_bytes = 0
        # M7（ADR-0094 §10，对齐 85f60aa 的 MVT epoch 防复活语义）：
        # (session, ref) → 单调 epoch。invalidate 递增；构建方在读取源数据前
        # 捕获 epoch，完成后仅当 epoch 未变才入缓存 —— 关闭
        # "invalidate-during-build → 旧 payload 复活 5s" 的窗口。
        self._epochs: "OrderedDict[Tuple[str, str], int]" = OrderedDict()
        self._lock = threading.Lock()

    @property
    def total_bytes(self) -> int:
        with self._lock:
            return self._total_bytes

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)

    def get(self, session_id: str, ref_id: str) -> Optional[Any]:
        """Return the shared parsed object, or None on miss/expiry."""
        key = (session_id, ref_id)
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            obj, expire_at, size = entry
            if time.monotonic() > expire_at:
                del self._entries[key]
                self._total_bytes -= size
                return None
            self._entries.move_to_end(key)
            return obj

    def current_epoch(self, session_id: str, ref_id: str) -> int:
        """当前 (session, ref) 的失效 epoch（构建方在读取源数据前捕获）。"""
        key = (session_id, ref_id)
        with self._lock:
            return self._epochs.get(key, 0)

    def put_if_current(self, session_id: str, ref_id: str, obj: Any, approx_bytes: int, epoch: int) -> bool:
        """仅当 epoch 未被 invalidate 递增时入缓存（防复活语义）。

        返回是否真正写入。与 ``put`` 共享容量/LRU 逻辑。
        """
        key = (session_id, ref_id)
        with self._lock:
            if self._epochs.get(key, 0) != epoch:
                return False
        self.put(session_id, ref_id, obj, approx_bytes)
        return True

    def put(self, session_id: str, ref_id: str, obj: Any, approx_bytes: int) -> None:
        """Store a parsed payload; evicts LRU entries beyond count/byte caps.

        ``approx_bytes`` 由调用方给出（Redis 路径用原始 JSON 字符串长度），
        只用于字节预算控制，不要求精确。
        """
        key = (session_id, ref_id)
        size = max(0, int(approx_bytes))
        with self._lock:
            old = self._entries.get(key)
            if old is not None:
                self._total_bytes -= old[2]
                del self._entries[key]
            self._entries[key] = (obj, time.monotonic() + self._ttl, size)
            self._total_bytes += size
            while self._entries and (
                len(self._entries) > self._max_entries
                or self._total_bytes > self._max_bytes
            ):
                _, evicted = self._entries.popitem(last=False)
                self._total_bytes -= evicted[2]

    def invalidate(self, session_id: str, ref_id: str) -> None:
        """移除条目并递增 epoch（正在构建的旧 payload 完成后不得复活）。"""
        key = (session_id, ref_id)
        with self._lock:
            self._epochs[key] = self._epochs.get(key, 0) + 1
            self._epochs.move_to_end(key)
            while len(self._epochs) > 4 * self._max_entries:
                self._epochs.popitem(last=False)
        key = (session_id, ref_id)
        with self._lock:
            entry = self._entries.pop(key, None)
            if entry is not None:
                self._total_bytes -= entry[2]

    def invalidate_session(self, session_id: str) -> None:
        with self._lock:
            for key in [k for k in self._entries if k[0] == session_id]:
                entry = self._entries.pop(key, None)
                if entry is not None:
                    self._total_bytes -= entry[2]


#: 进程级单例（与 spatial_index_cache 同生命周期）。
ref_payload_cache = RefPayloadCache()
