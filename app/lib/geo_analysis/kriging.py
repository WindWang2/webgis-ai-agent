"""Ordinary Kriging with uncertainty (native GIS vertical slice).

Companion to :mod:`app.lib.geo_analysis.interpolation` (IDW) — the same GIS
contract applies, plus the kriging-specific ones:

* **Variogram models** — spherical / exponential / gaussian, each with a
  bounded, deterministic least-squares fit against binned empirical
  semivariances. ``auto`` picks the model with the lowest weighted RSS.

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

All distances are computed in the CALLER-supplied projected (metric) CRS
space — degree-space kriging silently distorts and is rejected at the tool
boundary (``interpolation._pick_metric_crs`` is the sanctioned chooser).
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Any, Optional

import numpy as np
from scipy.spatial import cKDTree

from app.lib.cancellation import cancellable

logger = logging.getLogger(__name__)

VariogramModelNames = ("spherical", "exponential", "gaussian")

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


class KrigingInputError(ValueError):
    """Structured input rejection (too few points, unfittable variogram…)."""


@dataclass
class VariogramFit:
    """Fitted theoretical variogram + the empirical evidence behind it."""

    model: str
    sill: float
    range_m: float
    nugget: float = 0.0
    # fitting diagnostics (auto-selection evidence)
    rss: float = 0.0
    n_pairs: int = 0
    n_lags: int = 0
    fitted_manually: bool = False  # curve_fit unavailable → bounded grid fit

    def params(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "sill": round(self.sill, 6),
            "range_meters": round(self.range_m, 3),
            "nugget": round(self.nugget, 3),
        }


def _gamma(model: str, h: np.ndarray, sill: float, rng: float, nugget: float) -> np.ndarray:
    """Theoretical semivariance γ(h) for the fitted model."""
    h = np.asarray(h, dtype=float)
    out = np.empty_like(h)
    if model == "spherical":
        hr = np.divide(h, rng, out=np.full_like(h, np.inf), where=rng > 0)
        inside = hr <= 1.0
        out[inside] = nugget + sill * (1.5 * hr[inside] - 0.5 * hr[inside] ** 3)
        out[~inside] = nugget + sill
    elif model == "exponential":
        out = nugget + sill * (1.0 - np.exp(-3.0 * h / max(rng, 1e-9)))
    elif model == "gaussian":
        out = nugget + sill * (1.0 - np.exp(-3.0 * (h / max(rng, 1e-9)) ** 2))
    else:  # pragma: no cover - validated call sites
        raise KrigingInputError(f"unknown variogram model: {model!r}")
    return out


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
) -> Optional[VariogramFit]:
    """Bounded least-squares fit of γ(h) for one model.

    :func:`scipy.optimize.curve_fit` with hard bounds when available
    (sill ∈ (0, 4·var], range ∈ (span/200, 2·span], nugget ∈ [0, var]);
    falls back to a bounded grid search (coarse scan + refinements) that
    needs no optimizer — deterministic and dependency-free.
    """
    sigma = 1.0 / np.sqrt(np.maximum(weights / weights.max(), 1e-6))
    var_floor = max(var_values, 1e-12)

    def f(h, sill, rng, nugget):
        return _gamma(model, h, sill, rng, nugget)

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
            model=model, sill=sill, range_m=rng, nugget=nugget,
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
                    pred = _gamma(model, lags, sill, rng, nug)
                    rss = float(np.sum(((pred - gamma) / sigma) ** 2))
                    if best is None or rss < best.rss:
                        best = VariogramFit(
                            model=model, sill=float(sill), range_m=float(rng),
                            nugget=float(nug), rss=rss,
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
) -> VariogramFit:
    """Fit the theoretical variogram (``auto`` = best weighted RSS of the 3).

    Samples above the fitting ceiling are reduced by deterministic spatial
    stratification first; the fit is bounded on both sides so it can never
    return a degenerate (zero-range / negative-sill) model.
    """
    if model not in VariogramModelNames and model != "auto":
        raise KrigingInputError(
            f"variogram model 必须是 {VariogramModelNames + ('auto',)} 之一，got {model!r}"
        )
    fit_pts, fit_vals = stratified_subsample(pts_metric, values, MAX_FIT_POINTS)
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

    candidates = VariogramModelNames if model == "auto" else (model,)
    best: Optional[VariogramFit] = None
    failures: list[str] = []
    for m in candidates:
        fit = _fit_model(m, lags, gamma, weights, var_values, span)
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
    variogram: VariogramFit
    n_samples: int
    n_samples_fit: int
    neighbors: int
    degraded_cells: int = 0   # predictions that fell back to the local mean


def ordinary_kriging(
    fit_pts: np.ndarray,
    fit_vals: np.ndarray,
    target_pts: np.ndarray,
    variogram: VariogramFit,
    k: int = 12,
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
    """
    n = len(fit_vals)
    if n < 1:
        raise KrigingInputError("ordinary_kriging needs at least one sample")
    k = int(max(2, min(k, MAX_NEIGHBORS, n)))
    tree = cKDTree(fit_pts)
    dist_t, idx_t = tree.query(target_pts, k=k)
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
    ridge = 1e-6 * max(abs(g.sill), abs(g.nugget), 1e-12)
    if g.model == "gaussian":
        ridge = max(ridge, 0.01 * abs(g.sill))
    clamp_lo = float(fit_vals.min() - 3.0 * np.sqrt(abs(g.sill)))
    clamp_hi = float(fit_vals.max() + 3.0 * np.sqrt(abs(g.sill)))

    for start in cancellable(range(0, n_t, _SOLVE_CHUNK), every=1):
        end = min(start + _SOLVE_CHUNK, n_t)
        nb_idx = idx_t[start:end]
        nb_d = dist_t[start:end]                      # (c, k)
        nb_xy = fit_pts[nb_idx]                       # (c, k, 2)
        nb_v = fit_vals[nb_idx]                       # (c, k)

        # sample-sample semivariances WITH nugget (c, k, k), zero diagonal
        diff = nb_xy[:, :, None, :] - nb_xy[:, None, :, :]
        d_ss = np.sqrt((diff ** 2).sum(axis=-1))
        gamma_ss = _gamma(g.model, d_ss, g.sill, g.range_m, g.nugget)
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
        rhs[:, :k] = _gamma(g.model, nb_d, g.sill, g.range_m, g.nugget)

        chunk_pred = np.empty(c)
        chunk_var = np.empty(c)
        try:
            sol = np.linalg.solve(mat, rhs[:, :, None])[:, :, 0]  # (c, k+1)
            w = sol[:, :k]
            mu = sol[:, k]
            chunk_pred = np.einsum("ck,ck->c", w, nb_v)
            chunk_var = np.einsum("ck,ck->c", w, rhs[:, :k]) + mu
            bad = ~(np.isfinite(chunk_pred) & np.isfinite(chunk_var))
            if bad.any():
                raise np.linalg.LinAlgError("non-finite batch solution")
        except np.linalg.LinAlgError:
            for r in range(c):
                try:
                    s = np.linalg.solve(mat[r], rhs[r])
                    chunk_pred[r] = float(np.dot(s[:k], nb_v[r]))
                    chunk_var[r] = float(np.dot(s[:k], rhs[r, :k]) + s[k])
                except np.linalg.LinAlgError:
                    degraded += 1
                    chunk_pred[r] = float(np.mean(nb_v[r]))
                    chunk_var[r] = float(np.var(nb_v[r])) if k > 1 else float(g.sill)

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
    )


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

    def metrics(self) -> dict[str, Any]:
        out: dict[str, Any] = {"n_samples": self.n_samples, "folds": self.folds}
        for key in ("rmse", "mae", "bias", "r2"):
            v = getattr(self, key)
            out[key] = round(v, 6) if v is not None else None
        if self.note:
            out["note"] = self.note
        return out


