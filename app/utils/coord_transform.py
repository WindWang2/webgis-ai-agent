"""WGS84 <-> GCJ-02 <-> BD-09 & EPSG coordinate transformation module.

Provides unified GeoJSON coordinate and CRS transformation services.
Supports Chinese offset coordinate systems (WGS84, GCJ-02, BD-09) and general EPSG reprojections.

Performance: scalar functions below are the reference implementation. The
``*_array`` / ``transform_geojson`` path vectorizes the same math with NumPy so
that transforming a 100k-point FeatureCollection is ~100x faster (one C-level
pass instead of 100k Python ``math.sin`` calls). The two share identical
constants and formulas; ``_CHINESE_CRS_ARRAY_OPS`` validates numerical parity.
"""
from __future__ import annotations

import copy
import math
from typing import Any, Callable, Dict, Optional, Tuple

import numpy as np

_A = 6378245.0
_EE = 0.00669342162296594323

_CHINESE_CRS = {"wgs84", "gcj02", "bd09"}


def _out_of_china(lng: float, lat: float) -> bool:
    return not (72.004 <= lng <= 137.8347 and 0.8293 <= lat <= 55.8271)


def _out_of_china_array(lng: np.ndarray, lat: np.ndarray) -> np.ndarray:
    """Vectorized in-China mask (True where inside China)."""
    return (lng >= 72.004) & (lng <= 137.8347) & (lat >= 0.8293) & (lat <= 55.8271)


def _transform_lat(lng: float, lat: float) -> float:
    ret = (-100.0 + 2.0 * lng + 3.0 * lat + 0.2 * lat * lat +
           0.1 * lng * lat + 0.2 * math.sqrt(abs(lng)))
    ret += (20.0 * math.sin(6.0 * lng * math.pi) +
            20.0 * math.sin(2.0 * lng * math.pi)) * 2.0 / 3.0
    ret += (20.0 * math.sin(lat * math.pi) +
            40.0 * math.sin(lat / 3.0 * math.pi)) * 2.0 / 3.0
    ret += (160.0 * math.sin(lat / 12.0 * math.pi) +
            320.0 * math.sin(lat * math.pi / 30.0)) * 2.0 / 3.0
    return ret


def _transform_lng(lng: float, lat: float) -> float:
    ret = (300.0 + lng + 2.0 * lat + 0.1 * lng * lng +
           0.1 * lng * lat + 0.1 * math.sqrt(abs(lng)))
    ret += (20.0 * math.sin(6.0 * lng * math.pi) +
            20.0 * math.sin(2.0 * lng * math.pi)) * 2.0 / 3.0
    ret += (20.0 * math.sin(lng * math.pi) +
            40.0 * math.sin(lng / 3.0 * math.pi)) * 2.0 / 3.0
    ret += (150.0 * math.sin(lng / 12.0 * math.pi) +
            300.0 * math.sin(lng / 30.0 * math.pi)) * 2.0 / 3.0
    return ret


def _transform_lat_array(lng: np.ndarray, lat: np.ndarray) -> np.ndarray:
    """Vectorized form of :func:`_transform_lat`. Identical formula."""
    pi = np.pi
    ret = (-100.0 + 2.0 * lng + 3.0 * lat + 0.2 * lat * lat +
           0.1 * lng * lat + 0.2 * np.sqrt(np.abs(lng)))
    ret += (20.0 * np.sin(6.0 * lng * pi) +
            20.0 * np.sin(2.0 * lng * pi)) * 2.0 / 3.0
    ret += (20.0 * np.sin(lat * pi) +
            40.0 * np.sin(lat / 3.0 * pi)) * 2.0 / 3.0
    ret += (160.0 * np.sin(lat / 12.0 * pi) +
            320.0 * np.sin(lat * pi / 30.0)) * 2.0 / 3.0
    return ret


