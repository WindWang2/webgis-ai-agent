"""Trajectory construction from normalized observations (WP2).

Segmentation is explicit and traceable: a break is emitted between two
consecutive fixes when any configured threshold fires, carrying the reason
and both bounding ``source_row_id``s. Same-time positional conflicts
(``dt == 0`` with distance > 0) are contradictions in the data — they break
the segment rather than producing an undefined speed. Zero-dt coincident
pairs stay inside a segment as degenerate intervals (no fake zero speed).

Segments below ``min_points_per_segment`` are disclosed as dropped, never
silently kept. Segment identity is ``(entity_id, segment_index)`` within one
immutable build; the entity id — never an array index — is the authority.

Antimeridian: geodesic distance handles ±180° crossings exactly; no
degree-as-meter arithmetic anywhere.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from app.services.moving_object.contracts import (
    MAX_TRAJECTORY_SEGMENTS,
    EntityTrajectory,
    IngestConfig,
    MotionInterval,
    ObsPoint,
    SegmentationConfig,
    SegmentBreak,
    TrajectorySegmentModel,
)
from app.services.moving_object.ingest import group_by_entity
from app.services.moving_object.motion import (
    geodesic_distance_m,
    interval_motion,
)

BREAK_TIME_GAP = "time_gap"
BREAK_SPEED_JUMP = "speed_jump"
BREAK_DISTANCE_JUMP = "distance_jump"
BREAK_ZERO_DT_CONFLICT = "zero_dt_conflict"


class SegmentCapExceeded(MemoryError):
    """Bounded-output rejection (typed upstream as resource-scale mismatch)."""


@dataclass
class TrajectoryBuild:
    """Complete build output; full-resolution data stays computational —
    the tool layer stores the heavy parts behind session refs and inlines
    only bounded summaries."""

    trajectories: Dict[str, EntityTrajectory] = field(default_factory=dict)
    breaks: List[SegmentBreak] = field(default_factory=list)
    intervals: Dict[str, List[MotionInterval]] = field(default_factory=dict)
    #: Per entity: list of kept segment runs (fix lists, canonical order).
    segment_runs: Dict[str, List[List[ObsPoint]]] = field(default_factory=dict)
    #: Per entity: count of runs dropped for being below min_points.
    dropped_runs: Dict[str, int] = field(default_factory=dict)
    #: Per entity: degenerate (dt<=0) interval count.
    degenerate_intervals: Dict[str, int] = field(default_factory=dict)

    @property
    def n_segments(self) -> int:
        return sum(t.n_segments for t in self.trajectories.values())


def _evaluate_pair(
    a: ObsPoint, b: ObsPoint, cfg: SegmentationConfig
) -> Optional[Tuple[str, float, float]]:
    """Return (reason, dt_s, distance_m) when a break is required, else None."""
    dt_s = (b.t - a.t).total_seconds()
    distance_m = geodesic_distance_m(a.lon, a.lat, b.lon, b.lat)
    if dt_s < 0:  # defensive: builder guarantees sorted streams
        dt_s = 0.0
    if dt_s == 0.0:
        if distance_m > 0.0:
            return (BREAK_ZERO_DT_CONFLICT, dt_s, distance_m)
        return None  # coincident duplicate: degenerate interval, no break
    if distance_m > cfg.max_jump_m:
        return (BREAK_DISTANCE_JUMP, dt_s, distance_m)
    speed_kmh = (distance_m / dt_s) * 3.6
    if speed_kmh > cfg.max_speed_kmh:
        return (BREAK_SPEED_JUMP, dt_s, distance_m)
    if dt_s > cfg.max_gap_s:
        return (BREAK_TIME_GAP, dt_s, distance_m)
    return None


def _split_entity(
    entity_id: str,
    points: List[ObsPoint],
    cfg: SegmentationConfig,
) -> Tuple[List[List[ObsPoint]], List[SegmentBreak]]:
    """Split one entity's sorted fixes into segment runs + traceable breaks."""
    runs: List[List[ObsPoint]] = []
    breaks: List[SegmentBreak] = []
    current: List[ObsPoint] = [points[0]]
    for a, b in zip(points, points[1:]):
        hit = _evaluate_pair(a, b, cfg)
        if hit is not None:
            reason, _dt_s, _distance_m = hit
            breaks.append(SegmentBreak(
                entity_id=entity_id,
                reason=reason,
                from_row=a.source_row_id,
                to_row=b.source_row_id,
                t_from=a.t,
                t_to=b.t,
            ))
            runs.append(current)
            current = [b]
        else:
            current.append(b)
    runs.append(current)
    return runs, breaks


