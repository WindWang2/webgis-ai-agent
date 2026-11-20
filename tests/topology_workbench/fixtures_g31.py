"""G31 拓扑工作台 —— fixtures（人工可数 oracle）。

地理锚点：北京附近（~116.4E, 39.9N）。理论值按 UTM 32650 平面近似：
1° 经 ≈ 85.4 km、1° 纬 ≈ 111.2 km（该纬度）。所有期望面积/长度都允许
±1% 容差（投影弦化 + 收敛角效应），阈值附近用「略小/等于/略大」三态。
"""
from __future__ import annotations

import math
from typing import Any, Dict, List

LON0, LAT0 = 116.40, 39.90


def dlon(m: float) -> float:
    """米 → 经度偏移（该纬度保守换算，仅 fixture 构造用）。"""
    return m / (111_320.0 * math.cos(math.radians(LAT0)))


def dlat(m: float) -> float:
    """米 → 纬度偏移。"""
    return m / 111_320.0


def rect(lon: float, lat: float, w_m: float, h_m: float, holes: List[List[List[float]]] | None = None) -> Dict[str, Any]:
    """米制矩形 → WGS84 Polygon 坐标（逆时针外环）。"""
    w, e = lon, lon + dlon(w_m)
    s, n = lat, lat + dlat(h_m)
    ring = [[w, s], [e, s], [e, n], [w, n], [w, s]]
    return {"type": "Polygon", "coordinates": [ring] + (holes or [])}


def feat(fid: str, geometry: Dict[str, Any], extra: Dict[str, Any] | None = None) -> Dict[str, Any]:
    props = {"id": fid}
    if extra:
        props.update(extra)
    return {"type": "Feature", "properties": props, "geometry": geometry}


def fc(*features: Dict[str, Any]) -> Dict[str, Any]:
    return {"type": "FeatureCollection", "features": list(features)}


# ─── T01–T03：AOI 覆盖（1000m × 1000m 管理区） ─────────────────────────

AOI_1KM = rect(LON0, LAT0, 1000, 1000)
#: 两个地块完整覆盖（垂直分割，共享边 x=+500m）
PARCEL_W = rect(LON0, LAT0, 500, 1000)
PARCEL_E_RECT = rect(LON0 + dlon(500), LAT0, 500, 1000)
#: 东地块中央挖 200m × 200m 洞（理论面积 40_000 m²）
HOLE_RING = [
    [LON0 + dlon(600), LAT0 + dlat(400)],
    [LON0 + dlon(800), LAT0 + dlat(400)],
    [LON0 + dlon(800), LAT0 + dlat(600)],
    [LON0 + dlon(600), LAT0 + dlat(600)],
    [LON0 + dlon(600), LAT0 + dlat(400)],
]
PARCEL_E_HOLE = {
    "type": "Polygon",
    "coordinates": [PARCEL_E_RECT["coordinates"][0], HOLE_RING],
}
#: 湖面排除区：完全覆盖洞并向外扩 50m（300m × 300m，理论 90_000 m²）
LAKE = rect(LON0 + dlon(550), LAT0 + dlat(350), 300, 300)

#: T04：小面完整嵌套在西地块中央（100m × 100m ≈ 10_000 m²）
NESTED_SMALL = rect(LON0 + dlon(100), LAT0 + dlat(100), 100, 100)

#: T06：建筑部分跨出东地块（150m 宽，西起 940m → 东至 1090m，
#: 越界 90m（地块东界 1000m）× 100m ≈ 9_000 m²）
BUILDING_CROSS = rect(LON0 + dlon(940), LAT0 + dlat(700), 150, 100)

#: T10：两根线，端点语义：
#:   line-near —— 两端各有设施（末端 0.5m ≤ 容差 1m）→ 全部通过；
#:   line-far  —— 起端有设施，末端距最近设施 ~2.8m > 1m → 末端违规。
LINE_NEAR_FACILITY = {
    "type": "LineString",
    "coordinates": [
        [LON0 + dlon(100), LAT0 + dlat(100)],
        [LON0 + dlon(300), LAT0 + dlat(300)],
    ],
}
#: 起端设施（正好在端点上）
FACILITY_AT_START = {
    "type": "Point",
    "coordinates": [LON0 + dlon(100), LAT0 + dlat(100)],
}
#: line-near 末端设施（~0.5m，经度方向）
FACILITY_AT_ENDPOINT = {
    "type": "Point",
    "coordinates": [LON0 + dlon(300) + dlon(0.5), LAT0 + dlat(300)],
}
#: line-far 末端最近设施（~2m 经 + ~2m 纬 → ~2.8m）
FACILITY_FAR = {
    "type": "Point",
    "coordinates": [LON0 + dlon(400) + dlon(2.0), LAT0 + dlat(350) + dlat(2.0)],
}
LINE_FAR_ENDPOINT = {
    "type": "LineString",
    "coordinates": [
        [LON0 + dlon(100), LAT0 + dlat(100)],
        [LON0 + dlon(400), LAT0 + dlat(350)],
    ],
}

#: 合法出口区（覆盖 LINE_FAR_ENDPOINT 的远端）
EXIT_ZONE = rect(LON0 + dlon(380), LAT0 + dlat(330), 100, 100)

#: T09：病态几何
SELF_INTERSECTING_BOWTIE = {
    "type": "Polygon",
    "coordinates": [[[0, 0], [2, 2], [0, 2], [2, 0], [0, 0]]],
}
EMPTY_GEOM = {"type": "Polygon", "coordinates": []}
_MULTI_BASE = rect(10.0, 10.0, 1000, 1000)
_HOLE_X0 = 10.0 + dlon(200)
_HOLE_Y0 = 10.0 + dlat(200)
_HOLE_RING = [
    [_HOLE_X0, _HOLE_Y0],
    [_HOLE_X0 + dlon(100), _HOLE_Y0],
    [_HOLE_X0 + dlon(100), _HOLE_Y0 + dlat(100)],
    [_HOLE_X0, _HOLE_Y0 + dlat(100)],
    [_HOLE_X0, _HOLE_Y0],
]
MULTIPOLYGON_WITH_HOLE = {
    "type": "MultiPolygon",
    "coordinates": [
        [rect(12.0, 12.0, 1000, 1000)["coordinates"][0]],
        [_MULTI_BASE["coordinates"][0], _HOLE_RING],
    ],
}


def parcels_cover() -> Dict[str, Any]:
    return fc(feat("parcel-w", PARCEL_W), feat("parcel-e", PARCEL_E_RECT))


def parcels_with_hole() -> Dict[str, Any]:
    return fc(feat("parcel-w", PARCEL_W), feat("parcel-e", PARCEL_E_HOLE))


def aoi_source() -> Dict[str, Any]:
    return fc(feat("admin-zone", AOI_1KM))


def lake_source() -> Dict[str, Any]:
    return fc(feat("lake", LAKE))


__all__ = [
    "LON0", "LAT0", "dlon", "dlat", "rect", "feat", "fc",
    "AOI_1KM", "PARCEL_W", "PARCEL_E_RECT", "PARCEL_E_HOLE", "LAKE",
    "NESTED_SMALL", "BUILDING_CROSS",
    "LINE_NEAR_FACILITY", "FACILITY_AT_ENDPOINT", "FACILITY_FAR",
    "LINE_FAR_ENDPOINT", "EXIT_ZONE",
    "SELF_INTERSECTING_BOWTIE", "EMPTY_GEOM", "MULTIPOLYGON_WITH_HOLE",
    "parcels_cover", "parcels_with_hole", "aoi_source", "lake_source",
]
