"""Model comparison for spatial interpolation — honest LOOCV/CV horse race.

Foundation V2 · A2. Given one point FeatureCollection and a value field,
runs the validation evidence of every eligible interpolation method and
ranks them by RMSE so method choice is evidence-based, not folklore:

    methods = idw / tin(linear) / trend_surface(order by feasibility)
            / rbf(thin_plate_spline) / ordinary_kriging(auto variogram)

Design contract:

* **Deterministic** — fixed method order, no RNG; identical input yields a
  byte-identical comparison table.
* **Per-method guards, reused not reinvented** — each method runs its own
  library validation (``idw_loocv``, ``tin_loocv``, ``trend_loocv``,
  ``rbf_loocv``, ``cross_validate_kriging``), so sample floors and
  subsample disclosures match the drivers exactly. Methods below their
  floor get a row with ``eligible=False`` and a machine-usable
  ``skipped_reason`` — skips are visible, never silent.
* **CV budget cap** — the total LOOCV/CV residual-evaluation budget is
  bounded (``cv_budget``): methods are walked in the fixed order and the
  first whose cumulative cost estimate would exceed the budget is skipped
  with ``cv_budget_exhausted`` (disclosed).
* **Ranking** — by RMSE ascending, tie-broken by method name; the
  recommendation carries the per-method evidence blocks (ValidationMetrics
  shape) so the caller can attach them to scientific evidence.

Coordinates are projected to a metric CRS (``_pick_metric_crs``) before any
distance math (the shared interpolation CRS policy). The comparison runs on
the SAMPLES only (LOOCV/CV evidence) — it never evaluates a surface, so it
is cheap relative to the drivers it ranks.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

import numpy as np

from app.lib.gis.scientific_errors import DegenerateData, InsufficientSamples
from app.lib.gis.uncertainty import ValidationMetrics

logger = logging.getLogger(__name__)

#: Fixed deterministic evaluation order (budget walks this sequence).
COMPARE_METHODS = ("idw", "tin", "trend_surface", "rbf", "ordinary_kriging")

#: Sample floors below which a method cannot even produce CV evidence.
#: idw ≥ 2 (LOOCV needs a held-out), tin ≥ 4 (2-point triangulation is
#: degenerate), trend ≥ 6 (order-1 term-growth guard), rbf ≥ 3 (LOO needs
#: ≥ 2 points in the solve), kriging ≥ 20 (its CV floor MIN_CV_SAMPLES —
#: the method itself needs 8, but the comparison uses CV evidence only).
METHOD_SAMPLE_FLOORS = {
    "idw": 2,
    "tin": 4,
    "trend_surface": 6,
    "rbf": 3,
    "ordinary_kriging": 20,
}

DEFAULT_CV_BUDGET = 2500  # total residual-evaluation budget across methods


def _estimate_cv_cost(method: str, n: int) -> int:
    """Deterministic per-method cost estimate (residual evaluations)."""
    if method == "ordinary_kriging":
        return n  # k-fold: n held-out predictions (+ k variogram refits)
    if method == "idw":
        return n
    # rbf / tin / trend: LOOCV bounded to their per-library subsample caps
    return min(n, 500)


def _feasible_trend_order(n: int) -> Optional[int]:
    """Highest trend order whose term-growth guard passes (None = infeasible)."""
    from app.lib.geo_analysis.trend_surface import _check_order_feasible
    from app.lib.gis.scientific_errors import InsufficientSamples as _IS

    for order in (3, 2, 1):
        try:
            _check_order_feasible(order, n)
            return order
        except _IS:
            continue
    return None


def _evaluate_method(
    method: str, pts_metric: np.ndarray, values: np.ndarray, order: Optional[int]
) -> dict:
    """Run one method's library validation; returns a comparison-table row."""
    n = len(values)
    floor = METHOD_SAMPLE_FLOORS[method]
    if n < floor:
        return {
            "method": method, "eligible": False,
            "skipped_reason": f"样本量 {n} < {floor}（该方法 CV 证据的样本下限）",
            "rmse": None, "mae": None, "bias": None, "n_used": 0,
        }
    if method == "trend_surface":
        if order is None:
            return {
                "method": method, "eligible": False,
                "skipped_reason": "无可行阶数（1-3 的项增长守卫均不通过）",
                "rmse": None, "mae": None, "bias": None, "n_used": 0,
            }
        from app.lib.geo_analysis.trend_surface import trend_loocv

        metrics = trend_loocv(pts_metric, values, order=order)
    elif method == "idw":
        from app.lib.geo_analysis.interpolation import idw_loocv

        metrics = idw_loocv(pts_metric, values, power=2.0)
    elif method == "tin":
        from app.lib.geo_analysis.tin_interpolation import tin_loocv

        metrics = tin_loocv(pts_metric, values, method="linear")
    elif method == "rbf":
        from app.lib.geo_analysis.rbf_interpolation import rbf_loocv

        metrics = rbf_loocv(
            pts_metric, values, kernel="thin_plate_spline", smoothing=0.0,
        )
    elif method == "ordinary_kriging":
        from app.lib.geo_analysis.kriging import cross_validate_kriging

        report = cross_validate_kriging(pts_metric, values, model="auto")
        if report.rmse is None:
            return {
                "method": method, "eligible": False,
                "skipped_reason": report.note or "克里金交叉验证未产出指标",
                "rmse": None, "mae": None, "bias": None, "n_used": 0,
            }
        metrics = {
            "rmse": report.rmse, "mae": report.mae, "bias": report.bias,
            "method": "k_fold", "sample_count": report.n_samples,
            "folds": report.folds,
        }
    else:  # pragma: no cover - vocabulary-guarded
        return {
            "method": method, "eligible": False,
            "skipped_reason": f"未知方法 {method!r}",
            "rmse": None, "mae": None, "bias": None, "n_used": 0,
        }
    validation = ValidationMetrics(
        target=f"interpolation_compare:{method}",
        method="k_fold" if method == "ordinary_kriging" else "loocv",
        rmse=metrics.get("rmse"),
        mae=metrics.get("mae"),
        bias=metrics.get("bias"),
        folds=metrics.get("folds"),
        sample_count=metrics.get("sample_count"),
    )
    return {
        "method": method,
        "eligible": True,
        "skipped_reason": None,
        "rmse": float(metrics["rmse"]),
        "mae": float(metrics["mae"]),
        "bias": float(metrics["bias"]),
        "n_used": int(metrics.get("sample_count", n)),
        "validation": validation.to_evidence(),
    }


