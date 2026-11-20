"""Numerically stable 2D general affine solver and honest checkpoint diagnostics (R10-R16)."""
from __future__ import annotations

import math
from typing import List, Sequence, Tuple

import numpy as np

from app.schemas.georeferencing_schema import (
    AffineCoefficients,
    AffineFitMetrics,
    GcpPoint,
    PointResidual,
)

# Condition number threshold on centered/scaled pixel design matrix [1, u, v].
_MAX_COND_DESIGN = 1e6
# Minimum ratio of smaller to larger singular value on Jacobian J = [[a1, a2], [b1, b2]].
_MIN_JACOBIAN_SV_RATIO = 1e-4
_MIN_JACOBIAN_DET_NORM = 1e-10
_LOW_HULL_COVERAGE_THRESHOLD = 0.12


def _convex_hull_area_2d(points: Sequence[Tuple[float, float]]) -> float:
    """Monotonic chain 2D convex hull polygon area."""
    pts = sorted(set((float(x), float(y)) for x, y in points))
    if len(pts) < 3:
        return 0.0

    def _cross(o: Tuple[float, float], a: Tuple[float, float], b: Tuple[float, float]) -> float:
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower: List[Tuple[float, float]] = []
    for p in pts:
        while len(lower) >= 2 and _cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    upper: List[Tuple[float, float]] = []
    for p in reversed(pts):
        while len(upper) >= 2 and _cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    hull = lower[:-1] + upper[:-1]
    if len(hull) < 3:
        return 0.0
    area = 0.0
    n = len(hull)
    for i in range(n):
        x1, y1 = hull[i]
        x2, y2 = hull[(i + 1) % n]
        area += x1 * y2 - x2 * y1
    return abs(area) * 0.5


