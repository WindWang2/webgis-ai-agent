"""Global polynomial trend-surface analysis (order 1-3, OLS).

Foundation V2 · A2 — companion to the kriging module. A trend surface is a
global least-squares polynomial regression of the value field on the
coordinates:

    z(x, y) ≈ Σ_{i+j ≤ order} β_{ij}·u^i·v^j

with the coordinates scaled to the unit box (u, v ∈ [0, 1]) before the
design matrix is built — plain coordinates are badly conditioned at order
≥ 2 (x³ vs x terms differ by ~10⁸ for kilometre extents); the unit-box map
is affine, so it is exactly absorbed by a coefficient change and disclosed
in the metadata.

Honest semantics:

* **Model variance is real** — unlike IDW/RBF (no theoretical variance),
  OLS residual variance σ̂² = SS_res/(n−p) is a VALID model-variance
  estimate, surfaced alongside R² / adjusted R². It is the variance of the
  *fitted trend model*, not a kriging-style per-point prediction variance —
  the distinction is disclosed, never blurred.

* **Extrapolation is flagged, not hidden** — targets outside the sample
  bbox (in the projected metric space) are evaluated with the global
  polynomial (that is what a trend model does) but carry
  ``"extrapolated": true`` and are counted in metadata.

* **Feasibility guard** — order grows the term count quadratically; when
  the order's term growth ``order·(order+3)/2`` exceeds ``n/3`` the request
  is a typed :class:`InsufficientSamples` rejection (each fitted term needs
  ≥3 samples of support). Rank-deficient designs (e.g. all points collinear
  at order ≥ 1 after scaling) are :class:`DegenerateData`.

* **Deterministic** — OLS via ``np.linalg.lstsq``; LOOCV above a bounded
  budget runs on a deterministic stride subsample (``sample_count``
  discloses it). No RNG anywhere.

Coordinates are projected to a metric CRS first (``_pick_metric_crs``) —
although the OLS fit itself is affine-invariant, the extrapolation bbox and
the disclosed scaling live in the metric working space (IDW CRS policy).
"""
from __future__ import annotations

import logging
import math
from typing import Any

import numpy as np

from app.lib.gis.scientific_errors import (
    DegenerateData,
    InsufficientSamples,
    UnsupportedMethod,
)
from app.lib.gis.uncertainty import ValidationMetrics

logger = logging.getLogger(__name__)

TREND_ORDERS = (1, 2, 3)
TREND_LOOCV_MAX_POINTS = 500  # LOOCV refit budget


def _validate_order(order: Any) -> int:
    o = int(order)
    if o not in TREND_ORDERS:
        raise UnsupportedMethod(
            f"trend surface 阶数必须是 {TREND_ORDERS} 之一，got {order!r}",
            correction_hint="order=1（平面）/2（二次曲面）/3（三次曲面）。",
        )
    return o


def trend_terms(order: int) -> int:
    """Design width: monomials u^i·v^j with i+j ≤ order → (order+1)(order+2)/2."""
    return (int(order) + 1) * (int(order) + 2) // 2


def _check_order_feasible(order: int, n: int) -> None:
    """Feasibility guard: term growth vs sample support (≥3 samples per term)."""
    if order * (order + 3) / 2.0 > n / 3.0:
        raise InsufficientSamples(
            f"趋势面阶数 {order} 的项增长（{order}·{order + 3}/2）超过 n/3"
            f"（n={n}）——每项至少需要 3 个样本支撑。",
            correction_hint="降低阶数（order=1 最少 6 点），或补充观测。",
        )


