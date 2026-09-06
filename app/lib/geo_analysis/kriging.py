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
from app.lib.gis.scientific_errors import InsufficientSamples

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
    """Linear map ``A = diag(1, ratio)·R(θ)`` for geometric anisotropy.

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
    rot = np.array([[c, -s], [s, c]], dtype=float)
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
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Binned empirical semivariance γ*(h) over bounded row chunks.

    Returns ``(lag_centers, gamma, pair_counts)``; bins with zero pairs are
    dropped. The pair matrix is walked one row at a time (O(N) peak memory)
    and a deterministic row stride keeps total pairs within ``max_pairs``.
    """
    n = len(values)
    n_lags = max(4, min(int(n_lags), 64))
    span = float(np.linalg.norm(pts_metric.max(axis=0) - pts_metric.min(axis=0))) or 1.0
    edges = np.linspace(0.0, span, n_lags + 1)
    sum_g = np.zeros(n_lags)
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
        np.add.at(cnt, b[valid], 1)

    has = cnt > 0
    lags = 0.5 * (edges[:-1] + edges[1:])[has]
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
) -> VariogramFit:
    """Fit the theoretical variogram (``auto`` = best weighted RSS of the 3).

    Samples above the fitting ceiling are reduced by deterministic spatial
    stratification first; the fit is bounded on both sides so it can never
    return a degenerate (zero-range / negative-sill) model.

    V2: ``model`` may name any of :data:`ALL_VARIOGRAM_MODELS` (matern /
    wave / cubic are opt-in — ``auto`` keeps the legacy production trio);
    geometric anisotropy is applied to the coordinates before binning (the
    defaults are the identity); ``matern_smoothness`` is the fixed Matérn ν.
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
        fit_pts, fit_vals, n_lags=n_lags, max_pairs=max_pairs
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

    return KrigingResult(
        predictions=preds,
        variances=varis,
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

    return KrigingResult(
        predictions=preds,
        variances=varis,
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

    def metrics(self) -> dict[str, Any]:
        out: dict[str, Any] = {"n_samples": self.n_samples, "folds": self.folds}
        for key in ("rmse", "mae", "bias", "r2"):
            v = getattr(self, key)
            out[key] = round(v, 6) if v is not None else None
        if self.note:
            out["note"] = self.note
        out["scheme"] = self.scheme
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
    return CrossValidationReport(
        rmse=float(np.sqrt(np.mean(e ** 2))),
        mae=float(np.mean(np.abs(e))),
        bias=float(np.mean(e)),
        r2=(1.0 - ss_res / ss_tot) if ss_tot > 0 else None,
        n_samples=n,
        folds=folds_used,
        scheme=cv_scheme,
        per_fold=per_fold,
    )


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

    records = [
        {
            "h3_index": cell,
            "value": float(v),
            "kriging_variance": float(var),
            "kriging_stddev": float(np.sqrt(max(var, 0.0))),
        }
        for cell, v, var in zip(target_cells, result.predictions, result.variances)
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
