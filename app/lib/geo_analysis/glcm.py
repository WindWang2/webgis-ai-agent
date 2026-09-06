"""GLCM 纹理特征 —— 纯 numpy 手工实现（Foundation V2 · A6）。

灰度共生矩阵（Gray-Level Co-occurrence Matrix，Haralick 1973）窗口化
纹理：量化 → 固定偏移集共生计数 → 窗口内归一化 GLCM 属性。

实现约定（全部披露进 meta / 证据块）：

- **量化**：有效像元的 2-98 分位数线性拉伸到 ``levels`` 档
  （8/16/32/64），越界值钳入端点档（分位数归一化，非 min/max——
  抗离群；量化方案披露）；
- **对称约定 P + Pᵀ**：每对 (a,b) 与 (b,a) 同计（Haralick 常用对称
  形式）——偏移方向的正负号因此不敏感（d=1；±d 同线）；
- **偏移集**：d=1，"0"/"45"/"90"/"135" 单方向或 "all4" 全集；
  多方向输出 = 逐方向属性的**均值**（graycoprops 惯例，NaN 方向
  不稀释均值；全方向退化 → NaN）；
- **属性**（对归一化 P 求和；entropy 用自然对数，披露）：
  contrast、dissimilarity、homogeneity、asm（energy=√asm）、entropy、
  mean、variance、correlation（零方差窗口 → NaN，诚实披露不伪造）；
- **nodata 感知**：任一像元无效的共生对被剔除；窗口内有效对数为 0
  → 该窗口全部属性 NaN；
- **规模守卫（先估算后分配）**：H·W·window²·n_offsets ≤ 64M 操作
  估算，超限抛 ``ResourceScaleMismatch``；逐行分块 bincount 把峰值
  内存钉在常数预算内（块内全量，不做流式承诺）；
- scikit-image **不是**声明依赖——本模块零第三方依赖（numpy only），
  确定性（bincount 顺序求和）、无随机成分。
"""
from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Tuple, Union

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

from app.lib.gis.scientific_errors import (
    NoValidObservations,
    ResourceScaleMismatch,
)

__all__ = [
    "GLCM_PROPERTIES",
    "GLCM_DIRECTIONS",
    "GLCM_LEVELS",
    "GLCM_WINDOW_SIZES",
    "GLCM_OPS_LIMIT",
    "window_glcm_counts",
    "glcm_texture",
]

GLCM_PROPERTIES = (
    "contrast", "dissimilarity", "homogeneity", "asm", "energy",
    "entropy", "mean", "variance", "correlation",
)
GLCM_DIRECTIONS = ("all4", "0", "45", "90", "135")
GLCM_LEVELS = (8, 16, 32, 64)
GLCM_WINDOW_SIZES = (3, 5, 7)
GLCM_OPS_LIMIT = 64_000_000           # cells·window²·n_offsets 操作估算上界
_CHUNK_HIST_CELLS = 4_000_000         # 块内 bincount 缓冲上界（entries）
_QUANTILE_RANGE = (2.0, 98.0)         # 量化分位窗（有效像元；披露）

# d=1 偏移（dy, dx）。P+Pᵀ 对称 ⇒ (±dy, ±dx) 同线：(−1,1) 规范化为 (1,−1)。
_OFFSETS: Dict[str, Tuple[int, int]] = {
    "0": (0, 1),
    "45": (1, -1),    # 反对角线（右上-左下）
    "90": (1, 0),
    "135": (1, 1),    # 主对角线（左上-右下）
}


def _pair_views(
    windows: np.ndarray, dy: int, dx: int,
) -> Tuple[np.ndarray, np.ndarray]:
    """窗口张量 (..., w, w) 内偏移 (dy,dx) 的 (p, q) 切片视图。"""
    w = windows.shape[-1]
    rs = slice(0, w - dy) if dy >= 0 else slice(-dy, w)
    qr = slice(dy, w) if dy >= 0 else slice(0, w + dy)
    cs = slice(0, w - dx) if dx >= 0 else slice(-dx, w)
    qc = slice(dx, w) if dx >= 0 else slice(0, w + dx)
    return windows[..., rs, cs], windows[..., qr, qc]