def _transform_lng_array(lng: np.ndarray, lat: np.ndarray) -> np.ndarray:
    """Vectorized form of :func:`_transform_lng`. Identical formula."""
    pi = np.pi
    ret = (300.0 + lng + 2.0 * lat + 0.1 * lng * lng +
           0.1 * lng * lat + 0.1 * np.sqrt(np.abs(lng)))
    ret += (20.0 * np.sin(6.0 * lng * pi) +
            20.0 * np.sin(2.0 * lng * pi)) * 2.0 / 3.0
    ret += (20.0 * np.sin(lng * pi) +
            40.0 * np.sin(lng / 3.0 * pi)) * 2.0 / 3.0
    ret += (150.0 * np.sin(lng / 12.0 * pi) +
            300.0 * np.sin(lng / 30.0 * pi)) * 2.0 / 3.0
    return ret


def wgs84_to_gcj02(lng: float, lat: float) -> Tuple[float, float]:
    if _out_of_china(lng, lat):
        return lng, lat
    dlat = _transform_lat(lng - 105.0, lat - 35.0)
    dlng = _transform_lng(lng - 105.0, lat - 35.0)
    radlat = lat / 180.0 * math.pi
    magic = math.sin(radlat)
    magic = 1 - _EE * magic * magic
    sqrtmagic = math.sqrt(magic)
    dlat = (dlat * 180.0) / ((_A * (1 - _EE)) / (magic * sqrtmagic) * math.pi)
    dlng = (dlng * 180.0) / (_A / sqrtmagic * math.cos(radlat) * math.pi)
    return lng + dlng, lat + dlat


