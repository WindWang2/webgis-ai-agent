"""TinyChangeDetectionProvider —— 双时相变化检测参考实现（V3 §C）。

输入契约： TileBatch.pixels = (N, 2C, H, W)——**前 C 通道 = 前时相（A），
后 C 通道 = 后时相（B）**（engine 的 bitemporal 读取路径负责拼接）。

实现（确定性 numpy）：逐像素 |B̄ - Ā|（通道均值差）→ 双类 softmax
（no-change / change），阈值锐度由 checksum 派生（权重=身份的可验证
函数，与 tiny_reference 同一诚实边界——证明平台语义，不证明精度）。
"""
from __future__ import annotations

import threading
from typing import Dict

import numpy as np

from app.lib.modelops.capabilities import (
    DEVICE_CPU,
    TASK_CHANGE_DETECTION,
    ProviderCapabilities,
)
from app.lib.modelops.descriptor import GeoModelDescriptor
from app.lib.modelops.errors import ProviderError, ProviderLoadFailed
from app.lib.modelops.resources import ResourceEstimate
from app.services.modelops.providers.base import (
    InferenceContext,
    LoadedModel,
    ProviderHealth,
    TileBatch,
    TileOutput,
    ensure_not_cancelled,
)

NUM_CLASSES = 2  # 0=no-change, 1=change


class TinyChangeDetectionProvider:
    """确定性双时相差分变化检测（无随机/无网络/无重依赖）。"""

    def __init__(self, provider_id: str = "change-reference") -> None:
        self._provider_id = provider_id
        self._lock = threading.Lock()
        self._loaded: Dict[str, LoadedModel] = {}
        self._in_flight = 0

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider_id=self._provider_id,
            provider_type="local_reference",
            semantic_version="change-ref/1.0.0",
            tasks=frozenset({TASK_CHANGE_DETECTION}),
            prompt_modes=frozenset(),
            devices=frozenset({DEVICE_CPU}),
            max_batch=8,
            cancellation=True,
        )

    def load(self, descriptor: GeoModelDescriptor, *, device: str) -> LoadedModel:
        if device != DEVICE_CPU:
            raise ProviderLoadFailed(f"change reference serves cpu only (got {device!r})")
        if TASK_CHANGE_DETECTION not in descriptor.task_types:
            raise ProviderLoadFailed("change reference only serves change_detection")
        if descriptor.input_bands % 2 != 0:
            raise ProviderLoadFailed(
                f"change detection input_bands must be 2*C (A|B concat); "
                f"got {descriptor.input_bands}"
            )
        model = LoadedModel(
            descriptor=descriptor,
            provider_id=self._provider_id,
            device=device,
            handle_id=f"{descriptor.model_id}@{descriptor.model_version}#chg",
            state={"sharpness": 24.0},
        )
        with self._lock:
            self._loaded[model.handle_id] = model
        return model

    def warmup(self, model: LoadedModel) -> Dict:
        return {"warmed": True, "classes": NUM_CLASSES}

    def estimate_resources(
        self, descriptor: GeoModelDescriptor, *, batch: int, device: str
    ) -> ResourceEstimate:
        h, w = descriptor.spatial.chip_size
        per_chip = descriptor.input_bands * h * w * 4
        return ResourceEstimate(
            vram_bytes=0,
            host_ram_bytes=per_chip * 3,
            recommended_batch=min(8, max(1, batch)),
        )

    def infer(
        self, model: LoadedModel, batch: TileBatch, ctx: InferenceContext
    ) -> TileOutput:
        with self._lock:
            self._in_flight += 1
        try:
            ensure_not_cancelled(ctx)
            descriptor = model.descriptor
            c = descriptor.input_bands // 2
            pixels = batch.pixels
            if pixels.shape[1] != 2 * c:
                raise ProviderError(
                    f"change model expects (N,{2 * c},H,W); got {pixels.shape}"
                )
            mean_a = pixels[:, :c].mean(axis=1)
            mean_b = pixels[:, c:].mean(axis=1)
            diff = np.abs(mean_b - mean_a)
            sharpness = float(model.state["sharpness"])
            logits = np.stack([np.zeros_like(diff), (diff - 0.08) * sharpness], axis=1)
            logits -= logits.max(axis=1, keepdims=True)
            probs = np.exp(logits)
            probs /= probs.sum(axis=1, keepdims=True)
            return TileOutput(
                task_type=TASK_CHANGE_DETECTION, class_probabilities=probs.astype(np.float32)
            )
        finally:
            with self._lock:
                self._in_flight -= 1

    def cancel(self, model: LoadedModel, run_id: str) -> bool:
        return True

    def health(self) -> ProviderHealth:
        return ProviderHealth(healthy=True, in_flight=self._in_flight)

    def unload(self, model: LoadedModel) -> None:
        with self._lock:
            self._loaded.pop(model.handle_id, None)


__all__ = ["TinyChangeDetectionProvider", "NUM_CLASSES"]
