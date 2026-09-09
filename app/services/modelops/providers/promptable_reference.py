"""Promptable reference provider（SAM 类能力 = provider capability，不硬编码版本）。

契约（Epic §I）：point/box/mask(/text 可选) prompt → 目标掩膜。

确定性实现（诚实边界——证明契约与空间语义，不证明分割精度）：
- 输入单 chip (1,C,H,W) + PromptSpec（engine 已把 prompt 落到 chip 像素
  坐标）；
- ``point``：从点出发的种子区域生长（与种子亮度的带内相似度 + 4-邻接，
  scipy ndimage label 的确定性传播）；
- ``box``：box 内亮度相似像元；box 外强制背景；
- ``mask``（prior）：prior 掩膜 ∩ 亮度相似（prompt 组合时取交集语义）；
- 多目标 prompt：每个 prompt 独立产掩膜，输出按 prompt 序叠加 instance
  id 通道。
输出：2 类概率（object/background），shape (1,2,H,W)。
"""
from __future__ import annotations

import threading
from collections import deque
from typing import Any, Dict

import numpy as np

from app.lib.modelops.capabilities import (
    DEVICE_CPU,
    PROMPT_BOX,
    PROMPT_MASK,
    PROMPT_POINT,
    TASK_PROMPTABLE_SEGMENTATION,
    ProviderCapabilities,
)
from app.lib.modelops.descriptor import GeoModelDescriptor
from app.lib.modelops.errors import ProviderError, ProviderLoadFailed
from app.lib.modelops.promptable import PromptSpec
from app.lib.modelops.resources import ResourceEstimate
from app.services.modelops.providers.base import (
    InferenceContext,
    LoadedModel,
    ProviderHealth,
    TileBatch,
    TileOutput,
    ensure_not_cancelled,
)