def _segment_model(
    entity_id: str,
    segment_index: int,
    run: List[ObsPoint],
    interval_speeds_kmh: List[Optional[float]],
    path_length_m: float,
) -> TrajectorySegmentModel:
    duration_s = (run[-1].t - run[0].t).total_seconds()
    mean_speed = (path_length_m / duration_s) * 3.6 if duration_s > 0 else None
    finite_speeds = [s for s in interval_speeds_kmh if s is not None]
    max_speed = max(finite_speeds) if finite_speeds else None
    displacement_m = geodesic_distance_m(
        run[0].lon, run[0].lat, run[-1].lon, run[-1].lat
    )
    return TrajectorySegmentModel(
        entity_id=entity_id,
        segment_index=segment_index,
        n_points=len(run),
        t_start=run[0].t.isoformat(),
        t_end=run[-1].t.isoformat(),
        duration_s=duration_s,
        path_length_m=path_length_m,
        mean_speed_kmh=mean_speed,
        max_speed_kmh=max_speed,
        displacement_m=displacement_m,
        start_lon=run[0].lon,
        start_lat=run[0].lat,
        end_lon=run[-1].lon,
        end_lat=run[-1].lat,
    )


def build_entity_trajectories(
    points: List[ObsPoint],
    seg_cfg: SegmentationConfig,
    ingest_cfg: IngestConfig,
) -> TrajectoryBuild:
    """Build per-entity trajectories from globally sorted observations."""
    build = TrajectoryBuild()
    if not points:
        return build
    # Defensive canonical re-sort (keep_input mode / direct callers).
    pts = sorted(points, key=lambda p: (p.t, p.order))

    total_segments = 0
    for entity_id, stream in group_by_entity(pts):
        runs, breaks = _split_entity(entity_id, stream, seg_cfg)
        build.breaks.extend(breaks)

        kept_segments: List[TrajectorySegmentModel] = []
        kept_runs: List[List[ObsPoint]] = []
        intervals: List[MotionInterval] = []
        cumulative = 0.0
        degenerate = 0
        seg_idx = 0
        for run in runs:
            if len(run) < seg_cfg.min_points_per_segment:
                build.dropped_runs[entity_id] = (
                    build.dropped_runs.get(entity_id, 0) + 1
                )
                continue
            run_intervals, run_len = _run_intervals(entity_id, run, cumulative)
            degenerate += sum(1 for iv in run_intervals if iv.degenerate)
            kept_segments.append(_segment_model(
                entity_id, seg_idx, run,
                [iv.speed_kmh for iv in run_intervals],
                run_len,
            ))
            kept_runs.append(run)
            intervals.extend(run_intervals)
            cumulative += run_len
            seg_idx += 1
            total_segments += 1
            if total_segments >= MAX_TRAJECTORY_SEGMENTS:
                raise SegmentCapExceeded(
                    f"trajectory segments exceed hard cap "
                    f"{MAX_TRAJECTORY_SEGMENTS}; narrow the time window or "
                    f"entity filter"
                )
        if kept_segments:
            build.trajectories[entity_id] = EntityTrajectory(
                entity_id=entity_id,
                n_observations=len(stream),
                n_segments=len(kept_segments),
                segments=kept_segments,
                total_path_length_m=cumulative,
                observation_span_s=(
                    stream[-1].t - stream[0].t
                ).total_seconds(),
            )
            build.intervals[entity_id] = intervals
            build.segment_runs[entity_id] = kept_runs
            if degenerate:
                build.degenerate_intervals[entity_id] = degenerate

    return build


def _run_intervals(
    entity_id: str, run: List[ObsPoint], cumulative_before_m: float
) -> Tuple[List[MotionInterval], float]:
    """Motion intervals + path length for one kept run."""
    out: List[MotionInterval] = []
    cumulative = cumulative_before_m
    for a, b in zip(run, run[1:]):
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
            cumulative_distance_m=cumulative,
            degenerate=(dt_s <= 0),
        ))
    return out, cumulative - cumulative_before_m


def trajectory_line_features(
    build: TrajectoryBuild,
    max_features: int = 5000,
) -> List[Dict]:
    """GeoJSON LineString features (one per kept segment) with motion
    properties for map encoding.

    ``speed_encoded`` is the segment mean speed in km/h (None on
    zero-duration segments — never fabricated). Coordinates are raw
    lon/lat; antimeridian correctness is delegated to geodesic math and the
    render style (world-copy splitting), not to coordinate rewriting.
    """
    feats: List[Dict] = []
    for entity_id in sorted(build.segment_runs.keys()):
        feats.extend(_entity_line_features(entity_id, build))
        if len(feats) >= max_features:
            break
    # Zero-path segments (all fixes coincident in space) draw nothing and
    # carry no motion: exclude them for real (counted in product stats).
    feats = [f for f in feats
             if (f["properties"].get("path_length_m") or 0) > 0.0]
    return feats[:max_features]


def _entity_line_features(entity_id: str, build: TrajectoryBuild) -> List[Dict]:
    runs = build.segment_runs.get(entity_id, [])
    segs = build.trajectories[entity_id].segments
    out: List[Dict] = []
    for run, seg in zip(runs, segs):
        coords = [[p.lon, p.lat] for p in run]
        out.append({
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": coords},
            "properties": {
                "entity_id": entity_id,
                "segment_index": seg.segment_index,
                "t_start": seg.t_start,
                "t_end": seg.t_end,
                "duration_s": seg.duration_s,
                "path_length_m": seg.path_length_m,
                "mean_speed_kmh": seg.mean_speed_kmh,
                "max_speed_kmh": seg.max_speed_kmh,
                "speed_encoded": seg.mean_speed_kmh,
                "n_points": seg.n_points,
            },
        })
    return out