def window_glcm_counts(
    matrix: np.ndarray, levels: int, dy: int = 0, dx: int = 1,
) -> np.ndarray:
    """单个（已量化）方阵窗口的对称共生计数矩阵 P+Pᵀ（hand-golden 用）。

    Args:
        matrix: w×w 量化矩阵（值 ∈ [0, levels)）。
        levels: 量化档数（计数矩阵形状 levels×levels）。
        dy, dx: 共生偏移（默认 (0,1) 水平）。

    Returns:
        levels×levels int64 计数矩阵（对称：c + cᵀ）。
    """
    m = np.asarray(matrix, dtype=np.int64)
    w = m.shape[0]
    i_rs = max(0, -dy)
    i_re = w - max(0, dy)
    j_cs = max(0, -dx)
    j_ce = w - max(0, dx)
    p = m[i_rs:i_re, j_cs:j_ce]
    q = m[i_rs + dy:i_re + dy, j_cs + dx:j_ce + dx]
    counts = np.zeros((levels, levels), dtype=np.int64)
    np.add.at(counts, (p.ravel(), q.ravel()), 1)
    return counts + counts.T


def _resolve_properties(
    properties: Union[str, Sequence[str]],
) -> List[str]:
    """属性参数解析（"all" 或子集；按 GLCM_PROPERTIES 正典序输出）。"""
    if isinstance(properties, str):
        if properties.strip().lower() == "all":
            return list(GLCM_PROPERTIES)
        requested = [s.strip().lower() for s in properties.split(",") if s.strip()]
    else:
        requested = [str(s).strip().lower() for s in properties]
    if not requested:
        raise ValueError(
            f"properties 不能为空；valid: {list(GLCM_PROPERTIES)} 或 'all'")
    unknown = [p for p in requested if p not in GLCM_PROPERTIES]
    if unknown:
        raise ValueError(
            f"unsupported GLCM properties {unknown}; "
            f"valid: {list(GLCM_PROPERTIES)} 或 'all'")
    seen: List[str] = []
    for p in GLCM_PROPERTIES:
        if p in requested:
            seen.append(p)
    return seen


def _quantize(
    arr: np.ndarray, valid: np.ndarray, levels: int,
) -> Tuple[np.ndarray, Tuple[float, float]]:
    """2-98 分位数线性量化到 [0, levels)（无效像元置 0，由掩膜剔除）。"""
    vals = arr[valid]
    if vals.size == 0:
        raise NoValidObservations(
            "无有效像元：GLCM 量化无法进行",
            correction_hint="检查 nodata 设置或输入网格")
    lo, hi = (float(x) for x in np.percentile(vals, _QUANTILE_RANGE))
    q = np.zeros(arr.shape, dtype=np.int64)
    if hi - lo <= 1e-15:
        # 常数场：全图同一档（诚实路径——correlation 将以 NaN 披露退化）。
        q[valid] = 0
        return q, (lo, hi)
    scaled = (arr - lo) / (hi - lo) * levels
    qf = np.floor(np.where(valid, scaled, 0.0))
    q = np.clip(qf, 0, levels - 1).astype(np.int64)
    q[~valid] = 0
    return q, (lo, hi)


