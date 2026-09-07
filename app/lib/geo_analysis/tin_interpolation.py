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


# ── V3：自然邻域插值（Sibson）───────────────────────────────────────────────
#
# Foundation V3（Geostatistics/Interpolation 批次）追加：Sibson (1981)
# 自然邻域坐标——权重 = 插入点从每个自然邻域的 Voronoi 单元"窃取"的面积
# 比例（精确多边形裁剪面积，非近似核函数）。与 TIN 同属 Delaunay 家族：
# 凸包外 NaN（不外推）、米制坐标假设、精确插值（过样本点）。
# Watson (1981) 阶梯 walk：从包含单形出发只穿越外接圆包含目标点的单形。

SIBSON_MAX_SAMPLES = 200_000       # above this: typed rejection
SIBSON_MAX_GRID_CELLS = 4_000_000  # target-cell ceiling
_SIBSON_EXACT_HIT_M = 1e-9         # 与 IDW/TIN 同口径的精确重合阈值


def _clip_polygon_halfplane(poly: list, a: tuple, b: tuple) -> list:
    """Sutherland–Hodgman 裁剪：保留 |p−a| ≤ |p−b| 的一侧。

    不等式 |p−a|² ≤ |p−b|² 线性化为 2·(b−a)·p ≤ |b|²−|a|²。凸多边形输入
    输出均为凸（顶点元组列表；空列表 = 全部被裁掉）。
    """
    if not poly:
        return []
    ax, ay = float(a[0]), float(a[1])
    bx, by = float(b[0]), float(b[1])
    wx, wy = bx - ax, by - ay
    c = bx * bx + by * by - (ax * ax + ay * ay)
    out: list = []
    m = len(poly)
    for e in range(m):
        p = poly[e]
        q = poly[(e + 1) % m]
        fp = 2.0 * (wx * p[0] + wy * p[1]) - c
        fq = 2.0 * (wx * q[0] + wy * q[1]) - c
        if fp <= 0.0:
            out.append(p)
            if fq > 0.0:
                t = fp / (fp - fq)
                out.append((p[0] + t * (q[0] - p[0]), p[1] + t * (q[1] - p[1])))
        elif fq <= 0.0:
            t = fp / (fp - fq)
            out.append((p[0] + t * (q[0] - p[0]), p[1] + t * (q[1] - p[1])))
    return out


def _polygon_area(poly: list) -> float:
    """Shoelace 面积（凸多边形；<3 顶点为 0）。"""
    if len(poly) < 3:
        return 0.0
    s = 0.0
    m = len(poly)
    for e in range(m):
        p = poly[e]
        q = poly[(e + 1) % m]
        s += p[0] * q[1] - q[0] * p[1]
    return abs(s) * 0.5


def _circumcircle(pa, pb, pc):
    """外接圆 ``(cx, cy, r)``；退化（近共线）返回 None。"""
    d = 2.0 * ((pb[0] - pa[0]) * (pc[1] - pa[1]) - (pb[1] - pa[1]) * (pc[0] - pa[0]))
    if abs(d) < 1e-12:
        return None
    a2 = pa[0] * pa[0] + pa[1] * pa[1]
    b2 = pb[0] * pb[0] + pb[1] * pb[1]
    c2 = pc[0] * pc[0] + pc[1] * pc[1]
    ux = ((b2 - a2) * (pc[1] - pa[1]) - (pb[1] - pa[1]) * (c2 - a2)) / d
    uy = ((pb[0] - pa[0]) * (c2 - a2) - (pc[0] - pa[0]) * (b2 - a2)) / d
    return ux, uy, math.hypot(ux - pa[0], uy - pa[1])


