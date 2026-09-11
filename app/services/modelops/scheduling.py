"""GPU scheduling contract —— ModelOps × GeoCompute 资源对接（V3 §E）。

边界（ADR-0124 决策 8 的 V3 延伸）：ModelOps 自持 inference 语义维度的
资源账（设备/VRAM/warm pool），**复用** GeoCompute 的硬件探测与语义
（nvidia-smi probe / gpu 能力词表），不改写 GeoCompute scheduler。

组件：

- :func:`probe_gpu_devices`：GPU 设备清单（数量/显存），GeoCompute
  probe 优先，缺席 = 空（诚实——无 GPU 是常态不是故障）；
- :class:`VramLedger`：进程内 per-device VRAM 预订账本（线程安全；
  OOM 反馈收敛——超卖被记账下调，防连环 OOM）；
- :func:`model_affinity_index`：多 GPU 模型亲和（确定性 hash → 设备号，
  同模型常驻同卡提升 warm 命中）；
- :class:`WarmPoolManager`：warm pool（常驻加载；refcount 钉扎防 LRU
  驱逐）——推理语义的加载生命周期仍归 engine loaded cache。

全部失败路径 typed / honest skip，绝不阻塞 CPU 主路径。
"""
from __future__ import annotations

import hashlib
import logging
import threading
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from app.lib.modelops.errors import ResourceUnavailable

logger = logging.getLogger(__name__)


# ── GPU 探测（复用 GeoCompute）───────────────────────────────────────


@dataclass(frozen=True)
class GpuDeviceInfo:
    """一块 GPU 的探测结论（honest；探测失败 = 空清单）。"""

    index: int
    total_mem_bytes: int

    def as_dict(self) -> Dict[str, Any]:
        return {"index": self.index, "total_mem_bytes": self.total_mem_bytes}


def probe_gpu_devices() -> Tuple[GpuDeviceInfo, ...]:
    """GPU 设备清单（GeoCompute nvidia-smi probe；缺席/超时 = 空清单）。

    GeoCompute 的 ``WorkerCapabilities`` 探测是单值 (gpu_count, mem)——
    本平面按索引展开为设备清单；探测通道不可用时回退 torch CUDA probe
    （两通道都缺席 = 空，CPU 路径照常）。
    """
    devices: List[GpuDeviceInfo] = []
    total_count = 0
    mem_per_gpu = 0
    try:
        from app.services.geocompute.cluster.capabilities import _probe_gpu

        total_count, mem_mb = _probe_gpu()
        mem_per_gpu = int(mem_mb) * 1024 * 1024
        total_count = int(total_count)
    except Exception as exc:  # noqa: BLE001 — GeoCompute 缺席不影响主路径
        logger.debug("geocompute gpu probe unavailable: %s", exc)
    if total_count <= 0:
        # 回退通道：torch CUDA（多数深度学习部署有 torch）。
        from app.lib.modelops.backends import probe_backend

        info = probe_backend("torch")
        if info.available and info.cuda_available and info.cuda_device_count > 0:
            total_count = info.cuda_device_count
            mem_per_gpu = 0  # torch probe 不给显存；账本回退 settings 预算
    for index in range(max(0, total_count)):
        devices.append(GpuDeviceInfo(index=index, total_mem_bytes=mem_per_gpu))
    return tuple(devices)


# ── VRAM 账本 ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class VramReservation:
    """一次推理的 VRAM 预订句柄（release-only）。"""

    device: str
    device_index: int
    bytes_reserved: int
    run_id: str


class VramLedger:
    """per-device VRAM 预订账本（线程安全；OOM 反馈收敛）。

    - 容量：GPU 设备显存（探测可得时）× 系数，否则 settings 预算；
    - ``acquire``：容量不足 → typed ``ResourceUnavailable``（带 headroom
      语义的 fix hint），**绝不排队**（调用方自己去并发信号量等待）；
    - ``report_oom``：真实 OOM 反馈 → 记账系数下调（×0.5，下限 0.25），
      后续预订按收敛后的有效容量放行——防同卡连环 OOM。
    """

    def __init__(self, *, devices: Tuple[GpuDeviceInfo, ...] = (), fallback_budget_bytes: int) -> None:
        self._lock = threading.Lock()
        self._devices = devices
        self._fallback_budget = max(0, fallback_budget_bytes)
        self._used: Dict[Tuple[str, int], int] = {}
        self._oom_factor: Dict[Tuple[str, int], float] = {}

    def _capacity(self, device: str, device_index: int) -> int:
        if device == "cuda" and self._devices:
            for dev in self._devices:
                if dev.index == device_index and dev.total_mem_bytes > 0:
                    factor = self._oom_factor.get((device, device_index), 1.0)
                    return int(dev.total_mem_bytes * 0.8 * factor)
        # CPU / 显存未知：settings 预算记账（与 batch_for_budget 同源）。
        factor = self._oom_factor.get((device, device_index), 1.0)
        return int(self._fallback_budget * factor)

    def acquire(
        self, device: str, device_index: int, *, bytes_needed: int, run_id: str
    ) -> VramReservation:
        key = (device, device_index)
        with self._lock:
            capacity = self._capacity(device, device_index)
            used = self._used.get(key, 0)
            if capacity > 0 and used + max(0, bytes_needed) > capacity:
                raise ResourceUnavailable(
                    f"device {device}:{device_index} VRAM ledger exhausted: "
                    f"{used}+{bytes_needed} > {capacity} bytes",
                    correction_hint="wait for in-flight runs, reduce batch/vram "
                    "budget, or run on another device",
                )
            self._used[key] = used + max(0, bytes_needed)
            return VramReservation(
                device=device, device_index=device_index,
                bytes_reserved=max(0, bytes_needed), run_id=run_id,
            )

    def release(self, reservation: VramReservation) -> None:
        key = (reservation.device, reservation.device_index)
        with self._lock:
            used = self._used.get(key, 0) - reservation.bytes_reserved
            self._used[key] = max(0, used)

    def report_oom(self, device: str, device_index: int) -> float:
        """真实 OOM 反馈：有效容量系数 ×0.5（下限 0.25）；返回新系数。"""
        key = (device, device_index)
        with self._lock:
            new_factor = max(0.25, self._oom_factor.get(key, 1.0) * 0.5)
            self._oom_factor[key] = new_factor
            return new_factor

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "devices": [d.as_dict() for d in self._devices],
                "used": {
                    f"{dev}:{idx}": used for (dev, idx), used in sorted(self._used.items())
                },
                "oom_factor": {
                    f"{dev}:{idx}": factor
                    for (dev, idx), factor in sorted(self._oom_factor.items())
                },
            }


