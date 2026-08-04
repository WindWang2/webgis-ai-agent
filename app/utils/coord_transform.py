"""WGS84 <-> GCJ-02 <-> BD-09 & EPSG coordinate transformation module.

Provides unified GeoJSON coordinate and CRS transformation services.
Supports Chinese offset coordinate systems (WGS84, GCJ-02, BD-09) and general EPSG reprojections.
"""
from __future__ import annotations

import copy
import math
from typing import Any, Callable, Dict, Optional, Tuple

_A = 6378245.0
_EE = 0.00669342162296594323

_CHINESE_CRS = {"wgs84", "gcj02", "bd09"}


def _out_of_china(lng: float, lat: float) -> bool:
    return not (72.004 <= lng <= 137.8347 and 0.8293 <= lat <= 55.8271)


def _transform_lat(lng: float, lat: float) -> float:
    ret = (-100.0 + 2.0 * lng + 3.0 * lat + 0.2 * lat * lat +
           0.1 * lng * lat + 0.2 * math.sqrt(abs(lng)))
    ret += (20.0 * math.sin(6.0 * lng * math.pi) +
            20.0 * math.sin(2.0 * lng * math.pi)) * 2.0 / 3.0
    ret += (20.0 * math.sin(lat * math.pi) +
            40.0 * math.sin(lat / 3.0 * math.pi)) * 2.0 / 3.0
    ret += (160.0 * math.sin(lat / 12.0 * math.pi) +
            320 * math.sin(lat * math.pi / 30.0)) * 2.0 / 3.0
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


def gcj02_to_wgs84(lng: float, lat: float) -> Tuple[float, float]:
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
    return lng - dlng, lat - dlat


def gcj02_to_bd09(lng: float, lat: float) -> Tuple[float, float]:
    z = math.sqrt(lng * lng + lat * lat) + 0.00002 * math.sin(lat * math.pi * 3000.0 / 180.0)
    theta = math.atan2(lat, lng) + 0.000003 * math.cos(lng * math.pi * 3000.0 / 180.0)
    return z * math.cos(theta) + 0.0065, z * math.sin(theta) + 0.006


def bd09_to_gcj02(lng: float, lat: float) -> Tuple[float, float]:
    lng -= 0.0065
    lat -= 0.006
    z = math.sqrt(lng * lng + lat * lat) - 0.00002 * math.sin(lat * math.pi * 3000.0 / 180.0)
    theta = math.atan2(lat, lng) - 0.000003 * math.cos(lng * math.pi * 3000.0 / 180.0)
    return z * math.cos(theta), z * math.sin(theta)


def wgs84_to_bd09(lng: float, lat: float) -> Tuple[float, float]:
    gcj = wgs84_to_gcj02(lng, lat)
    return gcj02_to_bd09(gcj[0], gcj[1])


def bd09_to_wgs84(lng: float, lat: float) -> Tuple[float, float]:
    gcj = bd09_to_gcj02(lng, lat)
    return gcj02_to_wgs84(gcj[0], gcj[1])


def normalize_chinese_crs(crs_str: str) -> Optional[str]:
    """Normalize a Chinese-CRS string to its canonical lowercase form.

    Returns the canonical name ("wgs84" / "gcj02" / "bd09") if the input is a
    recognized Chinese CRS (case/separator-insensitive: "WGS-84", "GCJ 02" all
    resolve), or None if it is not. This is the single authority for "what
    counts as a Chinese CRS" — tool adapters use it as their policy gate rather
    than re-deriving the normalization and the supported set (Candidate #2).
    """
    cleaned = (crs_str or "").lower().replace("-", "").replace(" ", "")
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
    if isinstance(coords[0], (int, float)) and len(coords) >= 2:
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

    src_chinese = normalize_chinese_crs(from_crs)
    dst_chinese = normalize_chinese_crs(to_crs)

    transform_fn: Callable[[float, float], Tuple[float, float]]

    if src_chinese and dst_chinese:
        if src_chinese == dst_chinese:
            return copy.deepcopy(geojson)
        transform_fn = lambda x, y: _transform_chinese_point(x, y, src_chinese, dst_chinese)
    else:
        # Normalize EPSG representations (wgs84 -> EPSG:4326)
        src_epsg = "EPSG:4326" if src_chinese == "wgs84" else from_crs
        dst_epsg = "EPSG:4326" if dst_chinese == "wgs84" else to_crs

        if src_epsg.upper() == dst_epsg.upper():
            return copy.deepcopy(geojson)

        try:
            import pyproj
            transformer = pyproj.Transformer.from_crs(
                pyproj.CRS(src_epsg), pyproj.CRS(dst_epsg), always_xy=True
            )
            transform_fn = lambda x, y: transformer.transform(x, y)
        except Exception as e:
            raise ValueError(f"Unsupported CRS transformation: from '{from_crs}' to '{to_crs}': {e}") from e

    data = copy.deepcopy(geojson)
    geo_type = data.get("type")

    if geo_type == "FeatureCollection":
        new_features = []
        for feat in data.get("features", []) or []:
            if isinstance(feat, dict):
                new_feat = dict(feat)
                if "geometry" in feat and isinstance(feat["geometry"], dict):
                    new_feat["geometry"] = _transform_geometry_tree(feat["geometry"], transform_fn)
                new_features.append(new_feat)
        data["features"] = new_features
    elif geo_type == "Feature":
        if "geometry" in data and isinstance(data["geometry"], dict):
            data["geometry"] = _transform_geometry_tree(data["geometry"], transform_fn)
    else:
        data = _transform_geometry_tree(data, transform_fn)

    return data