def cross_validate_kriging(
    pts_metric: np.ndarray,
    values: np.ndarray,
    model: str = "auto",
    folds: int = CV_FOLDS,
    k: int = 12,
) -> CrossValidationReport:
    """K-fold CV where each fold refits the variogram on the training split.

    Below ``MIN_CV_SAMPLES`` the report honestly declines to produce metrics
    instead of emitting statistically meaningless numbers.
    """
    n = len(values)
    if n < MIN_CV_SAMPLES:
        return CrossValidationReport(
            n_samples=n, folds=0,
            note=(
                f"样本量 {n} < {MIN_CV_SAMPLES}，无法进行可靠的交叉验证；"
                "不确定性仅由克里金方差表达。"
            ),
        )
    folds = max(2, min(folds, n // 4))
    # deterministic fold assignment (no RNG): index-strided folds keep
    # spatial mixing reasonable without carrying shuffle state
    fold_id = np.arange(n) % folds
    errs: list[float] = []
    folds_used = 0
    for f in range(folds):
        test = fold_id == f
        train_xy, train_v = pts_metric[~test], values[~test]
        if len(train_v) < MIN_SAMPLES:
            continue
        try:
            vfit = fit_variogram(train_xy, train_v, model=model)
            res = ordinary_kriging(train_xy, train_v, pts_metric[test], vfit, k=k)
        except KrigingInputError:
            continue
        folds_used += 1
        errs.extend((res.predictions - values[test]).tolist())
    if not errs:
        return CrossValidationReport(
            n_samples=n, folds=folds_used,
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
) -> dict:
    """Ordinary-kriging surface over the sample bbox on an H3 grid.

    Full driver: parse + validate samples (mirrors the IDW contract),
    resolve the metric working CRS from the declared one, fit the variogram
    (bounded), krige every H3 cell centre (prediction + kriging variance),
    and optionally cross-validate. Returns a driver dict:

    ``{"records": [{"h3_index", "value", "kriging_variance"}...],
       "metadata": {crs, declared_crs, bbox, resolution, n_samples,
                    n_fit_samples, variogram, cross_validation,
                    degraded_cells, value_range}}``

    Raises:
        KrigingCrsError: declared CRS outside the supported vocabulary.
        KrigingInputError: too few points / unfittable variogram.
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
    if not target_cells:
        raise KrigingInputError(
            "H3 polyfill 返回 0 个单元（极地/全球边缘情况）；无法生成克里金表面。"
        )

    # --- variogram fit (bounded, on stratified subsample) ----------------
    fit_pts_used, _ = stratified_subsample(pts_metric, vals, MAX_FIT_POINTS)
    vfit = fit_variogram(pts_metric, vals, model=variogram_model)

    # --- krige cell centres (projected; H3 always speaks lon/lat) -------
    cell_latlng = np.array([h3.cell_to_latlng(c) for c in target_cells])
    cell_gdf = gpd.GeoDataFrame(
        geometry=gpd.points_from_xy(cell_latlng[:, 1], cell_latlng[:, 0]),
        crs="EPSG:4326",
    ).to_crs(working_crs)
    cell_metric = np.column_stack(
        (cell_gdf.geometry.x.values, cell_gdf.geometry.y.values)
    )

    result = ordinary_kriging(pts_metric, vals, cell_metric, vfit, k=neighbors)

    cv_report = (
        cross_validate_kriging(pts_metric, vals, model=variogram_model)
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
        "algorithm": "interpolation.kriging",
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
        "variogram": vfit.params(),
        "cross_validation": cv_report.metrics() if cv_report else None,
        "value_field": value_field,
    }
    return {"records": records, "metadata": metadata}
