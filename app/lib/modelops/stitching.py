"""Task-specific merge/postprocess（ADR-0119 §3.8；Epic §F；R1-m5 契约）。

契约（挑战修订后冻结）：

- **segmentation**：overlap blend 在**概率空间**累加（禁标签平均）；
  blend 权重对 nodata 像元置零（nodata 不渗入相邻 chip 输出）；context
  边距在融合前裁掉（只融合 core）；输出 nodata 掩膜 = 输入 nodata 掩膜
  ∪ 全零列（声明策略）；
- **detection**：tile-local box → 全局坐标（engine 提供 window 原点）→
  类内 NMS（确定性 tie-break：score 降序、坐标升序）；
- **instance**：mask 合并 + 确定性 instance id 分配 + 可选 polygonize；
- **embedding/classification**：批输出收集 + 空间锚定。

确定性：float32 累加 + 固定遍历序；同输入两次运行逐位一致。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from app.lib.modelops.errors import PreprocessError
from app.lib.modelops.planning import TilePlan

# blend 权重模式（进 fingerprint.postprocess）
BLEND_CROP = "crop"            # core 内 uniform 权重（context 裁剪语义）
BLEND_FEATHER = "feather"      # core 内距边线性衰减（seam suppression）

NMS_IOU_THRESHOLD = 0.5


@dataclass(frozen=True)
class SegmentationMergePolicy:
    """分割融合参数（进 fingerprint）。"""

    blend: str = BLEND_CROP             # crop | feather
    output_probabilities: bool = False  # True = 额外输出概率栈
    seam_suppression: bool = True       # feather 等价开关（crop 时无效）

    def fingerprint_payload(self) -> Dict[str, Any]:
        return {
            "blend": self.blend,
            "output_probabilities": self.output_probabilities,
            "seam_suppression": self.seam_suppression,
        }


@dataclass
class SegmentationMergeResult:
    classes: np.ndarray                  # (H,W) uint8
    confidence: np.ndarray               # (H,W) float32
    valid_mask: np.ndarray               # (H,W) bool True=有效
    probabilities: Optional[np.ndarray] = None  # (K,H,W) float32（可选）


def merge_segmentation(
    plan: TilePlan,
    outputs: Sequence[np.ndarray],       # 每 tile (K,chip_h,chip_w) 概率
    input_nodata: Optional[np.ndarray],  # (H,W) bool True=无效（源栅格）
    policy: SegmentationMergePolicy,
    num_classes: int,
) -> SegmentationMergeResult:
    """概率空间 overlap blend + argmax（确定性；R1-m5 契约）。

    - 每 tile 的 core 区域按 blend 权重累加进全局概率缓冲；
    - nodata 像元：权重置零（nodata 不从 padded/context 区渗入）；
    - 输出 valid_mask = 输入 nodata 取反 ∧ 概率覆盖（未覆盖=0 → invalid）。
    """
    h, w = plan.raster_height, plan.raster_width
    acc = np.zeros((num_classes, h, w), dtype=np.float32)
    weight = np.zeros((h, w), dtype=np.float32)
    for tile, probs in zip(plan.tiles, outputs):
        if probs is None:
            continue
        row, col, core_h, core_w = tile.core_window
        # 定位 core 在 chip 坐标里的偏移（pad/ context 语义）。
        off_y = row - (tile.read_window[0] - tile.pad[1])
        off_x = col - (tile.read_window[1] - tile.pad[0])
        core_probs = probs[:, off_y: off_y + core_h, off_x: off_x + core_w]
        if core_probs.shape[1] != core_h or core_probs.shape[2] != core_w:
            raise PreprocessError(
                f"tile {tile.index}: core slice {core_probs.shape[1:]} != core "
                f"{(core_h, core_w)} (planner/provider geometry mismatch)"
            )
        weights = _core_weights(policy, core_h, core_w)
        acc[:, row: row + core_h, col: col + core_w] += core_probs * weights[None]
        weight[row: row + core_h, col: col + core_w] += weights
    covered = weight > 0
    safe_weight = np.where(covered, weight, 1.0).astype(np.float32)
    mean_probs = acc / safe_weight[None]
    classes = mean_probs.argmax(axis=0).astype(np.uint8)
    classes[~covered] = 255  # 未覆盖（含 nodata/pad）不是类别 0
    confidence = mean_probs.max(axis=0).astype(np.float32)
    valid = covered.copy()
    if input_nodata is not None:
        valid &= ~input_nodata
        # nodata 像元的类别/置信度无意义 → 类别置 ignore（255）。
        classes = np.where(input_nodata, np.uint8(255), classes)
    return SegmentationMergeResult(
        classes=classes,
        confidence=confidence,
        valid_mask=valid,
        probabilities=mean_probs if policy.output_probabilities else None,
    )


def _core_weights(policy: SegmentationMergePolicy, core_h: int, core_w: int) -> np.ndarray:
    """core 区融合权重（crop=uniform；feather=距边线性衰减 seam 抑制）。"""
    if policy.blend == BLEND_CROP or not policy.seam_suppression:
        return np.ones((core_h, core_w), dtype=np.float32)
    ramp_y = np.minimum(np.arange(core_h), np.arange(core_h)[::-1]).astype(np.float32)
    ramp_x = np.minimum(np.arange(core_w), np.arange(core_w)[::-1]).astype(np.float32)
    feather_y = np.clip(ramp_y + 1.0, 1.0, float(min(core_h, 9)))
    feather_x = np.clip(ramp_x + 1.0, 1.0, float(min(core_w, 9)))
    return np.minimum.outer(feather_y, feather_x).astype(np.float32)


# ── Detection ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class DetectionRecord:
    """全局坐标检测（GeoJSON-ready）。"""

    label: int
    score: float
    box: Tuple[float, float, float, float]  # 全局像素坐标 (x, y, w, h)

    def as_dict(self) -> Dict[str, Any]:
        return {"label": self.label, "score": self.score, "box": list(self.box)}


def merge_detections(
    plan: TilePlan,
    outputs: Sequence[List[Dict[str, Any]]],  # 每 tile 的 tile-local 检测
    *,
    iou_threshold: float = NMS_IOU_THRESHOLD,
    score_threshold: float = 0.0,
    max_detections: int = 10_000,
) -> List[DetectionRecord]:
    """tile-local → 全局坐标 + 类内 NMS（边缘重复消除；确定性 tie-break）。"""
    mapped: List[DetectionRecord] = []
    for tile, dets in zip(plan.tiles, outputs):
        # provider 的 box 是 **chip（context/read window）像素坐标**；
        # 全局原点 = read_window 原点（R1-C4：core 原点在含 context 的
        # chip 里整体偏移 half_ctx，用 core 原点会系统性错位）。
        origin_row, origin_col = tile.read_window[0], tile.read_window[1]
        for det in dets or []:
            x, y, bw, bh = det["box"]
            score = float(det.get("score", 0.0))
            if score < score_threshold:
                continue
            mapped.append(
                DetectionRecord(
                    label=int(det.get("label", 1)),
                    score=score,
                    box=(x + origin_col, y + origin_row, bw, bh),
                )
            )
    mapped = _classwise_nms(mapped, iou_threshold=iou_threshold)
    mapped.sort(key=lambda d: (-d.score, d.box, d.label))
    return mapped[:max_detections]


def _iou(a: DetectionRecord, b: DetectionRecord) -> float:
    ax, ay, aw, ah = a.box
    bx, by, bw, bh = b.box
    ix = max(0.0, min(ax + aw, bx + bw) - max(ax, bx))
    iy = max(0.0, min(ay + ah, by + bh) - max(ay, by))
    inter = ix * iy
    union = aw * ah + bw * bh - inter
    return inter / union if union > 0 else 0.0


def _classwise_nms(
    records: List[DetectionRecord], *, iou_threshold: float
) -> List[DetectionRecord]:
    kept: List[DetectionRecord] = []
    by_label: Dict[int, List[DetectionRecord]] = {}
    for rec in sorted(records, key=lambda d: (-d.score, d.box, d.label)):
        by_label.setdefault(rec.label, []).append(rec)
    for label, recs in by_label.items():
        local_kept: List[DetectionRecord] = []
        for cand in recs:  # 已按分数降序
            if all(_iou(cand, k) <= iou_threshold for k in local_kept):
                local_kept.append(cand)
        kept.extend(local_kept)
    return kept


# ── Instance segmentation ────────────────────────────────────────────


@dataclass
class InstanceMergeResult:
    instance_ids: np.ndarray              # (H,W) int32，0=背景
    instance_labels: Dict[int, int]       # instance id → 类别
    polygon_geojson: Optional[Dict[str, Any]] = None


def merge_instances(
    plan: TilePlan,
    outputs: Sequence[np.ndarray],        # 每 tile (H_chip,W_chip) int 实例 id（tile 内 1..K）
    instance_classes: Sequence[Dict[int, int]],  # 每 tile {tile实例id: 类别}
    *,
    input_nodata: Optional[np.ndarray] = None,
    polygonize: bool = False,
) -> InstanceMergeResult:
    """mask 合并 + 确定性全局实例 id（tile 序优先；重叠区 tile 序小的赢）。"""
    h, w = plan.raster_height, plan.raster_width
    canvas = np.zeros((h, w), dtype=np.int32)
    global_classes: Dict[int, int] = {}
    next_id = 1
    for tile, inst_mask, cls_map in zip(plan.tiles, outputs, instance_classes):
        if inst_mask is None:
            continue
        row, col, core_h, core_w = tile.core_window
        off_y = row - (tile.read_window[0] - tile.pad[1])
        off_x = col - (tile.read_window[1] - tile.pad[0])
        core = inst_mask[off_y: off_y + core_h, off_x: off_x + core_w]
        for tile_inst in np.unique(core):
            if tile_inst == 0:
                continue
            tile_inst = int(tile_inst)
            gcls = cls_map.get(tile_inst, 1)
            region = core == tile_inst
            free = canvas[row: row + core_h, col: col + core_w] == 0
            take = region & free
            if not take.any():
                continue
            new_id = next_id
            next_id += 1
            region_global = canvas[row: row + core_h, col: col + core_w]
            region_global[take] = new_id
            global_classes[new_id] = gcls
    if input_nodata is not None:
        canvas[input_nodata] = 0
    result = InstanceMergeResult(instance_ids=canvas, instance_labels=global_classes)
    if polygonize:
        result.polygon_geojson = _polygonize(canvas)
    return result


def _polygonize(instance_ids: np.ndarray) -> Dict[str, Any]:
    """实例栅格 → GeoJSON FeatureCollection（rasterio.features，像素坐标）。"""
    try:
        from rasterio import features
    except Exception as exc:  # pragma: no cover — rasterio 缺失环境
        from app.lib.modelops.errors import ModelOpsError

        raise ModelOpsError(f"polygonize unavailable: {exc}") from exc
    features_iter = features.shapes(
        instance_ids, mask=instance_ids > 0, connectivity=4
    )
    out_features = []
    for geom, value in features_iter:
        out_features.append(
            {
                "type": "Feature",
                "properties": {"instance_id": int(value)},
                "geometry": geom,
            }
        )
    return {"type": "FeatureCollection", "features": out_features}


# ── Embedding / classification ───────────────────────────────────────


@dataclass(frozen=True)
class SpatialEmbedding:
    chip_index: int
    core_window: Tuple[int, int, int, int]
    vector: Tuple[float, ...]
    label: Optional[int] = None
    probabilities: Optional[Tuple[float, ...]] = None

    def as_dict(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "chip_index": self.chip_index,
            "core_window": list(self.core_window),
            "vector": list(self.vector),
        }
        if self.label is not None:
            payload["label"] = self.label
        if self.probabilities is not None:
            payload["probabilities"] = list(self.probabilities)
        return payload


def collect_embeddings(
    plan: TilePlan,
    outputs: Sequence[np.ndarray],       # 每 tile (D,) 或 (num_classes,)
    label_outputs: Optional[Sequence[np.ndarray]] = None,
) -> List[SpatialEmbedding]:
    """per-chip 输出收集 + core 窗口空间锚定（embedding/classification）。"""
    collected: List[SpatialEmbedding] = []
    for i, (tile, vec) in enumerate(zip(plan.tiles, outputs)):
        if vec is None:
            continue
        label = None
        probs = None
        if label_outputs is not None and i < len(label_outputs) and label_outputs[i] is not None:
            probs_arr = np.asarray(label_outputs[i], dtype=np.float32)
            probs = tuple(round(float(p), 6) for p in probs_arr)
            label = int(probs_arr.argmax())
        collected.append(
            SpatialEmbedding(
                chip_index=i,
                core_window=tuple(int(v) for v in tile.core_window),
                vector=tuple(round(float(v), 6) for v in np.asarray(vec).ravel()),
                label=label,
                probabilities=probs,
            )
        )
    return collected
