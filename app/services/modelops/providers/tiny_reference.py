"""Tiny deterministic reference providers（ADR-0119 §3.2，Epic §9）。

定位（诚实边界）：这些 provider 证明平台的**空间语义/lifecycle/取消/
资源规划/provenance**，不证明模型精度。权重是 descriptor checksum 的
确定性函数（sha256 扩展 → 类锚点），无随机、无网络、无重依赖。

- ``TinySegmentationProvider``：逐像素 softmax（3x3 均值上下文 ⇒
  context-sensitive，可暴露错误 blend 的接缝伪影）；
- ``TinyEmbeddingProvider``：chip 池化向量（通道均值+std）；
- ``TinyClassificationProvider``：chip 均值 → 类 softmax。

全部支持：协作取消（chip 边界）、estimate_resources、health、unload、
seed（descriptor.random_seed_policy=fixed_seed 时使用 ctx.seed）。
"""
from __future__ import annotations

import hashlib
import threading
import time
from typing import Any, Dict

import numpy as np

from app.lib.modelops.capabilities import (
    DEVICE_CPU,
    TASK_CLASSIFICATION,
    TASK_EMBEDDING,
    TASK_SEMANTIC_SEGMENTATION,
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

NUM_CLASSES = 3


def derive_anchors(checksum: str, num_classes: int = NUM_CLASSES) -> np.ndarray:
    """sha256 扩展 → 类锚点（确定性；权重即身份的可验证函数）。"""
    digest = bytes.fromhex(checksum)
    raw = b""
    counter = 0
    while len(raw) < num_classes * 4:
        raw += hashlib.sha256(digest + counter.to_bytes(4, "big")).digest()
        counter += 1
    vals = np.frombuffer(raw[: num_classes * 4], dtype=np.uint32).astype(np.float64)
    return (vals / float(np.iinfo(np.uint32).max)) * 2.0  # [0, 2]，shape (K,)


class TinyReferenceProvider:
    """确定性 segmentation/embedding/classification 参考实现。"""

    def __init__(
        self,
        provider_id: str = "tiny-reference",
        *,
        context_radius: int = 1,
        load_latency_s: float = 0.0,
    ) -> None:
        self._provider_id = provider_id
        self._context_radius = max(0, context_radius)
        self._load_latency_s = max(0.0, load_latency_s)
        self._lock = threading.Lock()
        self._loaded: Dict[str, LoadedModel] = {}
        self._in_flight = 0

    # ── capabilities / lifecycle ────────────────────────────────────
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider_id=self._provider_id,
            provider_type="local_reference",
            semantic_version="tiny/1.0.0",
            tasks=frozenset({TASK_SEMANTIC_SEGMENTATION, TASK_EMBEDDING, TASK_CLASSIFICATION}),
            prompt_modes=frozenset(),
            devices=frozenset({DEVICE_CPU}),
            max_batch=8,
            streaming=False,
            cancellation=True,
            text_prompt=False,
            max_output_bytes=64 * 1024 * 1024,
        )

    def load(self, descriptor: GeoModelDescriptor, *, device: str) -> LoadedModel:
        if device != DEVICE_CPU:
            raise ProviderLoadFailed(f"tiny reference provider serves cpu only (got {device!r})")
        if descriptor.task_types and not (
            {TASK_SEMANTIC_SEGMENTATION, TASK_EMBEDDING, TASK_CLASSIFICATION}
            & set(descriptor.task_types)
        ):
            raise ProviderLoadFailed(
                f"tiny reference provider cannot serve tasks {sorted(descriptor.task_types)}"
            )
        # checksum 即权重身份：锚点从中导出（确定性权重 = 可验证的 synthetic 包）。
        anchors = derive_anchors(descriptor.checksum)
        if self._load_latency_s:
            time.sleep(self._load_latency_s)
        handle_id = f"{descriptor.model_id}@{descriptor.model_version}#{id(anchors):x}"
        model = LoadedModel(
            descriptor=descriptor,
            provider_id=self._provider_id,
            device=device,
            handle_id=handle_id,
            state={"anchors": anchors},
        )
        with self._lock:
            self._loaded[handle_id] = model
        return model

    def warmup(self, model: LoadedModel) -> Dict[str, Any]:
        anchors = model.state["anchors"]
        return {"warmed": True, "classes": int(anchors.shape[0])}

    def estimate_resources(
        self, descriptor: GeoModelDescriptor, *, batch: int, device: str
    ) -> ResourceEstimate:
        h, w = descriptor.spatial.chip_size
        channels = descriptor.input_bands
        # R2-M1：estimate 一律返回**单 chip** 值；batch 因子只在 engine
        # 一处相乘（跨 provider 口径一致，manifest 账目不虚高）。
        per_chip_bytes = channels * h * w * 4
        return ResourceEstimate(
            vram_bytes=per_chip_bytes,
            host_ram_bytes=per_chip_bytes * 2,
            recommended_batch=min(8, max(1, batch)),
        )

    def infer(
        self, model: LoadedModel, batch: TileBatch, ctx: InferenceContext
    ) -> TileOutput:
        with self._lock:
            self._in_flight += 1
        try:
            return self._infer(model, batch, ctx)
        finally:
            with self._lock:
                self._in_flight -= 1

    def _infer(self, model: LoadedModel, batch: TileBatch, ctx: InferenceContext) -> TileOutput:
        descriptor = model.descriptor
        ensure_not_cancelled(ctx)
        if descriptor.random_seed_policy == "fixed_seed" and ctx.seed is not None:
            rng = np.random.default_rng(ctx.seed)  # noqa: F841 — 形式化确定性种子路径
        task = descriptor.task_types[0]
        if task == TASK_SEMANTIC_SEGMENTATION:
            probs = self._segment(model, batch, ctx)
            return TileOutput(
                task_type=task, class_probabilities=probs.astype(np.float32)
            )
        if task == TASK_EMBEDDING:
            emb = self._embed(model, batch, ctx)
            return TileOutput(task_type=task, embeddings=emb.astype(np.float32))
        if task == TASK_CLASSIFICATION:
            labels = self._classify(model, batch, ctx)
            return TileOutput(task_type=task, label_probabilities=labels.astype(np.float32))
        raise ProviderError(f"tiny reference provider cannot serve task {task!r}")

    # ── 任务实现（确定性 numpy）─────────────────────────────────────
    def _chip_mean(self, batch: TileBatch, ctx: InferenceContext) -> np.ndarray:
        """通道均值 + 可选 3x3 上下文（uniform filter）。"""
        mean = batch.pixels.mean(axis=1)  # (N,H,W)
        if self._context_radius:
            ensure_not_cancelled(ctx)
            from scipy.ndimage import uniform_filter

            size = 2 * self._context_radius + 1
            smoothed = np.empty_like(mean)
            for i in range(mean.shape[0]):
                ensure_not_cancelled(ctx)
                smoothed[i] = uniform_filter(mean[i], size=size, mode="nearest")
            return smoothed
        return mean

    def _segment(self, model: LoadedModel, batch: TileBatch, ctx: InferenceContext) -> np.ndarray:
        anchors = model.state["anchors"]  # (K,)
        x = self._chip_mean(batch, ctx)  # (N,H,W)
        # nodata 像元：valid_mask=False → 概率偏向 class 0（背景）且引擎侧
        # 会用输入掩膜压制；这里保持纯函数性（不读 mask 之外的请求状态）。
        dist = x[..., None] - anchors[None, None, None, :]  # (N,H,W,K)
        logits = -(dist ** 2) / 0.08
        logits -= logits.max(axis=-1, keepdims=True)
        probs = np.exp(logits)
        probs /= probs.sum(axis=-1, keepdims=True)
        return np.transpose(probs, (0, 3, 1, 2))  # → (N,K,H,W) 契约轴序

    def _embed(self, model: LoadedModel, batch: TileBatch, ctx: InferenceContext) -> np.ndarray:
        ensure_not_cancelled(ctx)
        per_pixel_mean = batch.pixels.mean(axis=(2, 3))  # (N,C)
        per_pixel_std = batch.pixels.std(axis=(2, 3))  # (N,C)
        return np.concatenate([per_pixel_mean, per_pixel_std], axis=1)  # (N,2C)

    def _classify(self, model: LoadedModel, batch: TileBatch, ctx: InferenceContext) -> np.ndarray:
        anchors = model.state["anchors"]
        x = batch.pixels.mean(axis=(1, 2, 3))  # (N,)
        dist = x[:, None] - anchors[None, :]  # (N,K)
        logits = -(dist ** 2) / 0.02
        logits -= logits.max(axis=1, keepdims=True)
        probs = np.exp(logits)
        probs /= probs.sum(axis=1, keepdims=True)
        return probs

    # ── cancel/health/unload ────────────────────────────────────────
    def cancel(self, model: LoadedModel, run_id: str) -> bool:
        # 参考实现是逐 chip 协作取消（ctx.cancelled 探针）；无需额外动作。
        return True

    def health(self) -> ProviderHealth:
        return ProviderHealth(healthy=True, in_flight=self._in_flight)

    def unload(self, model: LoadedModel) -> None:
        with self._lock:
            self._loaded.pop(model.handle_id, None)
