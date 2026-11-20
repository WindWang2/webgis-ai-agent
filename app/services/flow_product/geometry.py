"""Deterministic OD edge geometry (pure domain, no I/O).

Rules (G21):
- ``straight`` keeps the legacy two-vertex segment; when both directions of
  a pair render, each gets a two-segment chord with a deterministic midpoint
  offset so the directions do not overlap.
- ``curve`` is a deterministic quadratic Bezier sampled in Web Mercator and
  re-projected; the control-point side flips with direction so reversed
  pairs separate symmetrically.
- Antimeridian crossings take the shortest longitude difference and split
  into a MultiLineString — never a line across the whole globe.
- Self-loops (and zero-length distinct-node edges) get a small dedicated
  loop.
- At most ``MAX_EDGE_SEGMENTS`` segments per edge.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple

MAX_EDGE_SEGMENTS = 24
CURVE_VERSION = "bezier-quadratic-v1"

_LOOP_SEGMENTS = 8
_LOOP_RADIUS_MERC = 1500.0  # ≈1.5 km at the equator in Web Mercator units
_R = 6378137.0

Point = Tuple[float, float]


def _merc_x(lng: float) -> float:
    return math.radians(lng) * _R


def _merc_y(lat: float) -> float:
    lat = max(-85.06, min(85.06, lat))
    return math.log(math.tan(math.pi / 4.0 + math.radians(lat) / 2.0)) * _R


def _unmerc_x(x: float) -> float:
    return math.degrees(x / _R)


def _unmerc_y(y: float) -> float:
    return math.degrees(2.0 * math.atan(math.exp(y / _R)) - math.pi / 2.0)


def _wrap_lng(lng: float) -> float:
    return ((lng + 180.0) % 360.0) - 180.0


def _shortest_dlng(a: float, b: float) -> float:
    return (b - a + 540.0) % 360.0 - 180.0


def _loop_points(lng: float, lat: float) -> List[Point]:
    x, y = _merc_x(lng), _merc_y(lat)
    pts: List[Point] = []
    for i in range(_LOOP_SEGMENTS + 1):
        ang = 2.0 * math.pi * i / _LOOP_SEGMENTS
        pts.append((
            _unmerc_x(x + _LOOP_RADIUS_MERC * math.cos(ang)),
            _unmerc_y(y + _LOOP_RADIUS_MERC * math.sin(ang)),
        ))
    return pts


def _finalize(points: List[Point], kind: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Wrap longitudes to [-180, 180] and split at the antimeridian."""
    parts: List[List[Point]] = []
    cur: List[Point] = []
    prev: Optional[Point] = None
    for lng_u, lat in points:
        if prev is not None:
            a_u, a_lat = prev
            b_u, b_lat = lng_u, lat
            m_lo = math.floor((min(a_u, b_u) + 180.0) / 360.0)
            m_hi = math.floor((max(a_u, b_u) + 180.0) / 360.0)
            if m_lo != m_hi:
                boundary = -180.0 + 360.0 * m_hi
                t = (boundary - a_u) / (b_u - a_u)
                lat_c = a_lat + t * (b_lat - a_lat)
                if b_u > a_u:  # crossing eastward: close at +180, reopen at -180
                    close_lng, open_lng = 180.0, -180.0
                else:          # crossing westward
                    close_lng, open_lng = -180.0, 180.0
                cur.append((close_lng, lat_c))
                parts.append(cur)
                cur = [(open_lng, lat_c)]
        cur.append((_wrap_lng(lng_u), lat))
        prev = (lng_u, lat)
    parts.append(cur)
    parts = [p for p in parts if len(p) >= 2]
    if not parts:
        # Degenerate (should not happen: loops always produce points).
        lng, lat = points[0]
        parts = [[(lng, lat), (lng, lat)]]
    segments = sum(len(p) - 1 for p in parts)
    vertices = sum(len(p) for p in parts)
    if len(parts) == 1:
        geom: Dict[str, Any] = {
            "type": "LineString",
            "coordinates": [[round(lng, 6), round(lat, 6)] for lng, lat in parts[0]],
        }
    else:
        geom = {
            "type": "MultiLineString",
            "coordinates": [
                [[round(lng, 6), round(lat, 6)] for lng, lat in p] for p in parts
            ],
        }
    meta = {
        "geometry_kind": kind,
        "curve_version": CURVE_VERSION if kind == "curve" else None,
        "vertices": vertices,
        "segments": segments,
    }
    return geom, meta


def _bezier_points(o: Point, d: Point, curvature: float, forward: bool,
                   n: int) -> List[Point]:
    """Quadratic Bezier sampled in Web Mercator over unwrapped longitude."""
    olng, olat = o
    dlng, dlat = d
    x0, y0 = _merc_x(olng), _merc_y(olat)
    x2, y2 = _merc_x(olng + dlng), _merc_y(dlat)
    dx, dy = x2 - x0, y2 - y0
    length = math.hypot(dx, dy) or 1.0
    side = 1.0 if forward else -1.0
    off = curvature * length * side
    cx = (x0 + x2) / 2.0 - dy / length * off
    cy = (y0 + y2) / 2.0 + dx / length * off
    pts: List[Point] = []
    for i in range(n + 1):
        t = i / n
        mt = 1.0 - t
        x = mt * mt * x0 + 2.0 * mt * t * cx + t * t * x2
        y = mt * mt * y0 + 2.0 * mt * t * cy + t * t * y2
        pts.append((_unmerc_x(x), _unmerc_y(y)))
    return pts


def edge_geometry(
    origin: Point,
    destination: Point,
    *,
    kind: str = "straight",
    curvature: float = 0.2,
    separate: bool = False,
    forward: bool = True,
    max_segments: int = MAX_EDGE_SEGMENTS,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Build one edge geometry.

    ``origin``/``destination`` are (lng, lat) WGS84 pairs. ``separate``
    marks a rendered pair whose reverse direction is also rendered (the two
    geometries must not overlap). ``forward`` is the deterministic side
    selector: the canonical direction offsets one way, the reverse the
    other. Returns (geometry, meta) where meta carries geometry_kind,
    curve_version, vertices and segments.
    """
    olng, olat = origin
    dlng, dlat = destination
    if round(olng, 6) == round(dlng, 6) and round(olat, 6) == round(dlat, 6):
        # Self-loop or zero-length distinct-node edge: dedicated small loop.
        return _finalize(_loop_points(olng, olat), "loop")

    dlng = _shortest_dlng(olng, dlng)

    if kind == "curve":
        n = max_segments
        while True:
            pts = _bezier_points((olng, olat), (dlng, dlat), curvature, forward, n)
            geom, meta = _finalize(pts, "curve")
            if meta["segments"] <= max_segments or n <= 2:
                return geom, meta
            n //= 2

    if separate:
        x0, y0 = _merc_x(olng), _merc_y(olat)
        x1, y1 = _merc_x(olng + dlng), _merc_y(dlat)
        dx, dy = x1 - x0, y1 - y0
        length = math.hypot(dx, dy) or 1.0
        off = 0.05 * length * (1.0 if forward else -1.0)
        mx, my = (x0 + x1) / 2.0 - dy / length * off, (y0 + y1) / 2.0 + dx / length * off
        pts = [(olng, olat), (_unmerc_x(mx), _unmerc_y(my)), (olng + dlng, dlat)]
        return _finalize(pts, "straight-offset")

    return _finalize([(olng, olat), (olng + dlng, dlat)], "straight")
