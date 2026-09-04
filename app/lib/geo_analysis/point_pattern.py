"""点格局分析（ADR-0099 spatial-science VNext）。

- ``ripley_k``：同质 Ripley's K（Ripley 1976），矩形窗各向同性
  （isotropic）边缘校正；L(r)=√(K/π) 与 CSR 参考 πr² 随 r 网格输出。
- ``quadrat_test``：m×n 样方 χ² 离散检验 + 方差均值比（VMR）解读。

两函数吃**投影后的米制 xy 数组**——经纬度（度）输入直接抛
``InvalidCRS``（提示先投影；工具层会自动选局部 UTM）。全部确定性：
无随机成分，r 网格/样方划分只由参数与数据窗口决定。
"""
from __future__ import annotations

from typing import Dict, List, Optional, Sequence

import numpy as np
from scipy.stats import chi2 as chi2_dist

from app.lib.gis.crs_safety import classify_crs
from app.lib.gis.scientific_errors import (
    DegenerateData,
    InsufficientSamples,
    InvalidCRS,
    ResourceScaleMismatch,
)

# Ripley K 是 O(n²) 级成对统计（r_max 内的成对距离）——超过该上限诚实拒绝
# （ResourceScaleMismatch 先于 OOM）。
_MAX_RIPLEY_OBSERVATIONS = 20000

# 1/w 的下限：角落处 inside-fraction 可以很小，但 1/w 必须有界。
_MIN_INSIDE_FRACTION = 1e-9


def _assert_metric_xy(xy: np.ndarray, crs: Optional[str]) -> None:
    """Metric coordinates are a methodological requirement, not a hint.

    A declared geographic CRS (degrees) is rejected outright — Ripley's K /
    quadrat areas computed in degrees are meaningless. Unknown/absent CRS
    proceeds (honest default: the caller contract says "projected xy").
    """
    if crs and classify_crs(str(crs)) == "geographic":
        raise InvalidCRS(
            f"coordinates are geographic (degrees) under CRS {crs}; "
            "second-order distance statistics need metric units",
            correction_hint=(
                "reproject to a metric CRS first (e.g. local UTM); "
                "the ripley_k_analysis / quadrat_analysis tools do this automatically"
            ),
        )


def _require_window(xy: np.ndarray, window: Optional[Sequence[float]] = None) -> tuple:
    """Analysis window: caller-fixed (xmin, ymin, xmax, ymax) or data bbox.

    A fixed study-area window matters scientifically: a data-derived bbox
    self-normalizes concentration away (the quadrat test could never see
    "everything in one quadrant"). Degenerate (zero-area) windows rejected.
    """
    if window is not None:
        try:
            xmin, ymin, xmax, ymax = (float(v) for v in window)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "window must be (xmin, ymin, xmax, ymax)") from exc
        if not (xmax > xmin and ymax > ymin):
            raise DegenerateData(
                "analysis window has zero extent",
                correction_hint="check the window coordinates (xmin < xmax, ymin < ymax)",
            )
        return xmin, ymin, xmax, ymax
    xmin, ymin = xy.min(axis=0)
    xmax, ymax = xy.max(axis=0)
    if not (xmax > xmin and ymax > ymin):
        raise DegenerateData(
            "analysis window has zero extent (all points coincide on a line/point)",
            correction_hint="check for duplicated or collinear coordinates",
        )
    return float(xmin), float(ymin), float(xmax), float(ymax)


def _isotropic_inside_fraction(
    focal_xy: np.ndarray,
    window: tuple,
    d: np.ndarray,
) -> np.ndarray:
    """Ripley isotropic edge correction for a rectangular window.

    For each (focal point, distance) pair, the fraction of the circle of
    radius ``d`` that lies inside the window. Per crossed edge the outside
    arc is 2·arccos(dist_e/d); near a corner the two edge arcs overlap by
    arccos(a1) − arcsin(a2) (a1²+a2² < 1 ⟺ the circle passes the corner) —
    inclusion–exclusion over at most two adjacent edges because r_max is
    capped below half the smaller window span.
    """
    xmin, ymin, xmax, ymax = window
    d = np.maximum(np.asarray(d, dtype=float), 1e-12)
    outside = np.zeros_like(d)

    dists = {
        "left": focal_xy[:, 0] - xmin,
        "right": xmax - focal_xy[:, 0],
        "bottom": focal_xy[:, 1] - ymin,
        "top": ymax - focal_xy[:, 1],
    }
    angles = {}
    for name, dist_e in dists.items():
        crossed = d > dist_e
        a = np.clip(dist_e / d, -1.0, 1.0)
        angles[name] = np.where(crossed, 2.0 * np.arccos(a), 0.0)
        outside += angles[name]

    # Corner overlaps: adjacent edge pairs only (r_max < half min span makes
    # opposite-edge crossings impossible).
    for e1, e2 in (("left", "bottom"), ("left", "top"),
                   ("right", "bottom"), ("right", "top")):
        a1 = dists[e1] / d
        a2 = dists[e2] / d
        beyond_corner = (a1 * a1 + a2 * a2) < 1.0
        overlap = np.arccos(np.clip(a1, -1.0, 1.0)) - np.arcsin(np.clip(a2, -1.0, 1.0))
        outside -= np.where(beyond_corner & (overlap > 0.0), overlap, 0.0)

    inside = 1.0 - outside / (2.0 * np.pi)
    return np.clip(inside, _MIN_INSIDE_FRACTION, 1.0)


