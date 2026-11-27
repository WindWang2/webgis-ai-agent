"""Deterministic synthetic trajectory oracles (WP6).

Used by unit tests, perf harnesses and the scripted journey: every
generator is seed-controlled, returns the FeatureCollection a user would
ingest, and separately returns the *ground truth* the analysis must
reproduce. Generators use ``pyproj.Geod.fwd`` to place fixes at true
geodesic offsets (no degree-as-meter synthesis), so distance/speed
assertions compare analysis output against independent published geodesic
constants and the generator's own ground truth.

Scenarios (mapped to the WP6 fixture list):
- ``straight_line``        — constant speed due east; exact distances.
- ``two_entities``         — two objects, disjoint windows, ID handling.
- ``dateline_crossing``    — track through ±180 near a near-dateline region.
- ``sparse_region_skip``   — sampling coarser than the region → cross only.
- ``hole_and_multipoly``   — ring pass-through vs hole pass-through.
- ``zero_time_conflict``   — duplicate timestamps incl. positional conflict.
- ``static_with_noise``    — parked dwell with GPS jitter around the anchor.
- ``low_quality``          — naive times, missing IDs, bad geometry rows.
- ``bus_route``            — journey-1 fixture: route + stops (dwells).
"""
from __future__ import annotations

import math
import random
from typing import Any, Dict, List, Optional, Tuple

from pyproj import Geod

_GEOD = Geod(ellps="WGS84")


def _fwd(lon: float, lat: float, az_deg: float, dist_m: float) -> Tuple[float, float]:
    lon2, lat2, _ = _GEOD.fwd(lon, lat, az_deg, dist_m)
    return lon2, lat2


def _fc(features: List[Dict]) -> Dict[str, Any]:
    return {"type": "FeatureCollection", "features": features}


def _pt_feat(
    entity_id: str, t_iso: str, lon: float, lat: float,
    extra: Optional[Dict] = None,
) -> Dict:
    props = {"entity_id": entity_id, "timestamp": t_iso}
    if extra:
        props.update(extra)
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [lon, lat]},
        "properties": props,
    }


def straight_line_oracle(
    n_points: int = 21,
    dt_s: float = 60.0,
    speed_mps: float = 10.0,  # 36 km/h
    start_lon: float = 116.0,
    start_lat: float = 39.0,
    start_epoch_s: int = 1_700_000_000,
    azimuth_deg: float = 90.0,
    entity_id: str = "ORACLE-1",
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Due-east constant-speed run. Ground truth: per-interval distance ==
    speed*dt (±1 m geodesic fwd rounding), overall bearing == azimuth."""
    features: List[Dict] = []
    lon, lat = start_lon, start_lat
    t = start_epoch_s
    for i in range(n_points):
        from datetime import datetime, timezone
        t_iso = datetime.fromtimestamp(t, tz=timezone.utc).isoformat()
        features.append(_pt_feat(entity_id, t_iso, lon, lat))
        lon, lat = _fwd(lon, lat, azimuth_deg, speed_mps * dt_s)
        t += dt_s
    truth = {
        "n_points": n_points,
        "interval_distance_m": speed_mps * dt_s,
        "speed_kmh": speed_mps * 3.6,
        "bearing_deg": azimuth_deg,
        "path_length_m": speed_mps * dt_s * (n_points - 1),
    }
    return _fc(features), truth


def two_entities_oracle() -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Two objects with different windows/speeds; truth counts per entity."""
    fc1, t1 = straight_line_oracle(n_points=6, entity_id="BUS-A")
    fc2, t2 = straight_line_oracle(
        n_points=4, speed_mps=5.0, start_lon=116.0, start_lat=39.01,
        entity_id="BUS-B",
    )
    merged = _fc(fc1["features"] + fc2["features"])
    truth = {"BUS-A": t1, "BUS-B": t2}
    return merged, truth


def dateline_oracle() -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    """Track crossing ±180 plus a near-dateline region.

    Truth: enter when first fix inside region; cross when both fixes
    outside but the unwrapped segment reaches the region.
    """
    region = {
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "properties": {"region_id": "DATELINE-ZONE"},
            "geometry": {"type": "Polygon", "coordinates": [[
                [178.0, -5.0], [179.9, -5.0], [179.9, 5.0], [178.0, 5.0],
                [178.0, -5.0],
            ]]},
        }],
    }
    # Fixes: west of region → inside → inside near dateline → crossed to
    # the western hemisphere side (outside) via the short path.
    feats = [
        _pt_feat("DL-1", "2026-01-01T00:00:00+00:00", 177.0, 0.0),
        _pt_feat("DL-1", "2026-01-01T00:10:00+00:00", 178.5, 0.0),
        _pt_feat("DL-1", "2026-01-01T00:20:00+00:00", 179.5, 0.0),
        _pt_feat("DL-1", "2026-01-01T00:30:00+00:00", -179.5, 0.0),
        _pt_feat("DL-1", "2026-01-01T00:40:00+00:00", -178.5, 0.0),
    ]
    truth = {
        "events_expected": ["enter", "exit"],  # one enter, one exit; the
        # exit brackets [t(179.5), t(-179.5)] because (-179.5) is outside.
        "no_cross": True,
    }
    return _fc(feats), region, truth