def compare_interpolation_models(
    points_geojson: Any,
    value_field: str,
    cv_budget: int = DEFAULT_CV_BUDGET,
) -> dict:
    """Compare eligible interpolation methods by CV evidence on one FC.

    Returns:

    ``{"comparison": [{method, eligible, skipped_reason, rmse, mae, bias,
                       n_used, validation?}...],
       "recommended": {method, rmse, evidence_note} | None,
       "metadata": {algorithm, value_field, n_samples, working_crs,
                    cv_budget, budget_used, trend_order, method_order,
                    n_eligible}}``

    The table is deterministic: fixed method order, deterministic tie-break
    by method name, budget walk in :data:`COMPARE_METHODS` order. Methods
    are skipped honestly (sample floor / budget / per-method guard) — a
    skip is a row, not silence.

    Raises:
        ValueError: unparseable input, missing/non-numeric/NaN/inf fields.
    """
    from app.lib.geo_analysis.interpolation import (
        _parse_point_values,
        _pick_metric_crs,
    )
    import geopandas as gpd

    if not isinstance(value_field, str) or not value_field:
        raise ValueError("value_field must be a non-empty string")
    budget = int(cv_budget)
    if budget <= 0:
        raise ValueError(f"cv_budget must be a positive integer, got {cv_budget!r}")

    lonlat, values = _parse_point_values(
        points_geojson, value_field, purpose="插值模型比较", log_prefix="model_compare"
    )
    n = len(values)
    utm_crs = _pick_metric_crs(lonlat)
    pts_gdf = gpd.GeoDataFrame(
        {"v": values},
        geometry=gpd.points_from_xy(lonlat[:, 0], lonlat[:, 1]),
        crs="EPSG:4326",
    ).to_crs(utm_crs)
    pts_metric = np.column_stack(
        (pts_gdf.geometry.x.values, pts_gdf.geometry.y.values)
    )

    trend_order = _feasible_trend_order(n)
    comparison: list[dict] = []
    budget_used = 0
    n_errors = 0
    for method in COMPARE_METHODS:
        estimate = _estimate_cv_cost(method, n)
        if budget_used + estimate > budget:
            comparison.append({
                "method": method, "eligible": False,
                "skipped_reason": (
                    f"cv_budget_exhausted：预算 {budget}，已用 {budget_used}，"
                    f"该方法估计需要 {estimate}"
                ),
                "rmse": None, "mae": None, "bias": None, "n_used": 0,
            })
            continue
        try:
            row = _evaluate_method(method, pts_metric, values, trend_order)
        except (InsufficientSamples, DegenerateData) as exc:
            row = {
                "method": method, "eligible": False,
                "skipped_reason": f"{type(exc).__name__}: {exc.detail}",
                "rmse": None, "mae": None, "bias": None, "n_used": 0,
            }
        if row["eligible"]:
            budget_used += estimate
        else:
            n_errors += 1
        comparison.append(row)

    eligible_rows = [r for r in comparison if r["eligible"]]
    recommended: Optional[dict] = None
    if eligible_rows:
        best = min(eligible_rows, key=lambda r: (r["rmse"], r["method"]))
        recommended = {
            "method": best["method"],
            "rmse": best["rmse"],
            "evidence_note": (
                f"LOOCV/CV 证据：{best['method']} RMSE={best['rmse']:.6f} "
                f"(MAE={best['mae']:.6f})，为 {len(eligible_rows)} 个可行方法中最低"
                "（平局按方法名确定性打破）。"
            ),
        }

    metadata = {
        "algorithm": "interpolation.model_compare",
        "value_field": value_field,
        "n_samples": int(n),
        "working_crs": utm_crs,
        "cv_budget": budget,
        "budget_used": int(budget_used),
        "trend_order": trend_order,
        "method_order": list(COMPARE_METHODS),
        "n_eligible": len(eligible_rows),
    }
    if n_errors:
        metadata["n_skipped"] = n_errors
    return {
        "comparison": comparison,
        "recommended": recommended,
        "metadata": metadata,
    }
