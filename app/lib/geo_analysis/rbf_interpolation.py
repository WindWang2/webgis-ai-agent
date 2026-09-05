"""RBF interpolation (scipy.interpolate.RBFInterpolator wrapper).

Companion to :mod:`app.lib.geo_analysis.interpolation` (IDW) — the same GIS
contract applies, plus the RBF-specific ones:

* **Kernels** — thin_plate_spline (default) / linear / cubic / quintic
*   （``multiquadratic`` 已移除：scipy≥1.17 删除该核且需要本包装不
*    暴露的 epsilon —— 模式校验接受它只会让合法请求必然崩溃。）
  / quintic, with ``smoothing`` (0 = exact interpolant through the samples)
  and a local-RBF ``neighbors`` cap (scipy's KdTree neighbourhood, 1-64).
  Deterministic: no RNG anywhere on the path.

* **Reuse, not duplication** — the point-sample parse contract and the H3
  target-grid construction (bbox buffer, antimeridian split, resource guard)
  are imported from the IDW driver module.

* **Scale guards** — N > 20_000 samples are reduced by a deterministic stride
  subsample (disclosed in metadata); N > 100_000 is a typed
  :class:`ResourceScaleMismatch` rejection (dense RBF systems are O(N²)
  memory / O(N³) solve — refuse before OOM, never scale into it silently).

* **Honest validation** — LOOCV like IDW (per-point refit on the others);
  IDW/RBF have NO theoretical variance, so uncertainty is empirical residual
  evidence only. Above a bounded refit budget the LOOCV itself runs on a
  deterministic stride subsample (``sample_count`` discloses it).

All distances are computed in the CALLER-supplied projected (metric) CRS
space — the IDW CRS policy applies unchanged (``_pick_metric_crs`` chooser,
degree-space distance never used).
"""
from __future__ import annotations

import logging
import math
from typing import Any

import numpy as np

from app.lib.cancellation import cancellable
from app.lib.gis.scientific_errors import (
    InsufficientSamples,
    ResourceScaleMismatch,
    UnsupportedMethod,
)
from app.lib.gis.uncertainty import ValidationMetrics

logger = logging.getLogger(__name__)

RBF_KERNELS = ("thin_plate_spline", "linear", "cubic", "quintic")

# ── scale guards (execution-policy contract) ────────────────────────────────
RBF_SUBSAMPLE_TARGET = 20_000   # above this: deterministic stride subsample
RBF_HARD_CAP = 100_000          # above this: typed rejection (dense O(N²) system)
RBF_LOOCV_MAX_POINTS = 500      # LOOCV refit budget (per-point refit is O(n) fits)

_MIN_RBF_SAMPLES = 3            # leave-one-out must leave ≥2 points for the solve


def _validate_kernel(kernel: str) -> str:
    if kernel not in RBF_KERNELS:
        raise UnsupportedMethod(
            f"RBF kernel 必须是 {RBF_KERNELS} 之一，got {kernel!r}",
            correction_hint="选择 thin_plate_spline（默认）/linear/cubic/quintic。",
        )
    return kernel


def _validate_rbf_params(
    kernel: str, smoothing: float, neighbors: int, degree: int
) -> tuple[str, float, int, int]:
    kernel = _validate_kernel(kernel)
    s = float(smoothing)
    if not (0.0 <= s <= 10.0):
        raise ValueError(f"smoothing 必须在 [0, 10] 内，got {smoothing}")
    nb = int(neighbors)
    if not (1 <= nb <= 64):
        raise ValueError(f"neighbors 必须在 [1, 64] 内，got {neighbors}")
    dg = int(degree)
    if dg < -1:
        raise ValueError(f"degree 必须 ≥ -1（-1 = 无多项式附加项），got {degree}")
    return kernel, s, nb, dg


def rbf_predict(
    points_xy: np.ndarray,
    values: np.ndarray,
    targets_xy: np.ndarray,
    kernel: str = "thin_plate_spline",
    smoothing: float = 0.0,
    neighbors: int = 32,
    degree: int = -1,
) -> np.ndarray:
    """Evaluate the RBF interpolant at ``targets_xy`` (projected metric coords).

    Thin wrapper over :class:`scipy.interpolate.RBFInterpolator` with the
    validated parameter contract; ``smoothing=0`` is an exact interpolant
    through the samples. Deterministic.

    Raises:
        UnsupportedMethod: unknown kernel.
        InsufficientSamples: fewer than 2 samples (no RBF system).
        ValueError: smoothing/neighbors/degree out of range.
    """
    from scipy.interpolate import RBFInterpolator

    kernel, smoothing, neighbors, degree = _validate_rbf_params(
        kernel, smoothing, neighbors, degree
    )
    pts = np.asarray(points_xy, dtype=float)
    vals = np.asarray(values, dtype=float)
    if len(pts) < 2:
        raise InsufficientSamples(
            f"RBF 插值需要至少 2 个样本点，got {len(pts)}",
            correction_hint="增加采样点，或改用可处理单点常值面的 IDW。",
        )
    targets = np.atleast_2d(np.asarray(targets_xy, dtype=float))
    interp = RBFInterpolator(
        pts,
        vals,
        kernel=kernel,
        smoothing=smoothing,
        neighbors=int(min(neighbors, len(pts))),
        degree=degree,
    )
    return np.asarray(interp(targets), dtype=float)