def solve_affine_gcps(
    gcps: Sequence[GcpPoint],
    *,
    width: int,
    height: int,
) -> AffineFitMetrics:
    """Fit 2D general 6-parameter affine transform and compute independent checkpoint metrics.

    Model:
        map_x = a0 + a1 * px + a2 * py
        map_y = b0 + b1 * px + b2 * py

    Convention:
        (px, py) = (0, 0) corresponds to top-left corner of top-left pixel;
        pixel center is (col + 0.5, row + 0.5), matching GDAL/Rasterio Affine.
    """
    blockers: List[str] = []
    warnings: List[str] = []

    fit_pts: List[GcpPoint] = []
    check_pts: List[GcpPoint] = []
    disabled_count = 0

    for g in gcps:
        if not (
            math.isfinite(g.px)
            and math.isfinite(g.py)
            and math.isfinite(g.map_x)
            and math.isfinite(g.map_y)
        ):
            blockers.append(f"GCP '{g.label or g.gcp_id}' contains non-finite coordinates.")
            continue
        if g.px < 0.0 or g.px > float(width) or g.py < 0.0 or g.py > float(height):
            blockers.append(
                f"GCP '{g.label or g.gcp_id}' pixel ({g.px:.2f}, {g.py:.2f}) is outside image bounds [0,{width}]x[0,{height}]."
            )
            continue
        if not g.enabled:
            disabled_count += 1
            continue
        if g.role == "check":
            check_pts.append(g)
        else:
            fit_pts.append(g)

    if blockers:
        return AffineFitMetrics(
            status="invalid",
            fit_count=len(fit_pts),
            check_count=len(check_pts),
            disabled_count=disabled_count,
            blockers=blockers,
            warnings=warnings,
        )

    if len(fit_pts) < 3:
        blockers.append(
            f"At least 3 enabled fit GCPs are required for 2D affine fitting (got {len(fit_pts)})."
        )
        return AffineFitMetrics(
            status="insufficient",
            fit_count=len(fit_pts),
            check_count=len(check_pts),
            disabled_count=disabled_count,
            blockers=blockers,
            warnings=warnings,
        )

    px_arr = np.array([g.px for g in fit_pts], dtype=np.float64)
    py_arr = np.array([g.py for g in fit_pts], dtype=np.float64)
    mx_arr = np.array([g.map_x for g in fit_pts], dtype=np.float64)
    my_arr = np.array([g.map_y for g in fit_pts], dtype=np.float64)

    # Center & scale normalization prevents catastrophic cancellation on UTM magnitudes.
    px_mean = float(np.mean(px_arr))
    py_mean = float(np.mean(py_arr))
    px_scale = max(float(np.std(px_arr)), 1.0)
    py_scale = max(float(np.std(py_arr)), 1.0)

    mx_mean = float(np.mean(mx_arr))
    my_mean = float(np.mean(my_arr))
    mx_scale = max(float(np.std(mx_arr)), 1.0)
    my_scale = max(float(np.std(my_arr)), 1.0)

    u = (px_arr - px_mean) / px_scale
    v = (py_arr - py_mean) / py_scale
    X = (mx_arr - mx_mean) / mx_scale
    Y = (my_arr - my_mean) / my_scale

    A = np.column_stack([np.ones_like(u), u, v])
    sv = np.linalg.svd(A, compute_uv=False)
    sv_max = float(sv[0]) if len(sv) > 0 else 0.0
    sv_min = float(sv[-1]) if len(sv) >= 3 else 0.0
    rank = int(np.sum(sv > max(1e-10, sv_max * 1e-10)))
    cond = float(sv_max / sv_min) if sv_min > 1e-15 else float("inf")

    if rank < 3 or not math.isfinite(cond) or cond > _MAX_COND_DESIGN:
        blockers.append(
            f"Fit GCP geometry is collinear or numerically rank-deficient (rank={rank}, cond={cond:.2e}). Add non-collinear points spanning the image."
        )
        return AffineFitMetrics(
            status="degenerate",
            fit_count=len(fit_pts),
            check_count=len(check_pts),
            disabled_count=disabled_count,
            rank=rank,
            condition_number=None if not math.isfinite(cond) else cond,
            blockers=blockers,
            warnings=warnings,
        )

    cx, _, _, _ = np.linalg.lstsq(A, X, rcond=None)
    cy, _, _, _ = np.linalg.lstsq(A, Y, rcond=None)

    # Map normalized fit parameters back to original pixel -> target CRS space:
    # mx = mx_mean + mx_scale * (cx[0] + cx[1]*(px-px_mean)/px_scale + cx[2]*(py-py_mean)/py_scale)
    a1 = float(mx_scale * cx[1] / px_scale)
    a2 = float(mx_scale * cx[2] / py_scale)
    a0 = float(mx_mean + mx_scale * cx[0] - a1 * px_mean - a2 * py_mean)

    b1 = float(my_scale * cy[1] / px_scale)
    b2 = float(my_scale * cy[2] / py_scale)
    b0 = float(my_mean + my_scale * cy[0] - b1 * px_mean - b2 * py_mean)

    jac = np.array([[a1, a2], [b1, b2]], dtype=np.float64)
    j_sv = np.linalg.svd(jac, compute_uv=False)
    j_sv_max = float(j_sv[0])
    j_sv_min = float(j_sv[1])
    det = float(a1 * b2 - a2 * b1)

    if (
        j_sv_max <= 0.0
        or j_sv_min <= 0.0
        or (j_sv_min / j_sv_max) < _MIN_JACOBIAN_SV_RATIO
        or abs(det) <= _MIN_JACOBIAN_DET_NORM * (j_sv_max * j_sv_max)
    ):
        blockers.append(
            f"Solved 2D affine Jacobian is singular/degenerate (det={det:.3e}, sv_ratio={j_sv_min / max(j_sv_max, 1e-15):.3e}); inverse warp cannot be constructed."
        )
        return AffineFitMetrics(
            status="degenerate",
            fit_count=len(fit_pts),
            check_count=len(check_pts),
            disabled_count=disabled_count,
            rank=rank,
            condition_number=cond,
            blockers=blockers,
            warnings=warnings,
        )

    px_scale_x = float(math.hypot(a1, b1))
    px_scale_y = float(math.hypot(a2, b2))
    rotation_deg = float(math.degrees(math.atan2(b1, a1)))
    cos_shear = (a1 * a2 + b1 * b2) / max(px_scale_x * px_scale_y, 1e-18)
    cos_shear = max(-1.0, min(1.0, cos_shear))
    # For orthogonal axes, dot product = 0 -> cos=0 -> orthogonality angle = 90 deg -> shear = 0 deg.
    shear_deg = float(90.0 - math.degrees(math.acos(cos_shear)))
    # Standard down-positive pixel Y paired with up-positive Northing has det < 0;
    # det > 0 relative to image-down / map-up indicates mirrored handedness.
    is_reflection = det > 0.0
    if is_reflection:
        warnings.append(
            "Affine determinant is positive (det > 0), indicating mirror reflection or inverted axis orientation relative to standard north-up image coordinates."
        )

    coeffs = AffineCoefficients(
        a0=a0,
        a1=a1,
        a2=a2,
        b0=b0,
        b1=b1,
        b2=b2,
        px_scale_x=px_scale_x,
        px_scale_y=px_scale_y,
        rotation_deg=rotation_deg,
        shear_deg=shear_deg,
        determinant=det,
        is_reflection=is_reflection,
    )

    # Evaluate residuals for all GCPs cleanly.
    residuals: List[PointResidual] = []
    fit_radials: List[Tuple[str, float]] = []
    check_radials: List[float] = []
    for g in gcps:
        if not g.enabled:
            residuals.append(
                PointResidual(
                    gcp_id=g.gcp_id,
                    label=g.label,
                    role=g.role,
                    enabled=False,
                    px=g.px,
                    py=g.py,
                    map_x=g.map_x,
                    map_y=g.map_y,
                )
            )
            continue
        pred_x = a0 + a1 * g.px + a2 * g.py
        pred_y = b0 + b1 * g.px + b2 * g.py
        dx = g.map_x - pred_x
        dy = g.map_y - pred_y
        rad = float(math.hypot(dx, dy))
        residuals.append(
            PointResidual(
                gcp_id=g.gcp_id,
                label=g.label,
                role=g.role,
                enabled=True,
                px=g.px,
                py=g.py,
                map_x=g.map_x,
                map_y=g.map_y,
                pred_x=pred_x,
                pred_y=pred_y,
                dx=dx,
                dy=dy,
                radial_error=rad,
            )
        )
        if g.role == "check":
            check_radials.append(rad)
        else:
            fit_radials.append((g.gcp_id, rad))

    fit_errs = [r for _, r in fit_radials]
    fit_rmse = float(math.sqrt(sum(r * r for r in fit_errs) / len(fit_errs)))
    fit_max = float(max(fit_errs))

    check_rmse = (
        float(math.sqrt(sum(r * r for r in check_radials) / len(check_radials)))
        if check_radials
        else None
    )
    check_max = float(max(check_radials)) if check_radials else None

    zero_unverified = len(fit_pts) == 3
    if zero_unverified:
        warnings.append(
            "Exactly 3 fit points produce mathematically zero fitting residuals; accuracy is NOT independently verified. Add extra fit GCPs or independent checkpoints."
        )

    # Spatial distribution & extrapolation check via normalized convex hull coverage.
    norm_fit = [(g.px / max(width, 1), g.py / max(height, 1)) for g in fit_pts]
    hull_ratio = float(min(1.0, _convex_hull_area_2d(norm_fit)))
    low_cov = hull_ratio < _LOW_HULL_COVERAGE_THRESHOLD
    extrapolation = low_cov
    if low_cov:
        warnings.append(
            f"GCP spatial coverage is concentrated ({hull_ratio * 100:.1f}% convex-hull area); large parts of the image require extrapolation."
        )

    # Outlier detection (>= 4 fit points) without auto-dropping points.
    outlier_ids: List[str] = []
    if len(fit_radials) >= 4:
        med = float(np.median(fit_errs))
        mean = float(np.mean(fit_errs))
        thresh = max(3.0 * med, 2.0 * mean, 1e-6)
        # Require meaningful absolute difference relative to pixel ground sample distance.
        min_metric = 0.25 * min(px_scale_x, px_scale_y)
        for gid, rad in fit_radials:
            if rad > thresh and rad > min_metric:
                outlier_ids.append(gid)
        if outlier_ids:
            warnings.append(
                f"Potential outlier fit point(s) detected ({', '.join(outlier_ids)}); review manually rather than auto-deleting."
            )

    return AffineFitMetrics(
        status="valid",
        fit_count=len(fit_pts),
        check_count=len(check_pts),
        disabled_count=disabled_count,
        fit_rmse=fit_rmse,
        fit_max_error=fit_max,
        check_rmse=check_rmse,
        check_max_error=check_max,
        zero_residual_unverified=zero_unverified,
        condition_number=cond,
        rank=rank,
        hull_coverage_ratio=hull_ratio,
        low_coverage_warning=low_cov,
        extrapolation_warning=extrapolation,
        outlier_gcp_ids=outlier_ids,
        warnings=warnings,
        blockers=[],
        coefficients=coeffs,
        residuals=residuals,
    )