def sparse_region_skip_oracle() -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    """Two fixes 1200 s apart straddle the region; only a cross may fire."""
    region = {
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "properties": {"region_id": "SKIPPED-ZONE"},
            "geometry": {"type": "Polygon", "coordinates": [[
                [0.0, 0.0], [10.0, 0.0], [10.0, 10.0], [0.0, 10.0],
                [0.0, 0.0],
            ]]},
        }],
    }
    feats = [
        _pt_feat("S-1", "2026-01-01T00:00:00+00:00", -5.0, 5.0),
        _pt_feat("S-1", "2026-01-01T00:20:00+00:00", 15.0, 5.0),
    ]
    truth = {"events_expected": ["cross"]}
    return _fc(feats), region, truth


def hole_oracle() -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    """Outer square with a hole; ring pass and hole pass on the same run."""
    region = {
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "properties": {"region_id": "DONUT"},
            "geometry": {"type": "Polygon", "coordinates": [
                [[0.0, 0.0], [10.0, 0.0], [10.0, 10.0], [0.0, 10.0],
                 [0.0, 0.0]],
                [[4.0, 4.0], [4.0, 6.0], [6.0, 6.0], [6.0, 4.0],
                 [4.0, 4.0]],
            ]},
        }],
    }
    # y=5 straight through the hole: in(3) → exit [4→5] → enter [6→7].
    from datetime import datetime, timedelta, timezone
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    lons = [2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0]
    feats = []
    for i, lon in enumerate(lons):
        t_iso = (t0 + timedelta(minutes=10 * i)).isoformat()
        feats.append(_pt_feat("H-1", t_iso, lon, 5.0))
    truth = {
        "events_expected": ["enter", "exit", "enter"],
        # exit interval (lon 4 fix at boundary of hole → closed rule counts
        # inside; lon 5 inside hole → outside): exit [4→5]; enter [5→6].
    }
    return _fc(feats), region, truth


def zero_time_oracle() -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Duplicate timestamps: coincident pair (kept, degenerate) plus a
    same-time positional conflict (segment break)."""
    feats = [
        _pt_feat("Z-1", "2026-01-01T00:00:00+00:00", 0.0, 0.0),
        _pt_feat("Z-1", "2026-01-01T00:01:00+00:00", 1.0, 0.0),
        # same time as previous, different position → conflict break
        _pt_feat("Z-1", "2026-01-01T00:01:00+00:00", 1.0, 1.0),
        _pt_feat("Z-1", "2026-01-01T00:02:00+00:00", 2.0, 1.0),
        # exact duplicate row (same time AND same position) → degenerate
        _pt_feat("Z-1", "2026-01-01T00:02:00+00:00", 2.0, 1.0),
        _pt_feat("Z-1", "2026-01-01T00:03:00+00:00", 3.0, 1.0),
    ]
    truth = {
        "duplicate_pairs": 2,
        "conflict_breaks": 1,
        # Only the exact-duplicate pair remains an interval after the
        # conflict break splits its pair into separate runs.
        "degenerate_intervals": 1,
    }
    return _fc(feats), truth


def static_noise_oracle(
    n_parked: int = 12,
    jitter_m: float = 8.0,
    dt_s: float = 60.0,
    seed: int = 7,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Parked vehicle with bounded jitter → one dwell; then a fast exit."""
    rng = random.Random(seed)
    feats: List[Dict] = []
    base = _GEOD.fwd(116.0, 39.0, 90.0, 500.0)  # jump east 500m first
    # park
    lon0, lat0 = 116.0, 39.0
    from datetime import datetime, timezone, timedelta
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for i in range(n_parked):
        az = rng.uniform(0, 360)
        d = rng.uniform(0, jitter_m)
        lon, lat = _fwd(lon0, lat0, az, d)
        t_iso = (t0 + timedelta(seconds=i * dt_s)).isoformat()
        feats.append(_pt_feat("P-1", t_iso, lon, lat))
    # exit fast: one interval covering ~2000 m in dt → speed high but
    # below the default 300 km/h jump threshold.
    lon2, lat2 = _fwd(lon0, lat0, 90.0, 2000.0)
    t_iso = (t0 + timedelta(seconds=(n_parked + 1) * dt_s)).isoformat()
    feats.append(_pt_feat("P-1", t_iso, lon2, lat2))
    truth = {
        "dwell_count": 1,
        "dwell_min_duration_s": (n_parked - 1) * dt_s,
        "dwell_extent_within_m": jitter_m * 2.0,
    }
    return _fc(feats), truth


