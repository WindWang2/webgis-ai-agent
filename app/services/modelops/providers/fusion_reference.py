"""TinyFusionProvider —— SAR+光学融合分割参考实现（V3 §C）。

输入契约： TileBatch.pixels = (N, C_sar + C_opt, H, W)——前段 SAR 通道
（VV/VH…），后段光学通道（red/green/blue…）；descriptor.band_order 声明
语义顺序（qualifier 的极化/波段名校验照常生效）。

实现（确定性 numpy）：SAR 通道均值（后向散射强度）与光学亮度线性融合
→ checksum 派生锚点 softmax（K 类分割）。融合权重 = checksum 派生
（权重=身份的可验证函数）。
"""
from __future__ import annotations

import hashlib
import threading
from typing import Dict

import numpy as np

from app.lib.modelops.capabilities import (
    DEVICE_CPU,
    TASK_SAR_OPTICAL_FUSION,
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


def derive_fusion_params(checksum: str, num_classes: int) -> tuple:
    """(sar_weight, anchors)：sha256 派生；sar_weight ∈ [0.3, 0.7]。

    字数 = 1 + num_classes*4（首 word = sar 权重，其余每类 4 word 取均值）；
    sha256 扩展保证充足。
    """
    digest = bytes.fromhex(checksum)
    need_words = 1 + num_classes * 4
    raw = b""
    counter = 0
    while len(raw) < need_words * 4:
        raw += hashlib.sha256(digest + counter.to_bytes(4, "big")).digest()
        counter += 1
    vals = np.frombuffer(raw[: need_words * 4], dtype=np.uint32)
    sar_weight = 0.3 + 0.4 * float(vals[0] / np.iinfo(np.uint32).max)
    anchors = (vals[1:].reshape(num_classes, 4).mean(axis=1) /
               float(np.iinfo(np.uint32).max)) * 2.0
    return np.float32(sar_weight), anchors.astype(np.float32)


class TinyFusionProvider:
    """确定性 SAR+光学融合分割（融合语义 + 分割输出契约）。"""

    def __init__(self, provider_id: str = "fusion-reference") -> None:
        self._provider_id = provider_id
        self._lock = threading.Lock()
        self._loaded: Dict[str, LoadedModel] = {}
        self._in_flight = 0

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider_id=self._provider_id,
            provider_type="local_reference",
            semantic_version="fusion-ref/1.0.0",
            tasks=frozenset({TASK_SAR_OPTICAL_FUSION}),
            prompt_modes=frozenset(),
            devices=frozenset({DEVICE_CPU}),
            max_batch=8,
            cancellation=True,
        )

    def load(self, descriptor: GeoModelDescriptor, *, device: str) -> LoadedModel:
        if device != DEVICE_CPU:
            raise ProviderLoadFailed(f"fusion reference serves cpu only (got {device!r})")
        if TASK_SAR_OPTICAL_FUSION not in descriptor.task_types:
            raise ProviderLoadFailed("fusion reference only serves sar_optical_fusion")
        if descriptor.class_schema is None:
            raise ProviderLoadFailed("fusion segmentation requires class_schema")
        sar_w, anchors = derive_fusion_params(
            descriptor.checksum, len(descriptor.class_schema.classes)
        )
        model = LoadedModel(
            descriptor=descriptor,
            provider_id=self._provider_id,
            device=device,
            handle_id=f"{descriptor.model_id}@{descriptor.model_version}#fusion",
            state={"sar_weight": sar_w, "anchors": anchors},
        )
        with self._lock:
            self._loaded[model.handle_id] = model
        return model

    def warmup(self, model: LoadedModel) -> Dict:
        return {"warmed": True, "sar_weight": float(model.state["sar_weight"])}

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
            anchors = model.state["anchors"]
            k = anchors.shape[0]
            # 融合拆分：SAR 通道数由 band_order 的极化语义计数（缺省对半）。
            bands = list(descriptor.band_order)
            n_sar = sum(1 for b in bands if b in {"VV", "VH", "HH", "HV"}) if bands else \
                descriptor.input_bands // 2
            n_sar = max(1, min(n_sar, descriptor.input_bands - 1))
            pixels = batch.pixels
            sar_mean = pixels[:, :n_sar].mean(axis=1)       # (N,H,W)
            opt_mean = pixels[:, n_sar:].mean(axis=1)       # (N,H,W)
            sar_w = float(model.state["sar_weight"])
            fused = sar_w * sar_mean + (1.0 - sar_w) * opt_mean
            dist = fused[..., None] - anchors[None, None, None, :]  # (N,H,W,K)
            logits = -(dist ** 2) / 0.08
            logits -= logits.max(axis=-1, keepdims=True)
            probs = np.exp(logits)
            probs /= probs.sum(axis=-1, keepdims=True)
            return TileOutput(
                task_type=TASK_SAR_OPTICAL_FUSION,
                class_probabilities=np.transpose(probs, (0, 3, 1, 2)).astype(np.float32),
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


__all__ = ["TinyFusionProvider", "derive_fusion_params"]