def _unit_box_scale(xy: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Map coordinates into the unit box; returns ``(uv, (umin→…))``.

    Zero span on either axis (all samples on a vertical/horizontal line) is
    a :class:`DegenerateData` rejection — the polynomial is unidentifiable
    along the constant axis.
    """
    mn = xy.min(axis=0)
    span = xy.max(axis=0) - mn
    if float(span[0]) <= 0.0 or float(span[1]) <= 0.0:
        raise DegenerateData(
            "趋势面拟合退化：样本在某一坐标轴上零跨度（共线）——"
            "多项式沿该轴不可辨识。",
            correction_hint="补充平面展布的采样点，或改用剖面分析。",
        )
    uv = (xy - mn) / span
    return uv, mn


def _trend_design(uv: np.ndarray, order: int) -> np.ndarray:
    """Monomial design matrix [1, u, v, u², uv, v², …] in billed order."""
    u, v = uv[:, 0], uv[:, 1]
    cols = [np.ones(len(uv))]
    for total in range(1, order + 1):
        for i in range(total + 1):
            cols.append(u ** (total - i) * v ** i)
    return np.column_stack(cols)


def _fit_ols(design: np.ndarray, values: np.ndarray) -> np.ndarray:
    beta, *_ = np.linalg.lstsq(design, values, rcond=None)
    return beta


def _check_design_rank(design: np.ndarray) -> None:
    n_terms = design.shape[1]
    if np.linalg.matrix_rank(design) < n_terms:
        raise DegenerateData(
            "趋势面设计矩阵不满秩（坐标构型退化，如共线/重合）——"
            "多项式系数不可辨识。",
            correction_hint="补充平面展布的采样点或降低阶数。",
        )


def trend_predict(
    points_xy: np.ndarray,
    values: np.ndarray,
    targets_xy: np.ndarray,
    order: int = 1,
) -> dict:
    """Fit the global polynomial trend and evaluate at ``targets_xy``.

    Returns ``{"predictions", "beta", "residuals", "scale", "order"}``.
    Raises: UnsupportedMethod (order ∉ 1-3), InsufficientSamples (term
    growth guard), DegenerateData (zero-span / rank-deficient design).
    """
    order = _validate_order(order)
    pts = np.asarray(points_xy, dtype=float)
    vals = np.asarray(values, dtype=float)
    if len(vals) < trend_terms(order):
        raise InsufficientSamples(
            f"order={order} 趋势面需要 ≥{trend_terms(order)} 个样本（设计宽度），"
            f"got {len(vals)}",
            correction_hint="降低阶数或补充观测。",
        )
    _check_order_feasible(order, len(vals))
    uv, mn = _unit_box_scale(pts)
    design = _trend_design(uv, order)
    _check_design_rank(design)
    beta = _fit_ols(design, vals)
    resid = vals - design @ beta

    targets = np.atleast_2d(np.asarray(targets_xy, dtype=float))
    span = _span_of(pts)
    uv_t = (targets - mn) / span
    predictions = _trend_design(uv_t, order) @ beta
    return {
        "predictions": predictions,
        "beta": beta,
        "residuals": resid,
        "scale": {"min": mn.tolist(), "span": span.tolist()},
        "order": int(order),
    }


def _span_of(xy: np.ndarray) -> np.ndarray:
    return xy.max(axis=0) - xy.min(axis=0)


def trend_fit_stats(points_xy: np.ndarray, values: np.ndarray, order: int) -> dict:
    """In-sample fit evidence: R², adjusted R², residual variance σ̂²."""
    order = _validate_order(order)
    pts = np.asarray(points_xy, dtype=float)
    vals = np.asarray(values, dtype=float)
    uv, _ = _unit_box_scale(pts)
    design = _trend_design(uv, order)
    _check_design_rank(design)
    beta = _fit_ols(design, vals)
    resid = vals - design @ beta
    ss_res = float(np.sum(resid ** 2))
    ss_tot = float(np.sum((vals - vals.mean()) ** 2))
    n, p = len(vals), design.shape[1]
    r2 = (1.0 - ss_res / ss_tot) if ss_tot > 0 else None
    adj_r2 = (
        1.0 - (1.0 - r2) * (n - 1) / (n - p - 1)
        if r2 is not None and n > p + 1 else None
    )
    dof = max(n - p, 1)
    return {
        "r2": r2,
        "adj_r2": adj_r2,
        "residual_variance": ss_res / dof,
        "residual_stddev": math.sqrt(ss_res / dof),
        "n_params": int(p),
        "n_samples": int(n),
        "coefficients": [float(b) for b in beta],
        "order": int(order),
    }


def _trend_loocv_residuals(
    points_xy: np.ndarray,
    values: np.ndarray,
    order: int,
    max_points: int = TREND_LOOCV_MAX_POINTS,
) -> tuple[np.ndarray, int]:
    """LOOCV residuals of the trend fit (per-point refit on the others)."""
    order = _validate_order(order)
    pts = np.asarray(points_xy, dtype=float)
    vals = np.asarray(values, dtype=float)
    n = len(vals)
    if n < trend_terms(order) + 1:
        raise InsufficientSamples(
            f"趋势面 LOOCV 需要 ≥{trend_terms(order) + 1} 个样本点"
            f"（留一后仍需 ≥{trend_terms(order)} 点约束设计），got {n}",
            correction_hint="增加采样点后重试。",
        )
    if n > max_points:
        stride = int(math.ceil(n / max_points))
        keep = np.arange(0, n, stride)
        pts = pts[keep]
        vals = vals[keep]
        logger.info(
            "trend: LOOCV subsample %d -> %d points (deterministic stride %d)",
            n, len(vals), stride,
        )
        n = len(vals)
    uv, _mn = _unit_box_scale(pts)
    design = _trend_design(uv, order)
    _check_design_rank(design)
    preds = np.empty(n, dtype=np.float64)
    for i in range(n):
        mask = np.ones(n, dtype=bool)
        mask[i] = False
        beta, *_ = np.linalg.lstsq(design[mask], vals[mask], rcond=None)
        preds[i] = float(design[i] @ beta)
    return preds - vals, n


def trend_loocv(
    points_xy: np.ndarray,
    values: np.ndarray,
    order: int = 1,
    max_points: int = TREND_LOOCV_MAX_POINTS,
) -> dict:
    """Leave-one-out CV of the trend fit: rmse / mae / bias + sample_count."""
    resid, n_used = _trend_loocv_residuals(points_xy, values, order, max_points)
    return {
        "rmse": float(np.sqrt(np.mean(resid ** 2))),
        "mae": float(np.mean(np.abs(resid))),
        "bias": float(np.mean(resid)),
        "method": "loocv",
        "sample_count": int(n_used),
    }


def trend_surface(
    points_geojson: Any,
    value_field: str,
    resolution: int = 7,
    order: int = 1,
    cross_validate: bool = True,
) -> dict:
    """Global polynomial trend surface over the sample bbox on an H3 grid.

    Full driver: parse + validate samples (shared IDW contract), resolve the
    metric working CRS, fit the OLS trend (unit-box scaled — disclosed),
    evaluate every H3 cell centre, flag cells outside the sample bbox as
    ``extrapolated``, and attach R²/adjusted R²/residual-variance plus
    optional LOOCV evidence.

    Returns:

    ``{"records": [{"h3_index", "value", "extrapolated"}...],
       "metadata": {algorithm, order, resolution, working_crs, bbox,
                    n_samples, n_params, r2, adj_r2, residual_variance,
                    extrapolated_count, value_range, value_field,
                    fit, validation?, disclosures?}}``

    Raises:
        UnsupportedMethod: order outside 1-3.
        InsufficientSamples: term-growth guard (order·(order+3)/2 > n/3).
        DegenerateData: zero-span / rank-deficient coordinate design.
        ValueError: unparseable input or invalid H3 resolution.
        InterpolationResourceExceededError: H3 cell ceiling (IDW contract).
    """
    import geopandas as gpd

    from app.lib.geo_analysis.interpolation import (
        _parse_point_values,
        _pick_metric_crs,
        _target_cells_for_samples,
        _validate_resolution,
    )

    _validate_resolution(resolution)
    order = _validate_order(order)

    # --- parse + validate sample points (shared IDW contract) ---------------
    lonlat, values = _parse_point_values(
        points_geojson, value_field, purpose="趋势面分析", log_prefix="trend"
    )
    n = len(values)
    _check_order_feasible(order, n)
    if n < trend_terms(order):
        raise InsufficientSamples(
            f"order={order} 趋势面需要 ≥{trend_terms(order)} 个样本（设计宽度），got {n}",
            correction_hint="降低阶数或补充观测。",
        )

    # --- metric projection of sample points (IDW CRS policy) ----------------
    utm_crs = _pick_metric_crs(lonlat)
    pts_gdf = gpd.GeoDataFrame(
        {"v": values},
        geometry=gpd.points_from_xy(lonlat[:, 0], lonlat[:, 1]),
        crs="EPSG:4326",
    ).to_crs(utm_crs)
    pts_metric = np.column_stack(
        (pts_gdf.geometry.x.values, pts_gdf.geometry.y.values)
    )

    # --- OLS trend fit (unit-box scaled — disclosed) ------------------------
    fit = trend_fit_stats(pts_metric, values, order)
    uv_scale, _ = _unit_box_scale(pts_metric)
    span = _span_of(pts_metric)
    disclosures = [
        "坐标经仿射缩放至单位立方盒后拟合（条件数稳定；缩放被系数吸收，"
        "对预测无影响）。"
    ]

    metadata: dict[str, Any] = {
        "algorithm": "interpolation.trend_surface",
        "value_field": value_field,
        "resolution": int(resolution),
        "order": int(order),
        "n_params": int(fit["n_params"]),
        "r2": round(fit["r2"], 6) if fit["r2"] is not None else None,
        "adj_r2": round(fit["adj_r2"], 6) if fit["adj_r2"] is not None else None,
        "residual_variance": round(float(fit["residual_variance"]), 6),
        "working_crs": utm_crs,
        "n_samples": int(n),
    }

    # --- LOOCV evidence ------------------------------------------------------
    if cross_validate:
        try:
            resid, n_used = _trend_loocv_residuals(pts_metric, values, order)
        except InsufficientSamples as exc:
            metadata["validation_note"] = str(exc.detail)
        else:
            metadata["validation"] = ValidationMetrics(
                target="trend_surface",
                method="loocv",
                rmse=float(np.sqrt(np.mean(resid ** 2))),
                mae=float(np.mean(np.abs(resid))),
                bias=float(np.mean(resid)),
                sample_count=int(n_used),
            ).to_evidence()
    metadata["disclosures"] = disclosures

    # --- H3 target cells (lon/lat bbox) + resource guard (IDW contract) -----
    target_cells, (min_lon, min_lat, max_lon, max_lat) = _target_cells_for_samples(
        lonlat, resolution, label="趋势面"
    )
    metadata["bbox"] = [min_lon, min_lat, max_lon, max_lat]
    n_cells = len(target_cells)
    if n_cells == 0:
        logger.warning(
            "trend: H3 polyfill returned 0 cells for bbox lon[%s,%s] lat[%s,%s] "
            "(polar / whole-world edge case); returning empty surface.",
            min_lon, max_lon, min_lat, max_lat,
        )
        metadata["cell_count"] = 0
        return {"records": [], "metadata": metadata}
    metadata["cell_count"] = int(n_cells)

    # --- metric projection of cell centres + trend evaluation ---------------
    import h3

    cell_latlng = np.array([h3.cell_to_latlng(c) for c in target_cells])  # (n,2)
    cell_gdf = gpd.GeoDataFrame(
        geometry=gpd.points_from_xy(cell_latlng[:, 1], cell_latlng[:, 0]),
        crs="EPSG:4326",
    ).to_crs(utm_crs)
    cell_metric = np.column_stack(
        (cell_gdf.geometry.x.values, cell_gdf.geometry.y.values)
    )

    result = trend_predict(pts_metric, values, cell_metric, order)
    preds = np.asarray(result["predictions"], dtype=float)
    # Extrapolation flag: outside the sample bbox (metric working space).
    mn = pts_metric.min(axis=0)
    mx = pts_metric.max(axis=0)
    extrapolated = (cell_metric[:, 0] < mn[0]) | (cell_metric[:, 0] > mx[0]) \
        | (cell_metric[:, 1] < mn[1]) | (cell_metric[:, 1] > mx[1])

    records = [
        {
            "h3_index": cell,
            "value": float(v),
            "extrapolated": bool(e),
        }
        for cell, v, e in zip(target_cells, preds, extrapolated)
    ]
    metadata["extrapolated_count"] = int(extrapolated.sum())
    metadata["value_range"] = [
        round(float(preds.min()), 4),
        round(float(preds.max()), 4),
    ]
    metadata["fit"] = {
        "coefficients": [round(float(b), 6) for b in fit["coefficients"]],
        "scale": {
            "min": [round(float(x), 3) for x in (pts_metric.min(axis=0))],
            "span": [round(float(x), 3) for x in span],
        },
    }
    return {"records": records, "metadata": metadata}
