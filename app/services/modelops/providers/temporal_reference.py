"""Temporal reference provider（Epic §9 fixtures；时序契约证明）。

确定性线性趋势外推：输入 (1,C,T,H,W) 时序栈 → 输出 (1,C,H,W) 末值
（output_time_semantics=last）或预测增量。缺失观测按 quality/missing
flag 掩膜线性插值（mask/flag 策略已在 TemporalStackSpec 校验）。
"""
from __future__ import annotations

import threading
from typing import Any, Dict

import numpy as np

from app.lib.modelops.capabilities import (
    DEVICE_CPU,
    TASK_TEMPORAL_FORECAST,
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


class TemporalReferenceProvider:
    """确定性时序外推参考实现。"""

    def __init__(self, provider_id: str = "temporal-reference") -> None:
        self._provider_id = provider_id
        self._lock = threading.Lock()
        self._in_flight = 0

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider_id=self._provider_id,
            provider_type="local_reference",
            semantic_version="temporal-ref/1.0.0",
            tasks=frozenset({TASK_TEMPORAL_FORECAST}),
            prompt_modes=frozenset(),
            devices=frozenset({DEVICE_CPU}),
            max_batch=1,
            streaming=False,
            cancellation=True,
            text_prompt=False,
            max_output_bytes=64 * 1024 * 1024,
        )

    def load(self, descriptor: GeoModelDescriptor, *, device: str) -> LoadedModel:
        if device != DEVICE_CPU:
            raise ProviderLoadFailed(f"temporal reference serves cpu only (got {device!r})")
        if TASK_TEMPORAL_FORECAST not in descriptor.task_types:
            raise ProviderLoadFailed("temporal reference serves temporal_forecast only")
        if descriptor.temporal.max_length < 2:
            raise ProviderLoadFailed(
                "temporal forecast requires temporal.max_length >= 2 in descriptor"
            )
        return LoadedModel(
            descriptor=descriptor,
            provider_id=self._provider_id,
            device=device,
            handle_id=f"{descriptor.model_id}@{descriptor.model_version}#temporal",
            state=None,
        )

    def warmup(self, model: LoadedModel) -> Dict[str, Any]:
        return {"warmed": True}

    def estimate_resources(
        self, descriptor: GeoModelDescriptor, *, batch: int, device: str
    ) -> ResourceEstimate:
        h, w = descriptor.spatial.chip_size
        t = descriptor.temporal.max_length
        return ResourceEstimate(
            vram_bytes=descriptor.input_bands * t * h * w * 4,
            host_ram_bytes=descriptor.input_bands * t * h * w * 4 * 2,
            recommended_batch=1,
        )

    def infer(
        self, model: LoadedModel, batch: TileBatch, ctx: InferenceContext
    ) -> TileOutput:
        with self._lock:
            self._in_flight += 1
        try:
            ensure_not_cancelled(ctx)
            # 约定：时序栈展开为 (1, C*T, H, W) 的 TileBatch（engine 打包）；
            # flags 通道（missing_policy=flag）在最后 T 个"通道"（0/1）。
            pixels = batch.pixels  # (1, C*T(+T), H, W)
            descriptor = model.descriptor
            c = descriptor.input_bands
            # T 来自本次请求的栈长（≤ descriptor.temporal.max_length）。
            t = int(ctx.extras.get("stack_length") or descriptor.temporal.max_length)
            if t > descriptor.temporal.max_length:
                raise ProviderError(
                    f"stack length {t} exceeds model max_length "
                    f"{descriptor.temporal.max_length}"
                )
            has_flags = ctx.extras.get("missing_policy") == "flag"
            if has_flags:
                data, flags = pixels[:, : c * t], pixels[:, c * t:]
            else:
                data, flags = pixels, None
            n, ct, h, w = data.shape
            if ct != c * t:
                raise ProviderError(
                    f"temporal batch has {ct} channels; expected C*T={c * t}"
                )
            # 源通道布局 time-major（channel = t*C + c，engine/_run_temporal
            # 约定一致）：reshape 为 (n,t,c,h,w) 再换轴到 (n,c,t,h,w)。
            series = data.reshape(n, t, c, h, w).transpose(0, 2, 1, 3, 4)
            if flags is not None:
                flag_stack = flags.reshape(n, t, h, w)
                # 缺失时相线性插值（确定性；全缺失 → 时间均值）。
                valid = flag_stack < 0.5
                series = np.where(valid[:, None], series, np.nan)
                series = _fill_missing(series)
            # 最小二乘斜率外推一步（确定性；时间轴 0..T-1 → T）。
            time_axis = np.arange(t, dtype=np.float64)
            t_mean = time_axis.mean()
            slope = ((series - series.mean(axis=2, keepdims=True)) * (time_axis - t_mean)[None, None, :, None, None]).sum(axis=2)
            slope /= max(1e-6, float(((time_axis - t_mean) ** 2).sum()))
            forecast = series.mean(axis=2) + slope * (time_axis[-1] + 1 - t_mean)
            if ctx.extras.get("output_time_semantics") == "mean":
                forecast = series.mean(axis=2)
            # 输出复用 class_probabilities 通道（temporal 任务专用语义：
            # (1,C,H,W) 预测栈；engine 写 COG 时按波段展开）。
            return TileOutput(
                task_type=TASK_TEMPORAL_FORECAST,
                class_probabilities=forecast.astype(np.float32),
            )
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


def _fill_missing(series: np.ndarray) -> np.ndarray:
    """时间维 NaN 线性插值（纯 numpy，确定性；全 NaN 时相用邻均值）。"""
    n, c, t, h, w = series.shape
    out = series.copy()
    for ti in range(t):
        sl = out[:, :, ti]
        nan_mask = np.isnan(sl)
        if not nan_mask.any():
            continue
        if ti == 0:
            fill = out[:, :, 1] if t > 1 else np.nanmean(series, axis=2)
        elif ti == t - 1:
            fill = out[:, :, t - 2]
        else:
            prev = out[:, :, ti - 1]
            nxt = out[:, :, ti + 1]
            with np.errstate(invalid="ignore"):
                fill = (prev + nxt) / 2.0
        fill = np.where(np.isnan(fill), np.nanmean(series, axis=2), fill)
        out[:, :, ti] = np.where(nan_mask, fill, sl)
    return out