def natural_neighbor_interpolation(
    xy: np.ndarray, values: np.ndarray, grid_xy: np.ndarray
) -> tuple[np.ndarray, dict]:
    """自然邻域插值（Sibson 1981 坐标，Watson 1981 阶梯算法）。

    对每个格点 x：从包含单形出发做 Delaunay 邻接 walk，收集外接圆包含 x
    的全部单形（阶梯集合）——其顶点即 x 的**自然邻域**。Sibson 权重 =
    V_i ∩ V_x 的精确面积比例（V_i 为原点集的 Voronoi 单元，V_x 为插入 x
    后的单元；两者交集由平分线逐次凸裁剪得到）。Sibson 坐标精确再现线性
    函数（Σw=1 且 Σw·x_i = x）， therefore 平面场内部复现 ≤1e-6。

    诚实语义：

    * **凸包外 NaN** —— 不外推（无值格点计数披露，与 TIN 同口径）；
    * **精确命中** —— 与样本重合的格点直接返回样本值（float64 精确）；
    * **守卫** —— >``SIBSON_MAX_SAMPLES`` 样本或 >``SIBSON_MAX_GRID_CELLS``
      目标格点类型化拒绝；<3 非共线样本 :class:`DegenerateData`。

    返回 ``(values (n_cells,), meta)``；坐标必须已在投影米制 CRS。
    """
    from scipy.spatial import Delaunay, QhullError, cKDTree

    pts = np.asarray(xy, dtype=float)
    vals = np.asarray(values, dtype=float)
    targets = np.atleast_2d(np.asarray(grid_xy, dtype=float))
    n = len(vals)
    n_cells = len(targets)
    if n < _MIN_TIN_SAMPLES:
        raise DegenerateData(
            f"自然邻域插值至少需要 {_MIN_TIN_SAMPLES} 个非共线样本点，got {n}",
            correction_hint="补充采样点，或改用 IDW（单点也能成面）。",
        )
    if n > SIBSON_MAX_SAMPLES:
        raise ResourceScaleMismatch(
            f"自然邻域插值输入 {n:,} 个样本超过上限 {SIBSON_MAX_SAMPLES:,}"
            "（Qhull 三角剖分 + 逐格点 Voronoi 裁剪，超限先抽稀）。",
            estimated=f"{n:,} samples",
            limit=f"≤{SIBSON_MAX_SAMPLES:,} samples",
            correction_hint="先做确定性空间抽稀或降低采样密度。",
        )
    if n_cells > SIBSON_MAX_GRID_CELLS:
        raise ResourceScaleMismatch(
            f"自然邻域插值目标格点 {n_cells:,} 超过上限 {SIBSON_MAX_GRID_CELLS:,}。",
            estimated=f"{n_cells:,} target cells",
            limit=f"≤{SIBSON_MAX_GRID_CELLS:,} cells",
            correction_hint="降低目标网格分辨率或缩小范围。",
        )
    try:
        tri = Delaunay(pts)
    except QhullError as exc:
        raise DegenerateData(
            "自然邻域三角剖分失败：样本点共线或退化（凸包面积为零）——"
            "自然邻域插值需要 ≥3 个非共线平面点。",
            correction_hint="补充非共线采样点，或改用 IDW / kriging。",
        ) from exc

    pred = np.full(n_cells, np.nan)
    # 精确命中：格点与样本重合 → 直接返回样本值（float64 精确）
    tree = cKDTree(pts)
    d1, i1 = tree.query(targets, k=1)
    d1 = np.asarray(d1).reshape(n_cells)
    i1 = np.asarray(i1).reshape(n_cells)
    exact = d1 <= _SIBSON_EXACT_HIT_M
    pred[exact] = vals[i1[exact]]

    simplices = tri.simplices
    n_tri = len(simplices)
    neighbors = tri.neighbors
    # 顶点邻接（Voronoi 邻居 = Delaunay 邻接）
    adjacency: dict[int, set] = {}
    for s in simplices:
        a, b, c = int(s[0]), int(s[1]), int(s[2])
        adjacency.setdefault(a, set()).update((b, c))
        adjacency.setdefault(b, set()).update((a, c))
        adjacency.setdefault(c, set()).update((a, b))
    # 初始多边形：数据 bbox 外扩 10×（顶点单元有界化）
    span = max(float(np.ptp(pts[:, 0])), float(np.ptp(pts[:, 1])), 1.0)
    mx, my = float(pts[:, 0].mean()), float(pts[:, 1].mean())

    n_outside_hull = 0
    n_fallback = 0
    n_wall_escalations = 0
    epoch_stamp = np.full(n_tri, -1, dtype=np.int64)
    simplex_pts = pts[simplices]
    for ci in range(n_cells):
        if exact[ci]:
            continue
        x, y = float(targets[ci, 0]), float(targets[ci, 1])
        s0 = tri.find_simplex((x, y))
        if s0 < 0:
            n_outside_hull += 1
            continue  # 凸包外：NaN（不外推）
        # Watson 阶梯：只穿越外接圆包含格点的单形（从包含单形出发）
        epoch = ci
        stack = [int(s0)]
        nb_vertices: set = set()
        while stack:
            s = stack.pop()
            if epoch_stamp[s] == epoch:
                continue
            epoch_stamp[s] = epoch
            tp = simplex_pts[s]
            cc = _circumcircle(tp[0], tp[1], tp[2])
            if cc is None:
                continue
            ccx, ccy, r = cc
            eps = 1e-9 * max(r, 1.0)
            if (x - ccx) ** 2 + (y - ccy) ** 2 > (r + eps) ** 2:
                continue  # 外接圆不含格点：阶梯在此单形停止
            nb_vertices.update(int(v) for v in simplices[s])
            for nb in neighbors[s]:
                if nb >= 0 and epoch_stamp[nb] != epoch:
                    stack.append(int(nb))
        if len(nb_vertices) < 3:
            # 数值兜底（理论不达）：最近样本值——计数披露，绝不静默
            pred[ci] = vals[i1[ci]]
            n_fallback += 1
            continue
        order = sorted(nb_vertices)
        target_pt = (x, y)

        # Sibson 面积权重：V_i ∩ V_x = V_i 被格点-各自然邻域平分线依次裁剪。
        # 近共线构型下 V_x 呈长条（sliver）可延伸极远——初始外墙（10×span）
        # 截断会破坏面积精度；裁剪结果触墙则 ×100 外扩重裁（收敛后面积
        # 精确；外扩次数进 meta 披露）。
        weights: list = []
        wall_scale = 10.0 * span + 1.0
        for _attempt in range(4):
            m = wall_scale
            base = [
                (mx - m, my - m), (mx + m, my - m),
                (mx + m, my + m), (mx - m, my + m),
            ]
            weights = []
            touched = False
            wall_tol = m * 1e-9
            for i in order:
                poly = list(base)
                pi = (float(pts[i, 0]), float(pts[i, 1]))
                for j in sorted(adjacency[i]):
                    poly = _clip_polygon_halfplane(
                        poly, pi, (float(pts[j, 0]), float(pts[j, 1]))
                    )
                for j in order:
                    poly = _clip_polygon_halfplane(
                        poly, target_pt, (float(pts[j, 0]), float(pts[j, 1]))
                    )
                for vx_, vy_ in poly:
                    if (abs(vx_ - mx) >= m - wall_tol) or (
                        abs(vy_ - my) >= m - wall_tol
                    ):
                        touched = True
                        break
                weights.append(_polygon_area(poly))
            if not touched:
                break
            wall_scale *= 100.0
            n_wall_escalations += 1
        total = math.fsum(weights)
        if not (total > 0.0):
            pred[ci] = vals[i1[ci]]
            n_fallback += 1
            continue
        pred[ci] = math.fsum(w * float(vals[i]) for w, i in zip(weights, order)) / total

    meta = {
        "method": "natural_neighbor_sibson",
        "n_samples": int(n),
        "n_cells": int(n_cells),
        "n_exact_hits": int(exact.sum()),
        "n_outside_hull": int(n_outside_hull),
        "n_nearest_fallback": int(n_fallback),
        "n_wall_escalations": int(n_wall_escalations),
        "triangle_count": int(n_tri),
        "disclosures": [
            "Sibson (1981) 自然邻域坐标：权重=插入点窃取的 Voronoi 面积比例"
            "（精确多边形裁剪面积）；精确再现线性函数。",
            "凸包外 NaN——不外推（诚实空缺，与 TIN 同口径）。",
            "与样本重合的格点直接返回样本值（float64 精确）。",
        ],
    }
    return pred, meta


