"""点格局分析（ADR-0099 spatial-science VNext；Foundation V2 · A3 扩展）。

一阶/二阶点格局统计（全部吃**投影后的米制 xy 数组**——经纬度（度）
输入直接抛 ``InvalidCRS``，工具层会自动选局部 UTM）：

- ``ripley_k``：同质 Ripley's K（Ripley 1976），矩形窗各向同性
  （isotropic）边缘校正；L(r)=√(K/π) 与 CSR 参考 πr² 随 r 网格输出。
  可选 ``envelopes``（Foundation V2）：固定种子 42 的 CSR 模拟包络 +
  逐半径秩双侧 p 值（缺省 0 = 关，输出键与既有调用方逐位不变）。
- ``quadrat_test``：m×n 样方 χ² 离散检验 + 方差均值比（VMR）解读。
- ``g_f_j_functions``：G（最近邻距离 CDF）/ F（空空间函数，确定性
  低差异查询格）/ J=(1−G)/(1−F)（van Lieshout–Baddeley 1996）；
  原始（无边缘校正）估计 + 固定种子 CSR 包络做显著性。
- ``pcf``：成对相关函数 g(r)=K′(r)/(2πr)（K 的离散导数 +
  Epanechnikov 平滑）；CSR 参考 g=1；可选固定种子 CSR 包络
  （sup|g−1| 秩检验）。
- ``cross_k``：双变量 K12（与单变量同款各向同性边缘校正）；
  random-labelling 置换包络（Besag 1977），max|K12−πr²| 秩 p 值。
- ``knox_test``：Knox 时空交互检验（Knox 1964）——空间邻近对 × 时间
  邻近对的联合计数 vs 独立即期望 E=2·S·T/(n(n−1))；时间置换 p 值
  （单侧 greater，+1 校正）。空间对经 cKDTree.query_pairs 稀疏化
  （无 dense n×n）。

确定性策略：所有模拟/置换统一固定种子 42（``random_seed_policy=
"fixed_seed"``），同输入必同输出；显著性只经固定种子蒙特卡洛给出。
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
from scipy.stats import chi2 as chi2_dist

from app.lib.gis.crs_safety import classify_crs
from app.lib.gis.scientific_errors import (
    DegenerateData,
    InsufficientSamples,
    InvalidCRS,
    ResourceScaleMismatch,
    UnsupportedMethod,
)

# Ripley K 是 O(n²) 级成对统计（r_max 内的成对距离）——超过该上限诚实拒绝
# （ResourceScaleMismatch 先于 OOM）。
_MAX_RIPLEY_OBSERVATIONS = 20000
_MAX_RIPLEY_PAIRS = 50_000_000   # ~0.8 GB COO; estimate-before-allocate

# 1/w 的下限：角落处 inside-fraction 可以很小，但 1/w 必须有界。
_MIN_INSIDE_FRACTION = 1e-9

# ── Foundation V2 (A3)：模拟包络 / Knox 的共享常量 ────────────────────
_FIXED_SEED = 42                 # 所有模拟/置换的固定种子（fixed_seed 策略）
_MAX_ENVELOPES = 499             # 包络/置换次数上限（p 分辨率 1/500）
_MAX_KNOX_OBSERVATIONS = 20000   # Knox 上限（空间对走稀疏，仍设诚实上限）
_MAX_F_QUERY_GRID = 2000         # F 空空间函数的查询格点上限（4·n 封顶）


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


def _k_curve(
    xy: np.ndarray,
    area: float,
    window: tuple,
    r_grid: np.ndarray,
    tree=None,
) -> np.ndarray:
    """Raw isotropic-corrected K on ``r_grid`` (the shared K estimator).

    Single source of truth for the K estimator: ``ripley_k`` itself, its CSR
    simulation envelopes, and ``pcf`` (which differentiates K) all call THIS
    function — envelope simulations never re-implement the estimator.
    """
    from scipy.spatial import cKDTree

    n = len(xy)
    if tree is None:
        tree = cKDTree(xy)
    coo = tree.sparse_distance_matrix(
        tree, max_distance=float(r_grid[-1]), output_type="coo_matrix")
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
        return area / (n * (n - 1)) * cum[counts]
    return np.zeros(len(r_grid))


def _csr_envelope_curves(curve_fn, n: int, window: tuple, draws: int,
                         n_steps: int, seed: int = _FIXED_SEED) -> np.ndarray:
    """Fixed-seed homogeneous-Poisson (CSR) simulation curves.

    ``curve_fn(sim_xy) -> (n_steps,)`` is called on each synthetic CSR set
    (uniform in the same window, same n). Deterministic: one
    ``np.random.default_rng(seed)`` stream, sequential draws — the same
    estimator runs on the synthetic sets, never a re-implementation.
    """
    rng = np.random.default_rng(seed)
    xmin, ymin, xmax, ymax = window
    out = np.empty((draws, n_steps))
    for i in range(draws):
        sim_xy = np.column_stack([
            rng.uniform(xmin, xmax, n),
            rng.uniform(ymin, ymax, n),
        ])
        out[i] = curve_fn(sim_xy)
    return out


def _two_sided_rank_p(observed: float, sim_values: np.ndarray) -> float:
    """Two-sided Monte-Carlo rank p with the +1 correction: bounded in
    [1/(D+1), 1]; extreme on either tail counts."""
    greater = int(np.sum(sim_values >= observed))
    lesser = int(np.sum(sim_values <= observed))
    return float(min(1.0, 2.0 * (min(greater, lesser) + 1) / (len(sim_values) + 1)))


def ripley_k(
    xy: np.ndarray,
    crs: Optional[str] = None,
    n_steps: int = 10,
    max_distance_ratio: float = 0.25,
    window: Optional[Sequence[float]] = None,
    envelopes: int = 0,
) -> Dict:
    """Homogeneous Ripley's K with isotropic edge correction (Ripley 1976).

    K̂(r) = A/(n(n−1)) · Σ_{i≠j} I(d_ij ≤ r)/w_ij，w_ij 为焦点 i、距离
    d_ij 处圆周落在矩形窗内的比例（各向同性校正）。r 网格从
    r_max/n_steps 到 r_max 等距 n_steps 步，r_max = max_distance_ratio ×
    min(窗宽, 窗高)（比例上限 0.5 = 半窗，边缘校正不再可信）。
    ``window`` 可固定研究域（xmin, ymin, xmax, ymax）；缺省用数据 bbox。

    Foundation V2：可选 ``envelopes``（1..499）——固定种子 42 的同质
    Poisson（CSR）模拟包络：输出逐半径 p5/p50/p95 包络与秩双侧 p 值；
    缺省 0 = 关，输出键与既有调用方逐位不变。同一 K 估计器
    （``_k_curve``）跑在合成 CSR 集上——估计器不重复实现。
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
    envelopes = int(envelopes)
    if envelopes and not 1 <= envelopes <= _MAX_ENVELOPES:
        raise ValueError(
            f"envelopes must be 0 (off) or within 1..{_MAX_ENVELOPES} (got {envelopes})")
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

    coo_budget_tree = cKDTree(xy)
    # 评审 MAJOR-2：内存由 r_max 内的**配对数**驱动，n 上限不构成 OOM
    # 防线（紧簇 + 大窗 → 数亿配对）。先以 count_neighbors 估算配对
    # 预算（每查询点 O(log n)），超限先拒绝。
    pair_budget = _MAX_RIPLEY_PAIRS
    n_pairs_est = int(coo_budget_tree.count_neighbors(coo_budget_tree, r_max)) - n  # 含自身，去对角
    if n_pairs_est > pair_budget:
        raise ResourceScaleMismatch(
            f"Ripley's K pair budget exceeded: ~{n_pairs_est} pairs within "
            f"r_max={r_max:.1f} m",
            estimated=f"~{n_pairs_est * 16 / 1e9:.2f} GB COO pairs",
            limit=f"{pair_budget} pairs",
            correction_hint="reduce max_distance_ratio, aggregate to a grid "
                            "(h3_binning), or subsample",
        )

    k_vals = _k_curve(xy, area, window, r_grid, tree=coo_budget_tree)

    csr_vals = np.pi * r_grid**2
    l_vals = np.sqrt(k_vals / np.pi)

    out: Dict = {
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
    }

    if envelopes:
        env = _csr_envelope_curves(
            lambda sxy: _k_curve(sxy, area, window, r_grid),
            n=n, window=window, draws=envelopes, n_steps=n_steps,
        )
        env_lo = np.quantile(env, 0.05, axis=0)
        env_mid = np.quantile(env, 0.50, axis=0)
        env_hi = np.quantile(env, 0.95, axis=0)
        p_vals = np.array([
            _two_sided_rank_p(float(k_vals[i]), env[:, i])
            for i in range(n_steps)
        ])
        out.update({
            "envelopes": int(envelopes),
            "envelope_seed": _FIXED_SEED,
            "envelope_K_low": [round(float(v), 4) for v in env_lo],
            "envelope_K_median": [round(float(v), 4) for v in env_mid],
            "envelope_K_high": [round(float(v), 4) for v in env_hi],
            "K_p_values": [round(float(v), 6) for v in p_vals],
        })
        n_sig = int(np.sum(p_vals < 0.05))
        out["tendency"] = (
            f"{tendency_of(k_vals, csr_vals, n_steps)}；"
            f"固定种子 CSR 包络（{envelopes} 次模拟，seed={_FIXED_SEED}）："
            f"{n_sig}/{n_steps} 个半径双侧 p<0.05"
        )
        out["summary"] = (
            f"Ripley's K (isotropic edge correction, n={n}, r_max={r_max:.1f}): "
            f"{out['tendency']}。"
        )
    else:
        out["tendency"] = tendency_of(k_vals, csr_vals, n_steps)
        out["summary"] = (
            f"Ripley's K (isotropic edge correction, n={n}, r_max={r_max:.1f}): "
            f"{out['tendency']}。描述性对比——未做显著性检验；"
            "显著性需固定种子 CSR 模拟包络。"
        )
    return out