def ripley_k(
    xy: np.ndarray,
    crs: Optional[str] = None,
    n_steps: int = 10,
    max_distance_ratio: float = 0.25,
    window: Optional[Sequence[float]] = None,
) -> Dict:
    """Homogeneous Ripley's K with isotropic edge correction (Ripley 1976).

    K̂(r) = A/(n(n−1)) · Σ_{i≠j} I(d_ij ≤ r)/w_ij，w_ij 为焦点 i、距离
    d_ij 处圆周落在矩形窗内的比例（各向同性校正）。r 网格从
    r_max/n_steps 到 r_max 等距 n_steps 步，r_max = max_distance_ratio ×
    min(窗宽, 窗高)（比例上限 0.5 = 半窗，边缘校正不再可信）。
    ``window`` 可固定研究域（xmin, ymin, xmax, ymax）；缺省用数据 bbox。

    Deterministic：无模拟信封（显著性检验留给调用方模拟，如测试中的
    固定种子 CSR 包络）；输出为有界 r 网格上的 K / L / CSR 参考。
    """
    xy = np.asarray(xy, dtype=float)
    if xy.ndim != 2 or xy.shape[1] != 2:
        raise ValueError(f"xy must be an (n, 2) coordinate array (got shape {xy.shape})")
    _assert_metric_xy(xy, crs)
    n_steps = int(n_steps)
    if not 4 <= n_steps <= 32:
        raise ValueError(f"n_steps must be within 4..32 (got {n_steps})")
    max_distance_ratio = float(max_distance_ratio)
    if not 0.05 <= max_distance_ratio <= 0.5:
        raise ValueError(
            f"max_distance_ratio must be within 0.05..0.5 (got {max_distance_ratio})")
    n = len(xy)
    if n < 10:
        raise InsufficientSamples(
            f"Ripley's K needs at least 10 points for a usable r-grid (got {n})",
            correction_hint="add observations; for coarse clustering use nearest_neighbor or quadrat_analysis",
        )
    if n > _MAX_RIPLEY_OBSERVATIONS:
        raise ResourceScaleMismatch(
            f"Ripley's K is an O(n²) pair statistic at n={n}",
            estimated=f"{n} points",
            limit=f"{_MAX_RIPLEY_OBSERVATIONS} points",
            correction_hint="aggregate to a grid first (h3_binning) or sample down",
        )

    window = _require_window(xy, window)
    xmin, ymin, xmax, ymax = window
    width, height = xmax - xmin, ymax - ymin
    area = width * height
    r_max = max_distance_ratio * min(width, height)
    if r_max <= 0:
        raise DegenerateData("r_max degenerated to 0; window extent too small")
    r_grid = np.linspace(r_max / n_steps, r_max, n_steps)

    from scipy.spatial import cKDTree

    tree = cKDTree(xy)
    coo = tree.sparse_distance_matrix(
        tree, max_distance=r_max, output_type="coo_matrix")
    keep = coo.row != coo.col
    d = coo.data[keep]
    focal = coo.row[keep]

    if d.size:
        order = np.argsort(d, kind="stable")
        d_sorted = d[order]
        # w depends on the FOCAL point and the distance only.
        w_inv = 1.0 / _isotropic_inside_fraction(xy[focal[order]], window, d_sorted)
        cum = np.concatenate(([0.0], np.cumsum(w_inv)))
        counts = np.searchsorted(d_sorted, r_grid, side="right")
        k_vals = area / (n * (n - 1)) * cum[counts]
    else:
        k_vals = np.zeros(n_steps)

    csr_vals = np.pi * r_grid**2
    l_vals = np.sqrt(k_vals / np.pi)

    above = int(np.sum(k_vals > csr_vals))
    if above == n_steps:
        tendency = "K(r) 高于 CSR 参考于全部半径（聚集倾向）"
    elif above == 0:
        tendency = "K(r) 低于 CSR 参考于全部半径（规则/均匀倾向）"
    else:
        tendency = f"K(r) 在 {above}/{n_steps} 个半径上高于 CSR 参考"

    summary = (
        f"Ripley's K (isotropic edge correction, n={n}, r_max={r_max:.1f}): "
        f"{tendency}。描述性对比——未做显著性检验；显著性需固定种子 CSR 模拟包络。"
    )

    return {
        "r": [round(float(v), 4) for v in r_grid],
        "K": [round(float(v), 4) for v in k_vals],
        "L": [round(float(v), 4) for v in l_vals],
        "csr_K": [round(float(v), 4) for v in csr_vals],
        "n": int(n),
        "r_max": round(float(r_max), 4),
        "window": [round(float(v), 4) for v in window],
        "area": round(float(area), 4),
        "edge_correction": "isotropic (rectangular window, Ripley 1976)",
        "estimator": "K(r) = A/(n(n-1)) * sum_{i!=j} I(d_ij<=r)/w_ij",
        "tendency": tendency,
        "summary": summary,
    }