class PromptableReferenceProvider:
    """确定性 promptable segmentation 参考实现。"""

    def __init__(
        self,
        provider_id: str = "promptable-reference",
        *,
        support_text_prompt: bool = False,
        tolerance: float = 0.35,
    ) -> None:
        self._provider_id = provider_id
        self._support_text = support_text_prompt
        self._tolerance = tolerance
        self._lock = threading.Lock()
        self._in_flight = 0

    def capabilities(self) -> ProviderCapabilities:
        modes = {PROMPT_POINT, PROMPT_BOX, PROMPT_MASK}
        if self._support_text:
            modes.add("text")
        return ProviderCapabilities(
            provider_id=self._provider_id,
            provider_type="local_reference",
            semantic_version="promptable-ref/1.0.0",
            tasks=frozenset({TASK_PROMPTABLE_SEGMENTATION}),
            prompt_modes=frozenset(modes),
            devices=frozenset({DEVICE_CPU}),
            max_batch=1,
            streaming=False,
            cancellation=True,
            text_prompt=self._support_text,
            max_output_bytes=32 * 1024 * 1024,
        )

    def load(self, descriptor: GeoModelDescriptor, *, device: str) -> LoadedModel:
        if device != DEVICE_CPU:
            raise ProviderLoadFailed(f"promptable reference serves cpu only (got {device!r})")
        if TASK_PROMPTABLE_SEGMENTATION not in descriptor.task_types:
            raise ProviderLoadFailed("promptable reference serves promptable_segmentation only")
        return LoadedModel(
            descriptor=descriptor,
            provider_id=self._provider_id,
            device=device,
            handle_id=f"{descriptor.model_id}@{descriptor.model_version}#prompt",
            state={"tolerance": self._tolerance},
        )

    def warmup(self, model: LoadedModel) -> Dict[str, Any]:
        return {"warmed": True}

    def estimate_resources(
        self, descriptor: GeoModelDescriptor, *, batch: int, device: str
    ) -> ResourceEstimate:
        h, w = descriptor.spatial.chip_size
        return ResourceEstimate(
            vram_bytes=descriptor.input_bands * h * w * 4 * 2,
            host_ram_bytes=descriptor.input_bands * h * w * 4 * 4,
            recommended_batch=1,
        )

    def infer(
        self, model: LoadedModel, batch: TileBatch, ctx: InferenceContext
    ) -> TileOutput:
        with self._lock:
            self._in_flight += 1
        try:
            ensure_not_cancelled(ctx)
            prompt = PromptSpec.from_payload(ctx.extras.get("prompt"))
            if prompt is None:
                raise ProviderError(
                    "promptable inference requires a prompt in ctx.extras['prompt']"
                )
            # R1-M3：prior mask 数组不经 JSON 往返 —— engine 以窗口切片
            # 数组直接放入 extras（to_payload 只承载几何）。
            prior_arrays = ctx.extras.get("prompt_mask_arrays") or ()
            if prior_arrays:
                prompt = PromptSpec(
                    points=prompt.points,
                    boxes=prompt.boxes,
                    prior_masks=tuple(prior_arrays),
                    text=prompt.text,
                    combine=prompt.combine,
                    labels=prompt.labels,
                )
            if prompt.text and not self._support_text:
                raise ProviderError(
                    "provider does not declare text_prompt capability "
                    "(qualifier should have rejected this — defense in depth)"
                )
            pixels = batch.pixels[0]  # (C,H,W)
            intensity = pixels.mean(axis=0)  # (H,W)
            h, w = intensity.shape
            similarity = np.abs(intensity - float(np.median(intensity))) <= (
                self._tolerance * max(1e-6, float(np.std(intensity) + np.mean(intensity)))
            )
            object_mask = np.zeros((h, w), dtype=bool)

            for point in prompt.points[:64]:
                ensure_not_cancelled(ctx)
                px, py = int(point[0]), int(point[1])
                if 0 <= px < w and 0 <= py < h:
                    object_mask |= self._region_grow(similarity, px, py)
            for box in prompt.boxes[:64]:
                ensure_not_cancelled(ctx)
                bx, by, bw, bh = box
                x0, y0 = max(0, int(bx)), max(0, int(by))
                x1, y1 = min(w, int(bx + bw)), min(h, int(by + bh))
                if x1 > x0 and y1 > y0:
                    region = np.zeros((h, w), dtype=bool)
                    region[y0:y1, x0:x1] = similarity[y0:y1, x0:x1]
                    object_mask |= region
            for prior in prompt.prior_masks[:8]:
                ensure_not_cancelled(ctx)
                if prior.shape == (h, w):
                    object_mask &= prior.astype(bool)
            if prompt.text and self._support_text:
                # 声明 text 能力时的确定性"语义"：文本哈希选亮度带（不虚称
                # 真实文本理解；qualifier/manifest 如实记录 text_prompt=stub）。
                band = int(__import__("hashlib").sha256(prompt.text.encode()).hexdigest(), 16) % 100
                object_mask |= (intensity > band / 100.0) & similarity

            two_class = np.stack(
                [(~object_mask).astype(np.float32), object_mask.astype(np.float32)], axis=0
            )[None]  # (1,2,H,W)
            return TileOutput(
                task_type=TASK_PROMPTABLE_SEGMENTATION, class_probabilities=two_class
            )
        finally:
            with self._lock:
                self._in_flight -= 1

    @staticmethod
    def _region_grow(similarity: np.ndarray, px: int, py: int) -> np.ndarray:
        """4-邻接 BFS 种子生长（确定性；有界栈）。"""
        h, w = similarity.shape
        if not similarity[py, px]:
            # 种子不相似时退化为单点目标（诚实小目标，不扩散）。
            out = np.zeros_like(similarity)
            out[py, px] = True
            return out
        seen = np.zeros_like(similarity)
        queue = deque([(px, py)])
        seen[py, px] = True
        while queue:
            x, y = queue.popleft()
            for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
                if 0 <= nx < w and 0 <= ny < h and similarity[ny, nx] and not seen[ny, nx]:
                    seen[ny, nx] = True
                    queue.append((nx, ny))
        return seen

    def cancel(self, model: LoadedModel, run_id: str) -> bool:
        return True

    def health(self) -> ProviderHealth:
        with self._lock:
            return ProviderHealth(healthy=True, in_flight=self._in_flight)

    def unload(self, model: LoadedModel) -> None:
        return None
