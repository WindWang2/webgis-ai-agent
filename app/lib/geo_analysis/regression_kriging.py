"""Regression kriging (RK): OLS trend on covariates + kriged residuals.

Foundation V2 · A2 — hybrid of a regression model and ordinary kriging
(Odeh, McBratney & Chittleborough 1995):

    z(x) = f(x)ᵗβ + δ(x),      δ ~ (zero-mean, variogram γ)

Pipeline (full driver):

1. Parse the sample FC with BOTH the value field and the ``explanatory_
   fields`` (≥ 2 covariates, same FeatureCollection) — rows where any field
   is non-finite are dropped, exact-duplicate coordinates are mean-
   aggregated across all fields at once (deterministic, alignment-safe).
2. OLS ``z ~ [1, c₁, …, cₖ]`` at the sample points → trend coefficients β
   and residuals δ.
3. Fit the residual variogram (bounded, ``auto`` by default) and krige the
   residuals to every H3 cell centre — the residual kriging variance is
   RK's per-cell uncertainty.
4. At the targets, the covariates themselves are unknown: each covariate
   field is interpolated from its sample values by IDW (k=5 nearest,
   power=2 — the same neighbourhood as :func:`idw_surface`, exact-hit
   recovery included). RK prediction = f(x₀)ᵗβ̂ + δ̂(x₀).

Honest semantics (all disclosed, never blurred):

* **approximate covariates** — covariate values at targets are IDW
  *approximations* of the true fields; the descriptor is
  ``approximate=True`` with ``fallback_semantics["interpolation.kriging"]
  = "approximation"``.
* **variance = residual kriging variance only** — the trend-coefficient
  uncertainty is NOT propagated; ``rk_variance`` is the kriging variance of
  δ̂. The metadata says so explicitly.
* **zero-residual degenerate case** — when the OLS residuals have no
  spread (e.g. z is an exact linear function of the covariates) the trend
  IS the signal: exact-trend prediction with zero variance and the
  disclosure flag ``"zero_residual_variance"`` (no variogram is fitted or
  faked).

Guards: the kriging ceilings apply (``MAX_INPUT_POINTS``), ≥
``MIN_SAMPLES`` deduplicated samples, ≥ 2 covariate fields, and every
covariate must have strictly positive variance (a constant covariate is a
:class:`DegenerateData` rejection — its coefficient is unidentifiable).
All distance math runs in the declared-CRS-resolved metric working CRS
(the kriging CRS contract, reused unchanged).
"""
from __future__ import annotations

import logging
import math
from typing import Any, Optional

import numpy as np

from app.lib.geo_analysis.kriging import (
    KrigingInputError,
    MAX_INPUT_POINTS,
    MIN_SAMPLES,
    VariogramFit,
    _zero_residual_spread,
    fit_variogram,
    ordinary_kriging,
)
from app.lib.gis.scientific_errors import DegenerateData, InsufficientSamples
from app.lib.gis.uncertainty import ValidationMetrics

logger = logging.getLogger(__name__)

# RK refits the residual variogram on OLS residuals — the residual process is
# stationary by construction, so the full opt-in family vocabulary is fine.
V2_OK_VARIOGRAM_MODELS = ("spherical", "exponential", "gaussian", "matern")

RK_MIN_COVARIATES = 2          # a single covariate cannot support an RK trend
RK_IDW_NEIGHBORS = 5           # target-covariate IDW neighbourhood (= idw_surface k)
RK_IDW_POWER = 2.0             # target-covariate IDW exponent (= idw_surface default)
RK_LOOCV_MAX_POINTS = 200      # full-pipeline LOOCV budget (refits variogram)
_EXACT_HIT_M = 1e-9            # IDW exact-hit threshold (idw_surface parity)


