"""Evaluation metrics —— 模型评估平台（ADR-0119 §3.8；Epic §K）。

- segmentation：IoU/F1（macro/per-class）+ 混淆矩阵；
- detection：precision/recall/AP@IoU（贪心匹配，非 COCO 全量——如实声明）；
- classification：accuracy/balanced accuracy + 混淆矩阵；
- calibration：ECE（等宽分箱）；
- **泄漏防护**：spatial blocked 划分（网格 block → fold，确定性 hash）
  与 temporal split（按时间切，禁随机）——leakage report 显式输出，
  禁止隐藏 train/test 地理泄漏。

全部纯函数（numpy），确定性，小型 oracle 可手算。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from app.lib.data.fingerprints import sha256_hex

ECE_BINS = 10


# ── segmentation ─────────────────────────────────────────────────────


@dataclass(frozen=True)
class SegmentationMetrics:
    iou_per_class: Tuple[float, ...]
    f1_per_class: Tuple[float, ...]
    miou: float
    macro_f1: float
    confusion: Tuple[Tuple[int, ...], ...]
    support_per_class: Tuple[int, ...]

    def as_dict(self) -> Dict[str, Any]:
        return {
            "iou_per_class": list(self.iou_per_class),
            "f1_per_class": list(self.f1_per_class),
            "miou": self.miou,
            "macro_f1": self.macro_f1,
            "confusion": [list(row) for row in self.confusion],
            "support_per_class": list(self.support_per_class),
        }


def segmentation_metrics(
    y_true: np.ndarray,
    y_pred: np.ndarray,
    *,
    num_classes: int,
    ignore_index: int = 255,
) -> SegmentationMetrics:
    """混淆矩阵 → IoU/F1（ignore_index 像元排除；确定性）。"""
    valid = (y_true != ignore_index) & (y_pred != ignore_index)
    t = y_true[valid].astype(np.int64)
    p = y_pred[valid].astype(np.int64)
    confusion = np.zeros((num_classes, num_classes), dtype=np.int64)
    if t.size:
        np.add.at(confusion, (t, p), 1)
    iou = []
    f1 = []
    support = []
    for k in range(num_classes):
        tp = int(confusion[k, k])
        fp = int(confusion[:, k].sum()) - tp
        fn = int(confusion[k, :].sum()) - tp
        denom = tp + fp + fn
        iou.append(round(tp / denom, 6) if denom else 0.0)
        prec = tp / (tp + fp) if tp + fp else 0.0
        rec = tp / (tp + fn) if tp + fn else 0.0
        f1.append(
            round(2 * prec * rec / (prec + rec), 6) if prec + rec else 0.0
        )
        support.append(int(confusion[k, :].sum()))
    valid_iou = [v for k, v in enumerate(iou) if support[k] > 0]
    valid_f1 = [v for k, v in enumerate(f1) if support[k] > 0]
    return SegmentationMetrics(
        iou_per_class=tuple(iou),
        f1_per_class=tuple(f1),
        miou=round(sum(valid_iou) / len(valid_iou), 6) if valid_iou else 0.0,
        macro_f1=round(sum(valid_f1) / len(valid_f1), 6) if valid_f1 else 0.0,
        confusion=tuple(tuple(int(v) for v in row) for row in confusion),
        support_per_class=tuple(support),
    )


# ── detection ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class DetectionMetrics:
    precision: float
    recall: float
    f1: float
    ap: float                      # AP@iou（11 点插值，如实例明示非 COCO 全量）
    true_positives: int
    false_positives: int
    false_negatives: int

    def as_dict(self) -> Dict[str, Any]:
        return {
            "precision": self.precision,
            "recall": self.recall,
            "f1": self.f1,
            "ap_at_iou": self.ap,
            "true_positives": self.true_positives,
            "false_positives": self.false_positives,
            "false_negatives": self.false_negatives,
            "ap_method": "11-point interpolated (not full COCO)",
        }


def detection_metrics(
    predictions: Sequence[Dict[str, Any]],
    references: Sequence[Dict[str, Any]],
    *,
    iou_threshold: float = 0.5,
    label: Optional[int] = None,
) -> DetectionMetrics:
    """贪心匹配（score 降序、IoU 阈值）→ P/R/F1/AP。

    ``box`` 为全局像素坐标 (x,y,w,h)；``label`` 可选过滤。
    """
    preds = [
        p for p in predictions if label is None or int(p.get("label", 1)) == label
    ]
    refs = [
        r for r in references if label is None or int(r.get("label", 1)) == label
    ]
    preds = sorted(preds, key=lambda p: -float(p.get("score", 0.0)))
    matched = [False] * len(refs)
    tp_flags: List[bool] = []
    for pred in preds:
        best_iou = 0.0
        best_j = -1
        for j, ref in enumerate(refs):
            if matched[j]:
                continue
            iou = _box_iou(pred["box"], ref["box"])
            if iou > best_iou:
                best_iou = iou
                best_j = j
        if best_j >= 0 and best_iou >= iou_threshold:
            matched[best_j] = True
            tp_flags.append(True)
        else:
            tp_flags.append(False)
    tp = sum(tp_flags)
    fp = len(tp_flags) - tp
    fn = len(refs) - tp
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    # 11 点插值 AP（PASCAL VOC style，非 COCO 全量——as_dict 如实声明）。
    ap = _ap_11point(tp_flags, tp + fp, len(refs))
    return DetectionMetrics(
        precision=round(precision, 6),
        recall=round(recall, 6),
        f1=round(f1, 6),
        ap=round(ap, 6),
        true_positives=tp,
        false_positives=fp,
        false_negatives=max(0, fn),
    )


def _box_iou(a: Sequence[float], b: Sequence[float]) -> float:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    ix = max(0.0, min(ax + aw, bx + bw) - max(ax, bx))
    iy = max(0.0, min(ay + ah, by + bh) - max(ay, by))
    inter = ix * iy
    union = aw * ah + bw * bh - inter
    return inter / union if union > 0 else 0.0


def _ap_11point(tp_flags: List[bool], n_preds: int, n_refs: int) -> float:
    if n_refs == 0 or n_preds == 0:
        return 0.0
    recalls: List[float] = []
    precisions: List[float] = []
    tp_so_far = 0
    for i, ok in enumerate(tp_flags, start=1):
        tp_so_far += int(ok)
        recalls.append(tp_so_far / n_refs)
        precisions.append(tp_so_far / i)
    ap = 0.0
    for t in [i / 10 for i in range(11)]:
        eligible = [p for r, p in zip(recalls, precisions) if r >= t]
        ap += max(eligible) if eligible else 0.0
    return ap / 11.0


# ── classification / calibration ─────────────────────────────────────


def classification_metrics(
    y_true: Sequence[int], y_pred: Sequence[int], *, num_classes: int
) -> Dict[str, Any]:
    t = np.asarray(list(y_true), dtype=np.int64)
    p = np.asarray(list(y_pred), dtype=np.int64)
    acc = float((t == p).mean()) if t.size else 0.0
    recalls = []
    for k in range(num_classes):
        mask = t == k
        if mask.any():
            recalls.append(float((p[mask] == k).mean()))
    balanced = sum(recalls) / len(recalls) if recalls else 0.0
    confusion = np.zeros((num_classes, num_classes), dtype=np.int64)
    if t.size:
        np.add.at(confusion, (t, p), 1)
    return {
        "accuracy": round(acc, 6),
        "balanced_accuracy": round(balanced, 6),
        "confusion": confusion.tolist(),
    }


def expected_calibration_error(
    confidences: Sequence[float], correct: Sequence[bool], *, bins: int = ECE_BINS
) -> float:
    """ECE（等宽分箱；确定性）。"""
    conf = np.asarray(list(confidences), dtype=np.float64)
    corr = np.asarray(list(correct), dtype=np.float64)
    if conf.size == 0:
        return 0.0
    edges = np.linspace(0.0, 1.0, bins + 1)
    ece = 0.0
    for i in range(bins):
        mask = (conf > edges[i]) & (conf <= edges[i + 1]) if i else (conf >= 0) & (conf <= edges[1])
        if not mask.any():
            continue
        ece += mask.sum() / conf.size * abs(float(corr[mask].mean()) - float(conf[mask].mean()))
    return round(ece, 6)


# ── 泄漏防护 ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SpatialBlockedSplit:
    """网格 block → fold 划分（确定性 hash；防地理泄漏的评估划分）。"""

    block_size_px: int
    num_folds: int
    assignment: Tuple[Tuple[int, int, int], ...]   # (block_row, block_col, fold)

    def fold_of(self, row: int, col: int) -> int:
        block = (row // self.block_size_px, col // self.block_size_px)
        for br, bc, fold in self.assignment:
            if (br, bc) == block:
                return fold
        return -1

    def as_dict(self) -> Dict[str, Any]:
        return {
            "block_size_px": self.block_size_px,
            "num_folds": self.num_folds,
            "blocks": len(self.assignment),
        }


def spatial_blocked_split(
    height: int,
    width: int,
    *,
    block_size_px: int = 256,
    num_folds: int = 5,
) -> SpatialBlockedSplit:
    """整幅栅格的空间分块 fold 分配（确定性 sha256 hash，无随机源）。

    同一 block 的样本永远同 fold —— 评估集与训练集在空间上不相交，
    geographic leakage 在划分层被排除并在 report 中显式输出。
    """
    if block_size_px < 1 or num_folds < 2:
        raise ValueError("block_size_px >= 1 and num_folds >= 2 required")
    assignment = []
    for br in range(0, max(1, -(-height // block_size_px))):
        for bc in range(0, max(1, -(-width // block_size_px))):
            digest = sha256_hex(f"{br}:{bc}")
            fold = int(digest[:8], 16) % num_folds
            assignment.append((br, bc, fold))
    return SpatialBlockedSplit(
        block_size_px=block_size_px, num_folds=num_folds, assignment=tuple(assignment)
    )


@dataclass(frozen=True)
class LeakageGuardReport:
    spatial_blocked: bool
    temporal_split: bool
    detail: str

    def as_dict(self) -> Dict[str, Any]:
        return {
            "spatial_blocked": self.spatial_blocked,
            "temporal_split": self.temporal_split,
            "detail": self.detail,
        }


def leakage_guard(
    *,
    train_rows: Sequence[Tuple[int, int]],
    eval_rows: Sequence[Tuple[int, int]],
    split: Optional[SpatialBlockedSplit] = None,
    train_times: Optional[Sequence[str]] = None,
    eval_times: Optional[Sequence[str]] = None,
) -> LeakageGuardReport:
    """泄漏审计：train/eval 的空间 block 与时间区间**必须**不相交。"""
    problems: List[str] = []
    if split is not None:
        train_blocks = {(r // split.block_size_px, c // split.block_size_px)
                        for r, c in train_rows}
        eval_blocks = {(r // split.block_size_px, c // split.block_size_px)
                       for r, c in eval_rows}
        overlap = train_blocks & eval_blocks
        if overlap:
            problems.append(f"spatial block overlap: {sorted(overlap)[:5]}")
    if train_times is not None and eval_times is not None:
        overlap_t = sorted(set(train_times) & set(eval_times))
        if overlap_t:
            problems.append(f"temporal overlap: {overlap_t[:5]}")
    detail = "; ".join(problems) if problems else "no spatial/temporal overlap detected"
    return LeakageGuardReport(
        spatial_blocked=split is not None,
        temporal_split=train_times is not None and eval_times is not None,
        detail=detail,
    )


# ── 边界质量 / 分区指标 / 漂移（V3 §G）───────────────────────────────


def boundary_band(mask: np.ndarray) -> np.ndarray:
    """掩膜的边界带（内部 1 像元：erosion ⊕ mask；确定性 scipy）。"""
    from scipy.ndimage import binary_erosion

    if mask.size == 0 or not mask.any():
        return np.zeros_like(mask, dtype=bool)
    structure = np.array([[0, 1, 0], [1, 1, 1], [0, 1, 0]], dtype=bool)
    eroded = binary_erosion(mask, structure=structure, border_value=0)
    return mask & ~eroded


def boundary_metrics(
    y_true: np.ndarray,
    y_pred: np.ndarray,
    *,
    tolerance_px: int = 1,
    ignore_index: int = 255,
) -> Dict[str, Any]:
    """边界 F1/精确率/召回率（带容差膨胀匹配；同输入确定性）。

    边界带 = 各类前景边界（y!=ignore 的掩膜边界 ∪ 真值边界并集）；
    匹配在容差（膨胀 tolerance_px）内判定——GIS 边界评估的标准口径
    （boundary F1, cf. instance/seg 边界文献）。
    """
    from scipy.ndimage import binary_dilation

    valid = (y_true != ignore_index) & (y_pred != ignore_index)
    t_fore = valid & (y_true > 0)
    p_fore = valid & (y_pred > 0)
    if not t_fore.any() and not p_fore.any():
        return {"precision": 0.0, "recall": 0.0, "f1": 0.0, "tolerance_px": tolerance_px}
    t_edge = boundary_band(t_fore)
    p_edge = boundary_band(p_fore)
    # 容差足迹：半径 = tolerance_px 的方形结构。注意 scipy 的膨胀不扩大
    # 数组形状——重复膨胀是无效操作，必须直接构造 (2k+1)² 足迹。
    size = 2 * max(1, int(tolerance_px)) + 1
    dil = np.ones((size, size), dtype=bool)
    t_edge_near_p = t_edge & binary_dilation(p_edge, structure=dil, border_value=0)
    p_edge_near_t = p_edge & binary_dilation(t_edge, structure=dil, border_value=0)
    precision = float(t_edge_near_p.sum()) / float(max(1, t_edge.sum()))
    recall = float(p_edge_near_t.sum()) / float(max(1, p_edge.sum()))
    f1 = (
        2 * precision * recall / (precision + recall) if precision + recall else 0.0
    )
    return {
        "precision": round(precision, 6),
        "recall": round(recall, 6),
        "f1": round(f1, 6),
        "tolerance_px": tolerance_px,
    }


def per_region_metrics(
    y_true: np.ndarray,
    y_pred: np.ndarray,
    region_ids: np.ndarray,
    *,
    num_classes: int,
    ignore_index: int = 255,
) -> Dict[str, Any]:
    """分区（地理块）指标：每 region 的 IoU/支持度 + 全局对照。

    ``region_ids``: (H,W) int，0 = 无区域。同输入确定性；无样本的区域
    如实标注 support=0，不虚构指标。
    """
    if region_ids.shape != y_true.shape:
        raise ValueError(
            f"region_ids shape {region_ids.shape} != labels shape {y_true.shape}"
        )
    regions: Dict[str, Any] = {}
    for rid in np.unique(region_ids):
        rid = int(rid)
        if rid == 0:
            continue
        mask = region_ids == rid
        if not mask.any():
            continue
        m = segmentation_metrics(
            y_true[mask], y_pred[mask], num_classes=num_classes, ignore_index=ignore_index
        )
        regions[str(rid)] = {
            "miou": m.miou,
            "macro_f1": m.macro_f1,
            "support_px": int(mask.sum()),
        }
    return {"regions": regions, "region_count": len(regions)}


def population_stability_index(
    baseline: Sequence[float], current: Sequence[float], *, bins: int = 10, eps: float = 1e-6
) -> float:
    """PSI（分布稳定性指数；等宽分箱、确定性）。

    PSI = Σ (cur% - base%) * ln(cur% / base%)；<0.1 稳定，0.1–0.25 观察，
    >0.25 显著漂移（行业经验阈值，报告只给数值不给结论）。
    """
    base = np.asarray(list(baseline), dtype=np.float64)
    cur = np.asarray(list(current), dtype=np.float64)
    if base.size == 0 or cur.size == 0:
        return 0.0
    lo = min(float(base.min()), float(cur.min()))
    hi = max(float(base.max()), float(cur.max()))
    if hi <= lo:
        return 0.0
    edges = np.linspace(lo, hi, bins + 1)
    base_hist = np.histogram(base, bins=edges)[0].astype(np.float64)
    cur_hist = np.histogram(cur, bins=edges)[0].astype(np.float64)
    base_pct = np.maximum(base_hist / max(1, base_hist.sum()), eps)
    cur_pct = np.maximum(cur_hist / max(1, cur_hist.sum()), eps)
    psi = float(np.sum((cur_pct - base_pct) * np.log(cur_pct / base_pct)))
    return round(psi, 6)


def class_distribution_psi(
    baseline_counts: Sequence[int], current_counts: Sequence[int]
) -> float:
    """类别分布 PSI（分类占比漂移；封闭类别空间上的 categorical PSI）。"""
    base = np.asarray(list(baseline_counts), dtype=np.float64)
    cur = np.asarray(list(current_counts), dtype=np.float64)
    if base.size != cur.size or base.sum() == 0 or cur.sum() == 0:
        return 0.0
    eps = 1e-6
    base_pct = np.maximum(base / base.sum(), eps)
    cur_pct = np.maximum(cur / cur.sum(), eps)
    psi = float(np.sum((cur_pct - base_pct) * np.log(cur_pct / base_pct)))
    return round(psi, 6)
