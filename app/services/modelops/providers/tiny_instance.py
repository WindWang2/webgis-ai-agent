"""Tiny deterministic instance segmentation provider（R1-M1 fixture）。

确定性连通域实例化：强度阈值 → 4-邻接连通域（scipy.ndimage.label）→
(N,H,W) int32 instance id（tile 内 1..K）。支撑 instance 路径的批粒度
测试（此前零覆盖，R1-M1）。
"""
from __future__ import annotations

import threading
from typing import Any, Dict

import numpy as np

from app.lib.modelops.capabilities import (
    DEVICE_CPU,
    TASK_INSTANCE_SEGMENTATION,
    ProviderCapabilities,
)
from app.lib.modelops.descriptor import GeoModelDescriptor
from app.lib.modelops.errors import ProviderLoadFailed
from app.lib.modelops.resources import ResourceEstimate
from app.services.modelops.providers.base import (
    InferenceContext,
    LoadedModel,
    ProviderHealth,
    TileBatch,
    TileOutput,
    ensure_not_cancelled,
)


class TinyInstanceProvider:
    """确定性 instance segmentation 参考实现。"""

    def __init__(self, provider_id: str = "tiny-instance", *,
                 threshold: float = 0.8) -> None:
        self._provider_id = provider_id
        self._threshold = threshold
        self._lock = threading.Lock()
        self._in_flight = 0

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider_id=self._provider_id,
            provider_type="local_reference",
            semantic_version="tiny-instance/1.0.0",
            tasks=frozenset({TASK_INSTANCE_SEGMENTATION}),
            prompt_modes=frozenset(),
            devices=frozenset({DEVICE_CPU}),
            max_batch=8,
            streaming=False,
            cancellation=True,
            text_prompt=False,
            max_output_bytes=32 * 1024 * 1024,
        )

    def load(self, descriptor: GeoModelDescriptor, *, device: str) -> LoadedModel:
        if device != DEVICE_CPU:
            raise ProviderLoadFailed(f"tiny instance provider serves cpu only (got {device!r})")
        if TASK_INSTANCE_SEGMENTATION not in descriptor.task_types:
            raise ProviderLoadFailed("tiny instance provider serves instance_segmentation only")
        return LoadedModel(
            descriptor=descriptor,
            provider_id=self._provider_id,
            device=device,
            handle_id=f"{descriptor.model_id}@{descriptor.model_version}#instance",
            state={"threshold": self._threshold},
        )

    def warmup(self, model: LoadedModel) -> Dict[str, Any]:
        return {"warmed": True}

    def estimate_resources(
        self, descriptor: GeoModelDescriptor, *, batch: int, device: str
    ) -> ResourceEstimate:
        h, w = descriptor.spatial.chip_size
        return ResourceEstimate(
            vram_bytes=descriptor.input_bands * h * w * 4 * max(1, batch),
            host_ram_bytes=descriptor.input_bands * h * w * 4 * max(1, batch) * 2,
            recommended_batch=min(4, max(1, batch)),
        )

    def infer(
        self, model: LoadedModel, batch: TileBatch, ctx: InferenceContext
    ) -> TileOutput:
        with self._lock:
            self._in_flight += 1
        try:
            ensure_not_cancelled(ctx)
            from scipy import ndimage

            n = batch.pixels.shape[0]
            intensity = batch.pixels.mean(axis=1)  # (N,H,W)
            masks = np.zeros_like(intensity, dtype=np.int32)
            for i in range(n):
                ensure_not_cancelled(ctx)
                binary = intensity[i] >= float(model.state["threshold"])
                labels, k = ndimage.label(binary, structure=np.array(
                    [[0, 1, 0], [1, 1, 1], [0, 1, 0]], dtype=int))
                masks[i] = labels.astype(np.int32)
            return TileOutput(task_type=TASK_INSTANCE_SEGMENTATION, instance_masks=masks)
        finally:
            with self._lock:
                self._in_flight -= 1

    def cancel(self, model: LoadedModel, run_id: str) -> bool:
        return True

    def health(self) -> ProviderHealth:
        with self._lock:
            return ProviderHealth(healthy=True, in_flight=self._in_flight)

    def unload(self, model: LoadedModel) -> None:
        return None