def quadrat_test(
    xy: np.ndarray,
    crs: Optional[str] = None,
    grid_rows: int = 4,
    grid_cols: int = 4,
    window: Optional[Sequence[float]] = None,
) -> Dict:
    """m×n 样方 χ² 离散检验（期望 N/(mn)，df = mn−1）+ VMR 解读。

    VMR（方差/均值比）> 1 聚集、< 1 均匀；χ² 检验给出对 CSR 的显著性。
    ``window`` 固定研究域（xmin, ymin, xmax, ymax）——聚集判定依赖研究域
    的独立性，缺省退回数据 bbox（此时"集中在四分之一窗内"这类格局会被
    bbox 自归一化掉，解读需谨慎）。
    期望频数 < 5 时 χ² 近似变差 —— 以 ``chi2_approx_warning`` 披露而不是
    静默给出 p 值。
    """
    xy = np.asarray(xy, dtype=float)
    if xy.ndim != 2 or xy.shape[1] != 2:
        raise ValueError(f"xy must be an (n, 2) coordinate array (got shape {xy.shape})")
    _assert_metric_xy(xy, crs)
    grid_rows = int(grid_rows)
    grid_cols = int(grid_cols)
    if not 2 <= grid_rows <= 10 or not 2 <= grid_cols <= 10:
        raise ValueError(
            f"grid_rows/grid_cols must be within 2..10 "
            f"(got {grid_rows}×{grid_cols})")
    n = len(xy)
    if n < 4:
        raise InsufficientSamples(
            f"quadrat test needs at least 4 points (got {n})",
            correction_hint="add observations or reduce the quadrat grid",
        )

    window = _require_window(xy, window)
    xmin, ymin, xmax, ymax = window
    if window is not None:
        # Fixed study area: points outside it would be silently dropped by
        # histogram2d — refuse instead (a window/points mismatch, not CSR).
        inside = (
            (xy[:, 0] >= xmin) & (xy[:, 0] <= xmax)
            & (xy[:, 1] >= ymin) & (xy[:, 1] <= ymax)
        )
        if not inside.all():
            raise ValueError(
                f"{int((~inside).sum())} points lie outside the fixed window "
                f"[{xmin}, {ymin}, {xmax}, {ymax}] — the quadrat test would "
                "silently drop them; fix the window or the data"
            )
    counts, _, _ = np.histogram2d(
        xy[:, 0], xy[:, 1],
        bins=(grid_cols, grid_rows),
        range=[[xmin, xmax], [ymin, ymax]],
    )
    counts = counts.T.ravel()  # row-major (rows × cols) quadrat counts
    n_cells = grid_rows * grid_cols
    expected = n / n_cells
    if expected <= 0:
        raise DegenerateData("expected quadrat count degenerated to 0")

    chi2_stat = float(np.sum((counts - expected) ** 2 / expected))
    df = n_cells - 1
    p_value = float(chi2_dist.sf(chi2_stat, df))

    counts_var = float(np.var(counts, ddof=1))
    vmr = counts_var / expected if expected > 0 else 0.0

    if p_value < 0.05:
        pattern = "clustered" if vmr > 1.0 else "regular"
    else:
        pattern = "random"

    if pattern == "clustered":
        interp = (
            f"样方计数显著偏离均匀（χ²={chi2_stat:.2f}, df={df}, p={p_value:.4f}），"
            f"VMR={vmr:.2f}>1：点呈聚集分布。"
        )
    elif pattern == "regular":
        interp = (
            f"样方计数显著偏离均匀（χ²={chi2_stat:.2f}, df={df}, p={p_value:.4f}），"
            f"VMR={vmr:.2f}<1：点呈规则/均匀分布。"
        )
    else:
        interp = (
            f"未拒绝完全空间随机（χ²={chi2_stat:.2f}, df={df}, p={p_value:.4f}），"
            f"VMR={vmr:.2f}：与 CSR 一致。"
        )
    summary = f"Quadrat test ({grid_rows}×{grid_cols}, n={n}): {interp}"

    out: Dict = {
        "chi2": chi2_stat,
        "df": df,
        "p_value": p_value,
        "pattern": pattern,
        "variance_mean_ratio": round(vmr, 6),
        "expected_per_quadrat": round(expected, 6),
        "grid": [grid_rows, grid_cols],
        "n": int(n),
        "window": [round(float(v), 4) for v in window],
        "interpretation": interp,
        "summary": summary,
    }
    if expected < 5:
        out["chi2_approx_warning"] = (
            f"期望频数 {expected:.2f} < 5：χ² 近似偏乐观，p 值仅作参考"
            "（可减小网格或增加样本）"
        )
    return out


__all__: List[str] = ["ripley_k", "quadrat_test"]