def _properties_from_counts(
    counts: np.ndarray, levels: int,
) -> Dict[str, np.ndarray]:
    """块内 (nw, L, L) 对称计数 → 逐窗属性（N=0 窗口全 NaN）。"""
    nw = counts.shape[0]
    n_pairs = counts.sum(axis=(1, 2))
    with np.errstate(divide="ignore", invalid="ignore"):
        p_mat = counts / n_pairs[:, None, None]

    ii, jj = np.mgrid[0:levels, 0:levels]
    diff = (ii - jj).astype(float)
    p_flat = p_mat.reshape(nw, -1)
    diff_flat = diff.ravel()
    ii_flat = ii.ravel().astype(float)
    jj_flat = jj.ravel().astype(float)

    with np.errstate(divide="ignore", invalid="ignore"):
        contrast = (p_flat * (diff_flat ** 2)).sum(axis=1)
        dissimilarity = (p_flat * np.abs(diff_flat)).sum(axis=1)
        homogeneity = (p_flat / (1.0 + diff_flat ** 2)).sum(axis=1)
        asm = (p_flat ** 2).sum(axis=1)
        logp = np.log(p_flat)
        entropy = -np.where(p_flat > 0, p_flat * logp, 0.0).sum(axis=1)
        mean_w = (p_flat * ii_flat).sum(axis=1)     # 对称 ⇒ 行/列边际同均值
        centered = ii_flat - mean_w[:, None]
        variance = (p_flat * centered ** 2).sum(axis=1)
        cross = (p_flat * ii_flat * jj_flat).sum(axis=1)
        correlation = (cross - mean_w ** 2) / variance

    degenerate = ~(n_pairs > 0) | ~np.isfinite(variance) | (variance <= 0)
    correlation = np.where(degenerate, np.nan, correlation)
    nan_rows = ~(n_pairs > 0)
    out = {
        "contrast": contrast, "dissimilarity": dissimilarity,
        "homogeneity": homogeneity, "asm": asm, "entropy": entropy,
        "mean": mean_w, "variance": variance, "correlation": correlation,
    }
    for name in out:
        out[name] = np.where(nan_rows, np.nan, out[name])
    out["energy"] = np.sqrt(out["asm"])
    return out