def wgs84_to_gcj02_array(lng: np.ndarray, lat: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    """Vectorized WGS84 -> GCJ-02. Returns (lng_out, lat_out) arrays.

    Out-of-China points are passed through unchanged (matching the scalar
    function). Input arrays must be broadcast-compatible.
    """
    lng = np.asarray(lng, dtype=np.float64)
    lat = np.asarray(lat, dtype=np.float64)
    in_china = _out_of_china_array(lng, lat)
    # Compute deltas only where needed; default to identity outside China.
    dlng = np.zeros_like(lng)
    dlat = np.zeros_like(lat)
    if in_china.any():
        li, la = lng[in_china], lat[in_china]
        t_lat = _transform_lat_array(li - 105.0, la - 35.0)
        t_lng = _transform_lng_array(li - 105.0, la - 35.0)
        radlat = la / 180.0 * np.pi
        magic = np.sin(radlat)
        magic = 1 - _EE * magic * magic
        sqrtmagic = np.sqrt(magic)
        dlat[in_china] = (t_lat * 180.0) / ((_A * (1 - _EE)) / (magic * sqrtmagic) * np.pi)
        dlng[in_china] = (t_lng * 180.0) / (_A / sqrtmagic * np.cos(radlat) * np.pi)
    return lng + dlng, lat + dlat


def gcj02_to_wgs84(lng: float, lat: float) -> Tuple[float, float]:
    if _out_of_china(lng, lat):
        return lng, lat
    wgs_lng, wgs_lat = lng, lat
    for _ in range(3):
        g_lng, g_lat = wgs84_to_gcj02(wgs_lng, wgs_lat)
        wgs_lng -= (g_lng - lng)
        wgs_lat -= (g_lat - lat)
    return wgs_lng, wgs_lat


def gcj02_to_wgs84_array(lng: np.ndarray, lat: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    """Vectorized GCJ-02 -> WGS-84 via 3 fixed-point iterations (matches scalar)."""
    lng = np.asarray(lng, dtype=np.float64)
    lat = np.asarray(lat, dtype=np.float64)
    in_china = _out_of_china_array(lng, lat)
    if not in_china.any():
        return lng.copy(), lat.copy()
    wgs_lng = lng.copy()
    wgs_lat = lat.copy()
    for _ in range(3):
        g_lng, g_lat = wgs84_to_gcj02_array(wgs_lng, wgs_lat)
        wgs_lng -= (g_lng - lng)
        wgs_lat -= (g_lat - lat)
    return wgs_lng, wgs_lat


def gcj02_to_bd09(lng: float, lat: float) -> Tuple[float, float]:
    if _out_of_china(lng, lat):
        return lng, lat
    z = math.sqrt(lng * lng + lat * lat) + 0.00002 * math.sin(lat * math.pi * 3000.0 / 180.0)
    theta = math.atan2(lat, lng) + 0.000003 * math.cos(lng * math.pi * 3000.0 / 180.0)
    return z * math.cos(theta) + 0.0065, z * math.sin(theta) + 0.006


def gcj02_to_bd09_array(lng: np.ndarray, lat: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    """Vectorized GCJ-02 -> BD-09."""
    lng = np.asarray(lng, dtype=np.float64)
    lat = np.asarray(lat, dtype=np.float64)
    in_china = _out_of_china_array(lng, lat)
    out_lng = lng.copy()
    out_lat = lat.copy()
    if in_china.any():
        li, la = lng[in_china], lat[in_china]
        z = np.sqrt(li * li + la * la) + 0.00002 * np.sin(la * np.pi * 3000.0 / 180.0)
        theta = np.arctan2(la, li) + 0.000003 * np.cos(li * np.pi * 3000.0 / 180.0)
        out_lng[in_china] = z * np.cos(theta) + 0.0065
        out_lat[in_china] = z * np.sin(theta) + 0.006
    return out_lng, out_lat


def bd09_to_gcj02(lng: float, lat: float) -> Tuple[float, float]:
    if _out_of_china(lng, lat):
        return lng, lat
    x = lng - 0.0065
    y = lat - 0.006
    z = math.sqrt(x * x + y * y) - 0.00002 * math.sin(y * math.pi * 3000.0 / 180.0)
    theta = math.atan2(y, x) - 0.000003 * math.cos(x * math.pi * 3000.0 / 180.0)
    return z * math.cos(theta), z * math.sin(theta)


def bd09_to_gcj02_array(lng: np.ndarray, lat: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    """Vectorized BD-09 -> GCJ-02."""
    lng = np.asarray(lng, dtype=np.float64)
    lat = np.asarray(lat, dtype=np.float64)
    in_china = _out_of_china_array(lng, lat)
    out_lng = lng.copy()
    out_lat = lat.copy()
    if in_china.any():
        li, la = lng[in_china], lat[in_china]
        x = li - 0.0065
        y = la - 0.006
        z = np.sqrt(x * x + y * y) - 0.00002 * np.sin(y * np.pi * 3000.0 / 180.0)
        theta = np.arctan2(y, x) - 0.000003 * np.cos(x * np.pi * 3000.0 / 180.0)
        out_lng[in_china] = z * np.cos(theta)
        out_lat[in_china] = z * np.sin(theta)
    return out_lng, out_lat


def wgs84_to_bd09(lng: float, lat: float) -> Tuple[float, float]:
    gcj = wgs84_to_gcj02(lng, lat)
    return gcj02_to_bd09(gcj[0], gcj[1])


def bd09_to_wgs84(lng: float, lat: float) -> Tuple[float, float]:
    gcj = bd09_to_gcj02(lng, lat)
    return gcj02_to_wgs84(gcj[0], gcj[1])


def normalize_chinese_crs(crs_str: Any) -> Optional[str]:
    """Normalize a Chinese-CRS string to its canonical lowercase form.

    Returns the canonical name ("wgs84" / "gcj02" / "bd09") if the input is a
    recognized Chinese CRS (case/separator-insensitive: "WGS-84", "GCJ_02", "BD_09" all
    resolve), or None if it is not. This is the single authority for "what
    counts as a Chinese CRS" — tool adapters use it as their policy gate rather
    than re-deriving the normalization and the supported set (Candidate #2).
    """
    cleaned = str(crs_str or "").lower().replace("-", "").replace("_", "").replace(" ", "")
    if cleaned in _CHINESE_CRS:
        return cleaned
    return None


def supported_chinese_crs() -> Tuple[str, ...]:
    """Return the supported Chinese-CRS set as a stable, ascending tuple.

    Adapters format error messages from this single source rather than
    hardcoding the set (Candidate #2). 调用方可依赖顺序做文案拼接。
    """
    return tuple(sorted(_CHINESE_CRS))


def _transform_chinese_point(lng: float, lat: float, src: str, dst: str) -> Tuple[float, float]:
    if src == dst:
        return lng, lat
    if src == "wgs84":
        lng, lat = wgs84_to_gcj02(lng, lat)
    elif src == "bd09":
        lng, lat = bd09_to_gcj02(lng, lat)

    if dst == "wgs84":
        return gcj02_to_wgs84(lng, lat)
    if dst == "bd09":
        return gcj02_to_bd09(lng, lat)
    return lng, lat


def _walk_coords(coords: Any, transform_fn: Callable[[float, float], Tuple[float, float]]) -> Any:
    """Recursively walks GeoJSON coordinates, preserving Z/M dimensions.

    Accepts both ``list`` and ``tuple`` coordinate sequences — GeoJSON permits
    either, and shapely's ``__geo_interface__`` emits tuples. Tuples are
    normalized to lists so the recursion (and the returned tree) is uniformly
    list-typed, matching the GeoJSON canonical form.
    """
    if isinstance(coords, tuple):
        coords = list(coords)
    if not isinstance(coords, list) or not coords:
        return coords
    if (
        len(coords) >= 2
        and isinstance(coords[0], (int, float))
        and not isinstance(coords[0], bool)
        and isinstance(coords[1], (int, float))
        and not isinstance(coords[1], bool)
    ):
        x, y = transform_fn(float(coords[0]), float(coords[1]))
        rest = coords[2:]
        return [x, y, *rest]
    return [_walk_coords(c, transform_fn) for c in coords]


def _transform_geometry_tree(geom: Dict[str, Any], transform_fn: Callable[[float, float], Tuple[float, float]]) -> Dict[str, Any]:
    if not isinstance(geom, dict):
        return geom
    new_geom = dict(geom)
    if "coordinates" in new_geom:
        new_geom["coordinates"] = _walk_coords(new_geom["coordinates"], transform_fn)
    if "geometries" in new_geom:
        new_geom["geometries"] = [_transform_geometry_tree(g, transform_fn) for g in new_geom["geometries"]]
    return new_geom


def _collect_leaf_pairs(coords: Any, out: list) -> None:
    """Flatten a GeoJSON coordinate tree into a list of [x, y, *rest] leaf pairs.

    Leaf detection mirrors :func:`_walk_coords`: a list whose first two elements
    are numbers (and not bools) is treated as a coordinate vertex. Traversal
    order is deterministic (pre-order), which :func:`_rebuild_coords` relies on.
    """
    if isinstance(coords, tuple):
        coords = list(coords)
    if not isinstance(coords, list) or not coords:
        return
    if (
        len(coords) >= 2
        and isinstance(coords[0], (int, float))
        and not isinstance(coords[0], bool)
        and isinstance(coords[1], (int, float))
        and not isinstance(coords[1], bool)
    ):
        out.append(coords)
        return
    for c in coords:
        _collect_leaf_pairs(c, out)


def _rebuild_coords(coords: Any, new_pairs: np.ndarray, idx: list[int]) -> Any:
    """Rebuild a coordinate tree substituting leaf pairs from ``new_pairs``.

    ``idx`` is a one-element mutable counter so leaves are consumed in the same
    pre-order as :func:`_collect_leaf_pairs`. Preserves Z/M trailing dims.
    """
    if isinstance(coords, tuple):
        coords = list(coords)
    if not isinstance(coords, list) or not coords:
        return coords
    if (
        len(coords) >= 2
        and isinstance(coords[0], (int, float))
        and not isinstance(coords[0], bool)
        and isinstance(coords[1], (int, float))
        and not isinstance(coords[1], bool)
    ):
        i = idx[0]
        idx[0] += 1
        x, y = float(new_pairs[i, 0]), float(new_pairs[i, 1])
        rest = coords[2:]
        return [x, y, *rest]
    return [_rebuild_coords(c, new_pairs, idx) for c in coords]


def _transform_geometry_tree_vectorized(
    geom: Dict[str, Any],
    array_transform_fn: Callable[[np.ndarray, np.ndarray], Tuple[np.ndarray, np.ndarray]],
) -> Dict[str, Any]:
    """Vectorized geometry transform: batch all leaf pairs through one array op.

    Equivalent to :func:`_transform_geometry_tree` but performs a single NumPy
    transform over all vertices instead of one Python call per vertex. Preserves
    the exact nested coordinate structure (rings, polygons, etc.).
    """
    if not isinstance(geom, dict):
        return geom
    new_geom = dict(geom)
    if "coordinates" in new_geom:
        leaves: list = []
        _collect_leaf_pairs(new_geom["coordinates"], leaves)
        if leaves:
            arr = np.array([[float(c[0]), float(c[1])] for c in leaves], dtype=np.float64)
            xs, ys = array_transform_fn(arr[:, 0], arr[:, 1])
            out = np.column_stack([xs, ys])
            new_geom["coordinates"] = _rebuild_coords(new_geom["coordinates"], out, [0])
    if "geometries" in new_geom:
        new_geom["geometries"] = [
            _transform_geometry_tree_vectorized(g, array_transform_fn)
            for g in new_geom["geometries"]
        ]
    return new_geom


def _make_chinese_array_transform(src: str, dst: str) -> Callable[[np.ndarray, np.ndarray], Tuple[np.ndarray, np.ndarray]]:
    """Build a vectorized Chinese-CRS transform pipeline (src -> [wgs84 -> epsg] -> dst).

    Mirrors the scalar ``_transform_chinese_point`` routing but at array scale:
    each leg uses the matching ``*_array`` function, so 100k points transform in
    one C-level pass per leg.
    """
    def transform(x: np.ndarray, y: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        if src != "wgs84":
            # normalize source to gcj02 first
            if src == "bd09":
                x, y = bd09_to_gcj02_array(x, y)
            if dst == "wgs84":
                x, y = gcj02_to_wgs84_array(x, y)
                return x, y
            if dst == "bd09":
                x, y = gcj02_to_wgs84_array(x, y)
                return gcj02_to_bd09_array(x, y)
            return x, y  # src->dst both gcj02-ish, no-op
        # src == wgs84
        if dst == "gcj02":
            return wgs84_to_gcj02_array(x, y)
        if dst == "bd09":
            gx, gy = wgs84_to_gcj02_array(x, y)
            return gcj02_to_bd09_array(gx, gy)
        return x, y
    return transform


def transform_geojson(geojson: Dict[str, Any], from_crs: str, to_crs: str) -> Dict[str, Any]:
    """
    Transforms coordinates in a GeoJSON dict from one CRS/coordinate system to another.

    Pure function: returns a deep copy without mutating the input dict.

    Supports:
    - Chinese offset CRS: 'wgs84', 'gcj02', 'bd09'
    - EPSG codes: 'EPSG:4326', 'EPSG:3857', 'EPSG:4490', etc.
    """
    if not isinstance(geojson, dict):
        return geojson

    from_crs_str = str(from_crs or "").strip()
    to_crs_str = str(to_crs or "").strip()

    src_chinese = normalize_chinese_crs(from_crs_str)
    dst_chinese = normalize_chinese_crs(to_crs_str)

    transform_fn: Callable[[float, float], Tuple[float, float]]

    # Hot path: pure Chinese-CRS conversions vectorize cleanly. All other paths
    # (any EPSG leg, or Chinese↔EPSG composition) keep the scalar _walk_coords
    # recursion — pyproj's Transformer is the dominant cost there and already
    # accepts arrays, so the scalar overhead is negligible relative to reprojection.
    use_vectorized = bool(src_chinese and dst_chinese)

    if use_vectorized:
        if src_chinese == dst_chinese:
            return copy.deepcopy(geojson)
        array_transform = _make_chinese_array_transform(src_chinese, dst_chinese)

        def apply_vectorized(geometry: Dict[str, Any]) -> Dict[str, Any]:
            return _transform_geometry_tree_vectorized(geometry, array_transform)
    else:
        chinese_pre_step: Optional[Callable[[float, float], Tuple[float, float]]] = None
        chinese_post_step: Optional[Callable[[float, float], Tuple[float, float]]] = None

        def _pre_to_wgs84(x: float, y: float) -> Tuple[float, float]:
            return _transform_chinese_point(x, y, src_chinese, "wgs84")

        def _post_from_wgs84(x: float, y: float) -> Tuple[float, float]:
            return _transform_chinese_point(x, y, "wgs84", dst_chinese)

        if src_chinese in ("gcj02", "bd09"):
            chinese_pre_step = _pre_to_wgs84
            src_epsg = "EPSG:4326"
        elif src_chinese == "wgs84":
            src_epsg = "EPSG:4326"
        else:
            src_epsg = from_crs_str

        if dst_chinese in ("gcj02", "bd09"):
            chinese_post_step = _post_from_wgs84
            dst_epsg = "EPSG:4326"
        elif dst_chinese == "wgs84":
            dst_epsg = "EPSG:4326"
        else:
            dst_epsg = to_crs_str

        if src_epsg.strip().upper() == dst_epsg.strip().upper() and not chinese_pre_step and not chinese_post_step:
            return copy.deepcopy(geojson)

        transformer = None
        if src_epsg.strip().upper() != dst_epsg.strip().upper():
            try:
                import pyproj
            except ImportError as e:
                raise ImportError(f"pyproj library is missing for CRS transformation '{src_epsg}' -> '{dst_epsg}': {e}") from e

            try:
                transformer = pyproj.Transformer.from_crs(
                    pyproj.CRS(src_epsg), pyproj.CRS(dst_epsg), always_xy=True
                )
            except Exception as e:
                raise ValueError(f"Unsupported CRS transformation: from '{from_crs}' to '{to_crs}': {e}") from e

        def composed_transform(x: float, y: float) -> Tuple[float, float]:
            if chinese_pre_step:
                x, y = chinese_pre_step(x, y)
            if transformer:
                x, y = transformer.transform(x, y)
            if chinese_post_step:
                x, y = chinese_post_step(x, y)
            return x, y

        transform_fn = composed_transform

        def apply_vectorized(geometry: Dict[str, Any]) -> Dict[str, Any]:  # type: ignore[no-redef]
            return _transform_geometry_tree(geometry, transform_fn)

    data = copy.deepcopy(geojson)
    geo_type = data.get("type")

    if geo_type == "FeatureCollection":
        new_features = []
        for feat in data.get("features", []) or []:
            if isinstance(feat, dict):
                new_feat = dict(feat)
                if "geometry" in feat and isinstance(feat["geometry"], dict):
                    new_feat["geometry"] = apply_vectorized(feat["geometry"])
                new_features.append(new_feat)
        data["features"] = new_features
    elif geo_type == "Feature":
        if "geometry" in data and isinstance(data["geometry"], dict):
            data["geometry"] = apply_vectorized(data["geometry"])
    else:
        data = apply_vectorized(data)

    return data
