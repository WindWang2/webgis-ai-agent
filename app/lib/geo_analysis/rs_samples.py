"""Polygon/patch 样本挂接 + 地理/时间 split —— 防空间泄漏的样本通道。

定位（本方向 ownership：polygon/patch sample attachment + geographic split）：

- **挂接**（``attach_polygon_samples``）：多边形按 pixel-center 语义
  栅格化到 cube 网格（``rasterio.features.rasterize``，懒加载），逐
  多边形做 nan-aware 聚合——NaN 像元**排除并计数**（不充当 0）；
  出格多边形诚实排除（``excluded_offgrid``），绝不伪造样本；
- **空间 split**（``geographic_block_split``）：复用 ``cv.spatial_block_folds``
  （确定性无 RNG），核心不变量 = **同一 block 永远同一 fold**——
  块不相交使 fold↔block 成为函数；泄漏测试钉住该不变量。诚实披露：
  块尺度 ≈ 1/√folds 分位距，跨块近邻泄漏有界（不是零泄漏声明）；
- **时间 split**（``temporal_forward_split``）：复用 ``cv.temporal_forward_folds``
  严格前向链——逐折 ``max(train_t) < min(test_t)``，同一时刻的样本
  永不跨折拆分（零 future leakage）；
- **样本矩阵**（``build_sample_matrix``）：records → (X, y)；全 NaN 特征
  的样本按 ``min_valid_features`` 排除并计数（不伪造样本量）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np

from app.lib.gis.scientific_errors import DegenerateData


# ── 多边形挂接 ────────────────────────────────────────────────────────

def _grid_mapping(grid: Mapping[str, Any]):
    """grid dict → rasterio transform + (height, width)。"""
    from rasterio.transform import Affine

    tr = grid.get("transform")
    if not tr or len(tr) != 6:
        raise DegenerateData(
            "样本挂接需要 6 元 GDAL transform（无 transform 的网格无法把"
            "多边形映射到像元）",
            correction_hint="在描述符 grid 中提供 transform",
        )
    a, b, c, d, e, f = (float(x) for x in tr)
    return Affine(a, b, c, d, e, f), int(grid["height"]), int(grid["width"])


def _polygon_centroid(coords) -> Optional[Tuple[float, float]]:
    ring = coords[0]
    if len(ring) < 4:
        return None
    xs = [p[0] for p in ring[:-1]]
    ys = [p[1] for p in ring[:-1]]
    return (sum(xs) / len(xs), sum(ys) / len(ys))


def attach_polygon_samples(
    feature_stack: Mapping[str, np.ndarray],
    grid: Mapping[str, Any],
    polygons: Sequence[Mapping[str, Any]],
    *,
    agg: str = "mean",
    label_property: str = "label",
) -> Dict[str, Any]:
    """多边形 → cube 网格像元 → 逐特征 nan-aware 聚合样本记录。

    - 聚合只作用有效像元（NaN 排除并计数 ``nan_cells``）；
    - 无网格交叠的多边形：``cell_count=0``、``excluded_offgrid=True``、
      特征为空——诚实排除，不伪造；
    - ``label`` 从 ``properties[label_property]`` 透传（监督上下文用）。
    """
    if agg not in ("mean", "median"):
        raise ValueError(f"agg 只支持 mean/median，got {agg!r}")
    from rasterio import features as rfeatures

    transform, height, width = _grid_mapping(grid)
    shapes = []
    for i, poly in enumerate(polygons or []):
        geom_type = str(poly.get("type") or "Polygon")
        if geom_type != "Polygon":
            raise DegenerateData(
                f"polygons[{i}] 仅支持 Polygon（patch 语义），got {geom_type!r}")
        coords = poly.get("coordinates")
        if not coords or not isinstance(coords, (list, tuple)):
            raise DegenerateData(
                f"polygons[{i}] 缺少合法 coordinates（GeoJSON Polygon 环"
                "数组）——不伪造几何")
        shapes.append(({"type": "Polygon", "coordinates": coords}, i))

    # rasterize：像素中心语义（all_touched=False）；出格形状自然无像元
    cell_map = np.full((height, width), -1, dtype=np.int64)
    if shapes:
        burned = rfeatures.rasterize(
            shapes, out_shape=(height, width), transform=transform,
            fill=-1, all_touched=False, dtype=np.int64)
        cell_map = burned

    records: List[Dict[str, Any]] = []
    n_offgrid = 0
    for i, poly in enumerate(polygons or []):
        mask = cell_map == i
        n_cells = int(mask.sum())
        props = poly.get("properties") or {}
        rec: Dict[str, Any] = {
            "polygon_id": str(props.get("id", f"poly_{i}")),
            "cell_count": n_cells,
            "label": props.get(label_property),
            "nan_cells": {},
            "excluded_offgrid": False,
            "centroid_xy": _polygon_centroid(poly["coordinates"]),
        }
        if n_cells == 0:
            rec["excluded_offgrid"] = True
            rec["features"] = {}
            n_offgrid += 1
            records.append(rec)
            continue
        feats: Dict[str, float] = {}
        for name, arr in (feature_stack or {}).items():
            a = np.asarray(arr, dtype=float)
            if a.shape != (height, width):
                raise ValueError(
                    f"特征 {name!r} 形状 {a.shape} 与网格 "
                    f"{(height, width)} 不一致")
            vals = a[mask]
            finite = vals[np.isfinite(vals)]
            rec["nan_cells"][name] = int(vals.size - finite.size)
            if finite.size == 0:
                feats[name] = float("nan")
            elif agg == "mean":
                feats[name] = float(np.mean(finite))
            else:
                feats[name] = float(np.median(finite))
        rec["features"] = feats
        records.append(rec)
    return {
        "records": records,
        "meta": {
            "n_polygons": len(records),
            "n_excluded_offgrid": n_offgrid,
            "agg": agg,
            "grid": [int(height), int(width)],
            "rasterize_semantics": "pixel-center（all_touched=False）",
            "disclosures": [
                "NaN 像元排除并计数——不充当 0",
                "无网格交叠的多边形诚实排除（不伪造样本）",
            ],
        },
    }


# ── 地理 / 时间 split（防泄漏）────────────────────────────────────────

def geographic_block_split(
    xy: np.ndarray, *, folds: int = 4,
) -> Dict[str, Any]:
    """确定性空间分块折分配（复用 cv.spatial_block_folds；无 RNG）。"""
    from app.lib.geo_analysis.cv import spatial_block_folds

    coords = np.asarray(xy, dtype=float)
    if coords.ndim != 2 or coords.shape[0] < 4:
        raise DegenerateData(
            f"geographic_block_split 需要 ≥4 个 (x,y) 样本，got {coords.shape}")
    fold, block = spatial_block_folds(coords, int(folds))
    return {
        "fold": fold,
        "block": block,
        "meta": {
            "folds": int(folds),
            "n_blocks": int(np.unique(block).size),
            "n_samples": int(len(fold)),
            "fold_counts": {
                int(f): int(np.sum(fold == f))
                for f in np.unique(fold)
            },
            "disclosure": (
                "同 block 必同 fold（fold↔block 是函数）——分块限制"
                "（非消除）空间自相关泄漏；块尺度 ≈ 1/√folds 分位距"),
            "invariant": "block_disjoint_folds",
        },
    }


def temporal_forward_split(
    times_sec: np.ndarray, *, folds: int = 3,
) -> Dict[str, Any]:
    """确定性时间前向链折分配（复用 cv.temporal_forward_folds）。"""
    from app.lib.geo_analysis.cv import temporal_forward_folds

    fold, block = temporal_forward_folds(
        np.asarray(times_sec, dtype=float), int(folds))
    return {
        "fold": fold,
        "block": block,
        "meta": {
            "folds": int(folds),
            "n_samples": int(len(fold)),
            "disclosure": (
                "严格前向链：逐折 max(train_t) < min(test_t)；同一时刻"
                "的样本永不跨折拆分（零 future leakage）"),
            "invariant": "strict_forward_chain",
        },
    }


# ── 样本矩阵 ─────────────────────────────────────────────────────────

def build_sample_matrix(
    records: Sequence[Mapping[str, Any]],
    feature_names: Sequence[str],
    *,
    min_valid_features: int = 1,
) -> Dict[str, Any]:
    """样本记录 → (X, y) 矩阵（NaN 保留语义；不足样本诚实排除）。"""
    names = [str(n) for n in (feature_names or ())]
    if not names:
        raise ValueError("feature_names 不能为空")
    min_valid = max(1, int(min_valid_features))
    X_rows: List[List[float]] = []
    y: List[Optional[str]] = []
    xy: List[Tuple[float, float]] = []
    ids: List[str] = []
    excluded: List[str] = []
    for rec in records or []:
        feats = rec.get("features") or {}
        # int 与 float 同为合法数值（JSON 通道常产 int）；bool 显式排除
        n_valid = sum(
            1 for n in names
            if isinstance(feats.get(n), (int, float))
            and not isinstance(feats.get(n), bool)
            and np.isfinite(float(feats[n])))
        if n_valid < min_valid:
            excluded.append(str(rec.get("polygon_id")))
            continue
        X_rows.append([float(feats.get(n, float("nan"))) for n in names])
        y.append(rec.get("label"))
        c = rec.get("centroid_xy")
        xy.append((float(c[0]), float(c[1])) if c else (float("nan"),
                                                        float("nan")))
        ids.append(str(rec.get("polygon_id")))
    X = np.asarray(X_rows, dtype=float).reshape(len(X_rows), len(names))
    return {
        "X": X,
        "y": y,
        "ids": ids,
        "xy": np.asarray(xy, dtype=float).reshape(len(xy), 2),
        "excluded_ids": excluded,
        "feature_names": names,
        "meta": {
            "n_samples": int(X.shape[0]),
            "n_features": len(names),
            "n_excluded_insufficient": len(excluded),
            "min_valid_features": min_valid,
            "nan_semantics": "X 内 NaN=无效特征值（分割/插补由调用方声明）",
            "disclosures": [
                "样本全 NaN 特征不足 min_valid_features 时诚实排除",
                "split 泄漏控制由 geographic_block_split / "
                "temporal_forward_split 的不变量承担",
            ],
        },
    }
