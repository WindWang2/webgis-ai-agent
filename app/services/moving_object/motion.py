"""Interval motion quantities for trajectory observations (WP2).

All quantities are *interval estimates between two consecutive observations*:
- distance/bearing: geodesic on the WGS84 ellipsoid (pyproj.Geod.inv);
  degrees are never treated as meters; antimeridian crossings are exact.
- dt: UTC instant difference; ``dt_s == 0`` (duplicate-time pair kept by
  policy) yields ``speed=None`` + ``degenerate=True`` — no fake zero, no inf.
- negative dt cannot occur post-ingest (canonical sort is unconditional;
  the builder re-sorts defensively before entering this layer).

Performance: pyproj.Geod.inv is a C call (~0.7µs); 100k segments ≈ 0.1s.
No O(n²) passes anywhere.
"""
from __future__ import annotations

import math
from typing import List, Optional, Tuple

from pyproj import Geod

from app.services.moving_object.contracts import (
    MotionInterval,
    ObsPoint,
)

_GEOD = Geod(ellps="WGS84")


def geodesic_distance_m(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    """WGS84 geodesic distance in meters (antimeridian-safe, degree-safe)."""
    _, _, dist = _GEOD.inv(lon1, lat1, lon2, lat2)
    return float(dist)


def geodesic_bearing_deg(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    """Forward azimuth (degrees, 0=north, clockwise) from fix 1 to fix 2.

    Returns None-equivalent via NaN only for coincident points; callers
    normalize coincident points to bearing=None (ambiguous, not 0).
    """
    az12, _, _ = _GEOD.inv(lon1, lat1, lon2, lat2)
    return float(az12)


def interval_motion(
    a: ObsPoint, b: ObsPoint
) -> Tuple[float, float, Optional[float], Optional[float]]:
    """Compute (dt_s, distance_m, speed_kmh, bearing_deg) for one interval.

    ``speed_kmh``/``bearing_deg`` are None when undefined: dt==0 for speed
    (degenerate interval), coincident positions for bearing.
    """
    dt_s = (b.t - a.t).total_seconds()
    distance_m = geodesic_distance_m(a.lon, a.lat, b.lon, b.lat)
    speed_kmh: Optional[float] = None
    bearing_deg: Optional[float] = None
    if dt_s > 0:
        speed_kmh = (distance_m / dt_s) * 3.6
    if distance_m > 0:
        bearing_deg = geodesic_bearing_deg(a.lon, a.lat, b.lon, b.lat)
    return dt_s, distance_m, speed_kmh, bearing_deg


def build_intervals(
    points: List[ObsPoint],
    entity_id: str,
    cumulate: bool = True,
) -> Tuple[List[MotionInterval], float]:
    """Build per-interval motion records for one entity's sorted fixes.

    Returns (intervals, total_path_length_m). Intervals carry their bounding
    ``source_row_id``s; ``cumulative_distance_m`` is the running geodesic
    path length at the *end* fix of each interval.
    """
    out: List[MotionInterval] = []
    cumulative = 0.0
    for a, b in zip(points, points[1:]):
        dt_s, distance_m, speed_kmh, bearing_deg = interval_motion(a, b)
        cumulative += distance_m
        out.append(MotionInterval(
            entity_id=entity_id,
            from_row=a.source_row_id,
            to_row=b.source_row_id,
            t_from=a.t.isoformat(),
            t_to=b.t.isoformat(),
            dt_s=dt_s,
            distance_m=distance_m,
            speed_kmh=speed_kmh,
            bearing_deg=bearing_deg,
            cumulative_distance_m=cumulative if cumulate else 0.0,
            degenerate=(dt_s <= 0),
        ))
    return out, cumulative


def bearing_transition_deg(prev_bearing: float, next_bearing: float) -> float:
    """Absolute signed turn angle in degrees, normalized to (-180, 180]."""
    d = (next_bearing - prev_bearing + 180.0) % 360.0 - 180.0
    # Normalization edge: a full reversal lands on ±180; keep the positive.
    if d == -180.0:
        d = 180.0
    return d


def is_finite_nonneg(x: Optional[float]) -> bool:
    return x is not None and math.isfinite(x) and x >= 0.0
