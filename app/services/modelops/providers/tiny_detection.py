"""Tiny deterministic detection provider（Epic §9 fixtures）。

算法（确定性，无随机源）：强度图 3x3 上下文 → 固定网格 cell 局部极大
（每 cell 至多 1 个检测，消除平局歧义）→ 阈值过滤 → 固定尺寸 box
（tile 像素坐标，engine 负责全局变换与跨 tile NMS）。
"""
from __future__ import annotations

import threading
from typing import Any, Dict, List

import numpy as np

from app.lib.modelops.capabilities import (
    DEVICE_CPU,
    TASK_OBJECT_DETECTION,
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
from app.services.modelops.providers.tiny_reference import derive_anchors


class TinyDetectionProvider:
    """确定性 object detection 参考实现。"""

    def __init__(
        self,
        provider_id: str = "tiny-detection",
        *,
        cell: int = 16,
        box_hw: tuple = (12, 12),
        score_threshold: float = 0.6,
        load_latency_s: float = 0.0,
    ) -> None:
        self._provider_id = provider_id
        self._cell = max(4, cell)
        self._box_h, self._box_w = box_hw
        self._score_threshold = score_threshold
        self._load_latency_s = max(0.0, load_latency_s)
        self._lock = threading.Lock()
        self._in_flight = 0

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider_id=self._provider_id,
            provider_type="local_reference",
            semantic_version="tiny-detect/1.0.0",
            tasks=frozenset({TASK_OBJECT_DETECTION}),
            prompt_modes=frozenset(),
            devices=frozenset({DEVICE_CPU}),
            max_batch=8,
            streaming=False,
            cancellation=True,
            text_prompt=False,
            max_output_bytes=16 * 1024 * 1024,
        )

    def load(self, descriptor: GeoModelDescriptor, *, device: str) -> LoadedModel:
        if device != DEVICE_CPU:
            raise ProviderLoadFailed(f"tiny detection provider serves cpu only (got {device!r})")
        if TASK_OBJECT_DETECTION not in descriptor.task_types:
            raise ProviderLoadFailed("tiny detection provider serves object_detection only")
        anchors = derive_anchors(descriptor.checksum)
        if self._load_latency_s:
            import time as _time

            _time.sleep(self._load_latency_s)
        return LoadedModel(
            descriptor=descriptor,
            provider_id=self._provider_id,
            device=device,
            handle_id=f"{descriptor.model_id}@{descriptor.model_version}#detect",
            state={"anchors": anchors},
        )

    def warmup(self, model: LoadedModel) -> Dict[str, Any]:
        return {"warmed": True, "cell": self._cell}

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
            detections: List[Dict[str, Any]] = []
            pixels = batch.pixels  # (N,C,H,W)
            n, c, h, w = pixels.shape
            intensity = pixels.mean(axis=1)  # (N,H,W)
            for i in range(n):
                ensure_not_cancelled(ctx)
                img = intensity[i]
                # cell 内局部极大（确定性 argmax，行优先平局取首个）。
                for cy in range(0, h, self._cell):
                    for cx in range(0, w, self._cell):
                        cell_img = img[cy: cy + self._cell, cx: cx + self._cell]
                        peak = float(cell_img.max())
                        if peak < self._score_threshold:
                            continue
                        dy, dx = np.unravel_index(int(cell_img.argmax()), cell_img.shape)
                        detections.append(
                            {
                                "batch_index": i,
                                "box": [
                                    float(cx + dx - self._box_w // 2),
                                    float(cy + dy - self._box_h // 2),
                                    float(self._box_w),
                                    float(self._box_h),
                                ],
                                "score": round(min(1.0, peak), 6),
                                "label": 1,
                            }
                        )
            return TileOutput(task_type=TASK_OBJECT_DETECTION, detections=detections)
        finally:
            with self._lock:
                self._in_flight -= 1

    def cancel(self, model: LoadedModel, run_id: str) -> bool:
        return True

    def health(self) -> ProviderHealth:
        return ProviderHealth(healthy=True, in_flight=self._in_flight)

    def unload(self, model: LoadedModel) -> None:
        return None
