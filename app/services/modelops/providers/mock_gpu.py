"""Mock GPU / resource-aware provider（Epic §9 fixtures；OOM/降批/竞争测试面）。

诚实边界：``cuda`` 是**模拟**设备——provider 报告 VRAM 记账并按预算
拒绝（ProviderOOM/ResourceUnavailable），不虚称真实 GPU 能力。测试据此
验证平台语义（资源计划/降批/驱逐），不验证 CUDA。
"""
from __future__ import annotations

import threading
import time
from typing import Any, Dict, Optional

import numpy as np

from app.lib.modelops.capabilities import (
    DEVICE_CPU,
    DEVICE_CUDA,
    TASK_SEMANTIC_SEGMENTATION,
    ProviderCapabilities,
)
from app.lib.modelops.descriptor import GeoModelDescriptor
from app.lib.modelops.errors import ProviderLoadFailed, ProviderOOM
from app.lib.modelops.resources import ResourceEstimate
from app.services.modelops.providers.base import (
    InferenceContext,
    LoadedModel,
    ProviderHealth,
    TileBatch,
    TileOutput,
    ensure_not_cancelled,
)
from app.services.modelops.providers.tiny_reference import derive_anchors


class MockGPUProvider:
    """可编程资源行为的分割 provider（委派给确定性 tiny 语义核）。

    可编程项：
    - ``vram_limit_bytes``：load + infer 的 VRAM 预算（超限 OOM/拒绝）；
    - ``oom_on_batch_gt``：批 > N 时首批抛 ProviderOOM（触发引擎降批）；
    - ``per_batch_delay_s``：批延迟（取消/超时测试）；
    - ``devices``：声明支持的设备（默认 cpu+cuda 模拟）。
    """

    def __init__(
        self,
        provider_id: str = "mock-gpu",
        *,
        vram_limit_bytes: int = 64 * 1024 * 1024,
        oom_on_batch_gt: Optional[int] = None,
        per_batch_delay_s: float = 0.0,
        devices: tuple = (DEVICE_CPU, DEVICE_CUDA),
        max_batch: int = 8,
    ) -> None:
        self._provider_id = provider_id
        self._vram_limit = max(0, vram_limit_bytes)
        self._oom_on_batch_gt = oom_on_batch_gt
        self._delay = max(0.0, per_batch_delay_s)
        self._devices = frozenset(devices)
        self._max_batch = max(1, max_batch)
        self._lock = threading.Lock()
        self._in_flight = 0
        self._reserved_vram = 0
        self._oom_seen: Dict[str, bool] = {}

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider_id=self._provider_id,
            provider_type="local_reference",
            semantic_version="mock-gpu/1.0.0",
            tasks=frozenset({TASK_SEMANTIC_SEGMENTATION}),
            prompt_modes=frozenset(),
            devices=self._devices,
            max_batch=self._max_batch,
            streaming=False,
            cancellation=True,
            text_prompt=False,
            max_output_bytes=32 * 1024 * 1024,
        )

    # ── VRAM 记账（模拟）────────────────────────────────────────────
    def _batch_bytes(self, descriptor: GeoModelDescriptor, batch: int) -> int:
        h, w = descriptor.spatial.chip_size
        return max(1, batch) * descriptor.input_bands * h * w * 4 * 3  # in+out+scratch

    def load(self, descriptor: GeoModelDescriptor, *, device: str) -> LoadedModel:
        if device not in self._devices:
            raise ProviderLoadFailed(
                f"mock gpu provider cannot serve device {device!r} (declares {sorted(self._devices)})"
            )
        if TASK_SEMANTIC_SEGMENTATION not in descriptor.task_types:
            raise ProviderLoadFailed("mock gpu provider serves semantic_segmentation only")
        weights_bytes = max(1, len(descriptor.checksum) * 16)
        with self._lock:
            if self._reserved_vram + weights_bytes > self._vram_limit:
                raise ProviderOOM(
                    f"mock vram budget exhausted: reserved={self._reserved_vram} "
                    f"need={weights_bytes} limit={self._vram_limit}"
                )
            self._reserved_vram += weights_bytes
        return LoadedModel(
            descriptor=descriptor,
            provider_id=self._provider_id,
            device=device,
            handle_id=f"{descriptor.model_id}@{descriptor.model_version}#mockgpu",
            state={
                "anchors": derive_anchors(descriptor.checksum),
                "weights_bytes": weights_bytes,
                "vram_observed_peak": 0,
            },
        )

    def warmup(self, model: LoadedModel) -> Dict[str, Any]:
        return {"warmed": True, "vram_reserved": model.state["weights_bytes"]}

    def estimate_resources(
        self, descriptor: GeoModelDescriptor, *, batch: int, device: str
    ) -> ResourceEstimate:
        return ResourceEstimate(
            vram_bytes=self._batch_bytes(descriptor, batch),
            host_ram_bytes=self._batch_bytes(descriptor, batch),
            recommended_batch=min(self._max_batch, max(1, batch)),
            externally_enforced=False,
        )

    def infer(
        self, model: LoadedModel, batch: TileBatch, ctx: InferenceContext
    ) -> TileOutput:
        with self._lock:
            self._in_flight += 1
        try:
            ensure_not_cancelled(ctx)
            n = batch.pixels.shape[0]
            batch_bytes = self._batch_bytes(model.descriptor, n)
            if batch_bytes > self._vram_limit:
                raise ProviderOOM(
                    f"batch needs {batch_bytes} bytes > mock vram limit {self._vram_limit}"
                )
            if (
                self._oom_on_batch_gt is not None
                and n > self._oom_on_batch_gt
                and not self._oom_seen.get(ctx.run_id)
            ):
                # 每个运行只 OOM 一次（否则降批到阈值仍会失败——降批语义测试）。
                self._oom_seen[ctx.run_id] = True
                raise ProviderOOM(f"simulated OOM for batch {n} > {self._oom_on_batch_gt}")
            if self._delay:
                # 分片 sleep：延迟期间可协作取消（取消延迟测试）。
                slept = 0.0
                step = min(0.05, max(0.005, self._delay))
                while slept < self._delay:
                    ensure_not_cancelled(ctx)
                    time.sleep(step)
                    slept += step
            ensure_not_cancelled(ctx)
            anchors = model.state["anchors"]
            x = batch.pixels.mean(axis=1)  # (N,H,W)
            dist = x[..., None] - anchors[None, None, None, :]
            logits = -(dist ** 2) / 0.08
            logits -= logits.max(axis=-1, keepdims=True)
            probs = np.exp(logits)
            probs /= probs.sum(axis=-1, keepdims=True)
            probs = np.transpose(probs, (0, 3, 1, 2))  # → (N,K,H,W) 契约轴序
            peak = int(batch_bytes + probs.nbytes)
            model.state["vram_observed_peak"] = max(
                int(model.state["vram_observed_peak"]), peak
            )
            return TileOutput(
                task_type=TASK_SEMANTIC_SEGMENTATION, class_probabilities=probs.astype(np.float32)
            )
        finally:
            with self._lock:
                self._in_flight -= 1

    def cancel(self, model: LoadedModel, run_id: str) -> bool:
        return True

    def health(self) -> ProviderHealth:
        with self._lock:
            return ProviderHealth(
                healthy=True,
                in_flight=self._in_flight,
                detail=f"reserved_vram={self._reserved_vram}/{self._vram_limit}",
            )

    def unload(self, model: LoadedModel) -> None:
        with self._lock:
            self._reserved_vram = max(0, self._reserved_vram - int(model.state["weights_bytes"]))

    @property
    def reserved_vram(self) -> int:
        with self._lock:
            return self._reserved_vram
