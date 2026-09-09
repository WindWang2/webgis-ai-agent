"""PerfCounters —— 结构化推理性能计数（禁"感觉更快"式声明）。

所有计数器有界/可导出；引擎在 manifest 里持久化导出值。计数失败不阻断
推理（观测是补强），但导出形状必须稳定（评估平台按字段名读取）。
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class PerfCounters:
    """一次推理运行的结构化计数。线程安全（引擎在工作线程更新）。"""

    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    # 输入/窗口
    raster_windows_total: int = 0
    bytes_read: int = 0
    # 推理
    chips_total: int = 0
    chips_done: int = 0
    pixels_done: int = 0
    batch_sizes: List[int] = field(default_factory=list)
    oom_downshifts: int = 0
    # 延迟（秒）
    model_load_latency_s: float = 0.0
    warm_inference_latency_s: float = 0.0   # 首批之后的稳定批延迟（中位）
    cancel_latency_s: float = 0.0
    queue_wait_s: float = 0.0
    # merge/cache/provider
    merge_work_px: int = 0
    cache_hits: int = 0
    cache_misses: int = 0
    provider_rtt_s: float = 0.0
    # 资源观测
    peak_host_memory_bytes: int = 0
    estimated_vram_bytes: int = 0
    observed_vram_bytes: int = 0
    device: str = "cpu"
    batch_size_final: int = 0
    started_at: float = field(default_factory=time.time)
    finished_at: Optional[float] = None

    def record_batch(self, size: int, *, first: bool = False) -> None:
        with self._lock:
            self.batch_sizes.append(size)
            self.chips_done += size
            self.batch_size_final = size

    def note_oom_downshift(self) -> None:
        with self._lock:
            self.oom_downshifts += 1

    def note_window(self, count: int = 1, bytes_read: int = 0) -> None:
        with self._lock:
            self.raster_windows_total += count
            self.bytes_read += bytes_read

    def note_merge(self, pixels: int) -> None:
        with self._lock:
            self.merge_work_px += pixels

    def note_cache(self, hit: bool) -> None:
        with self._lock:
            if hit:
                self.cache_hits += 1
            else:
                self.cache_misses += 1

    def note_latency(
        self,
        *,
        load: Optional[float] = None,
        warm: Optional[float] = None,
        cancel: Optional[float] = None,
        queue_wait: Optional[float] = None,
        provider_rtt: Optional[float] = None,
    ) -> None:
        with self._lock:
            if load is not None:
                self.model_load_latency_s = load
            if warm is not None:
                # 滚动中位由导出时计算；这里保存最近值供无统计环境的退路。
                self.warm_inference_latency_s = warm
            if cancel is not None:
                self.cancel_latency_s = cancel
            if queue_wait is not None:
                self.queue_wait_s = queue_wait
            if provider_rtt is not None:
                self.provider_rtt_s = provider_rtt

    def note_resources(
        self,
        *,
        peak_host_memory_bytes: Optional[int] = None,
        estimated_vram_bytes: Optional[int] = None,
        observed_vram_bytes: Optional[int] = None,
        device: Optional[str] = None,
    ) -> None:
        with self._lock:
            if peak_host_memory_bytes is not None:
                self.peak_host_memory_bytes = max(self.peak_host_memory_bytes, peak_host_memory_bytes)
            if estimated_vram_bytes is not None:
                self.estimated_vram_bytes = estimated_vram_bytes
            if observed_vram_bytes is not None:
                self.observed_vram_bytes = observed_vram_bytes
            if device is not None:
                self.device = device

    def finish(self) -> None:
        with self._lock:
            self.finished_at = time.time()

    # ── 导出 ────────────────────────────────────────────────────────
    def export(self) -> Dict[str, Any]:
        with self._lock:
            sizes = self.batch_sizes
            median_batch = float(sizes[len(sizes) // 2]) if sizes else 0.0
            total_s = max((self.finished_at or time.time()) - self.started_at, 1e-9)
            return {
                "raster_windows_total": self.raster_windows_total,
                "bytes_read": self.bytes_read,
                "chips_total": self.chips_total,
                "chips_done": self.chips_done,
                "pixels_done": self.pixels_done,
                "pixels_per_s": round(self.pixels_done / total_s, 1),
                "tiles_per_s": round(self.chips_done / total_s, 3),
                "batch_sizes": list(self.batch_sizes),
                "batch_size_final": self.batch_size_final,
                "batch_size_median": median_batch,
                "oom_downshifts": self.oom_downshifts,
                "model_load_latency_s": round(self.model_load_latency_s, 6),
                "warm_inference_latency_s": round(self.warm_inference_latency_s, 6),
                "cancel_latency_s": round(self.cancel_latency_s, 6),
                "queue_wait_s": round(self.queue_wait_s, 6),
                "provider_rtt_s": round(self.provider_rtt_s, 6),
                "merge_work_px": self.merge_work_px,
                "cache_hits": self.cache_hits,
                "cache_misses": self.cache_misses,
                "peak_host_memory_bytes": self.peak_host_memory_bytes,
                "estimated_vram_bytes": self.estimated_vram_bytes,
                "observed_vram_bytes": self.observed_vram_bytes,
                "device": self.device,
                "wall_s": round(total_s, 6),
            }
