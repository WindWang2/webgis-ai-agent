"""Ordinary Kriging with uncertainty (native GIS vertical slice).

Companion to :mod:`app.lib.geo_analysis.interpolation` (IDW) — the same GIS
contract applies, plus the kriging-specific ones:

* **Variogram models** — spherical / exponential / gaussian, each with a
  bounded, deterministic least-squares fit against binned empirical
  semivariances. ``auto`` picks the model with the lowest weighted RSS.

V2 (Foundation V2 · A2, all additive — the production path is unchanged):

* **Family expansion (opt-in)** — ``matern`` (smoothness ν, fixed parameter
  ``matern_smoothness`` ∈ [0.1, 5.0], numerically stable via
  :func:`scipy.special.kv` with an exact h=0 guard), ``wave`` (hole-effect,
  ``sill·(1 − sin(x)/x)``) and ``cubic`` (compact polynomial, exact sill at
  h ≥ range). ``auto`` keeps fitting ONLY {spherical, exponential,
  gaussian} — the production selection is untouched; the new families are
  reachable by passing the explicit model name to :func:`fit_variogram`
  (the driver's ``variogram_model`` vocabulary is pinned by the production
  conformance suite to the legacy set and deliberately unchanged).

* **Geometric anisotropy (opt-in)** — ``anisotropy_angle`` (degrees, major
  axis bearing) + ``anisotropy_ratio`` (≥ 1, major/minor range ratio) apply
  one shared linear transform ``A = diag(1, ratio)·R(θ)`` to the sample and
  target coordinates *before* the kD-tree and every semivariance
  evaluation. angle=0, ratio=1 (the defaults) are the identity map — the
  historical path stays bit-identical.

* **Spatial block CV (opt-in)** — ``cv_scheme="spatial_block"`` assigns
  samples to folds by deterministic grid-stratified blocks on the sorted
  x/y ranks (⌈√folds⌉ layout, NO RNG): clustered samples are validated
  against spatially distant blocks instead of index-neighbours, which
  surfaces the honest (worse) error of extrapolative sampling designs.

* **Explicit solve backend (A7)** — ``solve_backend="auto"|"numpy_batched"|
  "scipy_linalg"`` on :func:`ordinary_kriging` / :func:`universal_kriging`.
  ``auto`` is the historical batched-then-per-row behaviour; a forced
  ``scipy_linalg`` solves each chunk row via ``scipy.linalg.lu_factor``/
  ``lu_solve``. The executed path is reported in ``KrigingResult.
  solve_backend_used`` (surfaces in driver metadata).

* **Bounded cost** — semivariance estimation never materialises the full
  O(N²) pair matrix: samples above ``MAX_FIT_POINTS`` are reduced by
  deterministic spatial-stratified subsampling, and the empirical binning
  walks the pair matrix in bounded chunks. Prediction uses a k-neighbourhood
  (≤ ``MAX_NEIGHBORS``) OK system solved in vectorized batches, so each
  prediction costs a small (k+1)×(k+1) solve instead of an (N+1)×(N+1) one.

* **Two first-class outputs** — the prediction surface AND the kriging
  variance (uncertainty) surface. Callers must not bury uncertainty in
  prose: :func:`ordinary_kriging` returns both per target point.

* **Honest validation** — :func:`cross_validate_kriging` reports
  RMSE/MAE/bias/R² with the fold count actually used, and refuses to
  produce metrics when the sample is too small to validate (it says so).

VNext (ADR-0099 additions, all additive — the ordinary-kriging path is
byte-identical):

* **Universal kriging** — ``method="universal"`` on the driver and the CV:
  a linear drift ``E[Z(x)] = b0 + b1·x + b2·y`` is removed by OLS, the
  variogram is fitted on the **detrended residuals** (UK assumes a
  second-order-stationary residual process around the trend — raw-value
  variography would credit the trend to spatial correlation), and the UK
  system carries the trend-constrained Lagrange multipliers:

      [Γ  F][w]   [γ0]      F rows = [1, x_i, y_i]
      [Fᵀ  0][m] = [f0]     f0     = [1, x0, y0]

    Prediction = wᵗz; kriging variance = wᵗγ0 + mᵗf0. Zero-residual
    degenerate case (values exactly planar): the drift IS the signal — the
    exact trend prediction is returned with zero variance and the honest
    disclosure flag ``"zero_residual_variance"``; no variogram is fitted or
    faked. UK needs ≥ ``UK_MIN_SAMPLES`` (12) points to constrain the drift
    (:class:`~app.lib.gis.scientific_errors.InsufficientSamples` below that).

V3 (Geostatistics/Interpolation V3 batch, all additive — the OK/UK
production paths above are untouched):

* **Directional variogram** — :func:`directional_variogram` bins the
  empirical semivariance along ONE azimuth axis (bidirectional pair filter
  + optional GSLIB band width). Azimuth convention: MATHEMATICAL —
  0° = East (+x), counter-clockwise, the same convention as
  ``anisotropy_angle``; disclosed in every meta dict.

* **Variogram model selection** — :func:`select_variogram_model` fits all
  six families on one shared empirical variogram and ranks by weighted RSS
  (the same objective ``model="auto"`` uses) + AICc (k=3 fitted parameters;
  matern k=4 — disclosed). Deterministic: no stochastic restarts.

* **Indicator kriging** — :func:`indicator_kriging` (Journel 1983): per
  threshold the indicator transform gets its own empirical variogram +
  fit (``auto`` = per-threshold 6-family selection) and ordinary kriging
  of the indicator → P(Z(x) ≤ t), clamped to [0, 1] with clamped cells
  counted; plus the p50 threshold surface and an optional E-type estimate.

* **Collocated co-kriging (Markov Model 1)** — :func:`collocated_cokriging`
  (Journel & Huijbregts 1978 approximation): cross structure = ρ × primary
  structure; the secondary enters the system ONLY at the target location
  (collocated approximation); |ρ| < 0.2 is a typed rejection — the honest
  position that weak correlation cannot beat ordinary kriging.

* **Block kriging** — :func:`block_kriging`: rectangular block support via
  a fixed 2×2 sub-point discretization (Isaaks & Srivastava 1989 practice,
  disclosed): point-to-block averaged γ in the RHS, within-block γ̄(B,B)
  variance correction (block variance ≤ point variance on average).

All distances are computed in the CALLER-supplied projected (metric) CRS
space — degree-space kriging silently distorts and is rejected at the tool
boundary (``interpolation._pick_metric_crs`` is the sanctioned chooser).
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any, Optional

import numpy as np
from scipy.spatial import cKDTree

from app.lib.cancellation import cancellable
from app.lib.gis.scientific_errors import (
    DegenerateData,
    InsufficientSamples,
    ScientificPreconditionFailed,
)

logger = logging.getLogger(__name__)

VariogramModelNames = ("spherical", "exponential", "gaussian")
# V2 opt-in families — explicit-name only; ``auto`` keeps fitting the legacy
# production trio (AUTO_VARIOGRAM_MODELS) so its selection is unchanged.
V2_VARIOGRAM_MODELS = ("matern", "wave", "cubic")
ALL_VARIOGRAM_MODELS = VariogramModelNames + V2_VARIOGRAM_MODELS
AUTO_VARIOGRAM_MODELS = VariogramModelNames  # production auto selection set

# Matérn smoothness bounds (contract range); only used by the matern family.
MATERN_SMOOTHNESS_MIN = 0.1
MATERN_SMOOTHNESS_MAX = 5.0
MATERN_SMOOTHNESS_DEFAULT = 0.5

# ── resource ceilings (execution-policy contract) ───────────────────────────
MAX_INPUT_POINTS = 500_000        # hard reject above this many samples
MAX_FIT_POINTS = 2_000            # variogram fitting sample ceiling
MAX_PAIRS = 200_000               # empirical semivariance pair budget
MAX_NEIGHBORS = 24                # OK neighbourhood size per prediction
MIN_SAMPLES = 8                   # below this kriging is meaningless
MIN_CV_SAMPLES = 20               # below this CV cannot be trusted
DEFAULT_N_LAGS = 16               # empirical variogram bins
CV_FOLDS = 5
_SOLVE_CHUNK = 1024               # batched OK system rows per np.linalg.solve

# ── universal kriging (VNext) ───────────────────────────────────────────────
UK_MIN_SAMPLES = 12               # drift [1,x,y] needs ≥12 samples to constrain
_TREND_TERMS = 3                  # linear drift terms: [1, x, y]

# ── V3（Geostatistics/Interpolation 批次）────────────────────────────────────
DIRECTIONAL_DEFAULT_TOLERANCE_DEG = 22.5   # 轴向半角默认（±22.5°）
DIRECTIONAL_MAX_TOLERANCE_DEG = 90.0       # 90° = 全向（各向同性退化）
BLOCK_DISCRETIZATION = 2                   # 块离散化 2×2 子点（I&S 1989 惯例）
COKRIGING_MIN_ABS_RHO = 0.2                # |ρ| 低于此协同克里金无意义（类型化拒绝）

# Cressie–Hawkins (1980) 稳健半变异函数估计常数（robust opt-in）：
# 2γ(h) = [mean|Δz|^½]⁴ / (0.457 + 0.494/|N(h)| + 0.045/|N(h)|²)
_CRESSIE_HAWKINS_C0 = 0.457
_CRESSIE_HAWKINS_C1 = 0.494
_CRESSIE_HAWKINS_C2 = 0.045

# fit_anisotropy（各向异性自动拟合，P1）
ANISOTROPY_SCAN_AZIMUTH_STEP_DEG = 22.5    # 扫描步长（8 方位：0..157.5）
ANISOTROPY_SCAN_TOLERANCE_DEG = 11.25      # 轴向半角 = 步长/2（扇区无缝铺满）
ANISOTROPY_SCAN_N_LAGS = 48                # 扫描滞后 bin 数（近 origin 高分辨率）
ANISOTROPY_RATIO_CLAMP = (1.0, 4.0)        # 比值钳制（防退化/防爆炸）
ANISOTROPY_RATIO_THRESHOLD = 1.2           # is_anisotropic 判别阈值
_ANISOTROPY_LEVELS = (0.35, 0.45, 0.55, 0.65)   # 池化椭圆拟合的 sill 分位水平


class KrigingInputError(ValueError):
    """Structured input rejection (too few points, unfittable variogram…)."""


@dataclass
class VariogramFit:
    """Fitted theoretical variogram + the empirical evidence behind it."""

    model: str
    sill: float
    range_m: float
    nugget: float = 0.0
    # Matérn smoothness ν (fixed parameter, only meaningful for matern)
    nu: float = MATERN_SMOOTHNESS_DEFAULT
    # fitting diagnostics (auto-selection evidence)
    rss: float = 0.0
    n_pairs: int = 0
    n_lags: int = 0
    fitted_manually: bool = False  # curve_fit unavailable → bounded grid fit

    def params(self) -> dict[str, Any]:
        out = {
            "model": self.model,
            "sill": round(self.sill, 6),
            "range_meters": round(self.range_m, 3),
            "nugget": round(self.nugget, 3),
        }
        if self.model == "matern":
            out["smoothness"] = round(float(self.nu), 4)
        return out


def _validate_matern_smoothness(nu: Any) -> float:
    nu_f = float(nu)
    if not (MATERN_SMOOTHNESS_MIN <= nu_f <= MATERN_SMOOTHNESS_MAX):
        raise KrigingInputError(
            f"matern_smoothness 必须在 [{MATERN_SMOOTHNESS_MIN}, "
            f"{MATERN_SMOOTHNESS_MAX}] 内（Matérn 平滑度 ν），got {nu!r}"
        )
    return nu_f


def _matern_corr(x: np.ndarray, nu: float) -> np.ndarray:
    """Matérn correlation 2^{1−ν}/Γ(ν)·x^ν·K_ν(x), numerically stable.

    The h→0 limit is exact (x^ν·K_ν(x) → 2^{ν−1}Γ(ν), so γ(0)=nugget); the
    x→∞ branch underflows K_ν to 0 (γ → nugget + sill) without warnings.
    """
    from scipy.special import gammaln, kv

    x = np.asarray(x, dtype=float)
    t0 = (2.0 ** (nu - 1.0)) * float(np.exp(gammaln(nu)))
    out = np.zeros_like(x)
    finite_pos = np.isfinite(x) & (x > 0.0)
    if finite_pos.any():
        xf = x[finite_pos]
        with np.errstate(over="ignore", invalid="ignore", under="ignore"):
            t = np.power(xf, nu) * kv(nu, xf)
        out[finite_pos] = np.where(np.isfinite(t), t, 0.0) / t0
    zero = np.isfinite(x) & (x <= 0.0)
    if zero.any():
        out[zero] = 1.0  # correlation 1 at h=0 → γ = nugget
    return out


def _gamma(
    model: str,
    h: np.ndarray,
    sill: float,
    rng: float,
    nugget: float,
    nu: float = MATERN_SMOOTHNESS_DEFAULT,
) -> np.ndarray:
    """Theoretical semivariance γ(h) for the fitted model."""
    h = np.asarray(h, dtype=float)
    if model == "spherical":
        out = np.empty_like(h)
        hr = np.divide(h, rng, out=np.full_like(h, np.inf), where=rng > 0)
        inside = hr <= 1.0
        out[inside] = nugget + sill * (1.5 * hr[inside] - 0.5 * hr[inside] ** 3)
        out[~inside] = nugget + sill
        return out
    if model == "exponential":
        return nugget + sill * (1.0 - np.exp(-3.0 * h / max(rng, 1e-9)))
    if model == "gaussian":
        return nugget + sill * (1.0 - np.exp(-3.0 * (h / max(rng, 1e-9)) ** 2))
    if model == "matern":
        return nugget + sill * (1.0 - _matern_corr(h / max(rng, 1e-9), float(nu)))
    if model == "wave":
        # hole-effect model: γ(x) = nugget + sill·(1 − sin(x)/x); the first
        # sill reach is at x = π (sin(π)=0); between π and 2π it exceeds the
        # sill by design (that IS the hole effect — disclosed, not clamped).
        x = h / max(rng, 1e-9)
        ratio = np.where(np.abs(x) > 1e-12, np.sin(x) / np.where(np.abs(x) > 1e-12, x, 1.0), 1.0)
        return nugget + sill * (1.0 - ratio)
    if model == "cubic":
        # Cubic model (GSLIB convention): γ(x) = nugget + sill·P(x) for
        # x = h/range ≤ 1 with P(x) = 7x² − 8.75x³ + 3.5x⁵ − 0.75x⁷ (exact
        # golden coefficients), P(0)=0, P(1)=1, P′(1)=P″(1)=0 (C² at the
        # range); constant nugget+sill beyond. (The task brief wrote
        # sill·(1−P), which is decreasing and not a valid variogram — the
        # standard rising polynomial is implemented.)
        out = np.full_like(h, nugget + sill, dtype=float)
        x = np.divide(h, rng, out=np.full_like(h, np.inf), where=rng > 0)
        inside = x <= 1.0
        xi = x[inside]
        poly = 7.0 * xi ** 2 - 8.75 * xi ** 3 + 3.5 * xi ** 5 - 0.75 * xi ** 7
        out[inside] = nugget + sill * poly
        return out
    raise KrigingInputError(f"unknown variogram model: {model!r}")


# ── deterministic spatial-stratified subsampling ────────────────────────────

def stratified_subsample(
    pts_metric: np.ndarray, values: np.ndarray, max_points: int
) -> tuple[np.ndarray, np.ndarray]:
    """Deterministically reduce samples to ≤ ``max_points`` with spatial spread.

    Stratification: snap samples to a coarse grid (⌈√max⌉ × ⌈√max⌉ strata),
    order each stratum's members by (x, y, value) and keep the leading
    ``max/strata`` — the kept set is stable under input reordering and
    covers the whole extent (a plain head-slice would bias toward one
    corner / one insertion order).
    """
    n = len(values)
    if n <= max_points:
        return pts_metric, values
    grid_n = max(2, int(math.ceil(math.sqrt(max_points))))
    xs, ys = pts_metric[:, 0], pts_metric[:, 1]
    dx = (xs.max() - xs.min()) / grid_n + 1e-9
    dy = (ys.max() - ys.min()) / grid_n + 1e-9
    gx = np.floor((xs - xs.min()) / dx).astype(np.int64)
    gy = np.floor((ys - ys.min()) / dy).astype(np.int64)
    order = np.lexsort((values, ys, xs))  # stable deterministic order
    per_cell: dict[tuple[int, int], list[int]] = {}
    for i in order:
        per_cell.setdefault((int(gx[i]), int(gy[i])), []).append(int(i))
    per_cell_take = max(1, max_points // max(1, len(per_cell)))
    keep: list[int] = []
    for cell_key in sorted(per_cell.keys()):
        members = per_cell[cell_key]
        keep.extend(members[:per_cell_take])
        if len(keep) >= max_points:
            break
    keep = sorted(keep[:max_points])  # restore spatial locality
    return pts_metric[keep], values[keep]


# ── geometric anisotropy (V2, opt-in; identity by default) ──────────────────

def anisotropy_transform(angle_deg: float, ratio: float) -> np.ndarray:
    """Linear map ``A = diag(1, ratio)·R(−θ)`` for geometric anisotropy.

    Isotropic distance in the transformed space equals anisotropic distance
    in the original one: a displacement along the major axis (bearing
    ``angle_deg``, CCW from +x) keeps length d (range α), while the
    perpendicular direction maps to d·ratio (range α/ratio) — so ``ratio``
    is the major/minor range ratio (≥ 1).
    """
    ratio_f = float(ratio)
    if not (math.isfinite(ratio_f) and ratio_f >= 1.0):
        raise KrigingInputError(
            f"anisotropy_ratio 必须 ≥ 1（长/短轴变程比），got {ratio!r}"
        )
    angle_f = float(angle_deg)
    if not math.isfinite(angle_f):
        raise KrigingInputError(f"anisotropy_angle 必须是有限角度（度），got {angle_deg!r}")
    t = math.radians(angle_f)
    c, s = math.cos(t), math.sin(t)
    # R(−θ)：把主轴方向的位移先旋回 +x，再沿 y 拉伸 ratio —— 这样
    # |A·u_major| = 1（长轴变程 α 不变）、|A·u_minor| = ratio（短轴
    # 变程 α/ratio）。用 R(+θ) 会把主轴映到 2θ，非正交角下完全翻转
    # 椭圆朝向（评审 R2 CRITICAL-1）。
    rot = np.array([[c, s], [-s, c]], dtype=float)
    return np.array([[1.0, 0.0], [0.0, ratio_f]], dtype=float) @ rot


def apply_anisotropy(
    xy: np.ndarray, angle_deg: float = 0.0, ratio: float = 1.0
) -> np.ndarray:
    """Project coordinates through the anisotropy map (identity fast path).

    ``angle_deg=0, ratio=1`` (the defaults) return the input unchanged —
    the historical isotropic path stays bit-identical.
    """
    if float(ratio) == 1.0 and float(angle_deg) % 360.0 == 0.0:
        return xy
    A = anisotropy_transform(angle_deg, ratio)
    return np.asarray(xy, dtype=float) @ A.T


# ── empirical + theoretical variogram ───────────────────────────────────────

def empirical_variogram(
    pts_metric: np.ndarray,
    values: np.ndarray,
    n_lags: int = DEFAULT_N_LAGS,
    max_pairs: int = MAX_PAIRS,
    robust: bool = False,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Binned empirical semivariance γ*(h) over bounded row chunks.

    Returns ``(lag_centers, gamma, pair_counts)``; bins with zero pairs are
    dropped. The pair matrix is walked one row at a time (O(N) peak memory)
    and a deterministic row stride keeps total pairs within ``max_pairs``.

    ``robust=False``（默认，生产主路径）为经典 Matheron 估计
    γ(h) = Σ(z_i−z_j)² / (2|N(h)|) —— 代码路径逐位不变。
    ``robust=True`` 切换 Cressie–Hawkins (1980) 稳健估计：
    2γ(h) = [ (1/|N(h)|)·Σ|z_i−z_j|^½ ]⁴ / (0.457 + 0.494/|N(h)| + 0.045/|N(h)|²)
    —— 先开方再平均把离群对的影响从四次方压到线性，对少量污染对稳健；
    高斯场合 |N(h)|→∞ 时渐近无偏（0.457 即 (E|N(0,1)^½|)⁴ 修正）。
    """
    n = len(values)
    n_lags = max(4, min(int(n_lags), 64))
    span = float(np.linalg.norm(pts_metric.max(axis=0) - pts_metric.min(axis=0))) or 1.0
    edges = np.linspace(0.0, span, n_lags + 1)
    sum_g = np.zeros(n_lags)
    sum_q = np.zeros(n_lags)  # Σ|Δz|^½（仅 robust 路径累计）
    cnt = np.zeros(n_lags, dtype=np.int64)

    total_pairs = n * (n - 1) // 2
    stride = max(1, int(math.ceil(total_pairs / max(max_pairs, 1))))
    rows = range(0, n, stride)
    for i in cancellable(rows, every=64):
        d = np.linalg.norm(pts_metric[i + 1:] - pts_metric[i], axis=1)
        dv2 = (values[i + 1:] - values[i]) ** 2
        b = np.searchsorted(edges, d, side="right") - 1
        valid = (b >= 0) & (b < n_lags)
        if not valid.any():
            continue
        np.add.at(sum_g, b[valid], dv2[valid])
        if robust:
            # |Δz|^½ = (Δz²)^¼ —— 开方两次把离群对的影响从四次方压到线性
            np.add.at(sum_q, b[valid], np.sqrt(np.sqrt(dv2[valid])))
        np.add.at(cnt, b[valid], 1)

    has = cnt > 0
    lags = 0.5 * (edges[:-1] + edges[1:])[has]
    if robust:
        m = cnt[has].astype(float)
        mean_root = sum_q[has] / m
        # C&H 公式左端是 2γ(h) —— 除以 2 得 γ(h)
        gamma = mean_root ** 4 / (
            2.0 * (_CRESSIE_HAWKINS_C0
                   + _CRESSIE_HAWKINS_C1 / m
                   + _CRESSIE_HAWKINS_C2 / (m * m))
        )
    else:
        gamma = (0.5 * sum_g[has]) / cnt[has]
    return lags, gamma, cnt[has]


