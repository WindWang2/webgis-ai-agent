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
from typing import Any, Dict, List

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
            semantic_version="promptable-ref/1.1.0",
            tasks=frozenset({TASK_PROMPTABLE_SEGMENTATION}),
            prompt_modes=frozenset(modes),
            devices=frozenset({DEVICE_CPU}),
            max_batch=1,
            streaming=False,
            cancellation=True,
            text_prompt=self._support_text,
            mask_candidates=True,
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
            # 引擎已验证权威 PromptSpec；extras 只承载几何 payload + 先验
            # 数组（数组不经 JSON 往返）。此处直接消费 payload 契约——
            # from_payload 无法表达 mask-only prompt（先验不进 JSON），重建
            # 会在纯掩膜路径伪报 "requires at least one prompt"。
            prompt_payload = ctx.extras.get("prompt") or {}
            if not isinstance(prompt_payload, dict):
                raise ProviderError("ctx.extras['prompt'] must be a mapping payload")
            points = [
                (float(p[0]), float(p[1])) for p in prompt_payload.get("points", [])
            ]
            boxes = [
                (float(b[0]), float(b[1]), float(b[2]), float(b[3]))
                for b in prompt_payload.get("boxes", [])
            ]
            text = prompt_payload.get("text") or None
            # R1-M3：prior mask 数组不经 JSON 往返 —— engine 以窗口切片
            # 数组直接放入 extras（to_payload 只承载几何）。
            prior_arrays = tuple(ctx.extras.get("prompt_mask_arrays") or ())
            if not points and not boxes and not prior_arrays and not text:
                raise ProviderError(
                    "promptable inference requires a prompt in ctx.extras['prompt']"
                )
            if text and not self._support_text:
                raise ProviderError(
                    "provider does not declare text_prompt capability "
                    "(qualifier should have rejected this — defense in depth)"
                )
            pixels = batch.pixels[0]  # (C,H,W)
            intensity = pixels.mean(axis=0)  # (H,W)
            h, w = intensity.shape
            want_candidates = bool(ctx.extras.get("return_candidates"))
            if want_candidates:
                return self._infer_candidates(
                    intensity, points, boxes, prior_arrays, text, ctx
                )
            similarity = np.abs(intensity - float(np.median(intensity))) <= (
                self._tolerance * max(1e-6, float(np.std(intensity) + np.mean(intensity)))
            )
            object_mask = np.zeros((h, w), dtype=bool)

            seeded = False
            for point in points[:64]:
                ensure_not_cancelled(ctx)
                px, py = int(point[0]), int(point[1])
                if 0 <= px < w and 0 <= py < h:
                    object_mask |= self._region_grow(similarity, px, py)
                    seeded = True
            for box in boxes[:64]:
                ensure_not_cancelled(ctx)
                bx, by, bw, bh = box
                x0, y0 = max(0, int(bx)), max(0, int(by))
                x1, y1 = min(w, int(bx + bw)), min(h, int(by + bh))
                if x1 > x0 and y1 > y0:
                    region = np.zeros((h, w), dtype=bool)
                    region[y0:y1, x0:x1] = similarity[y0:y1, x0:x1]
                    object_mask |= region
                    seeded = True
            if not seeded and prior_arrays:
                # mask-only prompt（Platform 11 起一等公民：多边形/参考层/
                # sidecar 先验）：无点/框种子时，先验自身即候选区域，受窗内
                # 相似度约束（SAM prior 语义的确定性投影）。此前 mask-only
                # 在 from_payload 重建处崩溃，此路径从未可达。
                object_mask |= similarity
            for prior in prior_arrays[:8]:
                ensure_not_cancelled(ctx)
                if prior.shape == (h, w):
                    object_mask &= prior.astype(bool)
            if text and self._support_text:
                # 声明 text 能力时的确定性"语义"：文本哈希选亮度带（不虚称
                # 真实文本理解；qualifier/manifest 如实记录 text_prompt=stub）。
                band = int(__import__("hashlib").sha256(text.encode()).hexdigest(), 16) % 100
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

    # ── 多候选路径（Platform 11 / WP-C；确定性 3 候选）───────────────
    def _infer_candidates(
        self,
        intensity: np.ndarray,
        points,
        boxes,
        prior_arrays,
        text,
        ctx: InferenceContext,
    ) -> TileOutput:
        """确定性 3 候选 + 启发式排序分（显式 heuristic——非模型置信度）。

        - c0 tight：当前容差的种子生长/先验路径（与单掩膜路径同语义）；
        - c1 relaxed：1.6× 容差（更宽的相似带）；
        - c2 box-fit：prompt 几何包围盒 ∩ tight 相似度。
        class_probabilities 承载 argmax(分数) 候选（兼容主路径语义）。
        """
        h, w = intensity.shape
        med = float(np.median(intensity))
        denom = max(1e-6, float(np.std(intensity) + np.mean(intensity)))
        sim_tight = np.abs(intensity - med) <= self._tolerance * denom
        sim_relaxed = np.abs(intensity - med) <= self._tolerance * 1.6 * denom

        c0 = self._mask_from_similarity(sim_tight, intensity, points, boxes, prior_arrays, text)
        c1 = self._mask_from_similarity(sim_relaxed, intensity, points, boxes, prior_arrays, text)
        c2 = self._box_fit_mask(sim_tight, points, boxes, prior_arrays)
        candidates = np.stack([c0, c1, c2])
        scores = self._heuristic_scores(candidates, points, boxes, h, w)
        chosen = int(np.argmax(scores))  # 平分取小 index（argmax 语义）
        two_class = np.stack(
            [(~candidates[chosen]).astype(np.float32),
             candidates[chosen].astype(np.float32)], axis=0
        )[None]
        return TileOutput(
            task_type=TASK_PROMPTABLE_SEGMENTATION,
            class_probabilities=two_class,
            mask_candidates=candidates,
            candidate_scores=scores,
            candidate_sources=("heuristic",) * 3,
        )

    def _mask_from_similarity(
        self, similarity, intensity, points, boxes, prior_arrays, text
    ) -> np.ndarray:
        h, w = similarity.shape
        object_mask = np.zeros((h, w), dtype=bool)
        seeded = False
        for point in points[:64]:
            px, py = int(point[0]), int(point[1])
            if 0 <= px < w and 0 <= py < h:
                object_mask |= self._region_grow(similarity, px, py)
                seeded = True
        for box in boxes[:64]:
            bx, by, bw, bh = box
            x0, y0 = max(0, int(bx)), max(0, int(by))
            x1, y1 = min(w, int(bx + bw)), min(h, int(by + bh))
            if x1 > x0 and y1 > y0:
                region = np.zeros((h, w), dtype=bool)
                region[y0:y1, x0:x1] = similarity[y0:y1, x0:x1]
                object_mask |= region
                seeded = True
        if not seeded and prior_arrays:
            object_mask |= similarity
        for prior in prior_arrays[:8]:
            if prior.shape == (h, w):
                object_mask &= prior.astype(bool)
        if text and self._support_text:
            # text stub：与单掩膜路径同一口径（文本哈希选亮度带 ∩ 相似度）。
            band = int(
                __import__("hashlib").sha256(text.encode()).hexdigest(), 16
            ) % 100
            object_mask |= (intensity > band / 100.0) & similarity
        return object_mask

    def _box_fit_mask(self, similarity, points, boxes, prior_arrays) -> np.ndarray:
        h, w = similarity.shape
        xs: List[float] = [p[0] for p in points]
        ys: List[float] = [p[1] for p in points]
        for bx, by, bw, bh in boxes:
            xs.extend([bx, bx + bw])
            ys.extend([by, by + bh])
        object_mask = np.zeros((h, w), dtype=bool)
        if xs:
            x0, x1 = max(0, int(min(xs))), min(w, int(max(xs)) + 1)
            y0, y1 = max(0, int(min(ys))), min(h, int(max(ys)) + 1)
            if x1 > x0 and y1 > y0:
                region = np.zeros((h, w), dtype=bool)
                region[y0:y1, x0:x1] = similarity[y0:y1, x0:x1]
                object_mask |= region
        elif prior_arrays:
            object_mask |= similarity
        for prior in prior_arrays[:8]:
            if prior.shape == (h, w):
                object_mask &= prior.astype(bool)
        return object_mask

    @staticmethod
    def _heuristic_scores(
        candidates: np.ndarray, points, boxes, h: int, w: int
    ) -> np.ndarray:
        """启发式排序分（确定性；语义 = 与 prompt 几何的一致度代理）。

        点：命中点的候选按 (1 - 面积占比) 计分（含点且更紧凑者更高）；
        框：与框并集的 IoU；两者平均；无几何（mask-only）：紧凑度
        (1 - 面积占比)。分数不冒充模型置信度（source=heuristic）。
        """
        total = float(h * w)
        box_union = None
        if boxes:
            bx0 = max(0, int(min(b[0] for b in boxes)))
            by0 = max(0, int(min(b[1] for b in boxes)))
            bx1 = min(w, int(max(b[0] + b[2] for b in boxes)))
            by1 = min(h, int(max(b[1] + b[3] for b in boxes)))
            box_union = np.zeros((h, w), dtype=bool)
            if bx1 > bx0 and by1 > by0:
                box_union[by0:by1, bx0:bx1] = True
        scores = []
        for cand in candidates:
            area = float(cand.sum())
            compact = 1.0 - min(1.0, area / total)
            if points:
                hits = 0
                for px, py in points[:64]:
                    ipx, ipy = int(px), int(py)
                    if 0 <= ipx < w and 0 <= ipy < h and cand[ipy, ipx]:
                        hits += 1
                s_pt = (hits / max(1, min(len(points), 64))) * compact
            else:
                s_pt = compact
            if box_union is not None and box_union.any():
                inter = float(np.logical_and(cand, box_union).sum())
                union = float(np.logical_or(cand, box_union).sum())
                s_box = inter / max(1.0, union)
            else:
                s_box = s_pt
            scores.append(min(1.0, max(0.0, 0.5 * (s_pt + s_box))))
        return np.asarray(scores, dtype=np.float32)

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