def _rbf_loocv_residuals(
    points_xy: np.ndarray,
    values: np.ndarray,
    kernel: str,
    smoothing: float,
    neighbors: int,
    degree: int,
    max_points: int = RBF_LOOCV_MAX_POINTS,
) -> tuple[np.ndarray, int]:
    """LOOCV residuals of the RBF fit (per-point refit on the others).

    Same metric-distance semantics as the driver (coordinates already in the
    projected working CRS). Above ``max_points`` a deterministic stride
    subsample bounds the refit budget — the returned count discloses the
    actual LOOCV sample size (never silently below n).
    Returns ``(residuals, n_used)``.
    """
    kernel, smoothing, neighbors, degree = _validate_rbf_params(
        kernel, smoothing, neighbors, degree
    )
    pts = np.asarray(points_xy, dtype=float)
    vals = np.asarray(values, dtype=float)
    n = len(vals)
    if n < _MIN_RBF_SAMPLES:
        raise InsufficientSamples(
            f"RBF LOOCV 需要至少 {_MIN_RBF_SAMPLES} 个样本点（留一后仍需 ≥2 点求解），got {n}",
            correction_hint="增加采样点后重试。",
        )
    if n > max_points:
        stride = int(math.ceil(n / max_points))
        keep = np.arange(0, n, stride)
        pts = pts[keep]
        vals = vals[keep]
        logger.info(
            "rbf: LOOCV subsample %d -> %d points (deterministic stride %d)",
            n, len(vals), stride,
        )
        n = len(vals)
    nb = int(min(neighbors, n - 1))
    preds = np.empty(n, dtype=np.float64)
    for i in cancellable(range(n), every=64):
        mask = np.ones(n, dtype=bool)
        mask[i] = False
        preds[i] = rbf_predict(
            pts[mask], vals[mask], pts[i:i + 1],
            kernel=kernel, smoothing=smoothing, neighbors=nb, degree=degree,
        )[0]
    return preds - vals, n


def rbf_loocv(
    points_xy: np.ndarray,
    values: np.ndarray,
    kernel: str = "thin_plate_spline",
    smoothing: float = 0.0,
    neighbors: int = 32,
    degree: int = -1,
    max_points: int = RBF_LOOCV_MAX_POINTS,
) -> dict:
    """Leave-one-out CV of the RBF fit; summary metrics over the residuals.

    Returns ``{"rmse", "mae", "bias", "method": "loocv", "sample_count"}``;
    RBF has NO theoretical variance — this empirical residual evidence is the
    only honest uncertainty statement.
    """
    resid, n_used = _rbf_loocv_residuals(
        points_xy, values, kernel, smoothing, neighbors, degree, max_points
    )
    return {
        "rmse": float(np.sqrt(np.mean(resid ** 2))),
        "mae": float(np.mean(np.abs(resid))),
        "bias": float(np.mean(resid)),
        "method": "loocv",
        "sample_count": int(n_used),
    }


