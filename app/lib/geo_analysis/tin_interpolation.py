"""TIN (triangulated irregular network) interpolation — Delaunay-based.

Companion to :mod:`app.lib.geo_analysis.interpolation` (IDW) — the same GIS
contract applies, plus the TIN-specific ones (Foundation V2 · A2):

* **Methods** — ``linear`` (scipy ``LinearNDInterpolator``: barycentric
  interpolation on the Delaunay simplices, C⁰) and ``clough_tocher``
  (scipy ``CloughTocher2DInterpolator``: C¹ cubic over each macro-triangle,
  Clough & Tocher 1966). Both are exact interpolators through the samples.

* **Honest hull** — ``fill_value=NaN`` outside the convex hull: TIN never
  extrapolates. The H3 surface simply has no records beyond the hull; the
  metadata reports ``fill_fraction`` (covered cells / evaluated cells),
  ``n_outside_hull`` and the hull-area / bbox-area fraction instead of
  inventing values.

* **Reuse, not duplication** — the point-sample parse contract, metric CRS
  chooser and H3 target-grid construction (bbox buffer, antimeridian split,
  resource guard) are imported from the IDW driver module.

* **Scale guards** — Delaunay is O(n log n) with bounded memory, but the
  surface cost still scales with the target grid: more than
  ``TIN_HARD_CAP`` samples is a typed :class:`ResourceScaleMismatch`
  rejection. Fewer than 3 non-degenerate samples (or a collinear point
  set — zero hull area) is a typed :class:`DegenerateData` rejection.

* **Honest validation** — LOOCV like IDW/RBF (per-point refit on the
  others; linear TIN LOOCV needs ≥ 4 samples: a 2-point triangulation is
  degenerate). No theoretical variance — uncertainty is empirical residual
  evidence only. Above a bounded refit budget the LOOCV runs on a
  deterministic stride subsample (``sample_count`` discloses it).

All coordinates are projected to a metric CRS (``_pick_metric_crs``) before
any triangulation — degree-space Delaunay topology would be CRS-invariant
but the barycentric value weights would not (planar-coordinate assumption).
"""
from __future__ import annotations

import logging
import math
from typing import Any

import numpy as np

from app.lib.gis.scientific_errors import (
    DegenerateData,
    InsufficientSamples,
    ResourceScaleMismatch,
    UnsupportedMethod,
)
from app.lib.gis.uncertainty import ValidationMetrics

logger = logging.getLogger(__name__)

TIN_METHODS = ("linear", "clough_tocher")

# ── scale guards (execution-policy contract) ────────────────────────────────
TIN_HARD_CAP = 200_000          # above this: typed rejection (Qhull memory bound)
TIN_LOOCV_MAX_POINTS = 500      # LOOCV refit budget (per-point refit is O(n) fits)

_MIN_TIN_SAMPLES = 3            # a triangulation needs ≥ 3 non-collinear points


def _validate_method(method: str) -> str:
    if method not in TIN_METHODS:
        raise UnsupportedMethod(
            f"TIN method 必须是 {TIN_METHODS} 之一，got {method!r}",
            correction_hint="选择 linear（默认，C⁰）或 clough_tocher（C¹ 三角网格三次）。",
        )
    return method


def _build_interpolator(points_xy: np.ndarray, values: np.ndarray, method: str):
    """Build the scipy triangulated interpolant (NaN fill outside the hull).

    Collinear / coincident point sets (zero hull area) are typed
    :class:`DegenerateData` rejections with a correction hint — Qhull's
    raw ``QhullError`` is never leaked.
    """
    from scipy.interpolate import CloughTocher2DInterpolator, LinearNDInterpolator
    from scipy.spatial import QhullError

    cls = LinearNDInterpolator if method == "linear" else CloughTocher2DInterpolator
    try:
        return cls(points_xy, values, fill_value=np.nan)
    except QhullError as exc:
        raise DegenerateData(
            "TIN 三角剖分失败：样本点共线或退化（凸包面积为零）——"
            "三角网格插值需要 ≥3 个非共线平面点。",
            correction_hint="补充非共线采样点，或改用 IDW / kriging（不要求平面构型）。",
        ) from exc