def natural_neighbor_surface(
    points_geojson: Any,
    value_field: str,
    resolution: int = 7,
) -> dict:
    """H3 自然邻域表面 driver（TIN driver parity：凸包外格网无记录）。"""
    import geopandas as gpd
    import h3

    from app.lib.geo_analysis.interpolation import (
        _parse_point_values,
        _pick_metric_crs,
        _target_cells_for_samples,
        _validate_resolution,
    )

    _validate_resolution(resolution)
    lonlat, values = _parse_point_values(
        points_geojson, value_field,
        purpose="自然邻域插值", log_prefix="natural_neighbor",
    )
    n = len(values)
    if n < _MIN_TIN_SAMPLES:
        raise DegenerateData(
            f"自然邻域插值至少需要 {_MIN_TIN_SAMPLES} 个去重后的非共线采样点，got {n}",
            correction_hint="补充采样点，或改用 IDW（单点也能成面）。",
        )
    if n > SIBSON_MAX_SAMPLES:
        raise ResourceScaleMismatch(
            f"自然邻域插值输入 {n:,} 个样本超过上限 {SIBSON_MAX_SAMPLES:,}。",
            estimated=f"{n:,} samples",
            limit=f"≤{SIBSON_MAX_SAMPLES:,} samples",
            correction_hint="先做确定性空间抽稀或降低采样密度。",
        )
    working_crs = _pick_metric_crs(lonlat)
    pts_gdf = gpd.GeoDataFrame(
        {"v": values},
        geometry=gpd.points_from_xy(lonlat[:, 0], lonlat[:, 1]),
        crs="EPSG:4326",
    ).to_crs(working_crs)
    pts_metric = np.column_stack(
        (pts_gdf.geometry.x.values, pts_gdf.geometry.y.values)
    )
    target_cells, bbox = _target_cells_for_samples(
        lonlat, resolution, label="自然邻域"
    )
    metadata: dict[str, Any] = {
        "algorithm": "interpolation.natural_neighbor",
        "value_field": value_field,
        "resolution": int(resolution),
        "working_crs": working_crs,
        "n_samples": int(n),
        "bbox": [bbox[0], bbox[1], bbox[2], bbox[3]],
    }
    if not target_cells:
        metadata["cell_count"] = 0
        return {"records": [], "metadata": metadata}
    cell_latlng = np.array([h3.cell_to_latlng(c) for c in target_cells])
    cell_gdf = gpd.GeoDataFrame(
        geometry=gpd.points_from_xy(cell_latlng[:, 1], cell_latlng[:, 0]),
        crs="EPSG:4326",
    ).to_crs(working_crs)
    cell_metric = np.column_stack(
        (cell_gdf.geometry.x.values, cell_gdf.geometry.y.values)
    )
    out, meta = natural_neighbor_interpolation(pts_metric, values, cell_metric)
    n_outside = int(np.sum(~np.isfinite(out)))
    metadata.update({
        "method": meta["method"],
        "triangle_count": meta["triangle_count"],
        "n_exact_hits": meta["n_exact_hits"],
        "n_outside_hull": n_outside,
        "n_nearest_fallback": meta["n_nearest_fallback"],
        "fill_fraction": round(float(np.isfinite(out).sum()) / len(target_cells), 6),
        "disclosures": meta["disclosures"],
        "cell_count": int(len(target_cells)),
    })
    records = [
        {"h3_index": cell, "value": float(v)}
        for cell, v in zip(target_cells, out)
        if np.isfinite(v)
    ]
    if records:
        vals_in = np.asarray([r["value"] for r in records])
        metadata["value_range"] = [
            round(float(vals_in.min()), 4),
            round(float(vals_in.max()), 4),
        ]
    return {"records": records, "metadata": metadata}