def _aggregate_duplicates_multi(
    lonlat: np.ndarray, rows: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """Deterministically collapse exact-duplicate coordinates by column means.

    Multi-column view of the IDW ``_aggregate_duplicates`` contract (that
    helper scalarises values, which cannot keep the value/covariate columns
    aligned). Grouping by exact (lon, lat) — reordering input features
    cannot change the result.
    """
    groups: dict[tuple[float, float], list[int]] = {}
    order: list[tuple[float, float]] = []
    for i in range(len(lonlat)):
        key = (float(lonlat[i, 0]), float(lonlat[i, 1]))
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(i)
    out_lonlat = np.asarray(order, dtype=float)
    out_rows = np.asarray(
        [np.mean(rows[groups[k]], axis=0) for k in order], dtype=float
    )
    return out_lonlat, out_rows


def _parse_multifield_samples(
    points_geojson: Any,
    value_field: str,
    explanatory_fields: list[str],
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Parse value + covariate columns from one FC, aligned and deduplicated.

    Non-Point features are skipped; rows where ANY of the fields is
    non-numeric raise; NaN/inf in ANY field drops the row (with a warning);
    exact-duplicate coordinates are mean-aggregated across ALL fields at
    once (per-field aggregation could misalign the columns).
    Returns ``(lonlat (n,2), values (n,), covariates (n,k))``.
    """
    import pandas as pd

    from app.lib.geo_processor.core import safe_parse, to_feature_collection

    parsed = safe_parse(points_geojson)
    if parsed is None:
        raise ValueError("无法解析输入点要素 GeoJSON")
    features = to_feature_collection(parsed).get("features", [])
    needed = [value_field] + list(explanatory_fields)

    lons: list[float] = []
    lats: list[float] = []
    raw_rows: list[list[Any]] = []
    skipped_non_point = 0
    skipped_missing = 0
    for f in features:
        if not isinstance(f, dict):
            continue
        geom = f.get("geometry")
        if not isinstance(geom, dict) or geom.get("type") != "Point":
            skipped_non_point += 1
            continue
        props = f.get("properties") or {}
        if any(name not in props for name in needed):
            skipped_missing += 1
            continue
        coords = geom.get("coordinates") or []
        if len(coords) < 2:
            continue
        lons.append(float(coords[0]))
        lats.append(float(coords[1]))
        raw_rows.append([props[name] for name in needed])

    if skipped_non_point:
        logger.warning("rk: skipped %d non-Point feature(s)", skipped_non_point)
    if skipped_missing:
        logger.warning(
            "rk: skipped %d feature(s) missing value/covariate field(s)",
            skipped_missing,
        )
    if not raw_rows:
        raise ValueError(
            f"没有可用于回归克里金的点要素（需要 Point 几何且同时含字段 "
            f"'{value_field}' 与 {list(explanatory_fields)}）"
        )

    df = pd.DataFrame(raw_rows, columns=needed)
    coerced = df.apply(pd.to_numeric, errors="coerce")
    non_numeric = (df.notna() & coerced.isna()).to_numpy()
    if non_numeric.any():
        bad_col = needed[int(np.argmax(non_numeric.any(axis=0)))]
        raise ValueError(
            f"字段 '{bad_col}' 包含非数值（无法回归克里金）"
        )
    arr = coerced.astype(float).to_numpy()
    finite = np.isfinite(arr).all(axis=1)
    if not finite.all():
        logger.warning(
            "rk: dropping %d row(s) with NaN/inf value(s)", int((~finite).sum())
        )
        arr = arr[finite]
        lons = [x for x, keep in zip(lons, finite) if keep]
        lats = [x for x, keep in zip(lats, finite) if keep]
    if not lons:
        raise ValueError("没有有限的数值行可用于回归克里金")

    lonlat = np.column_stack([np.asarray(lons, float), np.asarray(lats, float)])
    lonlat, agg = _aggregate_duplicates_multi(lonlat, arr)
    return lonlat, agg[:, 0], agg[:, 1:]


def _idw_interpolate_covariate(
    pts_metric: np.ndarray,
    values: np.ndarray,
    targets: np.ndarray,
) -> np.ndarray:
    """IDW (k=5, power=2, exact-hit) — the target-covariate approximation."""
    from scipy.spatial import cKDTree

    n = len(values)
    k = min(RK_IDW_NEIGHBORS, n)
    tree = cKDTree(pts_metric)
    dist, idx = tree.query(targets, k=k)
    n_t = len(targets)
    dist = np.asarray(dist).reshape(n_t, k)
    idx = np.asarray(idx).reshape(n_t, k)
    nb_vals = values[idx]
    out = np.empty(n_t, dtype=np.float64)
    hit = dist < _EXACT_HIT_M
    has_exact = hit.any(axis=1)
    if has_exact.any():
        first_hit = np.argmax(hit, axis=1)
        rows_ = np.nonzero(has_exact)[0]
        out[rows_] = nb_vals[rows_, first_hit[rows_]]
    non_exact = ~has_exact
    if non_exact.any():
        w = 1.0 / (dist[non_exact] ** RK_IDW_POWER)
        out[non_exact] = (w * nb_vals[non_exact]).sum(axis=1) / w.sum(axis=1)
    return out


def _validate_covariates(covariates: np.ndarray, names: Optional[list[str]] = None) -> np.ndarray:
    if covariates.ndim != 2 or covariates.shape[1] < RK_MIN_COVARIATES:
        raise KrigingInputError(
            f"回归克里金至少需要 {RK_MIN_COVARIATES} 个协变量字段"
            f"（explanatory_fields），got {covariates.shape[1] if covariates.ndim == 2 else 1}"
        )
    if names is None:
        names = [f"covariate_{j}" for j in range(covariates.shape[1])]
    for j, name in enumerate(names):
        if float(np.var(covariates[:, j])) <= 0.0:
            raise DegenerateData(
                f"协变量字段 '{name}' 方差为 0（常量场）——回归系数不可辨识。",
                correction_hint="移除常量协变量，或改用普通克里金。",
            )
    return covariates


def regression_kriging_predict(
    pts_metric: np.ndarray,
    values: np.ndarray,
    covariates: np.ndarray,
    targets_xy: np.ndarray,
    target_covariates: np.ndarray,
    variogram_model: str = "auto",
    neighbors: int = 12,
    matern_smoothness: float = 0.5,
) -> dict:
    """RK core: OLS trend + kriged residuals, evaluated at prepared targets.

    ``target_covariates`` are the (approximate) covariate values at the
    targets — the caller owns how they were obtained (the driver uses IDW).
    Returns ``{"predictions", "variances", "beta", "residuals",
    "variogram", "disclosures"}``.
    """
    pts = np.asarray(pts_metric, dtype=float)
    vals = np.asarray(values, dtype=float)
    cov = np.asarray(covariates, dtype=float)
    _validate_covariates(cov)
    if len(vals) < MIN_SAMPLES:
        raise KrigingInputError(
            f"回归克里金至少需要 {MIN_SAMPLES} 个去重后的采样点，got {len(vals)}"
            "（样本过少请改用 IDW）。"
        )
    F = np.column_stack([np.ones(len(pts)), cov])
    beta, *_ = np.linalg.lstsq(F, vals, rcond=None)
    resid = vals - F @ beta

    disclosures: list[str] = []
    if _zero_residual_spread(resid, vals):
        # trend IS the signal — exact-trend prediction, zero variance
        F_t = np.column_stack([np.ones(len(targets_xy)), target_covariates])
        return {
            "predictions": F_t @ beta,
            "variances": np.zeros(len(targets_xy), dtype=float),
            "beta": beta,
            "residuals": resid,
            "variogram": None,
            "disclosures": ["zero_residual_variance"],
        }

    vfit = fit_variogram(
        pts, resid, model=variogram_model, matern_smoothness=matern_smoothness
    )
    res = ordinary_kriging(pts, resid, targets_xy, vfit, k=neighbors)
    F_t = np.column_stack([np.ones(len(targets_xy)), target_covariates])
    disclosures.append(
        "rk_variance 仅含残差克里金方差——趋势系数的不确定性未传播（如实披露）。"
    )
    return {
        "predictions": F_t @ beta + res.predictions,
        "variances": res.variances,
        "beta": beta,
        "residuals": resid,
        "variogram": vfit,
        "disclosures": disclosures,
        "degraded_cells": res.degraded_cells,
    }


def regression_kriging_loocv(
    pts_metric: np.ndarray,
    values: np.ndarray,
    covariates: np.ndarray,
    variogram_model: str = "auto",
    neighbors: int = 12,
    matern_smoothness: float = 0.5,
    max_points: int = RK_LOOCV_MAX_POINTS,
) -> dict:
    """Leave-one-out CV of the FULL RK pipeline (per-point: OLS refit +
    residual variogram refit + residual OK prediction of the held-out
    point; target covariates = the held-out sample's own covariate values).

    Bounded by a deterministic stride subsample above ``max_points``
    (``sample_count`` discloses the actual size).
    """
    pts = np.asarray(pts_metric, dtype=float)
    vals = np.asarray(values, dtype=float)
    cov = np.asarray(covariates, dtype=float)
    n = len(vals)
    if n < MIN_SAMPLES + 1:
        raise InsufficientSamples(
            f"回归克里金 LOOCV 需要至少 {MIN_SAMPLES + 1} 个样本点"
            f"（留一后仍需 ≥{MIN_SAMPLES} 点），got {n}",
            correction_hint="增加采样点后重试。",
        )
    if n > max_points:
        stride = int(math.ceil(n / max_points))
        keep = np.arange(0, n, stride)
        pts, vals, cov = pts[keep], vals[keep], cov[keep]
        logger.info(
            "rk: LOOCV subsample %d -> %d points (deterministic stride %d)",
            n, len(vals), stride,
        )
        n = len(vals)
    preds = np.empty(n, dtype=np.float64)
    for i in range(n):
        mask = np.ones(n, dtype=bool)
        mask[i] = False
        out = regression_kriging_predict(
            pts[mask], vals[mask], cov[mask], pts[i:i + 1],
            cov[i:i + 1], variogram_model=variogram_model,
            neighbors=neighbors, matern_smoothness=matern_smoothness,
        )
        preds[i] = float(out["predictions"][0])
    resid = preds - vals
    return {
        "rmse": float(np.sqrt(np.mean(resid ** 2))),
        "mae": float(np.mean(np.abs(resid))),
        "bias": float(np.mean(resid)),
        "method": "loocv",
        "sample_count": int(n),
    }


def regression_kriging_surface(
    points_geojson: Any,
    value_field: str,
    explanatory_fields: list[str],
    resolution: int = 7,
    variogram_model: str = "auto",
    neighbors: int = 12,
    cross_validate: bool = True,
    declared_crs: Optional[str] = None,
    matern_smoothness: float = 0.5,
) -> dict:
    """Regression-kriging surface over the sample bbox on an H3 grid.

    Returns:

    ``{"records": [{"h3_index", "rk_prediction", "rk_variance",
                    "rk_stddev"}...],
       "metadata": {algorithm, value_field, explanatory_fields, declared_crs,
                    working_crs, bbox, resolution, n_samples, trend_coefficients,
                    variogram, n_loocv?, validation?, uncertainty?, disclosures}}``

    Raises:
        KrigingInputError: <2 covariate fields, too few samples, unfittable
            residual variogram, unknown method/model.
        DegenerateData: a constant (zero-variance) covariate field.
        KrigingCrsError: declared CRS outside the supported vocabulary.
        InterpolationResourceExceededError: H3 cell ceiling (IDW contract).
    """
    import geopandas as gpd
    import h3

    from app.lib.geo_analysis.interpolation import (
        _target_cells_for_samples,
        _validate_resolution,
    )
    from app.lib.geo_analysis.kriging import _metric_crs_for

    _validate_resolution(resolution)
    if variogram_model not in ("auto",) + V2_OK_VARIOGRAM_MODELS:
        raise KrigingInputError(
            f"variogram_model 必须是 auto/{'/'.join(V2_OK_VARIOGRAM_MODELS)}，"
            f"got {variogram_model!r}"
        )
    if not isinstance(explanatory_fields, (list, tuple)) or len(explanatory_fields) < 1:
        raise KrigingInputError(
            "explanatory_fields 必须是至少 1 个协变量字段名的列表"
        )
    names = [str(f) for f in explanatory_fields]

    # --- parse + validate (multi-field, alignment-safe) ----------------------
    lonlat, values, covariates = _parse_multifield_samples(
        points_geojson, value_field, names
    )
    n = len(values)
    if n > MAX_INPUT_POINTS:
        raise KrigingInputError(
            f"输入样本 {n:,} 超过克里金上限 {MAX_INPUT_POINTS:,}。"
        )
    _validate_covariates(covariates, names)

    # --- CRS contract (kriging contract, reused) -----------------------------
    working_crs, degree_input = _metric_crs_for(declared_crs, lonlat)
    if degree_input:
        pts_gdf = gpd.GeoDataFrame(
            {"v": values},
            geometry=gpd.points_from_xy(lonlat[:, 0], lonlat[:, 1]),
            crs="EPSG:4326",
        ).to_crs(working_crs)
        pts_metric = np.column_stack(
            (pts_gdf.geometry.x.values, pts_gdf.geometry.y.values)
        )
    else:
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

    metadata: dict[str, Any] = {
        "algorithm": "interpolation.regression_kriging",
        "value_field": value_field,
        "explanatory_fields": list(names),
        "declared_crs": declared_crs or "EPSG:4326",
        "working_crs": working_crs,
        "resolution": int(resolution),
        "n_samples": int(n),
        "covariate_idw": {
            "method": "idw",
            "k": RK_IDW_NEIGHBORS,
            "power": RK_IDW_POWER,
            "note": "目标处协变量值为样本协变量的 IDW 近似（approximate=True 语义）。",
        },
    }
    disclosures: list[str] = [
        "目标格网的协变量值由样本协变量经 IDW（k=5, power=2）近似——RK 预测是近似语义。",
        "rk_variance 仅含残差克里金方差——趋势系数不确定性未传播（如实披露）。",
    ]

    # --- H3 target cells + resource guard (IDW contract) --------------------
    target_cells, (min_lon, min_lat, max_lon, max_lat) = _target_cells_for_samples(
        lonlat, resolution, label="回归克里金"
    )
    metadata["bbox"] = [min_lon, min_lat, max_lon, max_lat]
    if not target_cells:
        metadata["cell_count"] = 0
        metadata["records_note"] = "H3 polyfill 返回 0 单元（极区/全球边缘情形）。"
        return {"records": [], "metadata": metadata}
    metadata["cell_count"] = int(len(target_cells))

    cell_latlng = np.array([h3.cell_to_latlng(c) for c in target_cells])
    cell_gdf = gpd.GeoDataFrame(
        geometry=gpd.points_from_xy(cell_latlng[:, 1], cell_latlng[:, 0]),
        crs="EPSG:4326",
    ).to_crs(working_crs)
    cell_metric = np.column_stack(
        (cell_gdf.geometry.x.values, cell_gdf.geometry.y.values)
    )

    # --- target covariates: per-field IDW from the sample values ------------
    target_cov = np.column_stack([
        _idw_interpolate_covariate(pts_metric, covariates[:, j], cell_metric)
        for j in range(covariates.shape[1])
    ])

    # --- RK core --------------------------------------------------------------
    out = regression_kriging_predict(
        pts_metric, values, covariates, cell_metric, target_cov,
        variogram_model=variogram_model, neighbors=neighbors,
        matern_smoothness=matern_smoothness,
    )
    vfit: Optional[VariogramFit] = out.get("variogram")
    metadata["trend_coefficients"] = {
        "terms": ["1"] + names,
        "values": [round(float(b), 6) for b in out["beta"]],
    }
    metadata["variogram"] = vfit.params() if vfit is not None else None
    if out.get("disclosures"):
        seen = set(disclosures)
        for d in out["disclosures"]:
            if d not in seen:
                disclosures.append(d)
                seen.add(d)

    # --- LOOCV evidence (bounded full-pipeline refits) -----------------------
    if cross_validate:
        try:
            loo = regression_kriging_loocv(
                pts_metric, values, covariates,
                variogram_model=variogram_model, neighbors=neighbors,
                matern_smoothness=matern_smoothness,
            )
        except InsufficientSamples as exc:
            metadata["validation_note"] = str(exc.detail)
        else:
            metadata["n_loocv"] = int(loo["sample_count"])
            metadata["validation"] = ValidationMetrics(
                target="regression_kriging_surface",
                method="loocv",
                rmse=loo["rmse"],
                mae=loo["mae"],
                bias=loo["bias"],
                sample_count=loo["sample_count"],
            ).to_evidence()
            abs_res_note = (
                "RK 不确定性：rk_variance 为残差克里金方差；LOOCV 为全流程经验残差证据。"
            )
            metadata["uncertainty"] = {
                "target": "regression_kriging_surface",
                "uncertainty_type": "scalar_uncertainty",
                "method": "residual_kriging_variance_plus_loocv",
                "sample_count": int(loo["sample_count"]),
                "note": abs_res_note,
            }

    records = [
        {
            "h3_index": cell,
            "rk_prediction": float(p),
            "rk_variance": float(v),
            "rk_stddev": float(np.sqrt(max(v, 0.0))),
        }
        for cell, p, v in zip(target_cells, out["predictions"], out["variances"])
    ]
    metadata["value_range"] = [
        round(float(np.min(out["predictions"])), 4),
        round(float(np.max(out["predictions"])), 4),
    ]
    metadata["variance_range"] = [
        round(float(np.min(out["variances"])), 6),
        round(float(np.max(out["variances"])), 6),
    ]
    metadata["disclosures"] = disclosures
    return {"records": records, "metadata": metadata}