def low_quality_oracle() -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Naive times, missing entity id, unparseable time, degenerate geom."""
    feats = [
        # naive timestamp (no tz) — must be flagged, not rejected
        {"type": "Feature",
         "geometry": {"type": "Point", "coordinates": [0.0, 0.0]},
         "properties": {"entity_id": "L-1", "timestamp": "2026-01-01T00:00:00"}},
        # missing entity id
        {"type": "Feature",
         "geometry": {"type": "Point", "coordinates": [1.0, 0.0]},
         "properties": {"timestamp": "2026-01-01T00:01:00Z"}},
        # unparseable time
        {"type": "Feature",
         "geometry": {"type": "Point", "coordinates": [2.0, 0.0]},
         "properties": {"entity_id": "L-1", "timestamp": "not-a-time"}},
        # degenerate geometry (out of domain lon)
        {"type": "Feature",
         "geometry": {"type": "Point", "coordinates": [181.0, 0.0]},
         "properties": {"entity_id": "L-1", "timestamp": "2026-01-01T00:02:00Z"}},
        # valid pair
        {"type": "Feature",
         "geometry": {"type": "Point", "coordinates": [3.0, 0.0]},
         "properties": {"entity_id": "L-1", "timestamp": "2026-01-01T00:03:00Z"}},
        {"type": "Feature",
         "geometry": {"type": "Point", "coordinates": [4.0, 0.0]},
         "properties": {"entity_id": "L-1", "timestamp": "2026-01-01T00:04:00Z"}},
    ]
    truth = {
        "accepted": 3,
        "missing_entity_id": 1,
        "unparseable_time": 1,
        "degenerate_geometry": 1,
        "naive_time_rows": 1,
    }
    return _fc(feats), truth


def bus_route_oracle(
    seed: int = 11,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Journey-1 fixture: 3 anonymous buses over a shared corridor with two
    stops each. Truth: 3 entities, 2 dwells each (>=120 s), known segment
    count (no jumps: speeds kept < thresholds)."""
    rng = random.Random(seed)
    from datetime import datetime, timezone, timedelta
    t0 = datetime(2026, 6, 1, 6, 0, tzinfo=timezone.utc)
    feats: List[Dict] = []
    truth_entities = ["BUS-01", "BUS-02", "BUS-03"]
    dwell_duration_s = 180
    cruise_leg_s = 300          # 5 min at ~14.4 km/h = 1200 m legs
    leg_m = 1200.0
    t = t0
    # Stops at lon offsets 0, 1200 m, 2400 m along a due-east corridor.
    stops_lonlat = [(116.0, 39.0)]
    lon, lat = 116.0, 39.0
    for _ in range(2):
        lon, lat = _fwd(lon, lat, 90.0, leg_m)
        stops_lonlat.append((lon, lat))
    for bus in truth_entities:
        clock = t + timedelta(minutes=3 * truth_entities.index(bus))
        lon0, lat0 = stops_lonlat[0]
        # approach stop 0
        clock = _emit_leg(feats, bus, clock, lon0, lat0, 90.0, 600.0,
                          cruise_leg_s // 2, rng)
        for stop_idx in range(3):
            slon, slat = stops_lonlat[stop_idx]
            clock = _emit_dwell(feats, bus, clock, slon, slat,
                                dwell_duration_s, rng)
            if stop_idx < 2:
                clock = _emit_leg(feats, bus, clock, slon, slat, 90.0,
                                  leg_m, cruise_leg_s, rng)
    truth = {
        "entities": truth_entities,
        "dwells_per_entity": 3,  # stops are dwells (approach stop counts)
        "n_points_per_entity": 2 + 3 * (6 + 6),
    }
    return _fc(feats), truth


def _emit_leg(feats, bus, clock, lon, lat, az, dist_m, n_steps, rng):
    """Emit a moving leg: n_steps+1 fixes at true geodesic offsets."""
    from datetime import timedelta
    step_s = 30
    step_m = dist_m / n_steps
    lon_c, lat_c = lon, lat
    for i in range(n_steps + 1):
        feats.append(_pt_feat(bus, clock.isoformat(), lon_c, lat_c,
                              extra={"accuracy_m": rng.uniform(3, 12)}))
        clock = clock + timedelta(seconds=step_s)
        lon_c, lat_c = _fwd(lon_c, lat_c, az, step_m)
    return clock


def _emit_dwell(feats, bus, clock, lon, lat, duration_s, rng):
    """Emit a parked dwell: fixes every 30 s within ~8 m jitter."""
    from datetime import timedelta
    n = max(2, int(duration_s // 30) + 1)
    for i in range(n):
        az = rng.uniform(0, 360)
        d = rng.uniform(0, 8.0)
        lon_j, lat_j = _fwd(lon, lat, az, d)
        feats.append(_pt_feat(bus, clock.isoformat(), lon_j, lat_j,
                              extra={"accuracy_m": rng.uniform(3, 12)}))
        clock = clock + timedelta(seconds=30)
    return clock


#: Published WGS84 geodesic constants (independent distance oracle).
WGS84_MERIDIAN_QUARTER_M = 10_001_965.729   # equator → pole
WGS84_ONE_DEG_LON_AT_EQUATOR_M = 111_319.4908
#: Meridian-arc degree length at the equator (published); the point-to-point
#: geodesic differs by ~0.1 m — asserted with 1 m tolerance.
WGS84_ONE_DEG_LAT_AT_EQUATOR_M = 110_574.388