# ── 多 GPU 亲和 ──────────────────────────────────────────────────────


def model_affinity_index(model_id: str, gpu_count: int) -> int:
    """确定性模型 → GPU 亲和映射（同模型常驻同卡；单卡/无卡 = 0）。"""
    if gpu_count <= 1:
        return 0
    digest = hashlib.sha256(model_id.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") % gpu_count


# ── Warm pool ────────────────────────────────────────────────────────


class WarmPoolManager:
    """warm pool：常驻加载的模型钉扎（refcount 持有，防 LRU 驱逐）。

    加载生命周期仍归 engine 的 LoadedModelCache（single-flight/refcount
    语义复用）；本管理器持有永不 release 的引用 + 提供状态查询。失败
    （模型不存在/运行时缺席）记入状态，不抛——warm pool 是性能优化，
    不是正确性依赖。
    """

    def __init__(self, loaded_cache: Any, registry: Any, providers: Any) -> None:
        self._cache = loaded_cache
        self._registry = registry
        self._providers = providers
        self._lock = threading.Lock()
        self._pinned: Dict[str, Dict[str, Any]] = {}

    def pin(
        self,
        model_id: str,
        *,
        model_version: Optional[str] = None,
        device: str = "cpu",
    ) -> Dict[str, Any]:
        """把一个模型钉进 warm pool（幂等；失败 honest 记录）。"""
        from app.lib.modelops.fingerprint import software_env_fingerprint
        from app.services.modelops.loaded_cache import load_key

        with self._lock:
            existing = self._pinned.get(model_id)
            if existing and existing.get("pinned"):
                return existing
        try:
            record = self._registry.resolve(model_id, model_version)
            descriptor = record.descriptor
            provider = self._providers.get(descriptor.provider_ref)
            cache_key = load_key(
                descriptor,
                provider_ref=descriptor.provider_ref,
                device=device,
                runtime_fingerprint=software_env_fingerprint(),
            )
            model, load_latency = self._cache.acquire(
                cache_key,
                descriptor,
                provider_ref=descriptor.provider_ref,
                device=device,
                load_fn=lambda: provider.load(descriptor, device=device),
                unload_fn=provider.unload,
            )
            provider.warmup(model)
            state = {
                "model_id": model_id,
                "device": device,
                "pinned": True,
                "cache_key": cache_key,
                "load_latency_s": round(load_latency, 6),
                "error": "",
            }
        except Exception as exc:  # noqa: BLE001 — warm pool 失败不阻断
            logger.warning("warm pool pin %s failed: %s", model_id, exc)
            state = {"model_id": model_id, "device": device, "pinned": False,
                     "error": str(exc)[:200]}
        with self._lock:
            self._pinned[model_id] = state
            return state

    def release_all(self) -> int:
        """释放全部钉扎（cache refcount 真实回落；LRU 恢复驱逐资格）。"""
        with self._lock:
            pinned = [
                (key, s) for key, s in self._pinned.items()
                if s.get("pinned") and s.get("cache_key")
            ]
            self._pinned.clear()
        for _key, state in pinned:
            try:
                self._cache.release(state["cache_key"])
            except Exception as exc:  # noqa: BLE001 — 释放失败不阻断其余
                logger.warning("warm pool release of %s failed: %s", state["model_id"], exc)
        return len(pinned)

    def status(self) -> Dict[str, Dict[str, Any]]:
        with self._lock:
            return {k: dict(v) for k, v in self._pinned.items()}


__all__ = [
    "GpuDeviceInfo",
    "VramLedger",
    "VramReservation",
    "WarmPoolManager",
    "model_affinity_index",
    "probe_gpu_devices",
]
