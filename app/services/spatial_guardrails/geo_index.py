"""bbox 空间哈希网格 + 等距圆柱近似测距（spec §4）。

纯标准库实现。10°×10° 网格把多边形按其 bbox 注册到覆盖 cell，
点查询取所在 cell 及相邻 8 cell 的候选集（≥1 cell ≈ 1110km，
远超 150km 海区置信余量），候选集内做 ray-casting 与线段测距。
"""
from __future__ import annotations

import math

EARTH_DEG_KM_LAT = 110.574
EARTH_DEG_KM_LNG = 111.320


def point_in_ring(lng: float, lat: float, ring: list[tuple[float, float]]) -> bool:
    """ray-casting 点在环内判定（环首尾可不闭合，内部闭合）。"""
    inside = False
    n = len(ring)
    j = n - 1
    for i in range(n):
        xi, yi = ring[i]
        xj, yj = ring[j]
        if (yi > lat) != (yj > lat):
            x_cross = (xj - xi) * (lat - yi) / (yj - yi) + xi
            if lng < x_cross:
                inside = not inside
        j = i
    return inside


def approx_distance_km(
    lng1: float, lat1: float, lng2: float, lat2: float
) -> float:
    """等距圆柱近似两点距离（km）。粗粒度掩膜场景足够，O(1)。"""
    dlng = (lng2 - lng1) * EARTH_DEG_KM_LNG * math.cos(math.radians((lat1 + lat2) / 2.0))
    dlat = (lat2 - lat1) * EARTH_DEG_KM_LAT
    return math.hypot(dlng, dlat)


def _point_segment_distance_km(
    px: float, py: float, ax: float, ay: float, bx: float, by: float
) -> float:
    """平面点到线段距离（输入已是局部 km 平面坐标）。"""
    vx, vy = bx - ax, by - ay
    wx, wy = px - ax, py - ay
    denom = vx * vx + vy * vy
    if denom <= 1e-12:
        return math.hypot(wx, wy)
    t = max(0.0, min(1.0, (wx * vx + wy * vy) / denom))
    return math.hypot(px - (ax + t * vx), py - (ay + t * vy))


def ring_min_distance_km(
    lng: float, lat: float, ring: list[tuple[float, float]]
) -> float:
    """点到环（各边线段）的最小距离（km），以查询点纬度做局部等距投影。"""
    cos_lat = math.cos(math.radians(lat))
    n = len(ring)
    j = n - 1
    best = float("inf")
    for i in range(n):
        ax = (ring[j][0] - lng) * EARTH_DEG_KM_LNG * cos_lat
        ay = (ring[j][1] - lat) * EARTH_DEG_KM_LAT
        bx = (ring[i][0] - lng) * EARTH_DEG_KM_LNG * cos_lat
        by = (ring[i][1] - lat) * EARTH_DEG_KM_LAT
        d = _point_segment_distance_km(0.0, 0.0, ax, ay, bx, by)
        if d < best:
            best = d
        j = i
    return best


def ring_bbox(ring: list[tuple[float, float]]) -> tuple[float, float, float, float]:
    lngs = [p[0] for p in ring]
    lats = [p[1] for p in ring]
    return (min(lngs), min(lats), max(lngs), max(lats))


def bboxes_intersect(
    a: tuple[float, float, float, float], b: tuple[float, float, float, float]
) -> bool:
    return not (a[2] < b[0] or b[2] < a[0] or a[3] < b[1] or b[3] < a[1])


class BboxSpatialHash:
    """10° 网格空间哈希：register 一次，O(1) 候选定位，进程内只读共享。"""

    def __init__(self, cell_deg: float = 10.0) -> None:
        self._cell = float(cell_deg)
        self._cells: dict[tuple[int, int], list[int]] = {}
        self._rings: dict[int, list[tuple[float, float]]] = {}
        self._next_id = 0

    def register(self, ring: list[tuple[float, float]]) -> int:
        ring_id = self._next_id
        self._next_id += 1
        self._rings[ring_id] = ring
        min_lng, min_lat, max_lng, max_lat = ring_bbox(ring)
        c = self._cell
        cx0, cx1 = int(math.floor(min_lng / c)), int(math.floor(max_lng / c))
        cy0, cy1 = int(math.floor(min_lat / c)), int(math.floor(max_lat / c))
        for cx in range(cx0, cx1 + 1):
            for cy in range(cy0, cy1 + 1):
                self._cells.setdefault((cx, cy), []).append(ring_id)
        return ring_id

    def candidates_near(self, lng: float, lat: float) -> list[list[tuple[float, float]]]:
        """所在 cell + 相邻 8 cell 的候选环集合（去重保序）。"""
        cx, cy = int(math.floor(lng / self._cell)), int(math.floor(lat / self._cell))
        seen: set[int] = set()
        out: list[list[tuple[float, float]]] = []
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for ring_id in self._cells.get((cx + dx, cy + dy), ()):
                    if ring_id not in seen:
                        seen.add(ring_id)
                        out.append(self._rings[ring_id])
        return out
