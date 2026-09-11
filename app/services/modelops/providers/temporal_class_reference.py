"""TinyTemporalClassificationProvider —— 逐时相类别参考实现（V3 §C）。

输入契约： TileBatch.pixels = (N, T*C, H, W)（time-major C*T 布局，与
temporal_forecast 同一约定）。输出 = ``label_sequence`` (N,T,K) 逐时相
类别概率（每时相独立分类，输出时间语义由 descriptor.temporal 声明）。

实现（确定性 numpy）：逐时相通道均值 → checksum 派生锚点的 softmax
（与 tiny_reference.classify 同核；趋势敏感性：锚点阈值化）。
"""
from __future__ import annotations

import hashlib
import threading
from typing import Dict

import numpy as np

from app.lib.modelops.capabilities import (
    DEVICE_CPU,
    TASK_TEMPORAL_CLASSIFICATION,
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


def derive_temporal_anchors(checksum: str, num_classes: int) -> np.ndarray:
    """sha256 扩展 → 每类锚点（[0,2]；确定性）。"""
    digest = bytes.fromhex(checksum)
    raw = b""
    counter = 0
    while len(raw) < num_classes * 4:
        raw += hashlib.sha256(digest + counter.to_bytes(4, "big")).digest()
        counter += 1
    vals = np.frombuffer(raw[: num_classes * 4], dtype=np.uint32).astype(np.float64)
    return (vals / float(np.iinfo(np.uint32).max)) * 2.0


class TinyTemporalClassificationProvider:
    """确定性逐时相分类（T 个时相各自 K 类概率）。"""

    def __init__(self, provider_id: str = "temporal-class-reference") -> None:
        self._provider_id = provider_id
        self._lock = threading.Lock()
        self._loaded: Dict[str, LoadedModel] = {}
        self._in_flight = 0

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider_id=self._provider_id,
            provider_type="local_reference",
            semantic_version="temporal-class-ref/1.0.0",
            tasks=frozenset({TASK_TEMPORAL_CLASSIFICATION}),
            prompt_modes=frozenset(),
            devices=frozenset({DEVICE_CPU}),
            max_batch=4,
            cancellation=True,
        )

    def load(self, descriptor: GeoModelDescriptor, *, device: str) -> LoadedModel:
        if device != DEVICE_CPU:
            raise ProviderLoadFailed(f"temporal classification serves cpu only (got {device!r})")
        if TASK_TEMPORAL_CLASSIFICATION not in descriptor.task_types:
            raise ProviderLoadFailed(
                "temporal classification reference only serves temporal_classification"
            )
        if descriptor.class_schema is None:
            raise ProviderLoadFailed("temporal classification requires class_schema")
        anchors = derive_temporal_anchors(
            descriptor.checksum, len(descriptor.class_schema.classes)
        )
        model = LoadedModel(
            descriptor=descriptor,
            provider_id=self._provider_id,
            device=device,
            handle_id=f"{descriptor.model_id}@{descriptor.model_version}#tcls",
            state={"anchors": anchors},
        )
        with self._lock:
            self._loaded[model.handle_id] = model
        return model

    def warmup(self, model: LoadedModel) -> Dict:
        return {"warmed": True, "classes": int(model.state["anchors"].shape[0])}

    def estimate_resources(
        self, descriptor: GeoModelDescriptor, *, batch: int, device: str
    ) -> ResourceEstimate:
        h, w = descriptor.spatial.chip_size
        per_chip = descriptor.input_bands * h * w * 4
        return ResourceEstimate(
            vram_bytes=0,
            host_ram_bytes=per_chip * 2,
            recommended_batch=min(4, max(1, batch)),
        )

    def infer(
        self, model: LoadedModel, batch: TileBatch, ctx: InferenceContext
    ) -> TileOutput:
        with self._lock:
            self._in_flight += 1
        try:
            ensure_not_cancelled(ctx)
            descriptor = model.descriptor
            anchors = model.state["anchors"]  # (K,)
            c = descriptor.input_bands          # 每时相波段数（C*T 布局）
            t = int(ctx.extras.get("stack_length") or 1)
            pixels = batch.pixels               # (N, T*C, H, W)
            if pixels.shape[1] != c * t:
                raise ProviderError(
                    f"temporal classification expects (N,{c * t},H,W) for stack "
                    f"length {t}; got {pixels.shape}"
                )
            means = np.stack(
                [pixels[:, ti * c: (ti + 1) * c].mean(axis=(1, 2, 3)) for ti in range(t)],
                axis=1,
            )  # (N,T)
            dist = means[..., None] - anchors[None, None, :]  # (N,T,K)
            logits = -(dist ** 2) / 0.02
            logits -= logits.max(axis=2, keepdims=True)
            probs = np.exp(logits)
            probs /= probs.sum(axis=2, keepdims=True)
            return TileOutput(
                task_type=TASK_TEMPORAL_CLASSIFICATION,
                label_sequence=probs.astype(np.float32),
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


__all__ = ["TinyTemporalClassificationProvider", "derive_temporal_anchors"]