def glcm_texture(
    arr: np.ndarray,
    *,
    window: int = 3,
    levels: int = 16,
    directions: str = "all4",
    properties: Union[str, Sequence[str]] = "all",
    nodata: Optional[float] = None,
) -> Dict[str, object]:
    """窗口化 GLCM 纹理属性（Haralick 1973；实现约定见模块 docstring）。

    Args:
        arr: 2D 栅格（任意连续值；内部 2-98 分位数量化）。
        window: 奇数窗口 ∈ {3,5,7}。
        levels: 量化档数 ∈ {8,16,32,64}。
        directions: "all4"（四方向均值）或 "0"/"45"/"90"/"135" 单方向。
        properties: "all" 或逗号分隔/序列子集（contrast,dissimilarity,
            homogeneity,asm,energy,entropy,mean,variance,correlation）。
        nodata: 标量哨兵值；NaN/Inf 自动视为无效。

    Returns:
        dict: properties（{属性名: 2D float 数组}）、window、levels、
        directions_used、quantiles（量化分位窗）、meta（约定披露）。
    """
    if window not in GLCM_WINDOW_SIZES:
        raise ValueError(
            f"window 必须是 {list(GLCM_WINDOW_SIZES)} 之一，got {window!r}")
    if levels not in GLCM_LEVELS:
        raise ValueError(
            f"levels 必须是 {list(GLCM_LEVELS)} 之一，got {levels!r}")
    dir_key = (directions or "all4").lower()
    if dir_key not in GLCM_DIRECTIONS:
        raise ValueError(
            f"unsupported directions '{directions}'; "
            f"valid: {list(GLCM_DIRECTIONS)}")
    offsets = ([_OFFSETS[d] for d in ("0", "45", "90", "135")]
               if dir_key == "all4" else [_OFFSETS[dir_key]])
    props = _resolve_properties(properties)

    plane = np.asarray(arr, dtype=float)
    if plane.ndim != 2:
        raise ValueError(f"GLCM 输入必须是 2D 数组，got ndim={plane.ndim}")
    h, w = plane.shape
    cells = int(h) * int(w)
    est_ops = cells * window * window * len(offsets)
    if est_ops > GLCM_OPS_LIMIT:
        raise ResourceScaleMismatch(
            f"GLCM 操作规模超限：H×W={h}×{w} × window²={window}² × "
            f"方向数={len(offsets)} ≈ {est_ops} 次共生对操作",
            estimated=f"~{est_ops / 1e6:.1f}M pair-ops",
            limit=f"≤{GLCM_OPS_LIMIT} pair-ops",
            correction_hint="降低窗口/方向数，或分块处理后聚合",
        )

    valid = np.isfinite(plane)
    if nodata is not None:
        valid &= plane != float(nodata)
    q, quantiles = _quantize(plane, valid, levels)

    pad = window // 2
    qpad = np.pad(q, pad, mode="constant", constant_values=0)
    vpad = np.pad(valid, pad, mode="constant", constant_values=False)
    wins_q = sliding_window_view(qpad, (window, window))      # (H, W, w, w)
    wins_v = sliding_window_view(vpad, (window, window))
    _, n_cols = wins_q.shape[:2]

    chunk_windows = max(1, _CHUNK_HIST_CELLS // (levels * levels))
    rows_per_chunk = max(1, chunk_windows // max(1, n_cols))

    acc_sum: Dict[str, np.ndarray] = {p: np.zeros((h, w)) for p in props}
    acc_cnt: Dict[str, np.ndarray] = {p: np.zeros((h, w)) for p in props}

    for dy, dx in offsets:
        pv, qv = _pair_views(wins_q, dy, dx)
        pvm, qvm = _pair_views(wins_v, dy, dx)
        n_pair_pos = pv.shape[-2] * pv.shape[-1]
        for r0 in range(0, h, rows_per_chunk):
            r1 = min(r0 + rows_per_chunk, h)
            nw = (r1 - r0) * n_cols
            p_block = pv[r0:r1].reshape(nw, n_pair_pos)
            q_block = qv[r0:r1].reshape(nw, n_pair_pos)
            both = (pvm[r0:r1].reshape(nw, n_pair_pos)
                    & qvm[r0:r1].reshape(nw, n_pair_pos))
            win_idx = np.repeat(
                np.arange(nw, dtype=np.int64), n_pair_pos,
            ).reshape(nw, n_pair_pos)
            flat = (win_idx[both] * levels * levels
                    + p_block[both] * levels + q_block[both])
            counts = np.bincount(
                flat, minlength=nw * levels * levels).reshape(
                nw, levels, levels)
            counts = counts + counts.transpose(0, 2, 1)     # P+Pᵀ 对称约定
            block_props = _properties_from_counts(counts, levels)
            shape2 = (r1 - r0, n_cols)
            for name in props:
                vals = block_props[name].reshape(shape2)
                finite = np.isfinite(vals)
                acc_sum[name][r0:r1] += np.where(finite, vals, 0.0)
                acc_cnt[name][r0:r1] += finite
        del pv, qv, pvm, qvm

    # 多方向 = NaN 感知均值（graycoprops 惯例；全方向退化 → NaN）。
    result: Dict[str, np.ndarray] = {}
    with np.errstate(divide="ignore", invalid="ignore"):
        for name in props:
            result[name] = np.where(
                acc_cnt[name] > 0,
                acc_sum[name] / np.where(acc_cnt[name] > 0, acc_cnt[name], 1),
                np.nan,
            )

    disclosure = (
        f"量化={levels} 档（有效像元 2-98 分位线性拉伸，越界钳端点）；"
        "对称约定 P+Pᵀ（±d 同线）；d=1 偏移"
        f"（{dir_key}，多方向输出=逐方向属性 NaN 感知均值）；"
        "entropy 为自然对数；零方差/无有效对窗口 → NaN（不伪造）；"
        "纯 numpy 手工实现（scikit-image 非声明依赖）；确定性无随机成分")
    meta: Dict[str, object] = {
        "window": window,
        "levels": levels,
        "directions": dir_key,
        "offsets": [list(o) for o in offsets],
        "quantile_range": list(_QUANTILE_RANGE),
        "disclosure": disclosure,
    }
    return {
        "properties": result,
        "window": window,
        "levels": levels,
        "directions_used": dir_key,
        "quantiles": quantiles,
        "meta": meta,
    }
