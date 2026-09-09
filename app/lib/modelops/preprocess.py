"""Preprocessing pipeline（ADR-0119 §3.4；Epic §E）。

DataObject → window → band select/order → [explicit reproject 已在
engine 的 ReprojectStage 完成] → normalization → nodata mask/fill →
pad → tensor/batch。全部参数显式、进 InferenceFingerprint（R1-M3-5：
归一化统计只允许 descriptor 固定声明——逐景统计在此层 typed 拒绝）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from app.lib.modelops.descriptor import GeoModelDescriptor
from app.lib.modelops.errors import PreprocessError
from app.lib.modelops.planning import TileSpec


@dataclass(frozen=True)
class PreprocessPlan:
    """规范化预处理参数（全部进 fingerprint）。"""

    band_indices: Tuple[int, ...]        # 0-based 源波段 → 模型输入顺序
    normalization: Dict[str, Any]        # descriptor.normalization.as_dict()
    pad_fill_value: float                # R1-C3 fill（preprocess 参数）
    nodata_fill: str                     # "zero" | "mean"（nodata 像元填充）
    context_pad: bool                    # 是否启用 descriptor context pad

    def fingerprint_payload(self) -> Dict[str, Any]:
        return {
            "band_indices": list(self.band_indices),
            "normalization": dict(self.normalization),
            "pad_fill_value": self.pad_fill_value,
            "nodata_fill": self.nodata_fill,
            "context_pad": self.context_pad,
        }

    def as_dict(self) -> Dict[str, Any]:
        return self.fingerprint_payload()


def build_plan(descriptor: GeoModelDescriptor, *, source_band_count: int) -> PreprocessPlan:
    """从 descriptor 构造预处理计划（band 语义→索引解析在此）。

    ``descriptor.band_order`` 为空 ⇒ 顺序取前 N 波段。R1-M3-5：归一化
    统计必须 descriptor 固定声明（``normalization`` 字段）；逐景采样
    统计是隐藏参数——本层没有也不允许有采样路径。
    """
    n = descriptor.input_bands
    if source_band_count < n:
        raise PreprocessError(
            f"source has {source_band_count} bands; model needs {n} "
            "(qualifier should have rejected this)"
        )
    return PreprocessPlan(
        band_indices=tuple(range(n)),
        normalization=descriptor.normalization.as_dict(),
        pad_fill_value=0.0,
        nodata_fill="zero",
        context_pad=descriptor.spatial.context_size != descriptor.spatial.chip_size,
    )


def preprocess_window(
    plan: PreprocessPlan,
    descriptor: GeoModelDescriptor,
    window_data: np.ndarray,           # (C_src,H,W) 原始窗口（band 全集）
    nodata_mask: Optional[np.ndarray],  # (H,W) bool True=无效
    tile: Optional[TileSpec] = None,
) -> Tuple[np.ndarray, np.ndarray]:
    """单窗口 → 模型输入张量 (C_model,H,W) float32 + 有效掩膜 (H,W)。

    步骤：band select/order → nodata fill → normalization →（可选）np.pad。
    """
    if window_data.ndim != 3:
        raise PreprocessError(f"window_data must be (C,H,W); got {window_data.shape}")
    src_c = window_data.shape[0]
    if max(plan.band_indices, default=0) >= src_c:
        raise PreprocessError(
            f"band index out of range: {plan.band_indices} vs {src_c} source bands"
        )
    chips = window_data[list(plan.band_indices)].astype(np.float32, copy=True)

    if nodata_mask is not None and nodata_mask.any():
        fill = 0.0 if plan.nodata_fill == "zero" else _masked_mean(chips, nodata_mask)
        chips[:, nodata_mask] = fill

    chips = _normalize(chips, plan.normalization)

    valid = np.ones(chips.shape[1:], dtype=bool) if nodata_mask is None else ~nodata_mask

    if tile is not None and plan.context_pad:
        left, top, right, bottom = tile.pad
        if any((left, top, right, bottom)):
            chips = np.pad(
                chips,
                ((0, 0), (top, bottom), (left, right)),
                mode="constant",
                constant_values=plan.pad_fill_value,
            )
            valid = np.pad(valid, ((top, bottom), (left, right)), mode="constant",
                           constant_values=False)
    return chips, valid


def _masked_mean(chips: np.ndarray, mask: np.ndarray) -> float:
    valid = chips[:, ~mask]
    return float(valid.mean()) if valid.size else 0.0


def _normalize(chips: np.ndarray, normalization: Dict[str, Any]) -> np.ndarray:
    kind = normalization.get("kind", "none")
    if kind == "mean_std":
        mean = np.asarray(normalization["mean"], dtype=np.float32)[:, None, None]
        std = np.asarray(normalization["std"], dtype=np.float32)[:, None, None]
        return (chips - mean) / std
    if kind == "min_max":
        vmin = np.asarray(normalization["vmin"], dtype=np.float32)[:, None, None]
        vmax = np.asarray(normalization["vmax"], dtype=np.float32)[:, None, None]
        rng = np.where(vmax - vmin == 0, 1.0, vmax - vmin)
        return (chips - vmin) / rng
    return chips


def preprocess_batch(
    plan: PreprocessPlan,
    descriptor: GeoModelDescriptor,
    windows: Sequence[Tuple[np.ndarray, Optional[np.ndarray]]],
    tiles: Optional[Sequence[Optional[TileSpec]]] = None,
) -> Tuple[np.ndarray, Optional[np.ndarray]]:
    """批量预处理 → (N,C,H,W) float32 + (N,1,H,W) 有效掩膜（None=全有效）。"""
    from app.services.modelops.providers.base import MAX_BATCH_ELEMENTS

    chips: List[np.ndarray] = []
    masks: List[np.ndarray] = []
    all_valid = True
    for i, (window_data, nodata_mask) in enumerate(windows):
        tile = tiles[i] if tiles is not None else None
        chip, valid = preprocess_window(plan, descriptor, window_data, nodata_mask, tile)
        if not bool(valid.all()):
            all_valid = False
        chips.append(chip)
        masks.append(valid)
    batch = np.stack(chips).astype(np.float32, copy=False)
    if batch.size > MAX_BATCH_ELEMENTS:
        raise PreprocessError(
            f"preprocessed batch exceeds {MAX_BATCH_ELEMENTS} elements (memory guard)"
        )
    if all_valid:
        return batch, None
    mask_stack = np.stack(masks)[:, None].astype(bool)  # (N,1,H,W)
    return batch, mask_stack
