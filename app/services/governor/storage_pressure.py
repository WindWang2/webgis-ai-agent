"""Storage / Artifact 压力只读视图（R15，ADR-0182 D10）。

artifact/ref/cache/export 的**存储子系统已存在**（artifact_cache LRU
5GiB、chunk cache 1GiB、S3 multipart、org_quota 存储 100GiB，recon §1）
—— governor 绝不重建存储面或第二份 GC。本模块只做一件事：把压力水位
投影为 admission 的一个输入（``pressure() ∈ [0,1]`` + level 词表）。

数据源 duck-typed 注入（owner 私有 API 不进 import 面）：
``provider() -> (used_bytes, cap_bytes) | None``。provider 不可用 →
unavailable（诚实未知：admission 按 0 压力处理但快照里留痕）。
"""
from __future__ import annotations

import logging
import threading
from typing import Callable, Optional, Tuple

logger = logging.getLogger(__name__)

LEVEL_LOW = "low"
LEVEL_ELEVATED = "elevated"
LEVEL_HIGH = "high"
LEVEL_UNKNOWN = "unknown"


def default_artifact_provider() -> Optional[Tuple[int, int]]:
    """默认数据源：artifact_cache 的 advisory 总量（import 防御式）。

    任何失败 → None（unavailable）。
    """
    try:
        from app.lib import artifact_cache as ac
        total = int(ac._advisory_total(  # noqa: SLF001 — 单点只读投影
            ac.ARTIFACT_DIR, ac._artifact_path))
        cap = int(ac.MAX_ARTIFACT_BYTES)
        if cap <= 0:
            return None
        return (max(0, total), cap)
    except Exception:  # noqa: BLE001 — owner 缺席/结构变化 → 诚实未知
        return None


class StoragePressureView:
    """存储压力只读投影（带 TTL 缓存 ——绝不阻塞执行路径）。"""

    def __init__(self,
                 provider: Optional[Callable[[], Optional[Tuple[int, int]]]] = None,
                 *, high_watermark: float = 0.9,
                 cache_ttl_s: float = 5.0):
        self._provider = provider or default_artifact_provider
        self._high = high_watermark
        self._ttl = cache_ttl_s
        self._cached: Optional[Tuple[float, float]] = None  # (pressure, ts)
        self._lock = threading.Lock()

    def pressure(self) -> float:
        """∈ [0,1]；unknown → 0（但 :meth:`level` 返回 unknown 留痕）。"""
        p, _ = self._reading()
        return p

    def level(self) -> str:
        p, known = self._reading()
        if not known:
            return LEVEL_UNKNOWN
        if p >= self._high:
            return LEVEL_HIGH
        if p >= self._high - 0.15:
            return LEVEL_ELEVATED
        return LEVEL_LOW

    def _reading(self) -> Tuple[float, bool]:
        import time as _time
        with self._lock:
            if self._cached is not None:
                p, ts = self._cached
                if _time.monotonic() - ts <= self._ttl:
                    return (p, True)
            try:
                reading = self._provider()
            except Exception:  # noqa: BLE001
                reading = None
            if reading is None:
                self._cached = (0.0, _time.monotonic())
                return (0.0, False)
            used, cap = reading
            p = max(0.0, min(1.0, used / cap)) if cap > 0 else 0.0
            self._cached = (p, _time.monotonic())
            return (p, True)

    def snapshot(self) -> dict:
        p, known = self._reading()
        return {"pressure": p, "level": self.level(), "known": known}


__all__ = [
    "LEVEL_LOW",
    "LEVEL_ELEVATED",
    "LEVEL_HIGH",
    "LEVEL_UNKNOWN",
    "StoragePressureView",
    "default_artifact_provider",
]