def tin_predict(
    points_xy: np.ndarray,
    values: np.ndarray,
    targets_xy: np.ndarray,
    method: str = "linear",
) -> np.ndarray:
    """Evaluate the TIN interpolant at ``targets_xy`` (projected metric coords).

    Returns NaN for targets outside the convex hull (honest: no
    extrapolation). Deterministic.

    Raises:
        UnsupportedMethod: unknown method.
        DegenerateData: fewer than 3 samples or a collinear point set.
        InsufficientSamples: fewer than 3 samples (typed sample-floor view).
    """
    method = _validate_method(method)
    pts = np.asarray(points_xy, dtype=float)
    vals = np.asarray(values, dtype=float)
    if len(pts) < _MIN_TIN_SAMPLES:
        raise DegenerateData(
            f"TIN 插值至少需要 {_MIN_TIN_SAMPLES} 个非共线样本点，got {len(pts)}",
            correction_hint="补充采样点，或改用 IDW（单点也能成面）。",
        )
    if len(pts) > TIN_HARD_CAP:
        raise ResourceScaleMismatch(
            f"TIN 输入 {len(pts):,} 个样本超过硬上限 {TIN_HARD_CAP:,}"
            "（Delaunay 剖分 O(n log n) 但内存有界，超限先抽稀）。",
            estimated=f"{len(pts):,} samples (Qhull triangulation)",
            limit=f"{TIN_HARD_CAP:,} samples",
            correction_hint="先做确定性空间抽稀（如 stratified_subsample）或降低采样密度。",
        )
    targets = np.atleast_2d(np.asarray(targets_xy, dtype=float))
    interp = _build_interpolator(pts, vals, method)
    return np.asarray(interp(targets), dtype=float)


def _tin_loocv_residuals(
    points_xy: np.ndarray,
    values: np.ndarray,
    method: str,
    max_points: int = TIN_LOOCV_MAX_POINTS,
) -> tuple[np.ndarray, int]:
    """LOOCV residuals of the TIN fit (per-point refit on the others).

    Coordinates must already be in the projected working CRS. Linear TIN
    leave-one-out needs ≥ 4 samples (a 2-point triangulation is degenerate).
    Above ``max_points`` a deterministic stride subsample bounds the refit
    budget — the returned count discloses the actual LOOCV sample size.
    Returns ``(residuals, n_used)``.
    """
    method = _validate_method(method)
    pts = np.asarray(points_xy, dtype=float)
    vals = np.asarray(values, dtype=float)
    n = len(vals)
    if n < _MIN_TIN_SAMPLES + 1:
        raise InsufficientSamples(
            f"TIN LOOCV 需要至少 {_MIN_TIN_SAMPLES + 1} 个样本点"
            f"（留一后仍需 ≥{_MIN_TIN_SAMPLES} 点构成三角网格），got {n}",
            correction_hint="增加采样点后重试。",
        )
    if n > max_points:
        stride = int(math.ceil(n / max_points))
        keep = np.arange(0, n, stride)
        pts = pts[keep]
        vals = vals[keep]
        logger.info(
            "tin: LOOCV subsample %d -> %d points (deterministic stride %d)",
            n, len(vals), stride,
        )
        n = len(vals)
    preds = np.empty(n, dtype=np.float64)
    for i in range(n):
        mask = np.ones(n, dtype=bool)
        mask[i] = False
        preds[i] = tin_predict(pts[mask], vals[mask], pts[i:i + 1], method=method)[0]
    finite = np.isfinite(preds)
    if not finite.all():
        # hull vertices leave a hole behind when held out — those residuals
        # are undefined outside the reduced hull; disclose via count and
        # compute metrics on the covered subset only (never silently).
        logger.info(
            "tin: %d/%d LOOCV point(s) outside the reduced hull (no residual)",
            int((~finite).sum()), n,
        )
    return preds[finite] - vals[finite], int(finite.sum())