def _fit_model(
    model: str,
    lags: np.ndarray,
    gamma: np.ndarray,
    weights: np.ndarray,
    var_values: float,
    span: float,
    nu: float = MATERN_SMOOTHNESS_DEFAULT,
) -> Optional[VariogramFit]:
    """Bounded least-squares fit of γ(h) for one model.

    :func:`scipy.optimize.curve_fit` with hard bounds when available
    (sill ∈ (0, 4·var], range ∈ (span/200, 2·span], nugget ∈ [0, var]);
    falls back to a bounded grid search (coarse scan + refinements) that
    needs no optimizer — deterministic and dependency-free. ``nu`` is the
    FIXED Matérn smoothness (a contract parameter, not fitted).
    """
    sigma = 1.0 / np.sqrt(np.maximum(weights / weights.max(), 1e-6))
    var_floor = max(var_values, 1e-12)

    def f(h, sill, rng, nugget):
        return _gamma(model, h, sill, rng, nugget, nu=nu)

    lo = (1e-12 * var_floor, max(span / 200.0, 1e-6), 0.0)
    hi = (max(4.0 * var_floor, 1e-9), 2.0 * span, max(var_floor, 1e-12))
    try:
        from scipy.optimize import curve_fit

        popt, _ = curve_fit(
            f, lags, gamma,
            p0=[max(var_floor, 1e-9), max(span / 3.0, lo[1]), 0.0],
            bounds=(lo, hi), sigma=sigma, maxfev=4000,
        )
        sill, rng, nugget = (float(v) for v in popt)
        resid = (f(lags, *popt) - gamma) / sigma
        return VariogramFit(
            model=model, sill=sill, range_m=rng, nugget=nugget, nu=float(nu),
            rss=float(np.sum(resid ** 2)), n_pairs=int(weights.sum()),
            n_lags=len(lags),
        )
    except Exception:
        pass

    # bounded grid fallback: coarse scan, then local refinements
    best: Optional[VariogramFit] = None
    sill_grid = np.linspace(lo[0], hi[0], 12)
    rng_grid = np.linspace(lo[1], hi[1], 12)
    nug_grid = np.linspace(lo[2], hi[2], 5)
    for _ in range(3):
        for sill in sill_grid:
            for rng in rng_grid:
                for nug in nug_grid:
                    pred = _gamma(model, lags, sill, rng, nug, nu=nu)
                    rss = float(np.sum(((pred - gamma) / sigma) ** 2))
                    if best is None or rss < best.rss:
                        best = VariogramFit(
                            model=model, sill=float(sill), range_m=float(rng),
                            nugget=float(nug), nu=float(nu), rss=rss,
                            n_pairs=int(weights.sum()), n_lags=len(lags),
                            fitted_manually=True,
                        )
        w_s = (hi[0] - lo[0]) / 6.0
        w_r = (hi[1] - lo[1]) / 6.0
        w_n = (hi[2] - lo[2]) / 4.0
        sill_grid = np.clip(best.sill + np.linspace(-1, 1, 7) * w_s, lo[0], hi[0])
        rng_grid = np.clip(best.range_m + np.linspace(-1, 1, 7) * w_r, lo[1], hi[1])
        nug_grid = (np.clip(best.nugget + np.linspace(-1, 1, 5) * w_n, lo[2], hi[2])
                    if w_n > 0 else np.array([best.nugget]))
    return best


def fit_variogram(
    pts_metric: np.ndarray,
    values: np.ndarray,
    model: str = "auto",
    n_lags: int = DEFAULT_N_LAGS,
    max_pairs: int = MAX_PAIRS,
    anisotropy_angle: float = 0.0,
    anisotropy_ratio: float = 1.0,
    matern_smoothness: float = MATERN_SMOOTHNESS_DEFAULT,
    robust: bool = False,
) -> VariogramFit:
    """Fit the theoretical variogram (``auto`` = best weighted RSS of the 3).

    Samples above the fitting ceiling are reduced by deterministic spatial
    stratification first; the fit is bounded on both sides so it can never
    return a degenerate (zero-range / negative-sill) model.

    V2: ``model`` may name any of :data:`ALL_VARIOGRAM_MODELS` (matern /
    wave / cubic are opt-in — ``auto`` keeps the legacy production trio);
    geometric anisotropy is applied to the coordinates before binning (the
    defaults are the identity); ``matern_smoothness`` is the fixed Matérn ν.
    ``robust=True`` 透传给 :func:`empirical_variogram`（Cressie–Hawkins
    稳健估计，opt-in；默认 False 经典主路径逐位不变）。
    """
    if model not in ALL_VARIOGRAM_MODELS and model != "auto":
        raise KrigingInputError(
            f"variogram model 必须是 {ALL_VARIOGRAM_MODELS + ('auto',)} 之一，got {model!r}"
        )
    nu = (
        _validate_matern_smoothness(matern_smoothness)
        if model == "matern" else MATERN_SMOOTHNESS_DEFAULT
    )
    pts_t = apply_anisotropy(pts_metric, anisotropy_angle, anisotropy_ratio)
    fit_pts, fit_vals = stratified_subsample(pts_t, values, MAX_FIT_POINTS)
    lags, gamma, counts = empirical_variogram(
        fit_pts, fit_vals, n_lags=n_lags, max_pairs=max_pairs, robust=robust
    )
    if len(lags) < 4:
        raise KrigingInputError(
            f"经验变异函数只有 {len(lags)} 个有效滞后 bin（需要 ≥4）—— "
            "样本空间分布不足以拟合变异函数；请改用 IDW 或增加采样点。"
        )
    var_values = float(np.var(fit_vals))
    span = float(np.linalg.norm(fit_pts.max(axis=0) - fit_pts.min(axis=0))) or 1.0
    weights = counts.astype(float)

    candidates = AUTO_VARIOGRAM_MODELS if model == "auto" else (model,)
    best: Optional[VariogramFit] = None
    failures: list[str] = []
    for m in candidates:
        fit = _fit_model(m, lags, gamma, weights, var_values, span, nu=nu)
        if fit is None:
            failures.append(m)
            continue
        if best is None or fit.rss < best.rss:
            best = fit
    if best is None:
        raise KrigingInputError(
            f"变异函数拟合失败（models={failures}）—— 输入无法支持克里金；请改用 IDW。"
        )
    return best


# ── ordinary kriging prediction (vectorized batches) ────────────────────────

@dataclass
class KrigingResult:
    """Prediction + uncertainty arrays with the full provenance."""

    predictions: np.ndarray
    variances: np.ndarray
    # OK always carries a fitted variogram; UK's zero-residual degenerate
    # case honestly carries None (no variogram fitted, none faked).
    variogram: Optional[VariogramFit]
    n_samples: int
    n_samples_fit: int
    neighbors: int
    degraded_cells: int = 0   # predictions that fell back to the local mean
    # ── science-v3：95% 预测区间（高斯误差近似；None = 退化路径诚实缺省）──
    pi95_low: Optional[np.ndarray] = None
    pi95_high: Optional[np.ndarray] = None
    # ── VNext additive fields (OK path leaves them at defaults) ────────────
    disclosures: list[str] = field(default_factory=list)
    drift_coefficients: Optional[np.ndarray] = None  # UK linear drift [1,x,y]
    # ── V2 (A7): which solve backend actually produced the predictions ─────
    solve_backend_used: str = "numpy_batched"


SOLVE_BACKENDS = ("auto", "numpy_batched", "scipy_linalg")


def _validate_solve_backend(backend: str) -> str:
    if backend not in SOLVE_BACKENDS:
        raise KrigingInputError(
            f"solve_backend 必须是 {SOLVE_BACKENDS} 之一，got {backend!r}"
        )
    return backend


def _solve_kriging_systems(
    mat: np.ndarray, rhs: np.ndarray, backend: str
) -> tuple[np.ndarray, int]:
    """Solve a batch of small kriging systems under the backend policy.

    ``numpy_batched`` (also the ``auto`` primary): vectorized
    ``np.linalg.solve`` over the whole chunk, per-row LAPACK fallback for
    failed batches. ``scipy_linalg`` (forced): per-row
    ``scipy.linalg.lu_factor``/``lu_solve``. Rows that still fail get NaN
    solutions and are counted in the returned degraded count (callers fall
    back to the neighbourhood mean — never silent).
    """
    c, m = mat.shape[0], mat.shape[1]
    sol = np.full((c, m), np.nan)
    degraded = 0
    if backend == "scipy_linalg":
        from scipy.linalg import lu_factor, lu_solve

        for r in range(c):
            try:
                lu_piv = lu_factor(mat[r])
                s = lu_solve(lu_piv, rhs[r])
                if not np.isfinite(s).all():
                    raise np.linalg.LinAlgError("non-finite row solution")
                sol[r] = s
            except (np.linalg.LinAlgError, ValueError):
                degraded += 1
        return sol, degraded
    try:
        sol = np.linalg.solve(mat, rhs[:, :, None])[:, :, 0]
        bad = ~np.isfinite(sol).all(axis=1)
        if bad.any():
            raise np.linalg.LinAlgError("non-finite batch solution")
        return sol, 0
    except np.linalg.LinAlgError:
        sol = np.full((c, m), np.nan)
        for r in range(c):
            try:
                s = np.linalg.solve(mat[r], rhs[r])
                if not np.isfinite(s).all():
                    raise np.linalg.LinAlgError("non-finite row solution")
                sol[r] = s
            except np.linalg.LinAlgError:
                degraded += 1
        return sol, degraded


