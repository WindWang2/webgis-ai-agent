"""TinySuperResolutionProvider —— 上采样重建参考实现（V3 §C）。

输入契约： TileBatch.pixels = (N,C,H,W)；输出 = ``raster_stack``
(N,C,H*s,W*s) float32（s = descriptor.output_transform.output_scale，
注册时声明、进指纹）。

实现（确定性 numpy）：scipy.ndimage.zoom order=1（双线性）通道独立
上采样——确定性、无随机、可手算 oracle（常数图 → 同常数放大图）。
"""
from __future__ import annotations

import threading
from typing import Dict

import numpy as np

from app.lib.modelops.capabilities import (
    DEVICE_CPU,
    TASK_SUPER_RESOLUTION,
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


class TinySuperResolutionProvider:
    """确定性双线性上采样重建（证明 SR 输出/georef/拼接链路）。"""

    def __init__(self, provider_id: str = "superres-reference") -> None:
        self._provider_id = provider_id
        self._lock = threading.Lock()
        self._loaded: Dict[str, LoadedModel] = {}
        self._in_flight = 0

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider_id=self._provider_id,
            provider_type="local_reference",
            semantic_version="superres-ref/1.0.0",
            tasks=frozenset({TASK_SUPER_RESOLUTION}),
            prompt_modes=frozenset(),
            devices=frozenset({DEVICE_CPU}),
            max_batch=4,
            cancellation=True,
        )

    def load(self, descriptor: GeoModelDescriptor, *, device: str) -> LoadedModel:
        if device != DEVICE_CPU:
            raise ProviderLoadFailed(f"super-resolution reference serves cpu only (got {device!r})")
        if TASK_SUPER_RESOLUTION not in descriptor.task_types:
            raise ProviderLoadFailed("super-resolution reference only serves super_resolution")
        scale = descriptor.output_transform.output_scale
        if scale < 2:
            raise ProviderLoadFailed(
                f"super_resolution requires output_transform.output_scale >= 2 (got {scale})"
            )
        model = LoadedModel(
            descriptor=descriptor,
            provider_id=self._provider_id,
            device=device,
            handle_id=f"{descriptor.model_id}@{descriptor.model_version}#sr",
            state={"scale": scale},
        )
        with self._lock:
            self._loaded[model.handle_id] = model
        return model

    def warmup(self, model: LoadedModel) -> Dict:
        return {"warmed": True, "scale": int(model.state["scale"])}

    def estimate_resources(
        self, descriptor: GeoModelDescriptor, *, batch: int, device: str
    ) -> ResourceEstimate:
        h, w = descriptor.spatial.chip_size
        scale = descriptor.output_transform.output_scale
        per_chip = (
            descriptor.input_bands * h * w * 4
            + descriptor.input_bands * h * scale * w * scale * 4
        )
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
            from scipy.ndimage import zoom

            scale = int(model.state["scale"])
            pixels = batch.pixels
            upsampled = np.empty(
                (
                    pixels.shape[0],
                    pixels.shape[1],
                    pixels.shape[2] * scale,
                    pixels.shape[3] * scale,
                ),
                dtype=np.float32,
            )
            for i in range(pixels.shape[0]):
                ensure_not_cancelled(ctx)
                for c in range(pixels.shape[1]):
                    upsampled[i, c] = zoom(
                        pixels[i, c], zoom=scale, order=1, mode="nearest"
                    ).astype(np.float32)
            return TileOutput(
                task_type=TASK_SUPER_RESOLUTION, raster_stack=upsampled
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


__all__ = ["TinySuperResolutionProvider"]