def tin_loocv(
    points_xy: np.ndarray,
    values: np.ndarray,
    method: str = "linear",
    max_points: int = TIN_LOOCV_MAX_POINTS,
) -> dict:
    """Leave-one-out CV of the TIN fit; summary metrics over the residuals.

    Returns ``{"rmse", "mae", "bias", "method": "loocv", "sample_count"}``;
    TIN has NO theoretical variance — this empirical residual evidence is
    the only honest uncertainty statement.
    """
    resid, n_used = _tin_loocv_residuals(points_xy, values, method, max_points)
    return {
        "rmse": float(np.sqrt(np.mean(resid ** 2))),
        "mae": float(np.mean(np.abs(resid))),
        "bias": float(np.mean(resid)),
        "method": "loocv",
        "sample_count": int(n_used),
    }


def tin_surface(
    points_geojson: Any,
    value_field: str,
    resolution: int = 7,
    method: str = "linear",
    cross_validate: bool = True,
) -> dict:
    """TIN surface over the sample bbox on an H3 grid (IDW driver parity).

    Full driver: parse + validate samples (shared IDW contract), resolve the
    metric working CRS, apply the scale guards, triangulate, evaluate every
    H3 cell centre (NaN outside the convex hull — those cells are omitted
    from the records and counted in metadata), and optionally LOOCV-validate.

    Returns:

    ``{"records": [{"h3_index", "value"}...],
       "metadata": {algorithm, method, resolution, working_crs, bbox,
                    n_samples, cell_count, n_evaluated, n_outside_hull,
                    fill_fraction, hull_area_fraction, triangle_count,
                    value_range, value_field, validation?, uncertainty?,
                    disclosures?}}``

    Raises:
        UnsupportedMethod: unknown method.
        ResourceScaleMismatch: more than ``TIN_HARD_CAP`` samples.
        DegenerateData: fewer than 3 samples or a collinear point set.
        InsufficientSamples: LOOCV with < 4 samples (skipped instead).
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
    method = _validate_method(method)

    # --- parse + validate sample points (shared IDW contract) ---------------
    lonlat, values = _parse_point_values(
        points_geojson, value_field, purpose="TIN 插值", log_prefix="tin"
    )
    n = len(values)
    if n < _MIN_TIN_SAMPLES:
        raise DegenerateData(
            f"TIN 插值至少需要 {_MIN_TIN_SAMPLES} 个去重后的非共线采样点，got {n}",
            correction_hint="补充采样点，或改用 IDW（单点也能成面）。",
        )

    # --- scale guard ---------------------------------------------------------
    disclosures: list[str] = []
    if n > TIN_HARD_CAP:
        raise ResourceScaleMismatch(
            f"TIN 输入 {n:,} 个样本超过硬上限 {TIN_HARD_CAP:,}"
            "（Delaunay 剖分 O(n log n) 但内存有界，超限先抽稀）。",
            estimated=f"{n:,} samples (Qhull triangulation)",
            limit=f"{TIN_HARD_CAP:,} samples",
            correction_hint="先做确定性空间抽稀（如 stratified_subsample）或降低采样密度。",
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

    # --- triangulation facts (triangles + hull geometry) ---------------------
    from scipy.spatial import Delaunay, QhullError

    try:
        tri = Delaunay(pts_metric)
    except QhullError as exc:
        raise DegenerateData(
            "TIN 三角剖分失败：样本点共线或退化（凸包面积为零）——"
            "三角网格插值需要 ≥3 个非共线平面点。",
            correction_hint="补充非共线采样点，或改用 IDW / kriging（不要求平面构型）。",
        ) from exc
    triangle_count = int(len(tri.simplices))
    from scipy.spatial import ConvexHull

    hull = ConvexHull(pts_metric)  # 2D hull: .volume IS the area
    x = pts_metric[:, 0]
    y = pts_metric[:, 1]
    bbox_area = max(float((x.max() - x.min()) * (y.max() - y.min())), 1e-12)
    hull_area_fraction = float(hull.volume / bbox_area)

    metadata: dict[str, Any] = {
        "algorithm": "interpolation.tin",
        "value_field": value_field,
        "resolution": int(resolution),
        "method": method,
        "working_crs": utm_crs,
        "n_samples": int(n),
        "triangle_count": triangle_count,
        "hull_area_fraction": round(hull_area_fraction, 6),
    }

    # --- LOOCV evidence (samples only — independent of the target grid) -----
    if cross_validate:
        try:
            resid, n_used = _tin_loocv_residuals(pts_metric, values, method)
        except InsufficientSamples as exc:
            metadata["validation_note"] = str(exc.detail)
        else:
            abs_res = np.abs(resid)
            metadata["validation"] = ValidationMetrics(
                target="tin_surface",
                method="loocv",
                rmse=float(np.sqrt(np.mean(resid ** 2))),
                mae=float(np.mean(abs_res)),
                bias=float(np.mean(resid)),
                sample_count=int(n_used),
            ).to_evidence()
            metadata["uncertainty"] = {
                "target": "tin_surface",
                "uncertainty_type": "scalar_uncertainty",
                "method": "loocv_residual_quantiles",
                "quantiles": {
                    "p50": round(float(np.quantile(abs_res, 0.5)), 6),
                    "p90": round(float(np.quantile(abs_res, 0.9)), 6),
                },
                "sample_count": int(n_used),
                "note": "TIN 无理论方差——不确定性以 LOOCV 绝对残差的经验分位数表达。",
            }
    if disclosures:
        metadata["disclosures"] = disclosures

    # --- H3 target cells (lon/lat bbox) + resource guard (IDW contract) -----
    target_cells, (min_lon, min_lat, max_lon, max_lat) = _target_cells_for_samples(
        lonlat, resolution, label="TIN"
    )
    metadata["bbox"] = [min_lon, min_lat, max_lon, max_lat]
    n_cells = len(target_cells)
    if n_cells == 0:
        logger.warning(
            "tin: H3 polyfill returned 0 cells for bbox lon[%s,%s] lat[%s,%s] "
            "(polar / whole-world edge case); returning empty surface.",
            min_lon, max_lon, min_lat, max_lat,
        )
        metadata["cell_count"] = 0
        return {"records": [], "metadata": metadata}
    metadata["cell_count"] = int(n_cells)

    # --- metric projection of cell centres + TIN evaluation -----------------
    import h3

    cell_latlng = np.array([h3.cell_to_latlng(c) for c in target_cells])  # (n,2)
    cell_gdf = gpd.GeoDataFrame(
        geometry=gpd.points_from_xy(cell_latlng[:, 1], cell_latlng[:, 0]),
        crs="EPSG:4326",
    ).to_crs(utm_crs)
    cell_metric = np.column_stack(
        (cell_gdf.geometry.x.values, cell_gdf.geometry.y.values)
    )

    out = tin_predict(pts_metric, values, cell_metric, method=method)
    inside = np.isfinite(out)
    n_outside = int((~inside).sum())

    # Honest hull: cells outside the convex hull carry no value (no
    # extrapolation) — omitted from records, counted in metadata.
    records = [
        {"h3_index": cell, "value": float(v)}
        for cell, v in zip(target_cells, out) if np.isfinite(v)
    ]
    metadata["n_evaluated"] = int(n_cells)
    metadata["n_outside_hull"] = n_outside
    metadata["fill_fraction"] = round(float(inside.sum()) / max(n_cells, 1), 6)
    if n_outside:
        disclosures.append(
            f"{n_outside}/{n_cells} 个目标单元位于样本凸包之外（TIN 不外推，"
            "无记录输出——诚实空缺而非虚构值）。"
        )
    if records:
        vals_in = np.asarray([r["value"] for r in records])
        metadata["value_range"] = [
            round(float(vals_in.min()), 4),
            round(float(vals_in.max()), 4),
        ]
    if disclosures:
        metadata["disclosures"] = disclosures
    return {"records": records, "metadata": metadata}