def ordinary_kriging(
    fit_pts: np.ndarray,
    fit_vals: np.ndarray,
    target_pts: np.ndarray,
    variogram: VariogramFit,
    k: int = 12,
    anisotropy_angle: float = 0.0,
    anisotropy_ratio: float = 1.0,
    solve_backend: str = "auto",
) -> KrigingResult:
    """Ordinary Kriging of ``target_pts`` from k-nearest neighbourhoods.

    Per target with its k nearest samples, solves the small OK system

        [Γ + nugget·I  1][w]   [γ₀]
        [1ᵀ           0][μ] = [1 ]

    (Γ holds sample-sample semivariances, γ₀ sample-target ones).
    Prediction = wᵗz; kriging variance = wᵗγ₀ + μ (clamped ≥ 0). Systems are
    solved in vectorized ``_SOLVE_CHUNK`` batches; singular batches fall
    back to per-row solves and finally to the neighbourhood mean with the
    sample variance as a conservative uncertainty (counted in
    ``degraded_cells`` — never silent).

    V2: ``anisotropy_angle``/``anisotropy_ratio`` apply the shared geometric
    anisotropy map to sample AND target coordinates before the kD-tree and
    all semivariance math (defaults = identity, bit-identical path);
    ``solve_backend`` forces the linear-solve implementation (``auto`` =
    historical batched numpy with per-row fallback); the executed backend
    is reported in ``solve_backend_used``.
    """
    solve_backend = _validate_solve_backend(solve_backend)
    n = len(fit_vals)
    if n < 2:
        # MINOR-3（数值评审）：n=1 时 k=max(2,…) 越界 → cKDTree padding
        # 索引触发 IndexError；公开 API 应类型化拒绝。
        from app.lib.gis.scientific_errors import InsufficientSamples

        raise InsufficientSamples(
            f"ordinary kriging needs at least 2 samples (got {n})")
    k = int(max(2, min(k, MAX_NEIGHBORS, n)))
    fit_pts_t = apply_anisotropy(fit_pts, anisotropy_angle, anisotropy_ratio)
    target_pts_t = apply_anisotropy(target_pts, anisotropy_angle, anisotropy_ratio)
    tree = cKDTree(fit_pts_t)
    dist_t, idx_t = tree.query(target_pts_t, k=k)
    n_t = len(target_pts)
    dist_t = np.asarray(dist_t).reshape(n_t, k)
    idx_t = np.asarray(idx_t).reshape(n_t, k)

    g = variogram
    preds = np.empty(n_t, dtype=float)
    varis = np.empty(n_t, dtype=float)
    degraded = 0
    # Canonical OK semivariance construction (Isaaks & Srivastava): the
    # fitted nugget appears in EVERY off-diagonal γ(h>0) entry and in γ₀,
    # with a ZERO sample-sample diagonal (γ(0)=0). A nugget added to the
    # diagonal only (the earlier draft) is NOT algebraically equivalent —
    # the weights oscillate on nuggety data and predictions/variances blow
    # up (numerics review #1: RMSE 4.95 vs canonical 0.56 at noise σ=1.5).
    #
    # Gaussian-model systems are additionally ill-conditioned at short lags
    # (γ → 0 quadratically), so a small solve-time ridge stays on the
    # diagonal; predictions beyond the sample range ±3√sill are clamped.
    # Both interventions are counted in degraded_cells, never silent.
    # Matérn behaves like the gaussian family at short lags only for large ν
    # (small ν is exponential-like and well conditioned) → ν-dependent boost.
    ridge = 1e-6 * max(abs(g.sill), abs(g.nugget), 1e-12)
    if g.model == "gaussian" or (g.model == "matern" and g.nu >= 2.0):
        ridge = max(ridge, 0.01 * abs(g.sill))
    clamp_lo = float(fit_vals.min() - 3.0 * np.sqrt(abs(g.sill)))
    clamp_hi = float(fit_vals.max() + 3.0 * np.sqrt(abs(g.sill)))

    for start in cancellable(range(0, n_t, _SOLVE_CHUNK), every=1):
        end = min(start + _SOLVE_CHUNK, n_t)
        nb_idx = idx_t[start:end]
        nb_d = dist_t[start:end]                      # (c, k)
        nb_xy = fit_pts_t[nb_idx]                     # (c, k, 2) anisotropic space
        nb_v = fit_vals[nb_idx]                       # (c, k)

        # sample-sample semivariances WITH nugget (c, k, k), zero diagonal
        diff = nb_xy[:, :, None, :] - nb_xy[:, None, :, :]
        d_ss = np.sqrt((diff ** 2).sum(axis=-1))
        gamma_ss = _gamma(g.model, d_ss, g.sill, g.range_m, g.nugget, nu=g.nu)
        idx_diag = np.arange(k)
        gamma_ss[:, idx_diag, idx_diag] = 0.0
        gamma_ss[:, idx_diag, idx_diag] = ridge

        c = end - start
        mat = np.zeros((c, k + 1, k + 1))
        mat[:, :k, :k] = gamma_ss
        mat[:, k, :k] = 1.0
        mat[:, :k, k] = 1.0
        rhs = np.ones((c, k + 1))
        # γ₀ also carries the nugget — a target exactly at a sample site
        # then recovers the sample value (exact interpolation honoured).
        rhs[:, :k] = _gamma(g.model, nb_d, g.sill, g.range_m, g.nugget, nu=g.nu)

        sol, row_degraded = _solve_kriging_systems(mat, rhs, solve_backend)
        failed = np.isnan(sol[:, 0])
        degraded += row_degraded
        chunk_pred = np.einsum("ck,ck->c", sol[:, :k], nb_v)
        chunk_var = np.einsum("ck,ck->c", sol[:, :k], rhs[:, :k]) + sol[:, k]
        if failed.any():
            # neighbourhood-mean fallback for rows the backend could not
            # solve (conservative local variance — counted, never silent)
            chunk_pred[failed] = [float(np.mean(nb_v[r])) for r in np.nonzero(failed)[0]]
            chunk_var[failed] = [
                float(np.var(nb_v[r])) if k > 1 else float(g.sill)
                for r in np.nonzero(failed)[0]
            ]

        # Negative kriging variances are numerical noise near zero — clamp
        # AND count them (an uncertainty surface must never hide solve
        # failures behind zeros).
        neg_var = chunk_var < 0.0
        if neg_var.any():
            degraded += int(neg_var.sum())
            np.clip(chunk_var, 0.0, None, out=chunk_var)
        clamped = (chunk_pred < clamp_lo) | (chunk_pred > clamp_hi)
        if clamped.any():
            degraded += int(clamped.sum())
            np.clip(chunk_pred, clamp_lo, clamp_hi, out=chunk_pred)
        preds[start:end] = chunk_pred
        varis[start:end] = chunk_var

    # science-v3（Wave 8/9）：95% 预测区间（高斯误差假设下的近似区间，
    # z=1.959963…，Isaaks & Srivastava 口径）。
    _PI95 = 1.959963984540054
    _sd = np.sqrt(varis)
    return KrigingResult(
        predictions=preds,
        variances=varis,
        pi95_low=preds - _PI95 * _sd,
        pi95_high=preds + _PI95 * _sd,
        variogram=g,
        n_samples=n,
        n_samples_fit=n,
        neighbors=k,
        degraded_cells=degraded,
        solve_backend_used="numpy_batched" if solve_backend == "auto" else solve_backend,
    )


# ── universal kriging (VNext — additive; OK path untouched) ─────────────────

