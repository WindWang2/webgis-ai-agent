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
  inf values, ``power<=0`` and invalid H3 resolution all raise real errors;
  polar / whole-world bboxes that yield 0 cells return an honest empty list
  with a warning instead of silent garbage.
"""
import logging
from typing import Any

import geopandas as gpd
import h3
import numpy as np
import pandas as pd
from scipy.spatial import cKDTree
from shapely.geometry import Polygon, mapping

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


def idw_interpolation(
    points_geojson: dict | str | list,
    value_field: str,
    resolution: int = 8,
    power: float = 2.0,
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

    Raises:
        ValueError: empty input, missing/non-numeric/NaN/inf value field,
            ``power<=0``, or out-of-range H3 resolution.
        InterpolationResourceExceededError: surface would exceed the cell
            ceiling; carries suggested lower resolutions.
    """
    _validate_resolution(resolution)
    if not (power > 0):
        raise ValueError(f"power must be > 0, got {power}")
    if not isinstance(value_field, str) or not value_field:
        raise ValueError("value_field must be a non-empty string")

    from app.lib.geo_processor.core import safe_parse, to_feature_collection

    # --- parse + validate sample points -------------------------------------
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
        logger.warning("idw: skipped %d non-Point feature(s)", skipped_non_point)
    if missing_field:
        logger.warning(
            "idw: skipped %d feature(s) missing field '%s'", missing_field, value_field
        )
    if not lons:
        raise ValueError(
            f"没有可用于插值的点要素（需要 Point 几何且含字段 '{value_field}'）"
        )

    # numeric coercion + finite filter (NaN/inf are not interpolatable)
    s = pd.Series(raw_vals)
    coerced = pd.to_numeric(s, errors="coerce")
    n_non_numeric = int((s.notna() & coerced.isna()).sum())
    if n_non_numeric:
        raise ValueError(
            f"字段 '{value_field}' 包含 {n_non_numeric} 个非数值（无法插值）"
        )
    vals = coerced.astype(float).to_numpy()
    finite = np.isfinite(vals)
    n_bad = int((~finite).sum())
    if n_bad:
        logger.warning("idw: dropping %d non-finite (NaN/inf) sample value(s)", n_bad)
        lons = [x for x, keep in zip(lons, finite) if keep]
        lats = [x for x, keep in zip(lats, finite) if keep]
        vals = vals[finite]
    if not lons:
        raise ValueError(f"字段 '{value_field}' 没有有限的数值可用于插值")

    lonlat = np.column_stack([np.asarray(lons, float), np.asarray(lats, float)])
    values = vals

    # --- deterministically collapse exact-duplicate samples -----------------
    lonlat, values = _aggregate_duplicates(lonlat, values)
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

    # --- H3 target cells (lon/lat bbox) + resource guard --------------------
    min_lon = max(float(lonlat[:, 0].min()) - _BBOX_BUF_DEG, -180.0)
    max_lon = min(float(lonlat[:, 0].max()) + _BBOX_BUF_DEG, 180.0)
    min_lat = max(float(lonlat[:, 1].min()) - _BBOX_BUF_DEG, -90.0)
    max_lat = min(float(lonlat[:, 1].max()) + _BBOX_BUF_DEG, 90.0)

    estimate = _estimate_h3_cells(min_lon, min_lat, max_lon, max_lat, resolution)
    if estimate > _MAX_H3_CELLS:
        raise InterpolationResourceExceededError(
            f"IDW 请求估计将生成约 {estimate:,} 个 H3 单元（上限 {_MAX_H3_CELLS:,}），"
            f"可能耗尽内存。请降低 H3 分辨率（建议 {resolution}→"
            f"{_suggest_lower_resolutions(min_lon, min_lat, max_lon, max_lat, resolution)}）"
            f"或缩小插值范围。",
            estimated_cells=estimate,
            suggested_resolutions=_suggest_lower_resolutions(
                min_lon, min_lat, max_lon, max_lat, resolution
            ),
        )

    bbox_polygon = {
        "type": "Polygon",
        "coordinates": [[
            [min_lon, min_lat], [max_lon, min_lat], [max_lon, max_lat],
            [min_lon, max_lat], [min_lon, min_lat],
        ]],
    }
    target_cells = h3.geo_to_cells(bbox_polygon, resolution)
    n_cells = len(target_cells)

    # h3.geo_to_cells returns [] for pathological bboxes (over a pole, or the
    # whole world). Surface that honestly instead of reporting success.
    if n_cells == 0:
        logger.warning(
            "idw: H3 polyfill returned 0 cells for bbox lon[%s,%s] lat[%s,%s] "
            "(polar / whole-world edge case); returning empty surface.",
            min_lon, max_lon, min_lat, max_lat,
        )
        return []
    if n_cells > _MAX_H3_CELLS:  # estimate underestimated — last line of defence
        raise InterpolationResourceExceededError(
            f"IDW polyfill produced {n_cells:,} H3 cells (limit {_MAX_H3_CELLS:,}); "
            f"aborting to avoid memory exhaustion.",
            estimated_cells=n_cells,
            suggested_resolutions=_suggest_lower_resolutions(
                min_lon, min_lat, max_lon, max_lat, resolution
            ),
        )

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

    return [{"h3_index": cell, "value": float(v)} for cell, v in zip(target_cells, out)]


def h3_to_geojson(results: list[dict], value_field: str = "value") -> dict:
    """Convert IDW/H3 ``[{h3_index, value}]`` records to a GeoJSON FeatureCollection.

    Shared canonical helper — ``aggregation.h3_binning`` reuses this rather
    than re-implementing cell-to-polygon conversion.
    """
    features = []
    for res in results:
        cell = res["h3_index"]
        val = res["value"]
        boundary = h3.cell_to_boundary(cell)  # [(lat, lng), ...]
        poly_coords = [(lng, lat) for lat, lng in boundary]  # shapely wants (lng, lat)
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
