"""Spatial interpolation utilities (IDW).

GIS contract (task §§14-16):

* **Distance model** — sample points and H3 cell centres are projected to a
  metric CRS (UTM via ``estimate_utm_crs``, falling back to polar
  stereographic poleward of 84°) *before* the cKDTree + inverse-distance
  math. Degree-space lon/lat distance is never used: it conflates longitude
  and latitude and varies with latitude (1° lon = 111 km at the equator but
  only 55 km at lat 60), so it silently distorts IDW weights.

* **Resource guard** — the H3 cell count for the requested bbox+resolution is
  estimated *before* polyfill; explosive requests fail fast with suggested
  lower resolutions instead of OOM-ing (mirrors ``raster_guard``).

* **Determinism** — duplicate sample coordinates are aggregated by mean
  *before* tree construction, so reordering input features cannot change the
  result (previously the KDTree tie-break decided).

* **Edge cases** — empty input, single point, missing / non-numeric / NaN /
  inf values, out-of-range ``power`` and invalid H3 resolution all raise real
  errors; polar / whole-world bboxes that yield 0 cells return an honest
  empty list with a warning instead of silent garbage.

VNext (ADR-0099 additions, all additive):

* :func:`idw_loocv` — leave-one-out cross-validation from first principles
  (same metric distance math / neighbourhood cap / exact-hit rule as the
  main path). IDW is an exact interpolator with NO theoretical variance —
  uncertainty is expressed exclusively as empirical LOOCV residual evidence
  (``validation`` metrics + absolute-residual quantiles), never a claimed
  variance.
* :func:`idw_surface` — the full driver returning ``{"records", "metadata"}``;
  :func:`idw_interpolation` stays the backward-compatible records-only view.
* Shared point-parse / H3 target-grid helpers (``_parse_point_values``,
  ``_target_cells_for_samples``) are reused by the RBF driver
  (:mod:`app.lib.geo_analysis.rbf_interpolation`) instead of duplicated.
"""
import logging
from typing import Any

import geopandas as gpd
import h3
import numpy as np
import pandas as pd
from scipy.spatial import cKDTree
from shapely.geometry import Polygon, mapping
# ADR-0052: 协作式取消检查点。cancellable() 在 chunk 边界读一次 contextvar，
# 未绑定 token 时开销为零；用户取消后长循环立即抛 OperationCancelled 退出，
# 真正释放 CPU 而不是只改 UI 状态。
from app.lib.cancellation import cancellable
from app.lib.gis.scientific_errors import InsufficientSamples, UnsupportedMethod
from app.lib.gis.uncertainty import ValidationMetrics

logger = logging.getLogger(__name__)

# Resource ceiling for an H3 interpolation surface. Each cell yields a small
# result record plus (n_cells, k) float64 matrices in the query; 1.5M cells
# keeps peak memory well under 1 GiB while still allowing large surfaces.
_MAX_H3_CELLS = 1_500_000
_H3_MIN_RES = 0
_H3_MAX_RES = 15
# Small lon/lat buffer (~1 km) around the sample extent to avoid hard edges.
_BBOX_BUF_DEG = 0.009
# Exact-hit threshold in projected metres (sub-micron): bit-identical
# projected coordinates produce dist == 0.0; this only admits true coincidence.
_EXACT_HIT_M = 1e-9


class InterpolationResourceExceededError(ValueError):
    """Raised when an H3 interpolation would exceed the safe cell/memory ceiling."""

    def __init__(self, message: str, estimated_cells: int, suggested_resolutions: list[int]):
        super().__init__(message)
        self.estimated_cells = estimated_cells
        self.suggested_resolutions = suggested_resolutions


def _world_h3_cells(resolution: int) -> int:
    """Total H3 cells on earth at ``resolution`` (exact when h3 exposes it)."""
    try:
        return int(h3.get_num_cells(resolution))
    except (AttributeError, TypeError):
        # 122 base cells × 7 children per resolution step is the documented
        # H3 hierarchy ratio; a safe fallback if the binding lacks the call.
        return int(122 * (7 ** resolution))