def ols_linear_trend(
    pts_metric: np.ndarray, values: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """OLS fit of the linear drift ``z ~ [1, x, y]``.

    Returns ``(beta (3,), residuals (n,))`` — the drift coefficients and the
    detrended residuals the UK variogram is fitted on.
    """
    pts = np.asarray(pts_metric, dtype=float)
    F = np.column_stack([np.ones(len(pts)), pts])
    beta, *_ = np.linalg.lstsq(F, np.asarray(values, dtype=float), rcond=None)
    resid = np.asarray(values, dtype=float) - F @ beta
    return beta, resid


def _zero_residual_spread(resid: np.ndarray, values: np.ndarray) -> bool:
    """True when the OLS residuals are zero to machine precision.

    Values lying exactly on a plane (including the constant field) detrend to
    residuals whose spread is ≤1e-9 of the value scale — treating them as
    nonzero noise would fit a fake micro-variogram to float dust.
    """
    scale = float(np.max(np.abs(values))) if len(values) else 0.0
    spread = float(np.max(np.abs(resid), initial=0.0))
    if scale == 0.0:
        return spread == 0.0
    return spread <= 1e-9 * scale


def _trend_only_result(
    beta: np.ndarray, target_pts: np.ndarray, n_samples: int
) -> KrigingResult:
    """Exact-trend prediction for the zero-residual degenerate case.

    Honest by construction: variance is exactly 0 (there is no residual
    process), no variogram object is manufactured, and the disclosure flag
    makes the degeneracy visible to the caller.
    """
    targets = np.asarray(target_pts, dtype=float)
    F_t = np.column_stack([np.ones(len(targets)), targets])
    return KrigingResult(
        predictions=F_t @ beta,
        variances=np.zeros(len(targets), dtype=float),
        variogram=None,
        n_samples=n_samples,
        n_samples_fit=n_samples,
        neighbors=0,
        degraded_cells=0,
        disclosures=["zero_residual_variance"],
        drift_coefficients=beta,
    )


def universal_kriging(
    fit_pts: np.ndarray,
    fit_vals: np.ndarray,
    target_pts: np.ndarray,
    variogram: VariogramFit,
    k: int = 12,
    anisotropy_angle: float = 0.0,
    anisotropy_ratio: float = 1.0,
    solve_backend: str = "auto",
) -> KrigingResult:
    """Universal Kriging of ``target_pts`` from k-nearest neighbourhoods.

    Same batching / ridge / clamp / degraded-cell accounting as the OK
    solver, but each system carries the linear-drift trend constraints with
    one Lagrange multiplier per drift term:

        [Γ  F][w]   [γ0]      Γ/γ0 from the variogram fitted on the
        [Fᵀ  0][m] = [f0]     OLS-detrended residuals (caller's duty)

    Prediction = wᵗz; kriging variance = wᵗγ0 + mᵗf0 (clamped ≥ 0, counted
    when negative). The neighbourhood needs ≥ ``_TREND_TERMS + 1`` samples —
    k is raised to 4 when the caller asks for less (fewer samples cannot
    constrain the drift and the system is singular).

    V2: anisotropy/solve-backend semantics identical to :func:`ordinary_kriging`
    — the drift design F stays in REAL coordinates (the trend is a physical
    field, not a distance); only the semivariance distances use the
    anisotropic space.
    """
    solve_backend = _validate_solve_backend(solve_backend)
    n = len(fit_vals)
    if n < _TREND_TERMS + 1:
        raise KrigingInputError(
            f"universal_kriging 需要至少 {_TREND_TERMS + 1} 个样本约束线性漂移，got {n}"
        )
    k = int(max(_TREND_TERMS + 1, min(k, MAX_NEIGHBORS, n)))
    fit_pts_t = apply_anisotropy(fit_pts, anisotropy_angle, anisotropy_ratio)
    target_pts_t = apply_anisotropy(target_pts, anisotropy_angle, anisotropy_ratio)
    tree = cKDTree(fit_pts_t)
    dist_t, idx_t = tree.query(target_pts_t, k=k)
    n_t = len(target_pts)
    dist_t = np.asarray(dist_t).reshape(n_t, k)
    idx_t = np.asarray(idx_t).reshape(n_t, k)

    g = variogram
    preds = np.empty(n_t, dtype=float)
    varis = np.empty(n_t, dtype=float)
    degraded = 0
    # Same solve-time stabilisation policy as OK: ridge on the diagonal
    # (stronger for the ill-conditioned gaussian model; matern only when
    # ν ≥ 2 is gaussian-like), predictions clamped to ±3√sill beyond the
    # sample range; every intervention counted in degraded_cells, never
    # silent.
    ridge = 1e-6 * max(abs(g.sill), abs(g.nugget), 1e-12)
    if g.model == "gaussian" or (g.model == "matern" and g.nu >= 2.0):
        ridge = max(ridge, 0.01 * abs(g.sill))
    clamp_lo = float(fit_vals.min() - 3.0 * np.sqrt(abs(g.sill)))
    clamp_hi = float(fit_vals.max() + 3.0 * np.sqrt(abs(g.sill)))

    for start in cancellable(range(0, n_t, _SOLVE_CHUNK), every=1):
        end = min(start + _SOLVE_CHUNK, n_t)
        nb_idx = idx_t[start:end]
        nb_d = dist_t[start:end]                      # (c, k) anisotropic space
        nb_xy = fit_pts[nb_idx]                       # (c, k, 2) REAL coords (drift)
        nb_xy_t = fit_pts_t[nb_idx]                   # (c, k, 2) anisotropic space
        nb_v = fit_vals[nb_idx]                       # (c, k)
        c = end - start

        # sample-sample semivariances WITH nugget, zero diagonal (canonical
        # construction shared with OK) — anisotropic space
        diff = nb_xy_t[:, :, None, :] - nb_xy_t[:, None, :, :]
        d_ss = np.sqrt((diff ** 2).sum(axis=-1))
        gamma_ss = _gamma(g.model, d_ss, g.sill, g.range_m, g.nugget, nu=g.nu)
        idx_diag = np.arange(k)
        gamma_ss[:, idx_diag, idx_diag] = 0.0
        gamma_ss[:, idx_diag, idx_diag] = ridge

        # drift design: F rows [1, x_i, y_i] in REAL coordinates; f0 likewise
        F = np.concatenate([np.ones((c, k, 1)), nb_xy], axis=2)      # (c, k, 3)
        f0 = np.column_stack([np.ones(c), target_pts[start:end]])    # (c, 3)

        mat = np.zeros((c, k + _TREND_TERMS, k + _TREND_TERMS))
        mat[:, :k, :k] = gamma_ss
        mat[:, :k, k:] = F
        mat[:, k:, :k] = F.transpose(0, 2, 1)
        rhs = np.zeros((c, k + _TREND_TERMS))
        # γ₀ carries the nugget (exact interpolation at sample sites honoured)
        rhs[:, :k] = _gamma(g.model, nb_d, g.sill, g.range_m, g.nugget, nu=g.nu)
        rhs[:, k:] = f0

        sol, row_degraded = _solve_kriging_systems(mat, rhs, solve_backend)
        failed = np.isnan(sol[:, 0])
        degraded += row_degraded
        chunk_pred = np.einsum("ck,ck->c", sol[:, :k], nb_v)
        chunk_var = np.einsum("ck,ck->c", sol[:, :k], rhs[:, :k]) + np.einsum(
            "cf,cf->c", sol[:, k:], f0
        )
        if failed.any():
            chunk_pred[failed] = [float(np.mean(nb_v[r])) for r in np.nonzero(failed)[0]]
            chunk_var[failed] = [
                float(np.var(nb_v[r])) if k > 1 else float(g.sill)
                for r in np.nonzero(failed)[0]
            ]

        neg_var = chunk_var < 0.0
        if neg_var.any():
            degraded += int(neg_var.sum())
            np.clip(chunk_var, 0.0, None, out=chunk_var)
        clamped = (chunk_pred < clamp_lo) | (chunk_pred > clamp_hi)
        if clamped.any():
            degraded += int(clamped.sum())
            np.clip(chunk_pred, clamp_lo, clamp_hi, out=chunk_pred)
        preds[start:end] = chunk_pred
        varis[start:end] = chunk_var

    # science-v3（Wave 8/9）：95% 预测区间（高斯误差假设下的近似区间，
    # z=1.959963…，Isaaks & Srivastava 口径）。
    _PI95 = 1.959963984540054
    _sd = np.sqrt(varis)
    return KrigingResult(
        predictions=preds,
        variances=varis,
        pi95_low=preds - _PI95 * _sd,
        pi95_high=preds + _PI95 * _sd,
        variogram=g,
        n_samples=n,
        n_samples_fit=n,
        neighbors=k,
        degraded_cells=degraded,
        solve_backend_used="numpy_batched" if solve_backend == "auto" else solve_backend,
    )


def universal_kriging_detrended(
    fit_pts: np.ndarray,
    fit_vals: np.ndarray,
    target_pts: np.ndarray,
    variogram_model: str = "auto",
    k: int = 12,
    anisotropy_angle: float = 0.0,
    anisotropy_ratio: float = 1.0,
    matern_smoothness: float = MATERN_SMOOTHNESS_DEFAULT,
    solve_backend: str = "auto",
) -> KrigingResult:
    """Full UK pipeline: OLS-detrend → residual variogram → UK solve.

    The variogram is fitted on the residuals of the OLS linear drift
    (``z − [1,x,y]·β̂``), NOT on the raw values: UK models a second-order
    stationary residual process around the trend, so raw-value variography
    would credit the trend to spatial correlation and inflate range/sill
    (Isaaks & Srivastava ch. 12 practice).

    Zero-residual degenerate case (values exactly planar): returns the exact
    trend prediction with zero kriging variance and the disclosure flag
    ``"zero_residual_variance"`` — no variogram is fitted or faked.
    """
    beta, resid = ols_linear_trend(fit_pts, fit_vals)
    if _zero_residual_spread(resid, fit_vals):
        return _trend_only_result(beta, target_pts, n_samples=len(fit_vals))
    vfit = fit_variogram(
        fit_pts, resid, model=variogram_model,
        anisotropy_angle=anisotropy_angle, anisotropy_ratio=anisotropy_ratio,
        matern_smoothness=matern_smoothness,
    )
    res = universal_kriging(
        fit_pts, fit_vals, target_pts, vfit, k=k,
        anisotropy_angle=anisotropy_angle, anisotropy_ratio=anisotropy_ratio,
        solve_backend=solve_backend,
    )
    res.drift_coefficients = beta
    return res


# ── cross validation ────────────────────────────────────────────────────────

@dataclass
class CrossValidationReport:
    """K-fold CV of the full fit+predict pipeline (no leakage across folds)."""

    rmse: Optional[float] = None
    mae: Optional[float] = None
    bias: Optional[float] = None
    r2: Optional[float] = None
    n_samples: int = 0
    folds: int = 0
    note: str = ""
    # ── V2 additive: fold-assignment scheme + per-fold evidence ────────────
    scheme: str = "index"       # "index" | "spatial_block"
    per_fold: list = field(default_factory=list)  # [{fold, rmse, n_test, block_ids}]
    # ── science-v3（Wave 8/9）：不确定性校准证据 —— LOOCV 误差与逐点
    # 克里金 σ 的 z-score 统计（σ 是否可信的直接度量）。
    z_score_mean: Optional[float] = None    # ≈0 = 无系统偏差
    z_coverage_95: Optional[float] = None   # |z|≤1.96 的比例（标定好 ≈0.95）
    z_count: int = 0                        # 参与统计的测试点数

    def metrics(self) -> dict[str, Any]:
        out: dict[str, Any] = {"n_samples": self.n_samples, "folds": self.folds}
        for key in ("rmse", "mae", "bias", "r2"):
            v = getattr(self, key)
            out[key] = round(v, 6) if v is not None else None
        if self.note:
            out["note"] = self.note
        out["scheme"] = self.scheme
        # 不确定性校准证据（science-v3）：z ≈0 均值 + |z|≤1.96 覆盖率
        # ≈0.95 表示克里金 σ 与实际误差尺度一致。
        if self.z_count > 0:
            out["uncertainty_calibration"] = {
                "z_score_mean": (
                    round(self.z_score_mean, 6)
                    if self.z_score_mean is not None else None),
                "z_coverage_95": (
                    round(self.z_coverage_95, 6)
                    if self.z_coverage_95 is not None else None),
                "n": self.z_count,
            }
        if self.per_fold:
            out["per_fold"] = [
                {
                    "fold": f["fold"],
                    "rmse": round(f["rmse"], 6) if f.get("rmse") is not None else None,
                    "n_test": f.get("n_test", 0),
                    "block_ids": list(f.get("block_ids", [])),
                }
                for f in self.per_fold
            ]
        return out


def _spatial_block_folds(xy: np.ndarray, folds: int) -> tuple[np.ndarray, np.ndarray]:
    """Deterministic grid-stratified fold assignment (NO RNG).

    Samples are ranked by x and by y (dense ranks via double argsort —
    stable under input reordering), snapped to a ⌈√folds⌉ × ⌈√folds⌉ block
    grid, and each block maps to fold ``block_id % folds``. Clustered
    samples therefore share one fold and are validated against spatially
    distant blocks — the honest error of an extrapolative design.
    Returns ``(fold_id, block_id)``.
    """
    n = len(xy)
    n_grid = max(1, int(math.ceil(math.sqrt(folds))))
    rx = np.argsort(np.argsort(xy[:, 0], kind="stable"), kind="stable")
    ry = np.argsort(np.argsort(xy[:, 1], kind="stable"), kind="stable")
    gx = (rx * n_grid) // max(n, 1)
    gy = (ry * n_grid) // max(n, 1)
    block = gy * n_grid + gx
    return block % folds, block


def cross_validate_kriging(
    pts_metric: np.ndarray,
    values: np.ndarray,
    model: str = "auto",
    folds: int = CV_FOLDS,
    k: int = 12,
    method: str = "ordinary",
    cv_scheme: str = "index",
    anisotropy_angle: float = 0.0,
    anisotropy_ratio: float = 1.0,
    matern_smoothness: float = MATERN_SMOOTHNESS_DEFAULT,
    solve_backend: str = "auto",
) -> CrossValidationReport:
    """K-fold CV where each fold refits the full pipeline on the training
    split — ordinary: refit the variogram; universal: refit the OLS trend AND
    the residual variogram (no leakage across folds).

    ``cv_scheme="index"`` (default) keeps the historical index-strided
    assignment; ``cv_scheme="spatial_block"`` assigns deterministic
    grid-stratified blocks on the sorted x/y ranks (no RNG) — clustered
    sampling designs are validated against spatially distant blocks and
    report their honest (worse) error, with per-fold RMSE + block ids.

    Below ``MIN_CV_SAMPLES`` the report honestly declines to produce metrics
    instead of emitting statistically meaningless numbers.
    """
    if method not in ("ordinary", "universal"):
        raise KrigingInputError(
            f"method 必须是 'ordinary' 或 'universal'，got {method!r}"
        )
    if cv_scheme not in ("index", "spatial_block"):
        raise KrigingInputError(
            f"cv_scheme 必须是 'index' 或 'spatial_block'，got {cv_scheme!r}"
        )
    solve_backend = _validate_solve_backend(solve_backend)
    n = len(values)
    if n < MIN_CV_SAMPLES:
        return CrossValidationReport(
            n_samples=n, folds=0, scheme=cv_scheme,
            note=(
                f"样本量 {n} < {MIN_CV_SAMPLES}，无法进行可靠的交叉验证；"
                "不确定性仅由克里金方差表达。"
            ),
        )
    folds = max(2, min(folds, n // 4))
    # deterministic fold assignment (no RNG): index-strided folds keep
    # spatial mixing reasonable without carrying shuffle state;
    # spatial_block snaps sorted x/y ranks onto a ⌈√folds⌉ block grid.
    if cv_scheme == "spatial_block":
        fold_id, block_id = _spatial_block_folds(pts_metric, folds)
    else:
        fold_id = np.arange(n) % folds
        block_id = None
    pts_t = apply_anisotropy(pts_metric, anisotropy_angle, anisotropy_ratio)
    errs: list[float] = []
    z_scores: list[float] = []
    folds_used = 0
    per_fold: list[dict] = []
    for f in range(folds):
        test = fold_id == f
        train_xy, train_v = pts_t[~test], values[~test]
        if len(train_v) < MIN_SAMPLES:
            continue
        try:
            if method == "universal":
                res = universal_kriging_detrended(
                    train_xy, train_v, pts_t[test],
                    variogram_model=model, k=k,
                    anisotropy_angle=anisotropy_angle,
                    anisotropy_ratio=anisotropy_ratio,
                    matern_smoothness=matern_smoothness,
                    solve_backend=solve_backend,
                )
            else:
                vfit = fit_variogram(
                    train_xy, train_v, model=model,
                    anisotropy_angle=anisotropy_angle,
                    anisotropy_ratio=anisotropy_ratio,
                    matern_smoothness=matern_smoothness,
                )
                res = ordinary_kriging(
                    train_xy, train_v, pts_t[test], vfit, k=k,
                    anisotropy_angle=anisotropy_angle,
                    anisotropy_ratio=anisotropy_ratio,
                    solve_backend=solve_backend,
                )
        except KrigingInputError:
            continue
        folds_used += 1
        fold_err = res.predictions - values[test]
        errs.extend(fold_err.tolist())
        z_scores.extend(
            (fold_err / np.sqrt(np.maximum(res.variances, 0.0))).tolist())
        entry: dict = {
            "fold": int(f),
            "rmse": float(np.sqrt(np.mean(fold_err ** 2))),
            "n_test": int(test.sum()),
        }
        if block_id is not None:
            entry["block_ids"] = sorted(int(b) for b in np.unique(block_id[test]))
        per_fold.append(entry)
    if not errs:
        return CrossValidationReport(
            n_samples=n, folds=folds_used, scheme=cv_scheme,
            note="所有折的变异函数拟合均失败，无法给出交叉验证指标。",
        )
    e = np.asarray(errs)
    ss_res = float(np.sum(e ** 2))
    ss_tot = float(np.sum((values - values.mean()) ** 2))
    z_mean: Optional[float] = None
    z_cover: Optional[float] = None
    if z_scores:
        z = np.asarray([v for v in z_scores if np.isfinite(v)])
        if z.size:
            z_mean = float(np.mean(z))
            z_cover = float(np.mean(np.abs(z) <= 1.96))
    return CrossValidationReport(
        rmse=float(np.sqrt(np.mean(e ** 2))),
        mae=float(np.mean(np.abs(e))),
        bias=float(np.mean(e)),
        r2=(1.0 - ss_res / ss_tot) if ss_tot > 0 else None,
        n_samples=n,
        folds=folds_used,
        scheme=cv_scheme,
        per_fold=per_fold,
        z_score_mean=z_mean,
        z_coverage_95=z_cover,
        z_count=len(z_scores),
    )


# ── V3：方向变异函数 / 模型选择 / 指示克里金 / 协同克里金 / 块克里金 ────────

def directional_variogram(
    pts_metric: np.ndarray,
    values: np.ndarray,
    azimuth_deg: float,
    tolerance_deg: float = DIRECTIONAL_DEFAULT_TOLERANCE_DEG,
    band_width: Optional[float] = None,
    n_lags: int = 12,
    max_pairs: int = MAX_PAIRS,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, dict]:
    """单方位角轴向经验半变异函数（方向变差函数）。

    方位角约定（本模块统一披露）：**数学约定** —— 0° = 东(+x) 轴、逆时针
    为正，与 ``anisotropy_angle`` 同一约定（不是罗盘方位角 0°=北）。配对
    过滤是**双向轴向**语义（轴而非射线）：方位角 +180° 的配对向量属同一
    条轴，因此 ``azimuth_deg`` 与 ``azimuth_deg + 180`` 返回逐位一致的曲线。

    ``tolerance_deg`` 为轴向半角（0 < t ≤ 90；90 = 全向，各向同性退化）。
    ``band_width``（工作 CRS 单位）进一步限制配对中点到轴线的垂距
    （GSLIB band 语义），进 meta 披露。滞后 bin 沿用
    :func:`empirical_variogram` 约定（同一 span/edges/空 bin 丢弃）；
    配对行走沿用行步幅策略控制 ``max_pairs`` 预算，确定性。

    入口统一预抽稀（审计 §4 第 7 行）：样本超过拟合上限
    :data:`MAX_FIT_POINTS` 时先做确定性分层抽稀（与 ``fit_variogram``
    同一机器）——否则行步幅会把方向过滤后的有效配对压到统计无效的极少数；
    meta 以 ``n_samples``（实际使用）/ ``n_samples_input``（原始）/
    ``subsample_applied`` 披露。

    返回 ``(lags, gamma, pair_counts, meta)``。
    """
    pts = np.asarray(pts_metric, dtype=float)
    vals = np.asarray(values, dtype=float)
    n = len(vals)
    if n < 2:
        raise KrigingInputError(f"方向变异函数至少需要 2 个样本点，got {n}")
    tol = float(tolerance_deg)
    if not (math.isfinite(tol) and 0.0 < tol <= DIRECTIONAL_MAX_TOLERANCE_DEG):
        raise KrigingInputError(
            f"tolerance_deg 必须在 (0, {DIRECTIONAL_MAX_TOLERANCE_DEG}] 内"
            f"（轴向半角，{DIRECTIONAL_MAX_TOLERANCE_DEG}=全向），got {tolerance_deg!r}"
        )
    az = float(azimuth_deg)
    if not math.isfinite(az):
        raise KrigingInputError(f"azimuth_deg 必须是有限角度（度），got {azimuth_deg!r}")
    bw: Optional[float] = None
    if band_width is not None:
        bw = float(band_width)
        if not (math.isfinite(bw) and bw > 0):
            raise KrigingInputError(
                f"band_width 必须为正数（工作 CRS 单位），got {band_width!r}"
            )

    n_input = int(len(vals))
    pts, vals = stratified_subsample(pts, vals, MAX_FIT_POINTS)
    n = int(len(vals))

    n_lags = max(4, min(int(n_lags), 64))
    span = float(np.linalg.norm(pts.max(axis=0) - pts.min(axis=0))) or 1.0
    edges = np.linspace(0.0, span, n_lags + 1)
    sum_g = np.zeros(n_lags)
    cnt = np.zeros(n_lags, dtype=np.int64)

    t = math.radians(az)
    ux, uy = math.cos(t), math.sin(t)
    total_pairs = n * (n - 1) // 2
    stride = max(1, int(math.ceil(total_pairs / max(max_pairs, 1))))
    kept_pairs = 0
    for i in cancellable(range(0, n, stride), every=64):
        v = pts[i + 1:] - pts[i]                      # (m, 2) 配对向量
        d = np.sqrt((v ** 2).sum(axis=1))
        ang = np.degrees(np.arctan2(v[:, 1], v[:, 0]))
        # 折叠到轴向 [0, 90]：方位角与其反向属同一条轴（双向语义）
        rel = np.abs((ang - az) % 180.0)
        rel = np.minimum(rel, 180.0 - rel)
        keep = rel <= tol
        if bw is not None:
            perp = np.abs(ux * v[:, 1] - uy * v[:, 0])   # 到轴线的垂距
            keep &= perp <= bw
        b = np.searchsorted(edges, d, side="right") - 1
        valid = keep & (b >= 0) & (b < n_lags)
        if not valid.any():
            continue
        dv2 = (vals[i + 1:] - vals[i]) ** 2
        np.add.at(sum_g, b[valid], dv2[valid])
        np.add.at(cnt, b[valid], 1)
        kept_pairs += int(valid.sum())

    has = cnt > 0
    lags = 0.5 * (edges[:-1] + edges[1:])[has]
    gamma = (0.5 * sum_g[has]) / cnt[has]
    meta = {
        "method": "directional_variogram",
        "azimuth_deg": float(az),
        "azimuth_convention": (
            "数学约定：0°=东(+x)、逆时针（与 anisotropy_angle 一致，非罗盘方位）；"
            "轴向双向（+180° 同轴，曲线逐位一致）"
        ),
        "tolerance_deg": float(tol),
        "band_width": bw,
        "n_samples": int(n),
        "n_samples_input": int(n_input),
        "subsample_applied": bool(n_input > n),
        "n_pairs_kept": int(kept_pairs),
        "n_pairs_total": int(total_pairs),
        "n_bins": int(len(lags)),
    }
    return lags, gamma, cnt[has], meta


def _level_crossing_lag(
    lags: np.ndarray,
    gamma: np.ndarray,
    counts: np.ndarray,
    level: float,
    cap: float,
    min_count: int = 3,
) -> Optional[float]:
    """经验曲线首达绝对 γ 水平 ``level`` 的滞后（线性插值，模型无关的程距代理）。

    ``fit_anisotropy`` 的方向程距读取器：调用方以 ``sill 的水平分位``
    （sill 以边际方差为锚，平稳场 γ(∞)=var）传入绝对水平，搜索窗口
    截断在 ``cap``（尾部配对稀疏且超 sill 的 bin 只携带噪声）。
    配对数 < min_count 的 bin 丢弃；全曲线未达 level 时返回 None
    （该方位/水平不进拟合）。
    """
    keep = counts >= min_count
    lags = np.minimum(lags[keep], cap)
    gamma = gamma[keep]
    if len(lags) < 2:
        return None
    idx = np.nonzero(gamma >= level)[0]
    if len(idx) == 0:
        return None
    i = int(idx[0])
    if i == 0:
        return float(max(lags[0] * level / max(float(gamma[0]), 1e-12), 1e-9))
    g0, g1 = float(gamma[i - 1]), float(gamma[i])
    if g1 <= g0:
        return float(lags[i])
    frac = (level - g0) / (g1 - g0)
    return max(float(lags[i - 1] + frac * (lags[i] - lags[i - 1])), 1e-9)


def fit_anisotropy(
    pts_metric: np.ndarray,
    values: np.ndarray,
    azimuth_step_deg: float = ANISOTROPY_SCAN_AZIMUTH_STEP_DEG,
    tolerance_deg: float = ANISOTROPY_SCAN_TOLERANCE_DEG,
    n_lags: int = ANISOTROPY_SCAN_N_LAGS,
    max_pairs: int = MAX_PAIRS,
) -> dict:
    """各向异性自动拟合：多方位角方向变异函数扫描 → 几何椭圆拟合。

    流程（审计 §8 建议 #2；Webster & Oliver 2007 实践）：

    1. 以 ``azimuth_step_deg``（默认 22.5° → 8 方位 0..157.5，双向轴向、
       轴向半角 ``tolerance_deg``）扫描 :func:`directional_variogram`；
    2. 每方位以"经验曲线达 sill 水平分位的滞后"为方向程距代理
       （sill 以边际方差为锚，线性插值，尾部截断 0.6·span）——
       ``directional_ranges`` 输出 0.5·sill 水平的各方位程距；
    3. 几何椭圆拟合：对水平 ℓ ∈ {0.35, 0.45, 0.55, 0.65}·sill 逐水平
       读取穿越滞后 h_ℓ(θ)（∝ 1/q(θ)），按水平归一（消除逐水平尺度）、
       跨全部方位×水平池化后做**闭式线性最小二乘**
       ``q²(θ) = m − c₂·cos2θ − s₂·sin2θ``
       （几何各向异性恒等式 q²(θ)=cos²ψ+ρ²sin²ψ 的线性形式，
       ψ=θ−φ）。主轴方位角 ``φ = ½·atan2(s₂, c₂)``，
       程距比 ``ρ = sqrt((m+d)/(m−d))``，d=hypot(c₂,s₂)，
       钳制到 :data:`ANISOTROPY_RATIO_CLAMP` = [1.0, 4.0]
       （防退化/防爆炸）。池化（8 方位 × 4 水平 ≈ 32 个读取）比
       单水平逐方位拟合显著降低单次实现噪声。

    角度约定（与 :func:`anisotropy_transform` 实现核对一致）：
    ``angle_degrees`` = **长轴方位角**，数学约定 0°=东(+x)、逆时针为正、
    折叠到 [0, 180)。``anisotropy_transform`` 以 ``A = diag(1, ratio)·R(−θ)``
    使主轴位移保长（长轴程距 = α）、垂直方向拉伸 ``ratio``（短轴程距 =
    α/ratio）——因此 ``(angle_degrees, ratio)`` 可直接作为
    :func:`apply_anisotropy` / :func:`fit_variogram` 的
    ``(anisotropy_angle, anisotropy_ratio)`` 输入（meta 逐字披露）。

    确定性：无 RNG（扫描 / 读取 / lstsq 全确定性）。返回 dict：
    ``{angle_degrees, ratio, directional_ranges, is_anisotropic, meta}``；
    ``is_anisotropic = ratio ≥ ANISOTROPY_RATIO_THRESHOLD``（1.2 判别阈值，
    各向同性场不误报）。m−d ≤ 0 的退化场合确定性回退：angle=最大程距
    方位角、ratio=方位程距极值比（meta 以 ``ellipse_fit_degenerate``
    披露）。
    """
    pts = np.asarray(pts_metric, dtype=float)
    vals = np.asarray(values, dtype=float)
    n_input = int(len(vals))
    if n_input < 2 * MIN_SAMPLES:
        raise KrigingInputError(
            f"各向异性自动拟合至少需要 {2 * MIN_SAMPLES} 个样本点"
            f"（8 方位扫描每方位需足够的方向配对），got {n_input}"
        )
    step = float(azimuth_step_deg)
    if not (math.isfinite(step) and 0.0 < step <= 90.0):
        raise KrigingInputError(
            f"azimuth_step_deg 必须在 (0, 90] 内（180°/step 个方位），got {azimuth_step_deg!r}"
        )
    tol = float(tolerance_deg)
    if not (math.isfinite(tol) and 0.0 < tol <= DIRECTIONAL_MAX_TOLERANCE_DEG):
        raise KrigingInputError(
            f"tolerance_deg 必须在 (0, {DIRECTIONAL_MAX_TOLERANCE_DEG}] 内，got {tolerance_deg!r}"
        )

    # 与 fit_variogram 同一确定性抽稀上限（directional_variogram 内部亦做，
    # 这里先做一次保证 sill/span 锚与扫描输入同源）
    pts, vals = stratified_subsample(pts, vals, MAX_FIT_POINTS)
    n_used = int(len(vals))
    sill = float(np.var(vals))
    span = float(np.linalg.norm(pts.max(axis=0) - pts.min(axis=0))) or 1.0
    cap = 0.6 * span

    n_az = int(round(180.0 / step))
    azimuths = [i * step for i in range(n_az)]
    curves: list[tuple[float, np.ndarray, np.ndarray, np.ndarray]] = []
    per_az: list[dict] = []
    for az in azimuths:
        lags, gamma, counts, _m = directional_variogram(
            pts, vals, az, tolerance_deg=tol, n_lags=n_lags, max_pairs=max_pairs
        )
        curves.append((float(az), lags, gamma, counts.astype(float)))
        r_half = _level_crossing_lag(lags, gamma, counts.astype(float), 0.5 * sill, cap)
        per_az.append({
            "azimuth_deg": float(az),
            "range_m": r_half,                       # 0.5·sill 方向程距代理
            "range_source": "half_sill_crossing",
            "n_pairs": int(counts.sum()),
            "n_bins": int(len(lags)),
        })

    # ── 池化椭圆拟合：q²(θ) = m − c₂cos2θ − s₂sin2θ（逐水平归一）──
    # y = 1/h(θ)² ∝ q²(θ)（h = h_iso/q ⇒ 1/h² = q²/h_iso²）
    fit_azs: list[float] = []
    fit_ys: list[float] = []
    for level in _ANISOTROPY_LEVELS:
        row: list[Optional[float]] = [
            _level_crossing_lag(lags, gamma, cnt, level * sill, cap)
            for _az, lags, gamma, cnt in curves
        ]
        present = [(az, 1.0 / (v * v)) for az, v in zip(azimuths, row) if v is not None]
        if len(present) < 4:
            continue
        med = float(np.median([v for _az, v in present]))
        if med <= 0.0:
            continue
        for az, v in present:
            fit_azs.append(az)
            fit_ys.append(v / med)          # 归一后 ∝ q²(θ)

    degenerate = False
    ratio_raw: Optional[float] = None
    if len(fit_azs) >= 8:
        theta = np.radians(fit_azs)
        y = np.array(fit_ys)
        design = np.column_stack(
            (np.ones_like(theta), -np.cos(2.0 * theta), -np.sin(2.0 * theta))
        )
        coef = np.linalg.lstsq(design, y, rcond=None)[0]
        m_c, c2, s2 = (float(v) for v in coef)
        d = math.hypot(c2, s2)
        angle_deg = (0.5 * math.degrees(math.atan2(s2, c2))) % 180.0
        if m_c - d > 0.0:
            ratio_raw = math.sqrt((m_c + d) / (m_c - d))
        else:
            degenerate = True
    else:
        degenerate = True
        angle_deg = 0.0

    if degenerate:
        # 确定性退化回退：主轴取程距最大方位（平局取方位角序），
        # 比值取方位程距极值比
        valid = [e for e in per_az if e["range_m"] is not None and e["range_m"] > 0.0]
        if len(valid) < 4:
            raise KrigingInputError(
                f"各向异性自动拟合只有 {len(valid)} 个方位得到有效方向程距（需要 ≥4）——"
                "样本空间分布不足以支撑方向扫描；请增加采样点。"
            )
        best = max(valid, key=lambda e: (e["range_m"], -e["azimuth_deg"]))
        worst = min(valid, key=lambda e: (e["range_m"], e["azimuth_deg"]))
        angle_deg = float(best["azimuth_deg"])
        ratio_raw = float(best["range_m"] / max(worst["range_m"], 1e-12))
    ratio = min(max(ratio_raw, ANISOTROPY_RATIO_CLAMP[0]), ANISOTROPY_RATIO_CLAMP[1])

    result = {
        "angle_degrees": float(angle_deg),
        "ratio": float(ratio),
        "directional_ranges": {
            f"{e['azimuth_deg']:g}": (None if e["range_m"] is None else float(e["range_m"]))
            for e in per_az
        },
        "is_anisotropic": bool(ratio >= ANISOTROPY_RATIO_THRESHOLD),
        "meta": {
            "method": "fit_anisotropy",
            "azimuths_deg": azimuths,
            "azimuth_step_deg": step,
            "tolerance_deg": tol,
            "n_lags": int(n_lags),
            "n_samples": n_used,
            "n_samples_input": n_input,
            "subsample_applied": bool(n_input > n_used),
            "azimuth_convention": (
                "数学约定：0°=东(+x)、逆时针（与 anisotropy_angle 一致，非罗盘方位）；轴向双向"
            ),
            "angle_semantics": (
                "angle_degrees = 各向异性椭圆长轴方位角（数学约定，[0,180)）；"
                "anisotropy_transform 以 A=diag(1,ratio)·R(−θ) 使长轴位移保长、"
                "垂直方向拉伸 ratio —— (angle_degrees, ratio) 可直接作为 "
                "apply_anisotropy/fit_variogram 的 (anisotropy_angle, anisotropy_ratio)"
            ),
            "range_estimator": (
                "方向程距代理=经验曲线达 sill 水平分位的滞后（sill=边际方差，线性插值，"
                "尾部截断 0.6·span）；directional_ranges 取 0.5·sill 水平"
            ),
            "ellipse_fit": (
                "闭式线性最小二乘：几何各向异性恒等式 q²(θ)=cos²ψ+ρ²sin²ψ 的线性形式 "
                "q²(θ)=m−c₂·cos2θ−s₂·sin2θ；对 0.35/0.45/0.55/0.65·sill 四个水平的穿越滞后"
                "取 1/h²（∝q²）逐水平归一后跨 8 方位池化 lstsq（≈32 读取）；"
                "φ=½·atan2(s₂,c₂)，ρ=sqrt((m+d)/(m−d))，d=hypot(c₂,s₂)；确定性，无 RNG"
            ),
            "ratio_raw": ratio_raw,
            "ratio_clamp": [ANISOTROPY_RATIO_CLAMP[0], ANISOTROPY_RATIO_CLAMP[1]],
            "anisotropy_threshold": ANISOTROPY_RATIO_THRESHOLD,
            "ellipse_fit_degenerate": bool(degenerate),
            "n_pooled_readings": int(len(fit_azs)),
            "per_azimuth": per_az,
        },
    }
    return result


def select_variogram_model(
    pts_metric: np.ndarray,
    values: np.ndarray,
    models: Optional[list] = None,
    n_lags: int = 12,
    matern_smoothness: float = MATERN_SMOOTHNESS_DEFAULT,
    robust: bool = False,
) -> tuple[list[dict], dict]:
    """变异函数模型选择：6 家族同一经验变异函数上同台、加权 RSS 排名 + AICc。

    加权 RSS 就是 :func:`fit_variogram` 拟合机器的目标（样本对计数 σ-权重）
    —— 排名证据与 ``model="auto"`` 同源，这里扩展到全部 6 家族并逐模型
    显式给出。AICc 自由度 k=3（sill/range/nugget 三个拟合参数）；
    ``matern`` k=4（固定平滑度 ν 计入——meta 逐字披露）。滞后 bin 数
    n ≤ k+2 时 AICc 诚实取 inf（不伪造小样本信息准则）。

    ``robust=True`` 透传给 :func:`empirical_variogram`（Cressie–Hawkins
    1980 稳健估计，opt-in；默认 False 经典主路径逐位不变，meta 披露）。

    确定性：无随机重启——每个家族跑同一有界最小二乘（curve_fit，
    失败回退有界网格搜索）。返回 ``(ranking, meta)``；``ranking`` 按
    weighted_rss 升序（平局按模型名，确定性）排序，条目为
    ``{model, params, weighted_rss, aicc, fitted_manually, n_pairs}``。
    """
    if models is None:
        models = list(ALL_VARIOGRAM_MODELS)
    models = list(models)
    unknown = [m for m in models if m not in ALL_VARIOGRAM_MODELS]
    if unknown:
        raise KrigingInputError(
            f"variogram model 必须是 {ALL_VARIOGRAM_MODELS} 之一，got {unknown!r}"
        )
    pts = np.asarray(pts_metric, dtype=float)
    vals = np.asarray(values, dtype=float)
    if len(vals) < MIN_SAMPLES:
        raise KrigingInputError(
            f"变异函数模型选择至少需要 {MIN_SAMPLES} 个样本点，got {len(vals)}"
        )
    nu_request = _validate_matern_smoothness(matern_smoothness)
    fit_pts, fit_vals = stratified_subsample(pts, vals, MAX_FIT_POINTS)
    lags, gamma, counts = empirical_variogram(
        fit_pts, fit_vals, n_lags=n_lags, robust=robust
    )
    if len(lags) < 4:
        raise KrigingInputError(
            f"经验变异函数只有 {len(lags)} 个有效滞后 bin（需要 ≥4）—— "
            "样本空间分布不足以支撑模型选择；请改用 IDW 或增加采样点。"
        )
    var_values = float(np.var(fit_vals))
    span = float(np.linalg.norm(fit_pts.max(axis=0) - fit_pts.min(axis=0))) or 1.0
    weights = counts.astype(float)
    n_bins = len(lags)

    ranking: list[dict] = []
    failures: list[str] = []
    for m in models:
        nu = nu_request if m == "matern" else MATERN_SMOOTHNESS_DEFAULT
        fit = _fit_model(m, lags, gamma, weights, var_values, span, nu=nu)
        if fit is None:
            failures.append(m)
            continue
        k_params = 4 if m == "matern" else 3
        rss = max(float(fit.rss), 1e-300)
        if n_bins > k_params + 2:
            aic = n_bins * math.log(rss / n_bins) + 2.0 * k_params
            aicc = aic + (2.0 * k_params * (k_params + 1.0)) / (n_bins - k_params - 1)
        else:
            aicc = float("inf")
        ranking.append({
            "model": m,
            "params": fit.params(),
            "weighted_rss": float(fit.rss),
            "aicc": float(aicc),
            "fitted_manually": bool(fit.fitted_manually),
            "n_pairs": int(fit.n_pairs),
        })
    if not ranking:
        raise KrigingInputError(
            f"变异函数模型选择全部失败（models={failures}）——输入无法支持地统计建模。"
        )
    ranking.sort(key=lambda r: (r["weighted_rss"], r["model"]))
    by_aicc = min(ranking, key=lambda r: (r["aicc"], r["model"]))
    meta = {
        "method": "variogram_model_selection",
        "n_samples": int(len(vals)),
        "n_samples_fit": int(len(fit_vals)),
        "n_bins": int(n_bins),
        "n_pairs": int(weights.sum()),
        "robust_estimator": "cressie_hawkins1980" if robust else "matheron_classic",
        "best_weighted_rss": ranking[0]["model"],
        "best_aicc": by_aicc["model"],
        "aicc_param_note": (
            "AICc 自由度 k=3（sill/range/nugget）；matern k=4（固定平滑度 ν 计入）——已披露"
        ),
        "failed_models": failures,
    }
    return ranking, meta


def indicator_kriging(
    xy: np.ndarray,
    values: np.ndarray,
    grid_xy: np.ndarray,
    thresholds: Any,
    variogram_model: str = "auto",
    n_lags: int = 12,
    k_neighbors: int = 16,
    etype_values: Optional[list] = None,
) -> tuple[dict, dict]:
    """指示克里金（Journel 1983）：逐阈值 P(Z(x) ≤ t) 概率面。

    每个阈值 t：指示变换 I = 1[z ≤ t] → 该指示场自己的经验变异函数 + 拟合
    （``variogram_model="auto"`` 经 :func:`select_variogram_model` 在全部
    6 家族里逐阈值选型——meta 披露逐阈值选中的模型）→ 指示场的普通克里金
    即 P(Z(x) ≤ t)。原始概率钳制到 [0, 1]，被钳制的格数计入 meta（绝不
    静默）。注意：逐阈值独立克里金**不保证**概率面在阈值间单调
    （P(Z≤t) 的单调性未强制——如实披露）。

    ``etype_values``（与 thresholds 等长的类代表值/中值）时额外产出 E-type
    估计 E[Z] ≈ Σ_j (p_j − p_{j−1})·m_j（p₀=0）——离散中值近似，已披露。

    返回 ``({"thresholds", "probabilities" (T,C), "p50_threshold" (C,),
    "etype" | None}, meta)``；``p50_threshold`` 为每格首个 p ≥ 0.5 的阈值
    （全低则 NaN，计数进 meta）。
    """
    pts = np.asarray(xy, dtype=float)
    vals = np.asarray(values, dtype=float)
    targets = np.atleast_2d(np.asarray(grid_xy, dtype=float))
    n = len(vals)
    if n < MIN_SAMPLES:
        raise KrigingInputError(f"指示克里金至少需要 {MIN_SAMPLES} 个样本点，got {n}")
    thr = np.asarray(sorted({float(t) for t in thresholds}), dtype=float)
    if thr.size == 0:
        raise KrigingInputError("thresholds 至少需要一个阈值")
    if not np.isfinite(thr).all():
        raise KrigingInputError("thresholds 必须全部为有限数值")
    ev: Optional[np.ndarray] = None
    if etype_values is not None:
        ev = np.asarray(etype_values, dtype=float)
        if ev.shape != thr.shape:
            raise KrigingInputError(
                f"etype_values 长度必须与 thresholds 一致（{thr.size}），got {len(ev)}"
            )
    if variogram_model not in ALL_VARIOGRAM_MODELS + ("auto",):
        raise KrigingInputError(
            f"variogram model 必须是 {ALL_VARIOGRAM_MODELS + ('auto',)} 之一，"
            f"got {variogram_model!r}"
        )
    n_cells = len(targets)
    n_thr = thr.size
    probabilities = np.empty((n_thr, n_cells), dtype=float)
    clamped = 0
    constant_thresholds: list[float] = []
    models_fitted: dict[str, str] = {}
    disclosures: list[str] = []
    for j in cancellable(range(n_thr), every=1):
        ind = (vals <= thr[j]).astype(float)
        if float(ind.max()) == float(ind.min()):
            # 常量指示场（阈值高于/低于全部样本值）：指示方差为 0，
            # 概率场为常量——诚实输出，不拟合变异函数、不伪造结构。
            probabilities[j, :] = float(ind.mean())
            constant_thresholds.append(float(thr[j]))
            continue
        if variogram_model == "auto":
            ranking, _ = select_variogram_model(pts, ind, n_lags=n_lags)
            best_model = ranking[0]["model"]
        else:
            best_model = variogram_model
        vfit = fit_variogram(pts, ind, model=best_model, n_lags=n_lags)
        models_fitted[repr(float(thr[j]))] = best_model
        res = ordinary_kriging(pts, ind, targets, vfit, k=k_neighbors)
        p = res.predictions
        outside = (p < 0.0) | (p > 1.0)
        clamped += int(outside.sum())
        probabilities[j, :] = np.clip(p, 0.0, 1.0)

    p50 = np.full(n_cells, np.nan)
    for j in range(n_thr):
        todo = np.isnan(p50) & (probabilities[j] >= 0.5)
        p50[todo] = thr[j]
    n_no_p50 = int(np.isnan(p50).sum())

    etype = None
    if ev is not None:
        cum = np.concatenate([np.zeros((1, n_cells)), probabilities], axis=0)
        masses = np.diff(cum, axis=0)
        etype = (masses * ev[:, None]).sum(axis=0)
        disclosures.append(
            "E-type 为离散中值近似：E[Z]≈Σ(p_j−p_{j−1})·m_j（p₀=0）；类内分布未建模。"
        )
    if constant_thresholds:
        disclosures.append(
            f"阈值 {constant_thresholds} 高于/低于全部样本值——指示场为常量，"
            "概率输出常量（未拟合变异函数）。"
        )
    disclosures.append(
        "逐阈值独立指示克里金：概率面在阈值间不保证单调（P(Z≤t) 单调性未强制）。"
    )
    result = {
        "thresholds": thr,
        "probabilities": probabilities,
        "p50_threshold": p50,
        "etype": etype,
    }
    meta = {
        "method": "indicator_kriging",
        "n_samples": int(n),
        "n_cells": int(n_cells),
        "n_thresholds": int(n_thr),
        "variogram_model_request": variogram_model,
        "models_fitted": models_fitted,
        "constant_thresholds": constant_thresholds,
        "clamped_cells": int(clamped),
        "p50_missing_cells": n_no_p50,
        "k_neighbors": int(max(2, min(int(k_neighbors), MAX_NEIGHBORS, n))),
        "disclosures": disclosures,
    }
    return result, meta


def collocated_cokriging(
    xy_primary: np.ndarray,
    z_primary: np.ndarray,
    xy_secondary: np.ndarray,
    y_secondary: np.ndarray,
    grid_xy: np.ndarray,
    correlation_rho: Optional[float] = None,
    variogram_model: str = "auto",
    n_lags: int = 12,
    k_neighbors: int = 12,
    variogram: Optional[VariogramFit] = None,
    solve_backend: str = "auto",
) -> tuple[dict, dict]:
    """协同定位协同克里金（Markov Model 1 近似，Journel & Huijbregts 1978）。

    近似核化（诚实披露，绝不冒充全模型）：

    * **MM1 交叉结构** — 交叉协方差 C_sy(h) = ρ·C_pp(h)（主变量结构 ×
      相关系数）；次变量自身变异函数不拟合，C_ss(0) 取主变量先验方差
      （次变量标准化假设）。
    * **协同定位近似** — 次变量只在目标格点以**单一数值**进入克里金系统
      （collocated cokriging 近似）；目标处无次变量样本时取最近次变量值
      （精确协同定位格数进 meta 披露）。
    * **相关性闸门** — ``correlation_rho`` 缺省时按最近配对的 Pearson
      估计；|ρ| < ``COKRIGING_MIN_ABS_RHO``（0.2）类型化拒绝——相关性过弱
      时协同克里金不会优于普通克里金，不输出无意义的表面。

    扩展系统（协方差形式，C(h) = (sill+nugget) − γ(h)，批量求解同 OK）：

        [C_pp  c_sy 1][w]    [c_p0]
        [c_syᵀ C_ss 1][w_s] = [c_s0]     c_sy = ρ·c_p0, C_s0 = ρ·(sill+nugget)
        [1ᵀ    1    0][μ ]   [1 ]

    预测 = wᵗz + w_s·y(目标)；方差 = C(0) − wᵗc_p0 − w_s·c_s0 − μ（钳 ≥0，
    计数）。``variogram`` 传入预拟合变异函数可跳过重拟合（LOO 对比协议用
    ——两种方法共用同一变异函数才公平）。

    返回 ``({"predictions", "variances", "stddev"}, meta)``。
    """
    solve_backend = _validate_solve_backend(solve_backend)
    ppts = np.asarray(xy_primary, dtype=float)
    z = np.asarray(z_primary, dtype=float)
    spts = np.asarray(xy_secondary, dtype=float)
    y = np.asarray(y_secondary, dtype=float)
    targets = np.atleast_2d(np.asarray(grid_xy, dtype=float))
    n_p = len(z)
    n_s = len(y)
    if n_p < MIN_SAMPLES:
        raise KrigingInputError(f"协同克里金至少需要 {MIN_SAMPLES} 个主变量样本，got {n_p}")
    if correlation_rho is not None:
        rho = float(correlation_rho)
        if not math.isfinite(rho) or abs(rho) > 1.0:
            raise KrigingInputError(
                f"correlation_rho 必须在 [-1, 1] 内，got {correlation_rho!r}"
            )
        if n_s < 1:
            raise KrigingInputError("次变量至少需要 1 个样本（目标协同定位）")
        rho_estimated = False
    else:
        if n_s < 2:
            raise KrigingInputError(
                f"correlation_rho 未给定时至少需要 2 个次变量样本以估计 ρ，got {n_s}"
            )
        tree_ps = cKDTree(spts)
        i_ps = tree_ps.query(ppts, k=1)[1]
        y_at_p = y[i_ps]
        if float(np.var(z)) <= 0.0 or float(np.var(y_at_p)) <= 0.0:
            raise DegenerateData(
                "主变量或配对次变量零方差——Pearson 相关系数 ρ 无法估计。",
                correction_hint="检查字段是否为常量；或显式传入 correlation_rho。",
            )
        rho = float(np.corrcoef(z, y_at_p)[0, 1])
        rho_estimated = True
    if abs(rho) < COKRIGING_MIN_ABS_RHO:
        raise ScientificPreconditionFailed(
            f"主/次变量相关系数 |ρ|={abs(rho):.3f} < {COKRIGING_MIN_ABS_RHO}"
            "——相关性过弱，协同克里金不会优于普通克里金（结构化拒绝，不输出）。",
            correction_hint="改用 kriging_interpolation（单变量 OK/UK），或提供更强相关的协变量。",
        )

    g = variogram if variogram is not None else fit_variogram(
        ppts, z, model=variogram_model, n_lags=n_lags
    )
    k = int(max(2, min(int(k_neighbors), MAX_NEIGHBORS, n_p)))
    # 次变量重网格化到目标（协同定位假设；非精确协同定位取最近值——披露）
    tree_s = cKDTree(spts)
    d_t, i_t = tree_s.query(targets, k=1)
    y_target = y[i_t]
    n_exact_colocated = int((np.asarray(d_t) < 1e-9).sum())
    tree_p = cKDTree(ppts)
    dist_t, idx_t = tree_p.query(targets, k=k)
    n_t = len(targets)
    dist_t = np.asarray(dist_t).reshape(n_t, k)
    idx_t = np.asarray(idx_t).reshape(n_t, k)

    a_priori = float(abs(g.sill) + abs(g.nugget))
    # 与 OK 相同的求解稳定化策略：对角 ridge（高斯族加强）+ 预测钳制 ±3√sill
    ridge = 1e-6 * max(a_priori, 1e-12)
    if g.model == "gaussian" or (g.model == "matern" and g.nu >= 2.0):
        ridge = max(ridge, 0.01 * abs(g.sill))
    clamp_lo = float(z.min() - 3.0 * math.sqrt(max(g.sill, 0.0)))
    clamp_hi = float(z.max() + 3.0 * math.sqrt(max(g.sill, 0.0)))

    preds = np.empty(n_t, dtype=float)
    varis = np.empty(n_t, dtype=float)
    degraded = 0
    diag = np.arange(k)
    for start in cancellable(range(0, n_t, _SOLVE_CHUNK), every=1):
        end = min(start + _SOLVE_CHUNK, n_t)
        nb_idx = idx_t[start:end]
        nb_d = dist_t[start:end]
        nb_xy = ppts[nb_idx]
        nb_v = z[nb_idx]
        c = end - start
        # 样本-样本协方差 C(h) = a_priori − γ(h)，对角 C(0) = a_priori
        diff = nb_xy[:, :, None, :] - nb_xy[:, None, :, :]
        d_ss = np.sqrt((diff ** 2).sum(axis=-1))
        C_ss = a_priori - _gamma(g.model, d_ss, g.sill, g.range_m, g.nugget, nu=g.nu)
        C_ss[:, diag, diag] = a_priori
        # MM1 交叉结构：C_sy(h) = ρ·C_pp(h)
        c_p0 = a_priori - _gamma(g.model, nb_d, g.sill, g.range_m, g.nugget, nu=g.nu)
        c_sy = rho * c_p0
        c_s0 = rho * a_priori  # 次变量(目标) ↔ 主变量(目标, 未观测) h=0 交叉

        mat = np.zeros((c, k + 2, k + 2))
        mat[:, :k, :k] = C_ss
        C_view = mat[:, :k, :k]
        C_view[:, diag, diag] += ridge
        mat[:, :k, k] = c_sy
        mat[:, k, :k] = c_sy
        mat[:, k, k] = a_priori + ridge   # C_ss(0)：标准化假设下的次变量先验方差
        mat[:, k + 1, :k + 1] = 1.0
        mat[:, :k + 1, k + 1] = 1.0
        rhs = np.ones((c, k + 2))
        rhs[:, :k] = c_p0
        rhs[:, k] = c_s0

        sol, row_degraded = _solve_kriging_systems(mat, rhs, solve_backend)
        failed = np.isnan(sol[:, 0])
        degraded += row_degraded
        w = sol[:, :k]
        w_s = sol[:, k]
        mu = sol[:, k + 1]
        chunk_pred = (w * nb_v).sum(axis=1) + w_s * y_target[start:end]
        chunk_var = a_priori - (w * c_p0).sum(axis=1) - w_s * c_s0 - mu
        if failed.any():
            chunk_pred[failed] = [float(np.mean(nb_v[r])) for r in np.nonzero(failed)[0]]
            chunk_var[failed] = [
                float(np.var(nb_v[r])) if k > 1 else float(g.sill)
                for r in np.nonzero(failed)[0]
            ]
        neg_var = chunk_var < 0.0
        if neg_var.any():
            degraded += int(neg_var.sum())
            np.clip(chunk_var, 0.0, None, out=chunk_var)
        clamped = (chunk_pred < clamp_lo) | (chunk_pred > clamp_hi)
        if clamped.any():
            degraded += int(clamped.sum())
            np.clip(chunk_pred, clamp_lo, clamp_hi, out=chunk_pred)
        preds[start:end] = chunk_pred
        varis[start:end] = chunk_var

    result = {
        "predictions": preds,
        "variances": varis,
        "stddev": np.sqrt(np.maximum(varis, 0.0)),
    }
    meta = {
        "method": "collocated_cokriging_mm1",
        "rho_used": float(rho),
        "rho_estimated": bool(rho_estimated),
        "n_primary": int(n_p),
        "n_secondary": int(n_s),
        "neighbors": int(k),
        "variogram": g.params(),
        "degraded_cells": int(degraded),
        "n_exact_colocated": n_exact_colocated,
        "solve_backend_used": (
            "numpy_batched" if solve_backend == "auto" else solve_backend
        ),
        "disclosures": [
            "Markov Model 1（Journel & Huijbregts 1978）近似核化：交叉协方差 "
            "C_sy(h)=ρ·C_pp(h)；全交叉协方差矩阵未建模。",
            "协同定位近似：次变量仅在目标格点以单一数值进入克里金系统。",
            f"次变量到目标格点取最近邻值（协同定位假设）；精确协同定位 "
            f"{n_exact_colocated}/{n_t} 格点。",
            "C_ss(0) 取主变量先验方差（次变量标准化缩放假设）；次变量自身变异函数未拟合。",
        ],
    }
    return result, meta


def block_kriging(
    xy: np.ndarray,
    values: np.ndarray,
    block_centers: np.ndarray,
    block_size: float,
    variogram_model: str = "auto",
    n_lags: int = 12,
    k_neighbors: int = 12,
    variogram: Optional[VariogramFit] = None,
    solve_backend: str = "auto",
) -> tuple[dict, dict]:
    """块克里金：点样本 → 矩形块支撑（Isaaks & Srivastava 1989 惯例）。

    每个块用固定的 **2×2 子点网格**离散化（块均值协方差的离散化近似，
    已披露）。克里金系统的 LHS 保持规范点支撑样本-样本 Γ（样本本身是点
    支撑）；块支撑经由：

    * **RHS** γ̄(x_i, B) —— 样本到块的平均半方差（子点平均），与
    * **方差块内修正** −γ̄(B, B) —— 全部 16 个有序子点对（含 4 个自对
      γ(0)=0）的平均半方差。

    组成——正是块克里金方差平均意义上不大于点克里金方差的原因。预测 =
    wᵗz；方差 = wᵗγ̄(x,B) + μ − γ̄(B,B)（钳 ≥0，负值计数）。批量求解与
    ridge/钳制策略与 OK 相同。``variogram`` 可传预拟合变异函数（对比协议
    共用）。

    返回 ``({"predictions", "variances", "stddev"}, meta)``。
    """
    solve_backend = _validate_solve_backend(solve_backend)
    pts = np.asarray(xy, dtype=float)
    vals = np.asarray(values, dtype=float)
    centers = np.atleast_2d(np.asarray(block_centers, dtype=float))
    bs = float(block_size)
    if not (math.isfinite(bs) and bs > 0.0):
        raise KrigingInputError(
            f"block_size 必须为正数（工作 CRS 单位），got {block_size!r}"
        )
    n = len(vals)
    if n < MIN_SAMPLES:
        raise KrigingInputError(f"块克里金至少需要 {MIN_SAMPLES} 个样本点，got {n}")
    g = variogram if variogram is not None else fit_variogram(
        pts, vals, model=variogram_model, n_lags=n_lags
    )
    k = int(max(2, min(int(k_neighbors), MAX_NEIGHBORS, n)))
    q = bs / (2.0 * BLOCK_DISCRETIZATION)   # 2×2 子点 → ±bs/4
    offs = np.array([[-q, -q], [q, -q], [-q, q], [q, q]], dtype=float)
    subpoints = centers[:, None, :] + offs[None, :, :]   # (B, 4, 2)

    tree = cKDTree(pts)
    dist_c, idx_c = tree.query(centers, k=k)   # 邻域以块心为参考
    n_b = len(centers)
    dist_c = np.asarray(dist_c).reshape(n_b, k)
    idx_c = np.asarray(idx_c).reshape(n_b, k)

    ridge = 1e-6 * max(abs(g.sill), abs(g.nugget), 1e-12)
    if g.model == "gaussian" or (g.model == "matern" and g.nu >= 2.0):
        ridge = max(ridge, 0.01 * abs(g.sill))
    clamp_lo = float(vals.min() - 3.0 * math.sqrt(max(g.sill, 0.0)))
    clamp_hi = float(vals.max() + 3.0 * math.sqrt(max(g.sill, 0.0)))

    preds = np.empty(n_b, dtype=float)
    varis = np.empty(n_b, dtype=float)
    degraded = 0
    diag = np.arange(k)
    for start in cancellable(range(0, n_b, _SOLVE_CHUNK), every=1):
        end = min(start + _SOLVE_CHUNK, n_b)
        nb_idx = idx_c[start:end]
        nb_xy = pts[nb_idx]                       # (c, k, 2)
        nb_v = vals[nb_idx]                       # (c, k)
        sub = subpoints[start:end]                # (c, 4, 2)
        c = end - start

        # LHS：规范点支撑 Γ（nugget 进全部 h>0 项，对角 0）
        diff = nb_xy[:, :, None, :] - nb_xy[:, None, :, :]
        d_ss = np.sqrt((diff ** 2).sum(axis=-1))
        gamma_ss = _gamma(g.model, d_ss, g.sill, g.range_m, g.nugget, nu=g.nu)
        gamma_ss[:, diag, diag] = 0.0
        gamma_ss[:, diag, diag] = ridge

        # RHS：γ̄(x_i, B) —— 样本到块 4 个子点距离的 γ 平均
        diff_sb = nb_xy[:, :, None, :] - sub[:, None, :, :]     # (c, k, 4, 2)
        d_sb = np.sqrt((diff_sb ** 2).sum(axis=-1))             # (c, k, 4)
        g_sb = _gamma(g.model, d_sb, g.sill, g.range_m, g.nugget, nu=g.nu).mean(axis=2)

        # 块内平均 γ̄(B,B)：16 个有序子点对（含 4 个自对 γ(0)=0）
        diff_in = sub[:, :, None, :] - sub[:, None, :, :]       # (c, 4, 4, 2)
        d_in = np.sqrt((diff_in ** 2).sum(axis=-1))
        g_in = _gamma(g.model, d_in, g.sill, g.range_m, g.nugget, nu=g.nu).mean(axis=(1, 2))

        mat = np.zeros((c, k + 1, k + 1))
        mat[:, :k, :k] = gamma_ss
        mat[:, k, :k] = 1.0
        mat[:, :k, k] = 1.0
        rhs = np.ones((c, k + 1))
        rhs[:, :k] = g_sb

        sol, row_degraded = _solve_kriging_systems(mat, rhs, solve_backend)
        failed = np.isnan(sol[:, 0])
        degraded += row_degraded
        w = sol[:, :k]
        mu = sol[:, k]
        chunk_pred = (w * nb_v).sum(axis=1)
        chunk_var = (w * g_sb).sum(axis=1) + mu - g_in
        if failed.any():
            chunk_pred[failed] = [float(np.mean(nb_v[r])) for r in np.nonzero(failed)[0]]
            chunk_var[failed] = [
                float(np.var(nb_v[r])) if k > 1 else float(g.sill)
                for r in np.nonzero(failed)[0]
            ]
        neg_var = chunk_var < 0.0
        if neg_var.any():
            degraded += int(neg_var.sum())
            np.clip(chunk_var, 0.0, None, out=chunk_var)
        clamped = (chunk_pred < clamp_lo) | (chunk_pred > clamp_hi)
        if clamped.any():
            degraded += int(clamped.sum())
            np.clip(chunk_pred, clamp_lo, clamp_hi, out=chunk_pred)
        preds[start:end] = chunk_pred
        varis[start:end] = chunk_var

    result = {
        "predictions": preds,
        "variances": varis,
        "stddev": np.sqrt(np.maximum(varis, 0.0)),
    }
    meta = {
        "method": "block_kriging",
        "block_size": float(bs),
        "discretization": (
            f"{BLOCK_DISCRETIZATION}×{BLOCK_DISCRETIZATION} 子点"
            "（Isaaks & Srivastava 1989 块离散化惯例，近似已披露）"
        ),
        "n_samples": int(n),
        "n_blocks": int(n_b),
        "neighbors": int(k),
        "variogram": g.params(),
        "degraded_cells": int(degraded),
        "solve_backend_used": (
            "numpy_batched" if solve_backend == "auto" else solve_backend
        ),
        "disclosures": [
            "2×2 子点离散化近似块均值协方差——块尺寸相对变程越大近似误差越大。",
            "LHS 保持点支撑样本-样本 γ（样本为点支撑）；块支撑经 RHS 点-块平均 γ "
            "与方差块内修正项 −γ̄(B,B) 进入。",
        ],
    }
    return result, meta


# ── CRS contract + H3 surface driver ────────────────────────────────────────

#: Declared CRS vocabulary the kriging driver accepts. Degree CRS (4326,
#: 4490) are reprojected to a metric working CRS before any distance math;
#: metric CRS pass through. Anything else is a structured rejection — a
#: silent WGS84 fallback would compute garbage distances.
SUPPORTED_DECLARED_CRS = ("EPSG:4326", "EPSG:4490", "EPSG:3857")


class KrigingCrsError(ValueError):
    """Declared CRS is outside the supported vocabulary (never a silent
    WGS84 fallback)."""

    def __init__(self, declared: str):
        self.declared = declared
        super().__init__(
            f"声明的 CRS '{declared}' 不在克里金支持列表 {SUPPORTED_DECLARED_CRS} + UTM"
            "（EPSG:326xx/327xx）内；拒绝静默按 WGS84 处理。"
        )


def _metric_crs_for(declared: Optional[str], lonlat: np.ndarray) -> tuple[str, bool]:
    """Return ``(working_crs, is_degree_input)`` for a declared CRS.

    Degree CRS map onto the UTM/polar chooser (``interpolation._pick_metric_crs``);
    EPSG:3857 and UTM zones are already metric and pass through unchanged.
    """
    if not declared:
        # No declaration: GeoJSON default is WGS84 lon/lat (RFC 7946).
        from app.lib.geo_analysis.interpolation import _pick_metric_crs

        return _pick_metric_crs(lonlat), True
    norm = str(declared).strip().upper()
    if norm in ("EPSG:4326", "EPSG:4490", "WGS84", "CGCS2000"):
        from app.lib.geo_analysis.interpolation import _pick_metric_crs

        return _pick_metric_crs(lonlat), True
    if norm == "EPSG:3857":
        return "EPSG:3857", False
    epsg_num = norm.split(":")[-1] if ":" in norm else ""
    if len(epsg_num) == 5 and epsg_num.isdigit() and epsg_num[:3] in ("326", "327"):
        return f"EPSG:{epsg_num}", False
    raise KrigingCrsError(declared or "<empty>")


def kriging_interpolation(
    points_geojson: Any,
    value_field: str,
    resolution: int = 8,
    variogram_model: str = "auto",
    neighbors: int = 12,
    cross_validate: bool = True,
    declared_crs: Optional[str] = None,
    method: str = "ordinary",
    anisotropy_angle: float = 0.0,
    anisotropy_ratio: float = 1.0,
    matern_smoothness: float = MATERN_SMOOTHNESS_DEFAULT,
    cv_scheme: str = "index",
    solve_backend: str = "auto",
) -> dict:
    """Kriging surface over the sample bbox on an H3 grid.

    Full driver: parse + validate samples (mirrors the IDW contract),
    resolve the metric working CRS from the declared one, fit the variogram
    (bounded), krige every H3 cell centre (prediction + kriging variance),
    and optionally cross-validate. ``method="ordinary"`` (default) is the
    bit-identical historical path; ``method="universal"`` detrends with an
    OLS linear drift, fits the variogram on the residuals and solves the UK
    system (needs ≥ ``UK_MIN_SAMPLES`` samples).

    V2 additive parameters (defaults = historical behaviour):
    ``anisotropy_angle``/``anisotropy_ratio`` (geometric anisotropy, opt-in),
    ``matern_smoothness`` (Matérn ν, only used by the matern family — the
    driver's ``variogram_model`` vocabulary stays pinned to the production
    auto set by the conformance suite; the full six-family vocabulary is
    available via :func:`fit_variogram`), ``cv_scheme`` ("index" | the
    opt-in "spatial_block") and ``solve_backend`` (A7 explicit backend).

    Returns a driver dict:

    ``{"records": [{"h3_index", "value", "kriging_variance"}...],
       "metadata": {crs, declared_crs, bbox, resolution, n_samples,
                    n_fit_samples, variogram, cross_validation,
                    degraded_cells, value_range, method, drift?,
                    solve_backend_used, disclosures?}}``

    Raises:
        KrigingCrsError: declared CRS outside the supported vocabulary.
        KrigingInputError: too few points / unfittable variogram / unknown
            method / out-of-range V2 parameters.
        InsufficientSamples: universal kriging with < UK_MIN_SAMPLES points.
        InterpolationResourceExceededError: H3 cell ceiling (IDW contract).
    """
    import h3

    from app.lib.geo_analysis.interpolation import (
        InterpolationResourceExceededError,
        _aggregate_duplicates,
        _estimate_h3_cells,
        _suggest_lower_resolutions,
        _validate_resolution,
    )
    from app.lib.geo_processor.core import safe_parse, to_feature_collection

    _validate_resolution(resolution)
    if variogram_model not in ("auto",) + VariogramModelNames:
        raise KrigingInputError(
            f"variogram_model 必须是 auto/{'/'.join(VariogramModelNames)}，got {variogram_model!r}"
        )
    if method not in ("ordinary", "universal"):
        raise KrigingInputError(
            f"method 必须是 'ordinary' 或 'universal'，got {method!r}"
        )
    if variogram_model == "matern":
        matern_smoothness = _validate_matern_smoothness(matern_smoothness)
    anisotropy_transform(anisotropy_angle, anisotropy_ratio)  # validate ≥1/finiteness
    if cv_scheme not in ("index", "spatial_block"):
        raise KrigingInputError(
            f"cv_scheme 必须是 'index' 或 'spatial_block'，got {cv_scheme!r}"
        )
    solve_backend = _validate_solve_backend(solve_backend)

    # --- parse + validate sample points (IDW contract mirror) -----------
    parsed = safe_parse(points_geojson)
    if parsed is None:
        raise ValueError("无法解析输入点要素 GeoJSON")
    features = to_feature_collection(parsed).get("features", [])
    lons: list[float] = []
    lats: list[float] = []
    raw_vals: list[Any] = []
    for f in features:
        if not isinstance(f, dict):
            continue
        geom = f.get("geometry")
        if not isinstance(geom, dict) or geom.get("type") != "Point":
            continue
        props = f.get("properties") or {}
        if value_field not in props:
            continue
        coords = geom.get("coordinates") or []
        if len(coords) < 2:
            continue
        lons.append(float(coords[0]))
        lats.append(float(coords[1]))
        raw_vals.append(props[value_field])
    if not lons:
        raise ValueError(
            f"没有可用于克里金的点要素（需要 Point 几何且含字段 '{value_field}'）"
        )
    import pandas as pd

    coerced = pd.to_numeric(pd.Series(raw_vals), errors="coerce")
    if coerced.isna().any():
        raise ValueError(f"字段 '{value_field}' 包含非数值（无法克里金）")
    vals = coerced.astype(float).to_numpy()
    finite = np.isfinite(vals)
    if not finite.all():
        lons = [x for x, keep in zip(lons, finite) if keep]
        lats = [x for x, keep in zip(lats, finite) if keep]
        vals = vals[finite]
    if not lons:
        raise ValueError(f"字段 '{value_field}' 没有有限的数值可用于克里金")
    if len(lons) > MAX_INPUT_POINTS:
        raise KrigingInputError(
            f"输入样本 {len(lons):,} 超过克里金上限 {MAX_INPUT_POINTS:,}"
            "（拟合前请做空间分层抽样）。"
        )

    lonlat = np.column_stack([np.asarray(lons, float), np.asarray(lats, float)])
    lonlat, vals = _aggregate_duplicates(lonlat, vals)
    if method == "universal" and len(vals) < UK_MIN_SAMPLES:
        raise InsufficientSamples(
            f"泛克里金（universal kriging）至少需要 {UK_MIN_SAMPLES} 个去重后的采样点"
            f"（约束线性漂移 [1,x,y]），got {len(vals)}",
            correction_hint="样本不足时改用 ordinary kriging（≥8 点）或 IDW，或补充观测。",
        )
    if len(vals) < MIN_SAMPLES:
        raise KrigingInputError(
            f"克里金至少需要 {MIN_SAMPLES} 个去重后的采样点，got {len(vals)}"
            "（样本过少请改用 IDW）。"
        )

    # --- CRS contract: declared → validated → working projected CRS ------
    import geopandas as gpd

    working_crs, degree_input = _metric_crs_for(declared_crs, lonlat)
    if degree_input:
        pts_gdf = gpd.GeoDataFrame(
            {"v": vals},
            geometry=gpd.points_from_xy(lonlat[:, 0], lonlat[:, 1]),
            crs="EPSG:4326",
        ).to_crs(working_crs)
        pts_metric = np.column_stack(
            (pts_gdf.geometry.x.values, pts_gdf.geometry.y.values)
        )
    else:
        # Metric-declared coordinates: the geometry already IS the working
        # CRS; the H3 bbox needs the 4326 view, so reproject once.
        pts_metric = lonlat.copy()
        lonlat = np.asarray(
            [
                (p.x, p.y)
                for p in gpd.GeoDataFrame(
                    geometry=gpd.points_from_xy(lonlat[:, 0], lonlat[:, 1]),
                    crs=working_crs,
                )
                .to_crs("EPSG:4326")
                .geometry
            ],
            dtype=float,
        )

    # --- H3 target cells (lon/lat bbox) + resource guard (IDW contract) --
    buf = 0.009
    raw_min_lon = float(lonlat[:, 0].min())
    raw_max_lon = float(lonlat[:, 0].max())
    crosses_am = (raw_max_lon - raw_min_lon) > 180.0
    min_lon = max(raw_min_lon - buf, -180.0)
    max_lon = min(raw_max_lon + buf, 180.0)
    min_lat = max(float(lonlat[:, 1].min()) - buf, -90.0)
    max_lat = min(float(lonlat[:, 1].max()) + buf, 90.0)
    if crosses_am:
        wrapped_width = 360.0 - (raw_max_lon - raw_min_lon)
        estimate = int(122 * (7 ** resolution) * (wrapped_width * (max_lat - min_lat) / 41253.0))
    else:
        estimate = _estimate_h3_cells(min_lon, min_lat, max_lon, max_lat, resolution)
    if estimate > 1_500_000:
        raise InterpolationResourceExceededError(
            f"克里金请求估计将生成约 {estimate:,} 个 H3 单元（上限 1,500,000），"
            f"请降低分辨率（建议 {_suggest_lower_resolutions(min_lon, min_lat, max_lon, max_lat, resolution)}）"
            "或缩小插值范围。",
            estimated_cells=estimate,
            suggested_resolutions=_suggest_lower_resolutions(
                min_lon, min_lat, max_lon, max_lat, resolution
            ),
        )
    if crosses_am:
        # antimeridian parity with IDW: split into two bboxes so polyfill
        # does not see a ~360°-wide ring (numerics review #5)
        bbox_a = {"type": "Polygon", "coordinates": [[[raw_max_lon, min_lat], [180.0, min_lat], [180.0, max_lat], [raw_max_lon, max_lat], [raw_max_lon, min_lat]]]}
        bbox_b = {"type": "Polygon", "coordinates": [[[-180.0, min_lat], [min_lon, min_lat], [min_lon, max_lat], [-180.0, max_lat], [-180.0, min_lat]]]}
        # IDW-parity degenerate guard: a sample exactly at ±180 collapses one
        # split bbox to a line — h3 rejects it, so skip that half.
        target_cells = set()
        if raw_max_lon < 180.0:
            target_cells |= set(h3.geo_to_cells(bbox_a, resolution))
        if min_lon > -180.0:
            target_cells |= set(h3.geo_to_cells(bbox_b, resolution))
    else:
        bbox_polygon = {
            "type": "Polygon",
            "coordinates": [[
                [min_lon, min_lat], [max_lon, min_lat], [max_lon, max_lat],
                [min_lon, max_lat], [min_lon, min_lat],
            ]],
        }
        target_cells = set(h3.geo_to_cells(bbox_polygon, resolution))
    # MINOR-8（数值评审）：set 迭代序随 PYTHONHASHSEED 跨进程漂移 ——
    # 复现性契约要求确定性记录序，统一排序后进入 latlng 循环。
    target_cells = sorted(target_cells)
    if not target_cells:
        raise KrigingInputError(
            "H3 polyfill 返回 0 个单元（极地/全球边缘情况）；无法生成克里金表面。"
        )

    # --- variogram fit (bounded; UK fits on OLS-detrended residuals) --------
    fit_pts_used, _ = stratified_subsample(
        apply_anisotropy(pts_metric, anisotropy_angle, anisotropy_ratio),
        vals, MAX_FIT_POINTS,
    )
    if method == "ordinary":
        vfit = fit_variogram(
            pts_metric, vals, model=variogram_model,
            anisotropy_angle=anisotropy_angle, anisotropy_ratio=anisotropy_ratio,
            matern_smoothness=matern_smoothness,
        )
    else:
        # fitted inside universal_kriging_detrended on the detrended
        # residuals; the zero-residual degenerate case fits none at all
        vfit = None

    # --- krige cell centres (projected; H3 always speaks lon/lat) -------
    cell_latlng = np.array([h3.cell_to_latlng(c) for c in target_cells])
    cell_gdf = gpd.GeoDataFrame(
        geometry=gpd.points_from_xy(cell_latlng[:, 1], cell_latlng[:, 0]),
        crs="EPSG:4326",
    ).to_crs(working_crs)
    cell_metric = np.column_stack(
        (cell_gdf.geometry.x.values, cell_gdf.geometry.y.values)
    )

    if method == "universal":
        result = universal_kriging_detrended(
            pts_metric, vals, cell_metric,
            variogram_model=variogram_model, k=neighbors,
            anisotropy_angle=anisotropy_angle, anisotropy_ratio=anisotropy_ratio,
            matern_smoothness=matern_smoothness,
            solve_backend=solve_backend,
        )
        vfit = result.variogram
    else:
        result = ordinary_kriging(
            pts_metric, vals, cell_metric, vfit, k=neighbors,
            anisotropy_angle=anisotropy_angle, anisotropy_ratio=anisotropy_ratio,
            solve_backend=solve_backend,
        )

    cv_report = (
        cross_validate_kriging(
            pts_metric, vals, model=variogram_model, method=method,
            cv_scheme=cv_scheme,
            anisotropy_angle=anisotropy_angle, anisotropy_ratio=anisotropy_ratio,
            matern_smoothness=matern_smoothness,
            solve_backend=solve_backend,
        )
        if cross_validate else None
    )

    # science-v3（Wave 8/9）：95% 预测区间面（由 ordinary_kriging 的
    # KrigingResult.pi95_* 投影；UK 零残差退化路径无 PI —— 诚实缺省，
    # 方差面本身精确为 0）。
    stddevs = np.sqrt(np.maximum(result.variances, 0.0))
    records = [
        {
            "h3_index": cell,
            "value": float(v),
            "kriging_variance": float(var),
            "kriging_stddev": float(sd),
            "pi95_low": float(lo),
            "pi95_high": float(hi),
        }
        for cell, v, var, sd, lo, hi in zip(
            target_cells, result.predictions, result.variances, stddevs,
            result.pi95_low if result.pi95_low is not None else stddevs,
            result.pi95_high if result.pi95_high is not None else stddevs)
    ]
    metadata = {
        "algorithm": "interpolation.kriging" if method == "ordinary" else "interpolation.universal_kriging",
        "method": method,
        "declared_crs": declared_crs or "EPSG:4326",
        "working_crs": working_crs,
        "bbox": [min_lon, min_lat, max_lon, max_lat],
        "resolution": int(resolution),
        "n_samples": int(len(vals)),
        "n_fit_samples": int(len(fit_pts_used)),
        "neighbors": int(result.neighbors),
        "degraded_cells": int(result.degraded_cells),
        "value_range": [
            round(float(result.predictions.min()), 4),
            round(float(result.predictions.max()), 4),
        ],
        "variance_range": [
            round(float(result.variances.min()), 6),
            round(float(result.variances.max()), 6),
        ],
        "prediction_interval_95": {
            "z": 1.959963984540054,
            "assumption": "gaussian errors (Isaaks & Srivastava); approximate interval",
        },
        "variogram": vfit.params() if vfit is not None else None,
        "cross_validation": cv_report.metrics() if cv_report else None,
        "value_field": value_field,
        # ── V2 additive provenance ────────────────────────────────────────
        "solve_backend_used": result.solve_backend_used,
        "cv_scheme": cv_scheme,
    }
    if (float(anisotropy_angle) != 0.0) or (float(anisotropy_ratio) != 1.0):
        metadata["anisotropy"] = {
            "angle_degrees": float(anisotropy_angle),
            "ratio": float(anisotropy_ratio),
        }
    if variogram_model == "matern":
        metadata["matern_smoothness"] = float(matern_smoothness)
    if result.drift_coefficients is not None:
        metadata["drift"] = {
            "terms": ["1", "x", "y"],
            "coefficients": [round(float(b), 6) for b in result.drift_coefficients],
        }
    if result.disclosures:
        metadata["disclosures"] = list(result.disclosures)
    return {"records": records, "metadata": metadata}


# ── V3 H3 surface drivers（IDW driver parity；共享 preamble 见
#    interpolation._metric_samples_and_target_grid）────────────────────────────

def indicator_kriging_surface(
    points_geojson: Any,
    value_field: str,
    thresholds: list,
    resolution: int = 7,
    variogram_model: str = "auto",
    n_lags: int = 12,
    k_neighbors: int = 16,
    etype: bool = False,
) -> dict:
    """H3 指示克里金表面 driver。

    records 主值 = E-type 估计（``etype=True``，类代表值取阈值本身——保守
    近似，已披露）否则 = p50 阈值（无格点达 p≥0.5 时取最高阈值，计数披露）；
    每条 record 另带 ``p50_threshold`` 与逐阈值概率 ``probabilities``。

    ``{"records", "metadata"}``； Raises 与 :func:`indicator_kriging` 相同，
    外加 InterpolationResourceExceededError（H3 单元上限，IDW 契约）。
    """
    from app.lib.geo_analysis.interpolation import _metric_samples_and_target_grid

    (
        lonlat, values, pts_metric, cell_metric, target_cells,
        working_crs, bbox,
    ) = _metric_samples_and_target_grid(
        points_geojson, value_field, resolution,
        purpose="指示克里金", label="指示克里金", log_prefix="indicator_kriging",
    )
    metadata: dict[str, Any] = {
        "algorithm": "interpolation.indicator_kriging",
        "value_field": value_field,
        "resolution": int(resolution),
        "working_crs": working_crs,
        "n_samples": int(len(values)),
        "bbox": [bbox[0], bbox[1], bbox[2], bbox[3]],
    }
    if not target_cells:
        metadata["cell_count"] = 0
        return {"records": [], "metadata": metadata}
    etype_values = (
        [float(t) for t in sorted({float(v) for v in thresholds})] if etype else None
    )
    result, ik_meta = indicator_kriging(
        pts_metric, values, cell_metric, thresholds,
        variogram_model=variogram_model, n_lags=n_lags,
        k_neighbors=k_neighbors, etype_values=etype_values,
    )
    thr = result["thresholds"]
    probabilities = result["probabilities"]
    p50 = result["p50_threshold"]
    etype_arr = result["etype"]
    if etype_arr is not None:
        main_value = etype_arr
        metadata["value_semantics"] = "E-type 估计（类代表值=阈值本身，保守离散近似）"
    else:
        main_value = np.where(np.isfinite(p50), p50, thr[-1])
        metadata["value_semantics"] = "p50 阈值（无格点达 p≥0.5 时取最高阈值）"
    metadata.update({
        "method": ik_meta["method"],
        "thresholds": [float(t) for t in thr],
        "models_fitted": ik_meta["models_fitted"],
        "clamped_cells": ik_meta["clamped_cells"],
        "p50_missing_cells": ik_meta["p50_missing_cells"],
        "k_neighbors": ik_meta["k_neighbors"],
        "disclosures": ik_meta["disclosures"],
        "probability_summary": [
            {
                "threshold": float(thr[j]),
                "p_mean": round(float(probabilities[j].mean()), 6),
                "p_min": round(float(probabilities[j].min()), 6),
                "p_max": round(float(probabilities[j].max()), 6),
            }
            for j in range(len(thr))
        ],
        "cell_count": int(len(target_cells)),
    })
    records = []
    for ci, cell in enumerate(target_cells):
        records.append({
            "h3_index": cell,
            "value": float(main_value[ci]),
            "p50_threshold": (
                float(p50[ci]) if np.isfinite(p50[ci]) else None
            ),
            "probabilities": {
                str(j): round(float(probabilities[j, ci]), 6)
                for j in range(len(thr))
            },
        })
    return {"records": records, "metadata": metadata}


def collocated_cokriging_surface(
    points_geojson: Any,
    value_field: str,
    secondary_geojson: Any,
    secondary_field: str,
    resolution: int = 7,
    correlation_rho: Optional[float] = None,
    neighbors: int = 12,
    variogram_model: str = "auto",
) -> dict:
    """H3 协同定位协同克里金表面 driver（主/次两个点要素集）。

    次变量与主变量共用同一工作 CRS（按主变量范围选取）；次变量到目标格点
    由 :func:`collocated_cokriging` 最近邻补格（协同定位假设，披露）。
    records 带 ``ck_variance``/``ck_stddev``——不确定面由工具层作为第二
    产物输出。 Raises：弱相关 → ScientificPreconditionFailed；其余同 OK。
    """
    import geopandas as gpd

    from app.lib.geo_analysis.interpolation import (
        _metric_samples_and_target_grid,
        _parse_point_values,
    )

    (
        lonlat, values, pts_metric, cell_metric, target_cells,
        working_crs, bbox,
    ) = _metric_samples_and_target_grid(
        points_geojson, value_field, resolution,
        purpose="协同克里金", label="协同克里金", log_prefix="cokriging",
    )
    metadata: dict[str, Any] = {
        "algorithm": "interpolation.cokriging",
        "value_field": value_field,
        "secondary_field": secondary_field,
        "resolution": int(resolution),
        "working_crs": working_crs,
        "n_samples": int(len(values)),
        "bbox": [bbox[0], bbox[1], bbox[2], bbox[3]],
    }
    if not target_cells:
        metadata["cell_count"] = 0
        return {"records": [], "metadata": metadata}
    # 次变量：同一份解析契约；投影到主变量的工作 CRS（共享空间才谈相关）
    lonlat_sec, values_sec = _parse_point_values(
        secondary_geojson, secondary_field,
        purpose="协同克里金次变量", log_prefix="cokriging_secondary",
    )
    sec_gdf = gpd.GeoDataFrame(
        {"v": values_sec},
        geometry=gpd.points_from_xy(lonlat_sec[:, 0], lonlat_sec[:, 1]),
        crs="EPSG:4326",
    ).to_crs(working_crs)
    sec_metric = np.column_stack(
        (sec_gdf.geometry.x.values, sec_gdf.geometry.y.values)
    )
    metadata["n_secondary"] = int(len(values_sec))
    result, ck_meta = collocated_cokriging(
        pts_metric, values, sec_metric, values_sec, cell_metric,
        correlation_rho=correlation_rho, variogram_model=variogram_model,
        n_lags=DEFAULT_N_LAGS, k_neighbors=neighbors,
    )
    metadata.update({
        "method": ck_meta["method"],
        "rho_used": ck_meta["rho_used"],
        "rho_estimated": ck_meta["rho_estimated"],
        "neighbors": ck_meta["neighbors"],
        "variogram": ck_meta["variogram"],
        "degraded_cells": ck_meta["degraded_cells"],
        "n_exact_colocated": ck_meta["n_exact_colocated"],
        "disclosures": ck_meta["disclosures"],
        "value_range": [
            round(float(result["predictions"].min()), 4),
            round(float(result["predictions"].max()), 4),
        ],
        "variance_range": [
            round(float(result["variances"].min()), 6),
            round(float(result["variances"].max()), 6),
        ],
        "cell_count": int(len(target_cells)),
    })
    records = [
        {
            "h3_index": cell,
            "value": float(pred),
            "ck_variance": float(var),
            "ck_stddev": float(sd),
        }
        for cell, pred, var, sd in zip(
            target_cells, result["predictions"], result["variances"],
            result["stddev"],
        )
    ]
    return {"records": records, "metadata": metadata}


def block_kriging_surface(
    points_geojson: Any,
    value_field: str,
    resolution: int = 7,
    block_size: float = 0.0,
    neighbors: int = 12,
    variogram_model: str = "auto",
) -> dict:
    """H3 块克里金表面 driver。

    ``block_size``（米，工作 CRS 单位）为 0 时按 H3 分辨率平均六边形边长
    自动取值（h3.average_hexagon_edge_length，全局平均近似——已披露）。
    records 带 ``block_variance``/``block_stddev``；块支撑经 2×2 离散化
    进入 RHS 与方差修正（Isaaks & Srivastava 1989，披露）。
    """
    import h3 as _h3

    from app.lib.geo_analysis.interpolation import _metric_samples_and_target_grid

    (
        lonlat, values, pts_metric, cell_metric, target_cells,
        working_crs, bbox,
    ) = _metric_samples_and_target_grid(
        points_geojson, value_field, resolution,
        purpose="块克里金", label="块克里金", log_prefix="block_kriging",
    )
    metadata: dict[str, Any] = {
        "algorithm": "interpolation.block_kriging",
        "value_field": value_field,
        "resolution": int(resolution),
        "working_crs": working_crs,
        "n_samples": int(len(values)),
        "bbox": [bbox[0], bbox[1], bbox[2], bbox[3]],
    }
    bs = float(block_size)
    if bs == 0.0:
        try:
            bs = float(_h3.average_hexagon_edge_length(int(resolution), unit="m"))
            metadata["block_size_auto"] = True
        except (AttributeError, TypeError, ValueError):
            bs = 1000.0
            metadata["block_size_auto"] = True
    if bs <= 0.0:
        raise KrigingInputError(f"block_size 必须为正数（米），got {block_size!r}")
    metadata["block_size"] = float(bs)
    if not target_cells:
        metadata["cell_count"] = 0
        return {"records": [], "metadata": metadata}
    result, bk_meta = block_kriging(
        pts_metric, values, cell_metric, bs,
        variogram_model=variogram_model, n_lags=DEFAULT_N_LAGS,
        k_neighbors=neighbors,
    )
    metadata.update({
        "method": bk_meta["method"],
        "discretization": bk_meta["discretization"],
        "neighbors": bk_meta["neighbors"],
        "variogram": bk_meta["variogram"],
        "degraded_cells": bk_meta["degraded_cells"],
        "disclosures": bk_meta["disclosures"],
        "variance_range": [
            round(float(result["variances"].min()), 6),
            round(float(result["variances"].max()), 6),
        ],
        "cell_count": int(len(target_cells)),
    })
    records = [
        {
            "h3_index": cell,
            "value": float(pred),
            "block_variance": float(var),
            "block_stddev": float(sd),
        }
        for cell, pred, var, sd in zip(
            target_cells, result["predictions"], result["variances"],
            result["stddev"],
        )
    ]
    return {"records": records, "metadata": metadata}