def tendency_of(k_vals: np.ndarray, csr_vals: np.ndarray, n_steps: int) -> str:
    """Descriptive CSR comparison phrasing (shared by K outputs)."""
    above = int(np.sum(k_vals > csr_vals))
    if above == n_steps:
        return "K(r) 高于 CSR 参考于全部半径（聚集倾向）"
    if above == 0:
        return "K(r) 低于 CSR 参考于全部半径（规则/均匀倾向）"
    return f"K(r) 在 {above}/{n_steps} 个半径上高于 CSR 参考"


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

    window = _require_window(xy, window)   # 恒非 None（缺省取数据 bbox）
    xmin, ymin, xmax, ymax = window
    if True:
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
    # M1（科学评审修复）：双侧离散检验 —— 单侧上尾在数学上永远到不了
    # "regular" 分支（p<0.05 ⇒ VMR>1）。分散（均匀）备择假设用下尾。
    p_upper = float(chi2_dist.sf(chi2_stat, df))     # 聚集备择
    p_lower = float(chi2_dist.cdf(chi2_stat, df))    # 均匀备择
    p_value = float(min(1.0, 2.0 * min(p_upper, p_lower)))  # 双侧

    counts_var = float(np.var(counts, ddof=1))
    vmr = counts_var / expected if expected > 0 else 0.0

    if p_value < 0.05:
        pattern = "clustered" if vmr > 1.0 else "regular"
    else:
        pattern = "random"

    if pattern == "clustered":
        interp = (
            f"样方计数显著高于均匀离散（χ²={chi2_stat:.2f}, df={df}, "
            f"双侧 p={p_value:.4f}），VMR={vmr:.2f}>1：点呈聚集分布。"
        )
    elif pattern == "regular":
        interp = (
            f"样方计数显著低于均匀离散（χ²={chi2_stat:.2f}, df={df}, "
            f"双侧 p={p_value:.4f}），VMR={vmr:.2f}<1：点呈规则/均匀分布。"
        )
    else:
        interp = (
            f"未拒绝完全空间随机（χ²={chi2_stat:.2f}, df={df}, "
            f"双侧 p={p_value:.4f}），VMR={vmr:.2f}：与 CSR 一致。"
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


# ── Foundation V2 (A3)：G / F / J、pcf、cross-K、Knox 时空交互 ─────────


def _gf_curves(xy: np.ndarray, window: tuple, r_grid: np.ndarray,
               query_xy: np.ndarray, data_tree=None) -> Tuple[np.ndarray, np.ndarray]:
    """Raw (edge-uncorrected) G and F curves on ``r_grid``.

    G：数据点最近邻距离的经验 CDF（cKDTree k=2 查询，去掉自身的
    0 距离）；F：查询格点到最近数据点距离的经验 CDF。同一个
    ``query_xy`` 网格同时用于观测与模拟集（配对比较）。
    """
    from scipy.spatial import cKDTree

    if data_tree is None:
        data_tree = cKDTree(xy)
    # G: drop the self-match (k=1 is self at distance 0 for duplicated-free
    # data; k=2 is the true nearest neighbour even under duplicates).
    nn = data_tree.query(xy, k=2)[0][:, 1]
    nn_sorted = np.sort(nn)
    g = np.searchsorted(nn_sorted, r_grid, side="right") / len(xy)
    f_dists = np.sort(data_tree.query(query_xy, k=1)[0])
    f = np.searchsorted(f_dists, r_grid, side="right") / len(query_xy)
    return g, f


def g_f_j_functions(
    xy: np.ndarray,
    crs: Optional[str] = None,
    n_steps: int = 10,
    max_distance_ratio: float = 0.25,
    window: Optional[Sequence[float]] = None,
    envelopes: int = 0,
) -> Dict:
    """G / F / J 距离函数（Diggle 1983；van Lieshout–Baddeley 1996）。

    G(r)：数据点最近邻距离的 CDF；F(r)：空空间函数——确定性低差异
    查询格（``default_rng(42)`` 均匀点，n_f = min(4n, 2000)）到最近
    数据点的距离 CDF；J(r) = (1−G)/(1−F)（CSR 下 J≡1）。

    如实披露：G/F 为**原始估计（无边缘校正）**——矩形窗
    reduced-sample 校正未实现，靠近边界的点低估 G/F；显著性只经
    固定种子（42）CSR 模拟包络给出。F(r)→1 时 J 分母退化 → J 记
    NaN 并在 ``j_undefined_from`` 披露（1−1e−12 阈值）。

    ``envelopes``（1..499，0=关）：同 n、同窗的同质 Poisson 模拟
    G/F 曲线（同一查询格配对），p5/p50/p95 包络 + r_max 处秩双侧
    p 值（+1 校正）。
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
    envelopes = int(envelopes)
    if envelopes and not 1 <= envelopes <= _MAX_ENVELOPES:
        raise ValueError(
            f"envelopes must be 0 (off) or within 1..{_MAX_ENVELOPES} (got {envelopes})")
    n = len(xy)
    if n < 10:
        raise InsufficientSamples(
            f"G/F/J need at least 10 points (got {n})",
            correction_hint="add observations; for tiny samples use nearest_neighbor (NNI)",
        )
    if n > _MAX_RIPLEY_OBSERVATIONS:
        raise ResourceScaleMismatch(
            f"G/F/J are O(n log n) but bounded at n={n}",
            estimated=f"{n} points",
            limit=f"{_MAX_RIPLEY_OBSERVATIONS} points",
            correction_hint="aggregate to a grid first (h3_binning) or sample down",
        )

    window = _require_window(xy, window)
    xmin, ymin, xmax, ymax = window
    area = (xmax - xmin) * (ymax - ymin)
    r_max = max_distance_ratio * min(xmax - xmin, ymax - ymin)
    if r_max <= 0:
        raise DegenerateData("r_max degenerated to 0; window extent too small")
    r_grid = np.linspace(r_max / n_steps, r_max, n_steps)

    n_query = int(min(4 * n, _MAX_F_QUERY_GRID))
    query_rng = np.random.default_rng(_FIXED_SEED)
    query_xy = query_rng.uniform(xmin, xmax, (n_query, 2))
    query_xy[:, 1] = query_rng.uniform(ymin, ymax, n_query)

    g, f = _gf_curves(xy, window, r_grid, query_xy)
    rho = n / area
    csr_gf = 1.0 - np.exp(-np.pi * rho * r_grid**2)

    one_minus_f = 1.0 - f
    j_undefined_from: Optional[int] = None
    degenerate = one_minus_f <= 1e-12
    if degenerate.any():
        j_undefined_from = int(np.argmax(degenerate))
        with np.errstate(divide="ignore", invalid="ignore"):
            j = np.where(degenerate, np.nan, (1.0 - g) / np.maximum(one_minus_f, 1e-12))
    else:
        j = (1.0 - g) / one_minus_f

    out: Dict = {
        "r": [round(float(v), 4) for v in r_grid],
        "G": [round(float(v), 6) for v in g],
        "F": [round(float(v), 6) for v in f],
        "J": ["NaN" if np.isnan(v) else round(float(v), 6) for v in j],
        "csr_G": [round(float(v), 6) for v in csr_gf],
        "csr_F": [round(float(v), 6) for v in csr_gf],
        "csr_J": [1.0] * n_steps,
        "n": int(n),
        "n_query_grid": n_query,
        "r_max": round(float(r_max), 4),
        "window": [round(float(v), 4) for v in window],
        "edge_correction": "none (raw G/F; rectangular-window reduced-sample not implemented)",
        "estimator": "G/F: empirical CDFs of NN / empty-space distances; J=(1-G)/(1-F)",
        "seed_policy": f"fixed_seed (query grid + envelopes, seed={_FIXED_SEED})",
    }
    if j_undefined_from is not None:
        out["j_undefined_from"] = j_undefined_from
        out["j_undefined_note"] = (
            f"F(r)≥1−1e−12 自 r 网格第 {j_undefined_from} 步起：J 分母退化记 NaN"
        )

    tendency = (
        "G(r) 高于 CSR 参考（最近邻偏近，聚集倾向）"
        if g[-1] > csr_gf[-1]
        else "G(r) 不高于 CSR 参考（最近邻距离与 CSR 相容或偏规则）"
    )
    out["tendency"] = tendency
    out["summary"] = (
        f"G/F/J functions (n={n}, r_max={r_max:.1f}, query grid={n_query}): "
        f"{tendency}。原始估计（无边缘校正）；显著性需固定种子 CSR 包络。"
    )

    if envelopes:
        def _curve_fn(sxy: np.ndarray) -> np.ndarray:
            gs, fs = _gf_curves(sxy, window, r_grid, query_xy)
            return np.concatenate([gs, fs])

        env = _csr_envelope_curves(
            _curve_fn, n=n, window=window, draws=envelopes, n_steps=2 * n_steps)
        env_g, env_f = env[:, :n_steps], env[:, n_steps:]
        g_p = _two_sided_rank_p(float(g[-1]), env_g[:, -1])
        f_p = _two_sided_rank_p(float(f[-1]), env_f[:, -1])
        out.update({
            "envelopes": int(envelopes),
            "envelope_seed": _FIXED_SEED,
            "envelope_G_low": [round(float(v), 6) for v in np.quantile(env_g, 0.05, axis=0)],
            "envelope_G_median": [round(float(v), 6) for v in np.quantile(env_g, 0.50, axis=0)],
            "envelope_G_high": [round(float(v), 6) for v in np.quantile(env_g, 0.95, axis=0)],
            "envelope_F_low": [round(float(v), 6) for v in np.quantile(env_f, 0.05, axis=0)],
            "envelope_F_median": [round(float(v), 6) for v in np.quantile(env_f, 0.50, axis=0)],
            "envelope_F_high": [round(float(v), 6) for v in np.quantile(env_f, 0.95, axis=0)],
            "G_p_value": round(g_p, 6),
            "F_p_value": round(f_p, 6),
            "p_method": f"two-sided rank at r_max, +1 correction ({envelopes} CSR draws)",
        })
        out["summary"] += (
            f" 固定种子 CSR 包络：G_p={g_p:.4f}, F_p={f_p:.4f}。"
        )
    return out


def _pcf_from_k(k_vals: np.ndarray, r_grid: np.ndarray,
                bandwidth: float) -> np.ndarray:
    """g(r) = K′(r)/(2πr)：K 的离散导数（np.gradient，端点单侧）+
    r 网格上的 Epanechnikov 平滑（权重 0.75·(1−u²)，|u|≤1）。"""
    dk = np.gradient(k_vals, r_grid)
    g_raw = dk / (2.0 * np.pi * r_grid)
    u = (r_grid[:, None] - r_grid[None, :]) / bandwidth
    w = np.where(np.abs(u) <= 1.0, 0.75 * (1.0 - u * u), 0.0)
    return (w @ g_raw) / w.sum(axis=1)


def pcf(
    xy: np.ndarray,
    crs: Optional[str] = None,
    n_steps: int = 10,
    max_distance_ratio: float = 0.25,
    window: Optional[Sequence[float]] = None,
    bandwidth: float = 0.0,
    envelopes: int = 0,
) -> Dict:
    """成对相关函数 g(r) = K′(r)/(2πr)（Illian et al. 2008）。

    由同款各向同性校正 K（``_k_curve``）的离散导数 + Epanechnikov
    平滑得到；``bandwidth``（r 单位，米）缺省 0 = 一个 r 步宽
    （r_max/n_steps，自动值在输出中披露）。CSR 参考 g≡1；g>1 聚集、
    g<1 规则。``envelopes``（1..499，0=关）：固定种子 42 的 CSR 模拟
    （同一 ``_pcf_from_k`` 后处理管线），p5/p50/p95 包络 + sup|g−1|
    秩检验 p 值（DCLF 风格）。
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
    envelopes = int(envelopes)
    if envelopes and not 1 <= envelopes <= _MAX_ENVELOPES:
        raise ValueError(
            f"envelopes must be 0 (off) or within 1..{_MAX_ENVELOPES} (got {envelopes})")
    bandwidth = float(bandwidth)
    if bandwidth < 0:
        raise ValueError(f"bandwidth must be >= 0 (0 = auto one-step width), got {bandwidth}")
    n = len(xy)
    if n < 10:
        raise InsufficientSamples(
            f"pcf needs at least 10 points (got {n})",
            correction_hint="add observations; for tiny samples use nearest_neighbor (NNI)",
        )
    if n > _MAX_RIPLEY_OBSERVATIONS:
        raise ResourceScaleMismatch(
            f"pcf is an O(n²) pair statistic at n={n}",
            estimated=f"{n} points",
            limit=f"{_MAX_RIPLEY_OBSERVATIONS} points",
            correction_hint="aggregate to a grid first (h3_binning) or sample down",
        )

    window = _require_window(xy, window)
    xmin, ymin, xmax, ymax = window
    area = (xmax - xmin) * (ymax - ymin)
    r_max = max_distance_ratio * min(xmax - xmin, ymax - ymin)
    if r_max <= 0:
        raise DegenerateData("r_max degenerated to 0; window extent too small")
    r_grid = np.linspace(r_max / n_steps, r_max, n_steps)
    bandwidth_resolved = bandwidth if bandwidth > 0 else float(r_grid[1] - r_grid[0])
    if bandwidth_resolved >= r_max:
        raise ValueError(
            f"bandwidth ({bandwidth_resolved:.2f} m) must stay below r_max ({r_max:.2f} m)")

    # 与 ripley_k 同款配对预算预估（先拒绝，后分配）。
    from scipy.spatial import cKDTree

    tree = cKDTree(xy)
    n_pairs_est = int(tree.count_neighbors(tree, r_max)) - n
    if n_pairs_est > _MAX_RIPLEY_PAIRS:
        raise ResourceScaleMismatch(
            f"pcf pair budget exceeded: ~{n_pairs_est} pairs within r_max={r_max:.1f} m",
            estimated=f"~{n_pairs_est * 16 / 1e9:.2f} GB COO pairs",
            limit=f"{_MAX_RIPLEY_PAIRS} pairs",
            correction_hint="reduce max_distance_ratio, aggregate to a grid, or subsample",
        )

    k_vals = _k_curve(xy, area, window, r_grid, tree=tree)
    g_vals = _pcf_from_k(k_vals, r_grid, bandwidth_resolved)

    out: Dict = {
        "r": [round(float(v), 4) for v in r_grid],
        "g": [round(float(v), 6) for v in g_vals],
        "K": [round(float(v), 4) for v in k_vals],
        "csr_g": [1.0] * n_steps,
        "csr_K": [round(float(np.pi * v * v), 4) for v in r_grid],
        "n": int(n),
        "r_max": round(float(r_max), 4),
        "bandwidth": round(bandwidth_resolved, 6),
        "bandwidth_auto": bool(bandwidth <= 0),
        "bandwidth_note": (
            f"bandwidth={'auto（一个 r 步宽）' if bandwidth <= 0 else 'caller 指定'}"
            f" = {bandwidth_resolved:.4f} m，Epanechnikov 平滑"
        ),
        "window": [round(float(v), 4) for v in window],
        "edge_correction": "isotropic (rectangular window, Ripley 1976) via K derivative",
        "estimator": "g(r) = K'(r)/(2*pi*r), Epanechnikov-smoothed on the r grid",
        "seed_policy": f"fixed_seed (envelopes, seed={_FIXED_SEED})" if envelopes else "deterministic",
    }

    peak_i = int(np.argmax(g_vals))
    tendency = (
        f"g(r) 峰值 {g_vals[peak_i]:.2f} @ r={r_grid[peak_i]:.1f} m"
        f"（{'聚集' if g_vals[peak_i] > 1 else '规则'}尺度）"
    )
    out["tendency"] = tendency
    out["summary"] = (
        f"Pair correlation g(r) (n={n}, r_max={r_max:.1f}, "
        f"bandwidth={bandwidth_resolved:.2f} m): {tendency}。"
        "g>1 聚集 / g<1 规则 / g≈1 与 CSR 相容。"
    )

    if envelopes:
        env = _csr_envelope_curves(
            lambda sxy: _pcf_from_k(
                _k_curve(sxy, area, window, r_grid), r_grid, bandwidth_resolved),
            n=n, window=window, draws=envelopes, n_steps=n_steps,
        )
        stat = float(np.max(np.abs(g_vals - 1.0)))
        sim_stats = np.max(np.abs(env - 1.0), axis=1)
        p_upper = (int(np.sum(sim_stats >= stat)) + 1) / (envelopes + 1)
        out.update({
            "envelopes": int(envelopes),
            "envelope_seed": _FIXED_SEED,
            "envelope_g_low": [round(float(v), 6) for v in np.quantile(env, 0.05, axis=0)],
            "envelope_g_median": [round(float(v), 6) for v in np.quantile(env, 0.50, axis=0)],
            "envelope_g_high": [round(float(v), 6) for v in np.quantile(env, 0.95, axis=0)],
            "p_value": round(float(p_upper), 6),
            "p_method": f"sup|g-1| rank vs {envelopes} CSR draws, +1 correction",
            "sup_abs_g_minus_1": round(stat, 6),
        })
        out["summary"] += (
            f" 固定种子 CSR 包络：sup|g−1|={stat:.3f}，p={p_upper:.4f}"
            f"（{envelopes} 次模拟，seed={_FIXED_SEED}）。"
        )
    return out


def cross_k(
    xy: np.ndarray,
    types: Sequence,
    crs: Optional[str] = None,
    n_steps: int = 10,
    max_distance_ratio: float = 0.25,
    window: Optional[Sequence[float]] = None,
    permutations: int = 199,
) -> Dict:
    """双变量 Ripley's K（cross-K，Besag 1977 random labelling）。

    K12(r) = A/(n1·n2) · Σ_{i∈type1, j∈type2} I(d_ij ≤ r)/w_ij，w_ij 与
    单变量同款各向同性边缘校正（焦点 i + 距离决定，逐对适用）。
    CSR/random-labelling 参考 K12 = πr²。``permutations``（默认 199，
    上限 499，0=关）：固定种子 42 置换类型标签——同一估计器跑在置换
    标签上；p 值 = max_r|K12−πr²| 在置换分布中的秩（+1 校正）。
    ``types`` 必须恰好 2 个不同取值（否则 UnsupportedMethod），
    每类 ≥5 点（否则 InsufficientSamples）。
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
    permutations = int(permutations)
    if permutations and not 1 <= permutations <= _MAX_ENVELOPES:
        raise ValueError(
            f"permutations must be 0 (off) or within 1..{_MAX_ENVELOPES} (got {permutations})")
    n = len(xy)
    if n > _MAX_RIPLEY_OBSERVATIONS:
        raise ResourceScaleMismatch(
            f"cross-K is an O(n²) pair statistic at n={n}",
            estimated=f"{n} points",
            limit=f"{_MAX_RIPLEY_OBSERVATIONS} points",
            correction_hint="aggregate to a grid first (h3_binning) or sample down",
        )

    type_arr = np.asarray(types)
    if type_arr.ndim != 1 or len(type_arr) != n:
        raise ValueError(
            f"types must be a length-{n} 1-D array matching xy rows "
            f"(got shape {type_arr.shape})")
    type_keys = [str(v) for v in type_arr.tolist()]
    distinct = list(dict.fromkeys(type_keys))  # 首现序、确定性
    if len(distinct) != 2:
        raise UnsupportedMethod(
            f"cross-K needs exactly 2 distinct type values (got {len(distinct)}: "
            f"{distinct[:5]})",
            correction_hint="pick a binary type field, or use univariate ripley_k",
        )
    is1 = np.array([t == distinct[0] for t in type_keys])
    is2 = ~is1
    n1, n2 = int(is1.sum()), int(is2.sum())
    if n1 < 5 or n2 < 5:
        raise InsufficientSamples(
            f"cross-K needs at least 5 points per type (got {n1} × '{distinct[0]}', "
            f"{n2} × '{distinct[1]}')",
            correction_hint="add observations of the minority type or merge sparse categories",
        )

    window = _require_window(xy, window)
    xmin, ymin, xmax, ymax = window
    area = (xmax - xmin) * (ymax - ymin)
    r_max = max_distance_ratio * min(xmax - xmin, ymax - ymin)
    if r_max <= 0:
        raise DegenerateData("r_max degenerated to 0; window extent too small")
    r_grid = np.linspace(r_max / n_steps, r_max, n_steps)

    from scipy.spatial import cKDTree

    # 池化成对表（全部点对、双向，同 ripley_k）——random labelling 的
    # 零假设分布必须从**池化**位置对里重抽类型，不能只看观测的跨类对
    # （否则强分离数据下置换包络塌缩，检验零功效）。
    tree = cKDTree(xy)
    n_pairs_est = int(tree.count_neighbors(tree, r_max)) - n
    if n_pairs_est > _MAX_RIPLEY_PAIRS:
        raise ResourceScaleMismatch(
            f"cross-K pair budget exceeded: ~{n_pairs_est} pairs within "
            f"r_max={r_max:.1f} m",
            estimated=f"~{n_pairs_est * 16 / 1e9:.2f} GB COO pairs",
            limit=f"{_MAX_RIPLEY_PAIRS} pairs",
            correction_hint="reduce max_distance_ratio, aggregate to a grid, or subsample",
        )
    coo = tree.sparse_distance_matrix(tree, max_distance=float(r_max),
                                      output_type="coo_matrix")
    keep = coo.row != coo.col
    order = np.argsort(coo.data[keep], kind="stable")
    d_sorted = coo.data[keep][order]
    g_focal = coo.row[keep][order]
    g_target = coo.col[keep][order]
    # 同一 w_inv 表（焦点 + 距离决定，逐对适用）服务观测与全部置换。
    w_inv = 1.0 / _isotropic_inside_fraction(xy[g_focal], window, d_sorted)

    lab = np.where(is1, 1, 2)  # 标签值与 _k12 的 sel 谓词（1 / 2）对齐

    def _k12(labels: np.ndarray) -> np.ndarray:
        """Directed K12 on the pooled pair table under a labelling (the
        shared estimator — observed labelling and every permutation run
        THIS function)."""
        sel = (labels[g_focal] == 1) & (labels[g_target] == 2)
        cum = np.concatenate(([0.0], np.cumsum(w_inv[sel])))
        counts = np.searchsorted(d_sorted[sel], r_grid, side="right")
        return area / (n1 * n2) * cum[counts]

    k12 = _k12(lab)
    n_pairs_used = int(np.sum((lab[g_focal] == 1) & (lab[g_target] == 2)))
    csr_vals = np.pi * r_grid**2

    out: Dict = {
        "r": [round(float(v), 4) for v in r_grid],
        "K12": [round(float(v), 4) for v in k12],
        "csr_K12": [round(float(v), 4) for v in csr_vals],
        "type_values": distinct,
        "n1": n1,
        "n2": n2,
        "n_pairs": n_pairs_used,
        "n": int(n),
        "r_max": round(float(r_max), 4),
        "window": [round(float(v), 4) for v in window],
        "edge_correction": "isotropic (rectangular window, Ripley 1976), per-pair",
        "estimator": "K12(r) = A/(n1*n2) * sum_{i in 1, j in 2} I(d_ij<=r)/w_ij",
        "seed_policy": f"fixed_seed (random labelling, seed={_FIXED_SEED})" if permutations else "deterministic",
    }

    interpretation = (
        "K12 高于 πr² 于短半径（两类空间吸引/共聚）"
        if k12[-1] > csr_vals[-1]
        else "K12 不高于 πr² 于 r_max（两类空间相斥或独立）"
    )
    out["tendency"] = interpretation
    out["summary"] = (
        f"Cross-K (n1={n1} '{distinct[0]}', n2={n2} '{distinct[1]}', "
        f"r_max={r_max:.1f}): {interpretation}。"
    )

    if permutations:
        rng = np.random.default_rng(_FIXED_SEED)
        sims = np.empty((permutations, n_steps))
        for i in range(permutations):
            sims[i] = _k12(lab[rng.permutation(n)])
        stat = float(np.max(np.abs(k12 - csr_vals)))
        sim_stats = np.max(np.abs(sims - np.pi * r_grid[None, :] ** 2), axis=1)
        p_upper = (int(np.sum(sim_stats >= stat)) + 1) / (permutations + 1)
        out.update({
            "permutations": int(permutations),
            "envelope_seed": _FIXED_SEED,
            "envelope_K12_low": [round(float(v), 4) for v in np.quantile(sims, 0.05, axis=0)],
            "envelope_K12_median": [round(float(v), 4) for v in np.quantile(sims, 0.50, axis=0)],
            "envelope_K12_high": [round(float(v), 4) for v in np.quantile(sims, 0.95, axis=0)],
            "p_value": round(float(p_upper), 6),
            "p_method": f"max_r|K12-pi*r^2| rank vs {permutations} random-labelling draws, +1 correction",
            "sup_abs_dev": round(stat, 4),
        })
        out["summary"] += (
            f" random-labelling 置换（{permutations} 次，seed={_FIXED_SEED}）："
            f"max|K12−πr²|={stat:.2f}，p={p_upper:.4f}"
            f"（{'拒绝随机标记假设' if p_upper < 0.05 else '未拒绝随机标记假设'}）。"
        )
    return out


def knox_test(
    xy: np.ndarray,
    times: np.ndarray,
    crs: Optional[str] = None,
    critical_distance: float = 0.0,
    critical_time: float = 0.0,
    permutations: int = 199,
) -> Dict:
    """Knox 时空交互检验（Knox 1964）。

    观测统计量 = 同时落在空间阈值（米）与时间阈值（秒）内的点对数；
    独立即期望 E = 2·S·T/(n(n−1))（S=空间邻近对数，T=时间邻近对数）。
    时间置换（固定种子 42）给单侧 greater p 值（+1 校正）。空间对经
    ``cKDTree.query_pairs`` 稀疏化——任何 n 下不分配 dense n×n；
    配对预算先估算后分配（与 ripley_k 同策略）。``critical_distance``
    ≤0 → 自动取中位最近邻距离（披露）；``critical_time`` ≤0 → 自动取
    排序后相邻时间隙的中位数（披露）。非有限时间行剔除并披露计数。
    """
    xy = np.asarray(xy, dtype=float)
    if xy.ndim != 2 or xy.shape[1] != 2:
        raise ValueError(f"xy must be an (n, 2) coordinate array (got shape {xy.shape})")
    _assert_metric_xy(xy, crs)
    times = np.asarray(times, dtype=float).ravel()
    if len(times) != len(xy):
        raise ValueError(
            f"times must match xy rows (got {len(times)} times, {len(xy)} points)")
    permutations = int(permutations)
    if permutations and not 1 <= permutations <= 999:
        raise ValueError(
            f"permutations must be 0 (off) or within 1..999 (got {permutations})")

    valid = np.isfinite(times)
    n_dropped = int((~valid).sum())
    if n_dropped:
        xy = xy[valid]
        times = times[valid]
    n = len(xy)
    if n < 4:
        raise InsufficientSamples(
            f"Knox needs at least 4 valid space-time points (got {n}; "
            f"{n_dropped} non-finite time rows dropped)",
            correction_hint="add observations with parseable timestamps",
        )
    if n > _MAX_KNOX_OBSERVATIONS:
        raise ResourceScaleMismatch(
            f"Knox is an O(n²) pair statistic at n={n}",
            estimated=f"{n} points",
            limit=f"{_MAX_KNOX_OBSERVATIONS} points",
            correction_hint="aggregate or clip to a time window first",
        )

    from scipy.spatial import cKDTree

    tree = cKDTree(xy)
    critical_distance_auto = bool(critical_distance <= 0)
    if critical_distance_auto:
        nn = tree.query(xy, k=2)[0][:, 1]
        critical_distance = float(np.median(nn))
    critical_distance = float(critical_distance)
    if critical_distance <= 0:
        raise DegenerateData(
            "critical_distance degenerated to 0 (median nearest-neighbour "
            "distance is 0 — duplicated points?)",
            correction_hint="pass an explicit critical_distance in meters",
        )
    critical_time_auto = bool(critical_time <= 0)
    if critical_time_auto:
        t_sorted = np.sort(times)
        gaps = np.diff(t_sorted)
        gaps = gaps[gaps > 0]
        critical_time = float(np.median(gaps)) if gaps.size else 0.0
    critical_time = float(critical_time)
    if critical_time <= 0:
        raise DegenerateData(
            "critical_time degenerated to 0 (all timestamps identical?)",
            correction_hint="pass an explicit critical_time in seconds",
        )

    # 空间邻近对（稀疏）——预算先估算后分配（评审 MAJOR-2 同策略）。
    n_pairs_est = int(tree.count_neighbors(tree, critical_distance)) - n
    if n_pairs_est > _MAX_RIPLEY_PAIRS:
        raise ResourceScaleMismatch(
            f"Knox spatial pair budget exceeded: ~{n_pairs_est} pairs within "
            f"critical_distance={critical_distance:.1f} m",
            estimated=f"~{n_pairs_est * 16 / 1e9:.2f} GB pairs",
            limit=f"{_MAX_RIPLEY_PAIRS} pairs",
            correction_hint="reduce critical_distance or subsample",
        )
    spatial_pairs = tree.query_pairs(critical_distance, output_type="ndarray")
    n_spatial = int(len(spatial_pairs))

    # 时间邻近对（排序 + searchsorted，无 dense n×n）。
    t_sorted = np.sort(times)
    right = np.searchsorted(t_sorted, t_sorted + critical_time, side="right")
    n_temporal = int(np.sum(right - np.arange(n) - 1))

    i_idx, j_idx = (spatial_pairs[:, 0], spatial_pairs[:, 1]) if n_spatial else \
        (np.empty(0, dtype=int), np.empty(0, dtype=int))
    d_time = np.abs(times[i_idx] - times[j_idx]) if n_spatial else np.empty(0)
    observed = int(np.sum(d_time <= critical_time))
    n_tied_spatial_pairs = int(np.sum(d_time == 0.0)) if n_spatial else 0

    expected = 2.0 * n_spatial * n_temporal / (n * (n - 1))

    out: Dict = {
        "n": int(n),
        "n_time_dropped": n_dropped,
        "observed": observed,
        "expected": round(float(expected), 6),
        "n_spatial_pairs": n_spatial,
        "n_temporal_pairs": n_temporal,
        "critical_distance": round(critical_distance, 6),
        "critical_distance_auto": critical_distance_auto,
        "critical_time": round(critical_time, 6),
        "critical_time_auto": critical_time_auto,
        "permutations": int(permutations),
        "estimator": ("observed = #pairs within (critical_distance m AND "
                      "critical_time s); E = 2*S*T/(n*(n-1)) under independence"),
        "seed_policy": f"fixed_seed (time permutation, seed={_FIXED_SEED})" if permutations else "deterministic",
    }
    if critical_distance_auto:
        out["critical_distance_note"] = (
            f"critical_distance=0 → 自动取中位最近邻距离 {critical_distance:.2f} m"
        )
    if critical_time_auto:
        out["critical_time_note"] = (
            f"critical_time=0 → 自动取相邻时间隙中位数 {critical_time:.1f} s"
        )
    if n_dropped:
        out["time_dropped_note"] = (
            f"{n_dropped} 行时间戳不可解析（NaT/NaN），已剔除后检验"
        )
    if n_tied_spatial_pairs:
        out["tie_note"] = (
            f"{n_tied_spatial_pairs} 个空间邻近对时间戳完全相同（Δt=0 亦计入 "
            "时间邻近）；平局对置换检验的分辨率有影响，结论需谨慎"
        )

    ratio = observed / expected if expected > 0 else float("inf")
    out["observed_over_expected"] = round(float(ratio), 6) if math.isfinite(ratio) else None

    if permutations:
        rng = np.random.default_rng(_FIXED_SEED)
        sim_counts = np.empty(permutations)
        for i in range(permutations):
            tp = times[rng.permutation(n)]
            sim_counts[i] = int(np.sum(np.abs(tp[i_idx] - tp[j_idx]) <= critical_time))
        p_value = (int(np.sum(sim_counts >= observed)) + 1) / (permutations + 1)
        out.update({
            "p_value": round(float(p_value), 6),
            "p_method": f"one-sided greater rank vs {permutations} time permutations, +1 correction",
            "perm_quantiles": {
                "p5": round(float(np.quantile(sim_counts, 0.05)), 4),
                "p50": round(float(np.quantile(sim_counts, 0.50)), 4),
                "p95": round(float(np.quantile(sim_counts, 0.95)), 4),
            },
        })
        verdict = (
            "存在显著时空交互（时空聚集）" if p_value < 0.05
            else "未拒绝时空独立"
        )
        out["interpretation"] = (
            f"时空联合对 {observed}（独立期望 {expected:.2f}，"
            f"比值 {ratio:.2f}），时间置换 p={p_value:.4f}：{verdict}。"
        )
    else:
        out["interpretation"] = (
            f"时空联合对 {observed}（独立期望 {expected:.2f}，比值 {ratio:.2f}）。"
            "描述性对比——未做显著性检验。"
        )
    out["summary"] = f"Knox space-time interaction test (n={n}): {out['interpretation']}"
    return out


__all__: List[str] = [
    "ripley_k", "quadrat_test",
    "g_f_j_functions", "pcf", "cross_k", "knox_test",
]