def _estimate_h3_cells(
    min_lon: float, min_lat: float, max_lon: float, max_lat: float, resolution: int
) -> int:
    """Cheap pre-polyfill cell-count estimate for a lon/lat bbox.

    Scales the world cell total by the bbox's spherical-area fraction. A mild
    over-estimate near the equator — only used to reject explosive requests.
    """
    bbox_area_deg2 = abs(max_lon - min_lon) * abs(max_lat - min_lat)
    earth_area_deg2 = 41253.0
    return int(_world_h3_cells(resolution) * (bbox_area_deg2 / earth_area_deg2))


def _suggest_lower_resolutions(
    min_lon: float, min_lat: float, max_lon: float, max_lat: float, resolution: int
) -> list[int]:
    out: list[int] = []
    for r in (resolution - 1, resolution - 2, resolution - 3):
        if r < _H3_MIN_RES:
            break
        if _estimate_h3_cells(min_lon, min_lat, max_lon, max_lat, r) <= _MAX_H3_CELLS:
            out.append(r)
    return out or [_H3_MIN_RES]


def _validate_resolution(resolution: Any) -> None:
    if isinstance(resolution, bool) or not isinstance(resolution, (int, np.integer)):
        raise ValueError(f"H3 resolution must be an integer 0-15, got {resolution!r}")
    if not (_H3_MIN_RES <= int(resolution) <= _H3_MAX_RES):
        raise ValueError(f"H3 resolution must be {_H3_MIN_RES}-{_H3_MAX_RES}, got {resolution}")


def _pick_metric_crs(lonlat: np.ndarray) -> str:
    """Choose a metric CRS for the sample extent (UTM, or polar stereographic)."""
    lats = lonlat[:, 1]
    if max(abs(float(lats.min())), abs(float(lats.max()))) > 84.0:
        return "EPSG:3413" if float(lats.mean()) >= 0 else "EPSG:3031"
    try:
        crs = gpd.GeoDataFrame(
            geometry=gpd.points_from_xy(lonlat[:, 0], lonlat[:, 1]), crs="EPSG:4326"
        ).estimate_utm_crs()
        if crs is not None:
            return str(crs)
    except Exception:
        pass
    lon = (float(lonlat[:, 0].mean()) + 180.0) % 360.0 - 180.0
    zone = max(1, min(60, int((lon + 180) / 6) + 1))
    hemi = 32600 if float(lats.mean()) >= 0 else 32700
    return f"EPSG:{hemi + zone}"