def rbf_interpolation(
    points_geojson: Any,
    value_field: str,
    resolution: int = 7,
    kernel: str = "thin_plate_spline",
    smoothing: float = 0.0,
    neighbors: int = 32,
    degree: int = -1,
    cross_validate: bool = True,
) -> dict:
    """RBF surface over the sample bbox on an H3 grid (IDW driver parity).

    Full driver: parse + validate samples (shared IDW contract), resolve the
    metric working CRS, apply the scale guards, evaluate the RBF interpolant
    at every H3 cell centre, and optionally LOOCV-validate. Returns:

    ``{"records": [{"h3_index", "value"}...],
       "metadata": {algorithm, kernel, smoothing, neighbors, resolution,
                    working_crs, bbox, n_samples, value_range, value_field,
                    validation, uncertainty, disclosures}}``

    Raises:
        UnsupportedMethod: unknown kernel.
        ResourceScaleMismatch: more than ``RBF_HARD_CAP`` samples.
        InsufficientSamples: fewer than 3 samples.
        ValueError: smoothing/neighbors/degree out of range, unparseable
            input, or invalid H3 resolution.
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
    kernel, smoothing, neighbors, degree = _validate_rbf_params(
        kernel, smoothing, neighbors, degree
    )

    # --- parse + validate sample points (shared IDW contract) ---------------
    lonlat, values = _parse_point_values(
        points_geojson, value_field, purpose="插值", log_prefix="rbf"
    )
    n = len(values)
    if n < _MIN_RBF_SAMPLES:
        raise InsufficientSamples(
            f"RBF 插值至少需要 {_MIN_RBF_SAMPLES} 个去重后的采样点，got {n}",
            correction_hint="减少平滑约束请改用 IDW，或补充观测。",
        )

    # --- scale guards --------------------------------------------------------
    disclosures: list[str] = []
    if n > RBF_HARD_CAP:
        est_gb = n * n * 8 / 1e9
        raise ResourceScaleMismatch(
            f"RBF 输入 {n:,} 个样本超过硬上限 {RBF_HARD_CAP:,}"
            "（稠密核矩阵 O(N²) 内存 / O(N³) 求解）。",
            estimated=f"{n:,} samples (~{est_gb:.1f} GB dense kernel matrix)",
            limit=f"{RBF_HARD_CAP:,} samples",
            correction_hint="先做确定性空间抽稀（如 stratified_subsample）或降低采样密度。",
        )
    if n > RBF_SUBSAMPLE_TARGET:
        stride = int(math.ceil(n / RBF_SUBSAMPLE_TARGET))
        disclosures.append(
            f"输入 {n:,} 点超过 RBF 子采样阈值 {RBF_SUBSAMPLE_TARGET:,}，"
            f"按确定性行距 {stride} 抽稀至 {len(np.arange(0, n, stride)):,} 点后拟合。"
        )
        lonlat = lonlat[::stride]
        values = values[::stride]
        n = len(values)
        logger.info("rbf: stride subsample %d -> %d (stride %d)", len(values), n, stride)

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

    metadata: dict[str, Any] = {
        "algorithm": "interpolation.rbf",
        "value_field": value_field,
        "resolution": int(resolution),
        "kernel": kernel,
        "smoothing": float(smoothing),
        "neighbors": int(min(neighbors, n)),
        "degree": int(degree),
        "working_crs": utm_crs,
        "n_samples": int(n),
    }

    # --- LOOCV evidence (samples only — independent of the target grid) -----
    if cross_validate:
        resid, n_used = _rbf_loocv_residuals(
            pts_metric, values, kernel, smoothing, neighbors, degree
        )
        abs_res = np.abs(resid)
        metadata["validation"] = ValidationMetrics(
            target="rbf_surface",
            method="loocv",
            rmse=float(np.sqrt(np.mean(resid ** 2))),
            mae=float(np.mean(abs_res)),
            bias=float(np.mean(resid)),
            sample_count=int(n_used),
        ).to_evidence()
        metadata["uncertainty"] = {
            "target": "rbf_surface",
            "uncertainty_type": "scalar_uncertainty",
            "method": "loocv_residual_quantiles",
            "quantiles": {
                "p50": round(float(np.quantile(abs_res, 0.5)), 6),
                "p90": round(float(np.quantile(abs_res, 0.9)), 6),
            },
            "sample_count": int(n_used),
            "note": "RBF 无理论方差——不确定性以 LOOCV 绝对残差的经验分位数表达。",
        }
    if disclosures:
        metadata["disclosures"] = disclosures

    # --- H3 target cells (lon/lat bbox) + resource guard (IDW contract) -----
    target_cells, (min_lon, min_lat, max_lon, max_lat) = _target_cells_for_samples(
        lonlat, resolution, label="RBF"
    )
    metadata["bbox"] = [min_lon, min_lat, max_lon, max_lat]
    n_cells = len(target_cells)
    if n_cells == 0:
        logger.warning(
            "rbf: H3 polyfill returned 0 cells for bbox lon[%s,%s] lat[%s,%s] "
            "(polar / whole-world edge case); returning empty surface.",
            min_lon, max_lon, min_lat, max_lat,
        )
        metadata["cell_count"] = 0
        return {"records": [], "metadata": metadata}
    metadata["cell_count"] = int(n_cells)

    # --- metric projection of cell centres + RBF evaluation -----------------
    import h3

    cell_latlng = np.array([h3.cell_to_latlng(c) for c in target_cells])  # (n,2) lat,lng
    cell_gdf = gpd.GeoDataFrame(
        geometry=gpd.points_from_xy(cell_latlng[:, 1], cell_latlng[:, 0]),
        crs="EPSG:4326",
    ).to_crs(utm_crs)
    cell_metric = np.column_stack(
        (cell_gdf.geometry.x.values, cell_gdf.geometry.y.values)
    )

    out = rbf_predict(
        pts_metric, values, cell_metric,
        kernel=kernel, smoothing=smoothing, neighbors=neighbors, degree=degree,
    )

    records = [{"h3_index": cell, "value": float(v)} for cell, v in zip(target_cells, out)]
    metadata["value_range"] = [
        round(float(out.min()), 4),
        round(float(out.max()), 4),
    ]
    return {"records": records, "metadata": metadata}
