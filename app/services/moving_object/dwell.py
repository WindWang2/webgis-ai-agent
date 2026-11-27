"""Dwell (stop) detection over trajectory fixes (WP2).

A dwell is a maximal chronological run of consecutive observations of one
entity that all lie within ``radius_m`` (geodesic) of the run's first fix
(the anchor) and last ``>= min_dwell_s`` with ``>= min_points`` fixes.

This is the explicitly parameterized threshold method the contract calls
for — not clustering: radius, minimum duration and minimum point count are
all visible inputs, every accepted dwell carries its evidence rows, and
near-misses are disclosed via diagnostics rather than merged.

Honesty rules:
- Sparse sampling / GPS drift can *split* one physical stop into adjacent
  dwells; results carry a note saying split behavior follows the thresholds
  (no auto-merging across gaps — a gap break is a data fact).
- The dwell instant range is exact at observation granularity: ``t_start`` /
  ``t_end`` are the first/last fix times of the run (arrival/departure
  between fixes is an interval, covered by region-event semantics in WP3).
- Runs failing the duration/point thresholds are counted (near_miss_runs),
  not hidden.

Complexity: O(n) geodesic checks per entity; no pairwise passes.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List

from app.services.moving_object.contracts import (
    DwellConfig,
    DwellEventModel,
    MAX_EVENTS,
    MovingObjectError,
    ObsPoint,
)
from app.services.moving_object.motion import geodesic_distance_m


@dataclass
class DwellDiagnostics:
    near_miss_runs: int = 0          # passed radius, failed duration/points
    total_runs: int = 0
    longest_run_points: int = 0


@dataclass
class DwellBuild:
    events: List[DwellEventModel] = field(default_factory=list)
    diagnostics: DwellDiagnostics = field(default_factory=DwellDiagnostics)


def detect_dwells(
    points: List[ObsPoint],
    cfg: DwellConfig,
) -> DwellBuild:
    """Detect dwells per entity from globally sorted observations.

    Dwell identity: ``(entity_id, dwell_index)`` with dwell_index 0-based in
    chronological order per entity. Output is capped at ``MAX_EVENTS`` with
    a typed rejection — scale honesty instead of silent truncation.
    """
    if not points:
        return DwellBuild()
    pts = sorted(points, key=lambda p: (p.t, p.order))

    build = DwellBuild()
    by_entity: Dict[str, List[ObsPoint]] = {}
    for p in pts:
        by_entity.setdefault(p.entity_id, []).append(p)

    for entity_id in sorted(by_entity.keys()):
        stream = by_entity[entity_id]
        dwell_index = 0
        runs = _split_runs(stream, cfg.radius_m)
        build.diagnostics.total_runs += len(runs)
        for run in runs:
            if build.diagnostics.longest_run_points < len(run):
                build.diagnostics.longest_run_points = len(run)
            duration_s = (run[-1].t - run[0].t).total_seconds()
            if duration_s < cfg.min_dwell_s or len(run) < cfg.min_points:
                build.diagnostics.near_miss_runs += 1
                continue
            extent = 0.0
            anchor = run[0]
            for q in run[1:]:
                d = geodesic_distance_m(anchor.lon, anchor.lat, q.lon, q.lat)
                if d > extent:
                    extent = d
            if len(build.events) >= MAX_EVENTS:
                raise MovingObjectError(
                    f"dwell events exceed hard cap {MAX_EVENTS}; narrow the "
                    f"time window or raise min_dwell_s"
                )
            build.events.append(DwellEventModel(
                entity_id=entity_id,
                dwell_index=dwell_index,
                t_start=run[0].t.isoformat(),
                t_end=run[-1].t.isoformat(),
                duration_s=duration_s,
                n_points=len(run),
                center_lon=anchor.lon,
                center_lat=anchor.lat,
                radius_m=cfg.radius_m,
                extent_from_anchor_m=extent,
                from_row=run[0].source_row_id,
                to_row=run[-1].source_row_id,
            ))
            dwell_index += 1

    if build.events:
        build.events[-1].note = (
            "dwells are observation-granularity runs; sampling sparsity or "
            "GPS drift may split one physical stop (thresholds visible in "
            "the request, no auto-merge)"
        )
    return build


def _split_runs(stream: List[ObsPoint], radius_m: float) -> List[List[ObsPoint]]:
    """Split one entity's stream into maximal anchor-radius runs (O(n))."""
    runs: List[List[ObsPoint]] = []
    current: List[ObsPoint] = [stream[0]]
    anchor = stream[0]
    for p in stream[1:]:
        d = geodesic_distance_m(anchor.lon, anchor.lat, p.lon, p.lat)
        if d <= radius_m:
            current.append(p)
        else:
            runs.append(current)
            current = [p]
            anchor = p
    runs.append(current)
    return runs


def dwell_point_features(
    build: DwellBuild,
    max_features: int = 5000,
) -> List[Dict]:
    """GeoJSON Point features for map rendering of dwell events."""
    feats: List[Dict] = []
    for ev in build.events[:max_features]:
        feats.append({
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": [ev.center_lon, ev.center_lat],
            },
            "properties": {
                "entity_id": ev.entity_id,
                "dwell_index": ev.dwell_index,
                "t_start": ev.t_start,
                "t_end": ev.t_end,
                "duration_s": ev.duration_s,
                "n_points": ev.n_points,
                "extent_from_anchor_m": ev.extent_from_anchor_m,
            },
        })
    return feats