def _aggregate_duplicates(lonlat: np.ndarray, values: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Deterministically collapse exact-duplicate coordinates by mean value.

    Grouping by exact (lon, lat) — not KDTree order — so reordering input
    features cannot change the interpolated surface. Near-but-distinct samples
    are intentionally kept separate (they are legitimate observations).
    """
    groups: dict[tuple[float, float], list[float]] = {}
    order: list[tuple[float, float]] = []
    for i in range(len(lonlat)):
        key = (float(lonlat[i, 0]), float(lonlat[i, 1]))
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(float(values[i]))
    out_lonlat = np.asarray(order, dtype=float)
    out_vals = np.array([float(np.mean(groups[k])) for k in order], dtype=float)
    return out_lonlat, out_vals


def _parse_point_values(
    points_geojson: Any,
    value_field: str,
    *,
    purpose: str = "插值",
    log_prefix: str = "idw",
) -> tuple[np.ndarray, np.ndarray]:
    """Shared point-sample parse + validate contract (IDW / RBF drivers).

    Non-Point features and features missing the field are skipped with a
    warning; non-numeric values raise; NaN/inf are dropped with a warning;
    exact-duplicate coordinates are mean-aggregated (deterministic, so
    reordering input features cannot change the surface).
    Returns ``(lonlat (n,2), values (n,))``.
    """
    from app.lib.geo_processor.core import safe_parse, to_feature_collection

    parsed = safe_parse(points_geojson)
    if parsed is None:
        raise ValueError("无法解析输入点要素 GeoJSON")
    features = to_feature_collection(parsed).get("features", [])

    lons: list[float] = []
    lats: list[float] = []
    raw_vals: list[Any] = []
    skipped_non_point = 0
    missing_field = 0
    for f in features:
        if not isinstance(f, dict):
            continue
        geom = f.get("geometry")
        if not isinstance(geom, dict) or geom.get("type") != "Point":
            skipped_non_point += 1
            continue
        props = f.get("properties") or {}
        if value_field not in props:
            missing_field += 1
            continue
        coords = geom.get("coordinates") or []
        if len(coords) < 2:
            continue
        lons.append(float(coords[0]))
        lats.append(float(coords[1]))
        raw_vals.append(props[value_field])

    if skipped_non_point:
        logger.warning("%s: skipped %d non-Point feature(s)", log_prefix, skipped_non_point)
    if missing_field:
        logger.warning(
            "%s: skipped %d feature(s) missing field '%s'", log_prefix, missing_field, value_field
        )
    if not lons:
        raise ValueError(
            f"没有可用于{purpose}的点要素（需要 Point 几何且含字段 '{value_field}'）"
        )

    # numeric coercion + finite filter (NaN/inf are not interpolatable)
    s = pd.Series(raw_vals)
    coerced = pd.to_numeric(s, errors="coerce")
    n_non_numeric = int((s.notna() & coerced.isna()).sum())
    if n_non_numeric:
        raise ValueError(
            f"字段 '{value_field}' 包含 {n_non_numeric} 个非数值（无法{purpose}）"
        )
    vals = coerced.astype(float).to_numpy()
    finite = np.isfinite(vals)
    n_bad = int((~finite).sum())
    if n_bad:
        logger.warning("%s: dropping %d non-finite (NaN/inf) sample value(s)", log_prefix, n_bad)
        lons = [x for x, keep in zip(lons, finite) if keep]
        lats = [x for x, keep in zip(lats, finite) if keep]
        vals = vals[finite]
    if not lons:
        raise ValueError(f"字段 '{value_field}' 没有有限的数值可用于{purpose}")

    lonlat = np.column_stack([np.asarray(lons, float), np.asarray(lats, float)])
    # deterministically collapse exact-duplicate samples
    return _aggregate_duplicates(lonlat, vals)


def _target_cells_for_samples(
    lonlat: np.ndarray, resolution: int, *, label: str = "IDW"
) -> tuple[Any, tuple[float, float, float, float]]:
    """H3 target cells over the sample extent (+~1 km buffer) + resource guard.

    Antimeridian wrap: naive min/max yields 358° for points at 179°/-179° —
    a wrapped span >180° is split into two half-boxes before polyfill; a
    sample sitting exactly on ±180° collapses one half to a line (that half
    is skipped, with a narrow-strip fallback when both halves come up empty).

    Returns ``(cells, (min_lon, min_lat, max_lon, max_lat))``; an empty cell
    set is a polar / whole-world edge case the caller surfaces honestly.
    Raises :class:`InterpolationResourceExceededError` when the pre-polyfill
    estimate or the polyfill result exceeds the cell ceiling (carries
    suggested lower resolutions).
    """
    raw_min_lon = float(lonlat[:, 0].min())
    raw_max_lon = float(lonlat[:, 0].max())
    crosses_am = (raw_max_lon - raw_min_lon) > 180.0
    if crosses_am:
        # Unwrap negatives (+360) to measure the narrow band across the date line
        wrapped_width = 360.0 - (raw_max_lon - raw_min_lon)
    else:
        wrapped_width = None  # type: ignore[assignment]

    min_lon = max(raw_min_lon - _BBOX_BUF_DEG, -180.0)
    max_lon = min(raw_max_lon + _BBOX_BUF_DEG, 180.0)
    min_lat = max(float(lonlat[:, 1].min()) - _BBOX_BUF_DEG, -90.0)
    max_lat = min(float(lonlat[:, 1].max()) + _BBOX_BUF_DEG, 90.0)

    if crosses_am and wrapped_width is not None:
        estimate = int(_world_h3_cells(resolution) * (abs(float(wrapped_width)) * abs(max_lat - min_lat) / 41253.0))
    else:
        estimate = _estimate_h3_cells(min_lon, min_lat, max_lon, max_lat, resolution)
    if estimate > _MAX_H3_CELLS:
        raise InterpolationResourceExceededError(
            f"{label} 请求估计将生成约 {estimate:,} 个 H3 单元（上限 {_MAX_H3_CELLS:,}），"
            f"可能耗尽内存。请降低 H3 分辨率（建议 {resolution}→"
            f"{_suggest_lower_resolutions(min_lon, min_lat, max_lon, max_lat, resolution)}）"
            f"或缩小插值范围。",
            estimated_cells=estimate,
            suggested_resolutions=_suggest_lower_resolutions(
                min_lon, min_lat, max_lon, max_lat, resolution
            ),
        )

    if crosses_am:
        # Split across the date line into two bboxes for correct polyfill
        # Interval A: [raw_max_lon, 180], Interval B: [-180, raw_min_lon]
        bbox_a = {"type": "Polygon", "coordinates": [[[raw_max_lon, min_lat], [180.0, min_lat], [180.0, max_lat], [raw_max_lon, max_lat], [raw_max_lon, min_lat]]]}
        bbox_b = {"type": "Polygon", "coordinates": [[[-180.0, min_lat], [raw_min_lon, min_lat], [raw_min_lon, max_lat], [-180.0, max_lat], [-180.0, min_lat]]]}
        cells_a = h3.geo_to_cells(bbox_a, resolution) if raw_max_lon < 180.0 else set()
        cells_b = h3.geo_to_cells(bbox_b, resolution) if raw_min_lon > -180.0 else set()
        target_cells: Any = sorted(set(cells_a) | set(cells_b))
        # Fallback: if split produced nothing (tiny band at exact 180), fall back to narrow strip
        if not target_cells:
            narrow = {"type": "Polygon", "coordinates": [[[min_lon, min_lat], [max_lon, min_lat], [max_lon, max_lat], [min_lon, max_lat], [min_lon, min_lat]]]}
            target_cells = h3.geo_to_cells(narrow, resolution)
    else:
        bbox_polygon = {
            "type": "Polygon",
            "coordinates": [[
                [min_lon, min_lat], [max_lon, min_lat], [max_lon, max_lat],
                [min_lon, max_lat], [min_lon, min_lat],
            ]],
        }
        target_cells = h3.geo_to_cells(bbox_polygon, resolution)
    n_cells = len(target_cells)
    if n_cells > _MAX_H3_CELLS:  # estimate underestimated — last line of defence
        raise InterpolationResourceExceededError(
            f"{label} polyfill produced {n_cells:,} H3 cells (limit {_MAX_H3_CELLS:,}); "
            f"aborting to avoid memory exhaustion.",
            estimated_cells=n_cells,
            suggested_resolutions=_suggest_lower_resolutions(
                min_lon, min_lat, max_lon, max_lat, resolution
            ),
        )
    return target_cells, (min_lon, min_lat, max_lon, max_lat)


def _validate_power(power: Any) -> float:
    """IDW weight-exponent guard: ``0 < power <= 5`` (typed scientific error).

    Above 5 the weights collapse onto the single nearest sample (silent
    nearest-neighbour degeneration); at/below 0 the weights are meaningless.
    """
    try:
        p = float(power)
    except (TypeError, ValueError):
        raise UnsupportedMethod(
            f"IDW 幂次 power 必须是数值，got {power!r}",
            correction_hint="使用 0 < power ≤ 5 的幂次（默认 2）。",
        ) from None
    if not (0.0 < p <= 5.0):
        raise UnsupportedMethod(
            f"IDW 幂次 power={p} 越界（需要 0 < power ≤ 5）："
            "幂次 >5 时权重完全集中于最近样本（退化为最近邻），≤0 无意义。",
            correction_hint="使用默认 power=2，或在 (0, 5] 内选择。",
        )
    return p


# ── LOOCV validation (IDW has no theoretical variance — evidence only) ──────

def _idw_loocv_residuals(points_xy: np.ndarray, values: np.ndarray, power: float) -> np.ndarray:
    """LOOCV residuals of IDW on projected metric coordinates.

    Each sample i is re-interpolated from the OTHERS with the same weight
    exponent and the main path's distance math: metric Euclidean distances,
    the k=5 nearest-neighbour cap (main-path neighbourhood), and exact-hit
    recovery under ``_EXACT_HIT_M``. Coordinates must already be in a
    projected metric CRS (the driver projects before calling).
    """
    pts = np.asarray(points_xy, dtype=float)
    vals = np.asarray(values, dtype=float)
    n = len(vals)
    if n < 2:
        raise InsufficientSamples(
            f"IDW LOOCV 需要至少 2 个样本点，got {n}",
            correction_hint="增加采样点后重试；单点表面没有预测误差可言。",
        )
    power = _validate_power(power)
    k = min(5, n - 1)  # main-path neighbourhood cap, minus the held-out point
    tree = cKDTree(pts)
    dist, idx = tree.query(pts, k=k + 1)  # self included at distance 0
    dist = np.asarray(dist).reshape(n, k + 1)
    idx = np.asarray(idx).reshape(n, k + 1)

    # Drop the self entry per row (stable: distance-sorted, self pushed last)
    self_mask = idx == np.arange(n)[:, None]
    order = np.argsort(self_mask, axis=1, kind="stable")
    idx = np.take_along_axis(idx, order, axis=1)
    dist = np.take_along_axis(dist, order, axis=1)
    d_nb = dist[:, :k]
    i_nb = idx[:, :k]
    nb_vals = vals[i_nb]

    pred = np.empty(n, dtype=np.float64)
    hit = d_nb < _EXACT_HIT_M  # near-coincident sample → recover its value
    has_exact = hit.any(axis=1)
    if has_exact.any():
        first_hit = np.argmax(hit, axis=1)
        rows_ = np.nonzero(has_exact)[0]
        pred[rows_] = nb_vals[rows_, first_hit[rows_]]
    non_exact = ~has_exact
    if non_exact.any():
        w = 1.0 / (d_nb[non_exact] ** power)
        pred[non_exact] = (w * nb_vals[non_exact]).sum(axis=1) / w.sum(axis=1)
    return pred - vals


def idw_loocv(points_xy: np.ndarray, values: np.ndarray, power: float) -> dict:
    """Leave-one-out cross-validation of IDW (projected metric coordinates).

    Returns ``{"rmse", "mae", "bias", "method": "loocv", "sample_count"}``.
    IDW is an exact interpolator with NO theoretical variance — these
    empirical residual metrics are the only honest uncertainty statement
    (never a claimed/analytic variance).

    Raises:
        InsufficientSamples: fewer than 2 samples.
        UnsupportedMethod: power outside (0, 5].
    """
    resid = _idw_loocv_residuals(points_xy, values, power)
    n = len(resid)
    return {
        "rmse": float(np.sqrt(np.mean(resid ** 2))),
        "mae": float(np.mean(np.abs(resid))),
        "bias": float(np.mean(resid)),
        "method": "loocv",
        "sample_count": int(n),
    }


def idw_surface(
    points_geojson: dict | str | list,
    value_field: str,
    resolution: int = 8,
    power: float = 2.0,
    cross_validate: bool = True,
) -> dict:
    """Full IDW driver: ``{"records": [...], "metadata": {...}}``.

    Records are the H3 cells the legacy :func:`idw_interpolation` returns
    (bit-identical); metadata adds the driver facts plus, when
    ``cross_validate`` and n ≥ 2, the ADDITIVE evidence blocks:

    * ``validation`` — :meth:`ValidationMetrics.to_evidence` (LOOCV
      rmse/mae/bias), and
    * ``uncertainty`` — LOOCV absolute-residual quantiles (p50/p90,
      ``method="loocv_residual_quantiles"``). IDW never claims a theoretical
      variance.

    Raises:
        UnsupportedMethod: power outside (0, 5].
        ValueError: empty input, missing/non-numeric/NaN/inf value field,
            or out-of-range H3 resolution.
        InsufficientSamples: LOOCV requested with < 2 valid samples (skipped
            instead — surface is still produced).
        InterpolationResourceExceededError: surface would exceed the cell
            ceiling; carries suggested lower resolutions.
    """
    _validate_resolution(resolution)
    power = _validate_power(power)
    if not isinstance(value_field, str) or not value_field:
        raise ValueError("value_field must be a non-empty string")

    # --- parse + validate sample points (shared contract) -------------------
    lonlat, values = _parse_point_values(
        points_geojson, value_field, purpose="插值", log_prefix="idw"
    )
    n = len(values)

    # --- metric projection of sample points ---------------------------------
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
        "algorithm": "interpolation.idw",
        "value_field": value_field,
        "resolution": int(resolution),
        "power": float(power),
        "working_crs": utm_crs,
        "n_samples": int(n),
    }

    # --- LOOCV evidence (samples only — independent of the target grid) -----
    if cross_validate and n >= 2:
        resid = _idw_loocv_residuals(pts_metric, values, power)
        abs_res = np.abs(resid)
        metadata["validation"] = ValidationMetrics(
            target="idw_surface",
            method="loocv",
            rmse=float(np.sqrt(np.mean(resid ** 2))),
            mae=float(np.mean(abs_res)),
            bias=float(np.mean(resid)),
            sample_count=int(n),
        ).to_evidence()
        metadata["uncertainty"] = {
            "target": "idw_surface",
            "uncertainty_type": "scalar_uncertainty",
            "method": "loocv_residual_quantiles",
            "quantiles": {
                "p50": round(float(np.quantile(abs_res, 0.5)), 6),
                "p90": round(float(np.quantile(abs_res, 0.9)), 6),
            },
            "sample_count": int(n),
            "note": "IDW 无理论方差——不确定性以 LOOCV 绝对残差的经验分位数表达。",
        }
    elif cross_validate:
        metadata["validation_note"] = "样本 <2，无法 LOOCV；无验证证据。"

    # --- H3 target cells (lon/lat bbox) + resource guard --------------------
    target_cells, (min_lon, min_lat, max_lon, max_lat) = _target_cells_for_samples(
        lonlat, resolution, label="IDW"
    )
    metadata["bbox"] = [min_lon, min_lat, max_lon, max_lat]
    n_cells = len(target_cells)

    # h3.geo_to_cells returns [] for pathological bboxes (over a pole, or the
    # whole world). Surface that honestly instead of reporting success.
    if n_cells == 0:
        logger.warning(
            "idw: H3 polyfill returned 0 cells for bbox lon[%s,%s] lat[%s,%s] "
            "(polar / whole-world edge case); returning empty surface.",
            min_lon, max_lon, min_lat, max_lat,
        )
        metadata["cell_count"] = 0
        return {"records": [], "metadata": metadata}
    metadata["cell_count"] = int(n_cells)

    # --- metric projection of cell centres + vectorized IDW -----------------
    cell_latlng = np.array([h3.cell_to_latlng(c) for c in target_cells])  # (n,2) lat,lng
    cell_gdf = gpd.GeoDataFrame(
        geometry=gpd.points_from_xy(cell_latlng[:, 1], cell_latlng[:, 0]),
        crs="EPSG:4326",
    ).to_crs(utm_crs)
    cell_metric = np.column_stack(
        (cell_gdf.geometry.x.values, cell_gdf.geometry.y.values)
    )

    tree = cKDTree(pts_metric)
    k = min(5, n)
    dist, idx = tree.query(cell_metric, k=k)
    # cKDTree squeezes the k axis when k == 1; normalize to (n_cells, k).
    dist = np.asarray(dist).reshape(n_cells, k)
    idx = np.asarray(idx).reshape(n_cells, k)
    neighbor_vals = values[idx]  # (n_cells, k)

    out = np.empty(n_cells, dtype=np.float64)
    hit = dist < _EXACT_HIT_M  # exact coincidence → recover sample value
    has_exact = hit.any(axis=1)
    if has_exact.any():
        first_hit = np.argmax(hit, axis=1)
        rows_ = np.nonzero(has_exact)[0]
        out[rows_] = neighbor_vals[rows_, first_hit[rows_]]
    non_exact = ~has_exact
    if non_exact.any():
        w = 1.0 / (dist[non_exact] ** power)
        out[non_exact] = (w * neighbor_vals[non_exact]).sum(axis=1) / w.sum(axis=1)

    records = [{"h3_index": cell, "value": float(v)} for cell, v in zip(target_cells, out)]
    return {"records": records, "metadata": metadata}


def idw_interpolation(
    points_geojson: dict | str | list,
    value_field: str,
    resolution: int = 8,
    power: float = 2.0,
    cross_validate: bool = True,
) -> list[dict]:
    """Inverse-distance-weighted interpolation of point samples onto an H3 grid.

    Pure computation primitive returning ``[{"h3_index": str, "value": float}]``
    over the sample bounding box — the shape :func:`h3_to_geojson` consumes.
    The tool adapter composes this into a FeatureCollection response.

    Args:
        points_geojson: point FeatureCollection (dict / GeoJSON str / ref).
        value_field: numeric property field to interpolate.
        resolution: H3 resolution 0-15 (default 8).
        power: inverse-distance power, must be > 0 (default 2).
        cross_validate: kept for driver parity — the LOOCV evidence blocks
            are surfaced via :func:`idw_surface` (``metadata.validation`` /
            ``metadata.uncertainty``); this records-only view is unchanged.

    Raises:
        UnsupportedMethod: ``power`` outside (0, 5] (typed scientific error,
            still a ``ValueError`` subclass).
        ValueError: empty input, missing/non-numeric/NaN/inf value field,
            or out-of-range H3 resolution.
        InterpolationResourceExceededError: surface would exceed the cell
            ceiling; carries suggested lower resolutions.
    """
    return idw_surface(
        points_geojson,
        value_field,
        resolution=resolution,
        power=power,
        cross_validate=cross_validate,
    )["records"]


def h3_cell_ring(cell: str) -> list[tuple[float, float]]:
    """H3 cell boundary as a (lng, lat) ring, unwrapped across the
    antimeridian (#763).

    ``h3.cell_to_boundary`` returns lngs in [-180, 180]; a cell straddling
    ±180° would otherwise build a ring whose edges run through lng 0 — a
    ~360°-wide world-spanning polygon (measured ~160,000x area blow-up at
    res 9). Mirrors ``app/services/mvt.py``'s antimeridian handling: when the
    lng span exceeds 180°, negative lngs are shifted by +360 so the ring
    stays continuous. Coordinates may then exceed [-180, 180], which GeoJSON
    consumers tolerate (MapLibre renders wrapped; PROJ folds on reprojection).
    """
    boundary = h3.cell_to_boundary(cell)  # [(lat, lng), ...]
    ring = [(lng, lat) for lat, lng in boundary]
    lngs = [lng for lng, _ in ring]
    if max(lngs) - min(lngs) > 180.0:
        ring = [(lng + 360.0 if lng < 0.0 else lng, lat) for lng, lat in ring]
    return ring


def h3_to_geojson(results: list[dict], value_field: str = "value") -> dict:
    """Convert IDW/H3 ``[{h3_index, value}]`` records to a GeoJSON FeatureCollection.

    Shared canonical helper — ``aggregation.h3_binning`` reuses this rather
    than re-implementing cell-to-polygon conversion.
    """
    features = []
    for res in cancellable(results, every=512):
        cell = res["h3_index"]
        val = res["value"]
        poly_coords = h3_cell_ring(cell)  # shapely wants (lng, lat); #763 AM-safe
        features.append({
            "type": "Feature",
            "geometry": mapping(Polygon(poly_coords)),
            "properties": {
                "h3_index": cell,
                value_field: round(float(val), 4),
            },
        })

    return {
        "type": "FeatureCollection",
        "features": features,
    }
