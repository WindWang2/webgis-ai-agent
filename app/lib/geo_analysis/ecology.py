"""生态科学（Goal 07 taxonomy「Ecology」族实现层）。

生境适宜度指数（HSI, Habitat Suitability Index）与景观格局指标
（FRAGSTATS 同族）。HSI 是要素属性层的响应曲线评分（无几何运算）；
景观指标是分类栅格的邻接/连通统计（4 邻接口径）。

职责边界（CONTRACT_BACKBONE §1）：纯数学 —— 不读文件、不写 artifact、
不挂证据块（工具层职责）。

科学约定：

- HSI 响应曲线：``trapezoid(a,b,c,d)``（四参数梯形：a 下限 0 → b 达 1
  → c 保持 1 → d 回落 0）与 ``gaussian(mu, sigma)``（最优幅适）；聚合
  arithmetic（加权平均）/ geometric（加权几何平均，限制因子语义：任一
  变量为 0 → 整体为 0）；权重归一化后披露；
- 景观指标 4 邻接（FRAGSTATS 缺省口径；8 邻接会高估连通）：
  PLAND / NP / PD（每 100ha）/ LPI / ED（边缘密度，米/ha）与
  SHDI/SIDI（景观级多样性）；nodata 不计面积也不算边缘；
- meta 全部为确定性事实（无时间戳）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from app.lib.gis.scientific_errors import (
    DegenerateData,
    MissingRequiredField,
    NoValidObservations,
)

__all__ = [
    "hsi_score_features",
    "landscape_metrics",
]

#: 每特征变量数上限（先拒绝后分配；契约 MAX_CONTRACT_PARAMS 同族护栏）。
_MAX_HSI_VARIABLES = 12


# ── HSI（habitat suitability index）──────────────────────────────────

def _trapezoid_score(x: np.ndarray, a: float, b: float, c: float,
                     d: float) -> np.ndarray:
    """四参数梯形响应：[a,b] 线性升、[b,c] 保持 1、[c,d] 线性降。"""
    if not (a <= b <= c <= d):
        raise ValueError(
            f"trapezoid curve requires a<=b<=c<=d (got {a},{b},{c},{d})")
    score = np.zeros_like(x, dtype=float)
    if b > a:
        score = np.where((x >= a) & (x <= b), (x - a) / (b - a), score)
    elif a == b:
        score = np.where(x == a, 1.0, score)
    score = np.where((x > b) & (x < c), 1.0, score)
    if c == b:
        score = np.where(x == b, 1.0, score)
    if d > c:
        score = np.where((x >= c) & (x <= d), (d - x) / (d - c), score)
    elif c == d:
        score = np.where(x == d, 1.0, score)
    return np.clip(score, 0.0, 1.0)


def _gaussian_score(x: np.ndarray, mu: float, sigma: float) -> np.ndarray:
    """高斯响应：exp(-0.5·((x-mu)/sigma)²)，sigma>0。"""
    if sigma <= 0:
        raise ValueError(f"gaussian curve requires sigma > 0 (got {sigma})")
    return np.exp(-0.5 * ((x - mu) / sigma) ** 2)


def hsi_score_features(
    geojson: dict,
    variables: List[Dict[str, Any]],
    *,
    aggregation: str = "arithmetic",
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """生境适宜度指数（USFWS 1981 HSI 程序的响应曲线/聚合口径）。

    ``variables`` 每项：``{field, curve, params, weight}``：

    - ``curve="trapezoid"``，``params=[a,b,c,d]``；
    - ``curve="gaussian"``，``params=[mu, sigma]``；
    - ``weight`` 缺省 1（归一化后披露）。

    ``aggregation``：``arithmetic``（加权平均）/ ``geometric``（加权
    几何平均 —— 限制因子语义：任一变量得分为 0 → 整体 0）。

    返回 (FeatureCollection（逐要素 hsi + 逐变量得分）, meta)。
    """
    import geopandas as gpd
    import pandas as pd

    from app.lib.geo_processor.core import safe_parse, to_feature_collection

    if not (1 <= len(variables) <= _MAX_HSI_VARIABLES):
        raise NoValidObservations(
            f"HSI requires 1..{_MAX_HSI_VARIABLES} variables "
            f"(got {len(variables)})")
    if aggregation not in ("arithmetic", "geometric"):
        raise ValueError(
            f"aggregation must be 'arithmetic' or 'geometric' (got {aggregation!r})")

    parsed = safe_parse(geojson)
    if parsed is None:
        raise NoValidObservations("invalid GeoJSON input")
    features = to_feature_collection(parsed).get("features", [])
    if not features:
        raise NoValidObservations("no features in the analysis frame")

    gdf = gpd.GeoDataFrame.from_features(features)
    gdf = gdf.reset_index(drop=True)

    weights = np.asarray(
        [float(v.get("weight", 1.0)) for v in variables], dtype=float)
    if not np.all(np.isfinite(weights)) or (weights < 0).any():
        raise ValueError("variable weights must be finite and >= 0")
    if weights.sum() <= 0:
        raise ValueError("variable weights sum to zero; at least one weight > 0")
    weights_norm = weights / weights.sum()

    field_names: List[str] = []
    for v in variables:
        f = str(v.get("field") or "")
        if not f:
            raise ValueError("each variable needs a 'field'")
        if f not in gdf.columns:
            raise MissingRequiredField(
                f"variable field '{f}' not found on features",
                correction_hint=f"add a '{f}' property or drop the variable",
            )
        raw = pd.to_numeric(gdf[f], errors="coerce").to_numpy(float)
        if np.all(~np.isfinite(raw)):
            raise DegenerateData(
                f"variable field '{f}' has no finite numeric values",
                correction_hint="check the attribute values on the frame",
            )
        field_names.append(f)
        v["_values"] = raw

    score_cols: Dict[str, List[float]] = {}
    for k, v in enumerate(variables):
        x = np.nan_to_num(v["_values"], nan=0.0)
        curve = str(v.get("curve") or "")
        params = v.get("params")
        if curve == "trapezoid":
            if not (isinstance(params, (list, tuple)) and len(params) == 4):
                raise ValueError(
                    f"variable '{field_names[k]}': trapezoid needs params [a,b,c,d]")
            s = _trapezoid_score(x, *[float(p) for p in params])
        elif curve == "gaussian":
            if not (isinstance(params, (list, tuple)) and len(params) == 2):
                raise ValueError(
                    f"variable '{field_names[k]}': gaussian needs params [mu,sigma]")
            s = _gaussian_score(x, float(params[0]), float(params[1]))
        else:
            raise ValueError(
                f"variable '{field_names[k]}': unknown curve {curve!r} "
                "(expected trapezoid/gaussian)")
        # 非有限原始值的变量得分记 0 并在 meta 披露计数（不静默）。
        s = np.where(np.isfinite(v["_values"]), s, 0.0)
        score_cols[f"hsi_{field_names[k]}"] = [round(float(t), 6) for t in s]
        v["_scores"] = s

    score_matrix = np.column_stack([v["_scores"] for v in variables])
    if aggregation == "arithmetic":
        hsi = score_matrix @ weights_norm
    else:
        with np.errstate(divide="ignore"):
            log_terms = np.where(score_matrix > 0, np.log(score_matrix), 0.0)
        any_zero = (score_matrix <= 0).any(axis=1)
        hsi = np.where(any_zero, 0.0, np.exp(log_terms @ weights_norm))

    out_features = []
    props_records = gdf.drop(columns="geometry", errors="ignore").to_dict("records")
    for i, f in enumerate(features):
        p = {
            "hsi": round(float(hsi[i]), 6),
            "hsi_class": ("unsuitable" if hsi[i] < 0.25
                          else "marginal" if hsi[i] < 0.5
                          else "suitable" if hsi[i] < 0.75 else "optimal"),
        }
        p.update({k: v[i] for k, v in score_cols.items()})
        out_features.append({
            "type": "Feature",
            "properties": p,
            "geometry": f.get("geometry"),
        })

    meta = {
        "algorithm": "hsi_response_curve_weighted",
        "aggregation": aggregation,
        "variables": [
            {
                "field": field_names[k],
                "curve": str(variables[k].get("curve")),
                "params": list(variables[k].get("params") or []),
                "weight_normalized": round(float(weights_norm[k]), 6),
            }
            for k in range(len(variables))
        ],
        "n_features": len(features),
        "hsi_mean": round(float(np.nanmean(hsi)), 6),
        "hsi_class_counts": {
            c: int(np.sum(
                (hsi < 0.25) if c == "unsuitable" else
                ((hsi >= 0.25) & (hsi < 0.5)) if c == "marginal" else
                ((hsi >= 0.5) & (hsi < 0.75)) if c == "suitable" else
                (hsi >= 0.75)))
            for c in ("unsuitable", "marginal", "suitable", "optimal")
        },
    }
    fc = {"type": "FeatureCollection", "features": out_features}
    return fc, meta


# ── 景观格局指标（FRAGSTATS 同族）────────────────────────────────────

_LANDSCAPE_STRUCTURE_4N = np.array([[0, 1, 0], [1, 1, 1], [0, 1, 0]],
                                   dtype=bool)

_M2_TO_HA = 10_000.0


def landscape_metrics(
    raster: np.ndarray,
    cell_size: float,
    cell_size_x: Optional[float] = None,
    *,
    nodata: Optional[float] = None,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """分类栅格景观格局指标（FRAGSTATS 口径，4 邻接）。

    类级：PLAND（类面积占比 %）、NP（斑块数）、PD（斑块密度 /100ha）、
    LPI（最大斑块占比 %）。景观级：ED（边缘密度 m/ha）、SHDI、SIDI、
    PR（类丰富度）。nodata 像元不计面积、不产生边缘。

    返回 (逐类指标表 dict, meta dict)。护栏：≤ 50M 像元；类别 ≤ 256。
    """
    from scipy import ndimage

    if not np.isfinite(cell_size) or cell_size <= 0:
        raise ValueError(f"cell_size must be positive (got {cell_size!r})")
    cs_x = float(cell_size_x) if cell_size_x is not None else float(cell_size)
    a = np.asarray(raster)
    if getattr(a, "ndim", 0) != 2:
        raise NoValidObservations(
            f"raster must be 2D (got ndim {getattr(a, 'ndim', 0)})")
    if a.size == 0:
        raise NoValidObservations("raster is empty")
    if a.size > 50_000_000:
        raise NoValidObservations(
            f"raster has {a.size:,} cells (limit 50,000,000); "
            "coarsen or clip before computing landscape metrics")

    valid = np.isfinite(a)
    if nodata is not None:
        valid &= (a != float(nodata))
    if not valid.any():
        raise NoValidObservations("no valid cells (all nodata/NaN)")
    classes = np.unique(a[valid])
    if classes.size > 256:
        raise DegenerateData(
            f"categorical raster with {classes.size} classes (limit 256); "
            "reclassify before computing landscape metrics",
            correction_hint="coarse the class map (e.g. group rare classes)",
        )
    cell_area_ha = (cell_size * cs_x) / _M2_TO_HA
    total_area_ha = float(valid.sum()) * cell_area_ha

    structure = _LANDSCAPE_STRUCTURE_4N
    class_rows: List[Dict[str, Any]] = []
    edge_count_total = 0  # 类-类（含类-背景 nodata/边界）4 邻接对比边数
    for cls in classes:
        mask = valid & (a == cls)
        n_patch = int(ndimage.label(mask, structure=structure)[1])
        patch_cells = int(mask.sum())
        # 4 邻接对比边：本类像元的右/下（及左/上由对称计入）邻居为异类
        # 或 nodata/边界（NaN != cls 恒真 —— 背景边缘按 FRAGSTATS 计入）。
        contrast_h = (
            (mask[:, :-1] & (a[:, 1:] != cls)).sum()
            + (mask[:, 1:] & (a[:, :-1] != cls)).sum())
        contrast_v = (
            (mask[:-1, :] & (a[1:, :] != cls)).sum()
            + (mask[1:, :] & (a[:-1, :] != cls)).sum())
        n_edge = int(contrast_h + contrast_v)
        edge_count_total += n_edge
        edge_m = n_edge * (cell_size + cs_x) / 2.0
        pland = 100.0 * patch_cells / float(valid.sum())
        class_rows.append({
            "class_value": float(cls) if float(cls).is_integer()
            else round(float(cls), 6),
            "n_cells": patch_cells,
            "area_ha": round(patch_cells * cell_area_ha, 6),
            "pland_pct": round(pland, 4),
            "n_patches": n_patch,
            "patch_density_per_100ha": round(
                n_patch / (patch_cells * cell_area_ha) * 100.0, 6)
            if patch_cells else 0.0,
            "lpi_pct": round(100.0 * _largest_patch_cells(
                mask, structure) / float(valid.sum()), 4),
            "edge_density_m_per_ha": round(
                edge_m / (patch_cells * cell_area_ha), 6)
            if patch_cells else 0.0,
        })

    # 景观级多样性
    props = np.asarray([r["pland_pct"] / 100.0 for r in class_rows])
    props = props[props > 0]
    shdi = float(-np.sum(props * np.log(props)))
    sidi = float(1.0 - np.sum(props ** 2))

    # 景观级边缘密度：总对比边（类 vs 类 + 类 vs nodata/边界）
    edge_m_total = edge_count_total * (cell_size + cs_x) / 2.0
    meta = {
        "algorithm": "fragstats_4n_landscape_metrics",
        "connectivity": 4,
        "cell_size": float(cell_size),
        "cell_size_x": cs_x,
        "cell_area_ha": round(cell_area_ha, 8),
        "total_area_ha": round(total_area_ha, 6),
        "n_classes": int(classes.size),
        "edge_density_m_per_ha": round(edge_m_total / total_area_ha, 6),
        "shdi": round(shdi, 6),
        "sidi": round(sidi, 6),
        "pr": int(classes.size),
        "edge_policy": "class vs differing class / nodata / raster boundary",
    }
    table = {"classes": class_rows}
    return table, meta


def _largest_patch_cells(mask: np.ndarray, structure: np.ndarray) -> int:
    """最大斑块像元数（4 邻接连通分量）。"""
    from scipy import ndimage

    labeled, n = ndimage.label(mask, structure=structure)
    if n == 0:
        return 0
    counts = np.bincount(labeled.ravel())
    return int(counts[1:].max()) if counts.size > 1 else 0
