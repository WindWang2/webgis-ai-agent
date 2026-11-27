"""WP2 motion + builder + dwell tests, with published geodesic oracles."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.services.moving_object.builder import (
    BREAK_DISTANCE_JUMP,
    BREAK_SPEED_JUMP,
    BREAK_TIME_GAP,
    BREAK_ZERO_DT_CONFLICT,
    build_entity_trajectories,
    trajectory_line_features,
)
from app.services.moving_object.contracts import (
    IngestConfig,
    MovingObjectError,
    ObsPoint,
    SegmentationConfig,
)
from app.services.moving_object.dwell import detect_dwells
from app.services.moving_object.motion import (
    bearing_transition_deg,
    geodesic_distance_m,
)
from app.services.moving_object.oracles import (
    WGS84_MERIDIAN_QUARTER_M,
    WGS84_ONE_DEG_LAT_AT_EQUATOR_M,
    WGS84_ONE_DEG_LON_AT_EQUATOR_M,
    static_noise_oracle,
    straight_line_oracle,
    zero_time_oracle,
)
from app.services.moving_object.ingest import ingest_point_collection


T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _pts(rows, cfg=None):
    feats = []
    for entity, t_iso, lon, lat in rows:
        feats.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": {"entity_id": entity, "timestamp": t_iso},
        })
    pts, diag = ingest_point_collection(feats, cfg or IngestConfig())
    return pts, diag


# --- geodesic oracles (published WGS84 constants, tolerance 0.1 m) ---------


def test_one_degree_longitude_at_equator() -> None:
    d = geodesic_distance_m(0.0, 0.0, 1.0, 0.0)
    assert abs(d - WGS84_ONE_DEG_LON_AT_EQUATOR_M) < 0.1


def test_one_degree_latitude_at_equator() -> None:
    d = geodesic_distance_m(0.0, 0.0, 0.0, 1.0)
    assert abs(d - WGS84_ONE_DEG_LAT_AT_EQUATOR_M) < 0.1


def test_meridian_quarter_equator_to_pole() -> None:
    d = geodesic_distance_m(0.0, 0.0, 0.0, 90.0)
    assert abs(d - WGS84_MERIDIAN_QUARTER_M) < 0.5


def test_antimeridian_is_short_way() -> None:
    # 179.5E → 179.5W is 1° of longitude apart, not 359°.
    d = geodesic_distance_m(179.5, 0.0, -179.5, 0.0)
    assert abs(d - WGS84_ONE_DEG_LON_AT_EQUATOR_M) < 0.1


def test_distance_symmetry_and_triangle_inequality() -> None:
    a, b, c = (116.0, 39.0), (117.0, 39.5), (116.5, 40.0)
    dab = geodesic_distance_m(*a, *b)
    dba = geodesic_distance_m(*b, *a)
    assert abs(dab - dba) < 1e-6
    dac = geodesic_distance_m(*a, *c)
    dab_c = dab + geodesic_distance_m(*b, *c)
    assert dac <= dab_c + 1e-6


def test_coincident_points_zero_distance_and_bearing_guard() -> None:
    from app.services.moving_object.motion import geodesic_bearing_deg, interval_motion
    assert geodesic_distance_m(10.0, 10.0, 10.0, 10.0) == 0.0
    # PROJ defines azimuth 180 for coincident points; the interval layer
    # must NOT expose it — undefined bearing → None.
    a = ObsPoint("A", T0, 10.0, 10.0, "r0", 0)
    b_ = ObsPoint("A", T0, 10.0, 10.0, "r1", 1)
    dt_s, dist_m, speed, bearing = interval_motion(a, b_, 0.0)
    assert dt_s == 0.0 and dist_m == 0.0
    assert speed is None and bearing is None


def test_bearing_transition_normalization() -> None:
    assert bearing_transition_deg(10.0, 350.0) == -20.0
    assert bearing_transition_deg(350.0, 10.0) == 20.0
    assert bearing_transition_deg(0.0, 180.0) == 180.0


# --- segmentation -----------------------------------------------------------


def test_straight_line_oracle_builds_single_segment() -> None:
    fc, truth = straight_line_oracle()
    pts, diag = ingest_point_collection(fc["features"], IngestConfig())
    build = build_entity_trajectories(pts, SegmentationConfig(), IngestConfig())
    assert diag.accepted == truth["n_points"]
    assert len(build.trajectories) == 1
    traj = build.trajectories["ORACLE-1"]
    assert traj.n_segments == 1
    assert not build.breaks
    seg = traj.segments[0]
    # oracle: 20 intervals × 360 m = 7200 m (tolerance for fwd rounding)
    assert abs(traj.total_path_length_m - truth["path_length_m"]) < 2.0
    assert abs(seg.mean_speed_kmh - truth["speed_kmh"]) < 0.2
    assert seg.duration_s == 60 * (truth["n_points"] - 1)


def test_gap_breaks_segment_with_traceable_rows() -> None:
    rows = [
        ("A", "2026-01-01T00:00:00Z", 0.0, 0),
        ("A", "2026-01-01T00:01:00Z", 0.01, 0),   # ~1.1 km spacing
        # 3 h gap > default max_gap_s (3600 s)
        ("A", "2026-01-01T03:01:00Z", 0.02, 0),
    ]
    pts, _ = _pts(rows)
    build = build_entity_trajectories(pts, SegmentationConfig(), IngestConfig())
    assert len(build.breaks) == 1
    br = build.breaks[0]
    assert br.reason == BREAK_TIME_GAP
    assert br.from_row == "row:1" and br.to_row == "row:2"
    traj = build.trajectories["A"]
    # The post-gap run has a single fix → below min_points → dropped and
    # disclosed (never silently kept, never padded).
    assert traj.n_segments == 1
    assert traj.segments[0].n_points == 2
    assert build.dropped_runs.get("A") == 1


def test_speed_jump_breaks_when_aliasing() -> None:
    rows = [
        ("A", "2026-01-01T00:00:00Z", 0.0, 0),
        # ~11 km in 60 s ≈ 665 km/h >> default 300; distance 11 km < 50 km cap
        ("A", "2026-01-01T00:01:00Z", 0.1, 0.0),
    ]
    pts, _ = _pts(rows)
    build = build_entity_trajectories(pts, SegmentationConfig(), IngestConfig())
    assert [b.reason for b in build.breaks] == [BREAK_SPEED_JUMP]


def test_distance_jump_breaks_regardless_of_speed() -> None:
    rows = [
        ("A", "2026-01-01T00:00:00Z", 0, 0),
        # 1° lat ≈ 111 km in 10 days: slow but > default 50 km jump cap
        ("A", "2026-01-11T00:00:00Z", 0, 1.0),
    ]
    pts, _ = _pts(rows)
    build = build_entity_trajectories(pts, SegmentationConfig(), IngestConfig())
    assert [b.reason for b in build.breaks] == [BREAK_DISTANCE_JUMP]


def test_zero_dt_positional_conflict_breaks() -> None:
    fc, truth = zero_time_oracle()
    pts, diag = ingest_point_collection(fc["features"], IngestConfig())
    assert diag.duplicate_time_pairs == truth["duplicate_pairs"]
    build = build_entity_trajectories(pts, SegmentationConfig(), IngestConfig())
    conflicts = [b for b in build.breaks
                 if b.reason == BREAK_ZERO_DT_CONFLICT]
    assert len(conflicts) == truth["conflict_breaks"]
    assert build.degenerate_intervals.get("Z-1") == \
        truth["degenerate_intervals"]
    # conflicting interval must NOT fabricate a speed
    intervals = build.intervals["Z-1"]
    assert all(iv.speed_kmh is not None or iv.degenerate for iv in intervals)


def test_zero_dt_coincident_pair_has_no_fake_speed() -> None:
    rows = [
        ("A", "2026-01-01T00:00:00Z", 0, 0),
        ("A", "2026-01-01T00:00:00Z", 0, 0),  # exact duplicate position
        ("A", "2026-01-01T00:01:00Z", 0.01, 0),
    ]
    pts, _ = _pts(rows)
    build = build_entity_trajectories(pts, SegmentationConfig(), IngestConfig())
    ivs = build.intervals["A"]
    assert ivs[0].degenerate is True
    assert ivs[0].speed_kmh is None and ivs[0].distance_m == 0.0
    assert ivs[0].bearing_deg is None  # undefined bearing never fabricated
    assert ivs[1].speed_kmh is not None and ivs[1].speed_kmh > 0
    # no inf anywhere
    for iv in ivs:
        for v in (iv.speed_kmh, iv.distance_m, iv.dt_s):
            if v is not None:
                assert v == v and abs(v) != float("inf")


def test_sub_threshold_runs_dropped_and_disclosed() -> None:
    rows = [
        ("A", "2026-01-01T00:00:00Z", 0.0, 0),
        ("A", "2026-01-01T00:01:00Z", 0.01, 0),
        # isolated single-fix island after a big gap
        ("A", "2026-01-01T09:00:00Z", 0.02, 0),
        ("A", "2026-01-01T09:01:00Z", 0.03, 0),
    ]
    pts, _ = _pts(rows)
    build = build_entity_trajectories(pts, SegmentationConfig(), IngestConfig())
    traj = build.trajectories["A"]
    assert traj.n_segments == 2
    assert build.dropped_runs.get("A", 0) == 0  # both runs have 2 points


def test_two_entities_stay_independent() -> None:
    from app.services.moving_object.oracles import two_entities_oracle
    fc, truth = two_entities_oracle()
    pts, _ = ingest_point_collection(fc["features"], IngestConfig())
    build = build_entity_trajectories(pts, SegmentationConfig(), IngestConfig())
    assert set(build.trajectories.keys()) == {"BUS-A", "BUS-B"}
    for eid, t in truth.items():
        traj = build.trajectories[eid]
        assert traj.n_segments == 1
        assert abs(traj.total_path_length_m - t["path_length_m"]) < 2.0


def test_line_features_carry_honest_encoding() -> None:
    fc, _ = straight_line_oracle()
    pts, _ = ingest_point_collection(fc["features"], IngestConfig())
    build = build_entity_trajectories(pts, SegmentationConfig(), IngestConfig())
    feats = trajectory_line_features(build)
    assert len(feats) == 1
    props = feats[0]["properties"]
    assert props["speed_encoded"] == pytest.approx(36.0, abs=0.5)
    assert props["entity_id"] == "ORACLE-1"
    assert len(feats[0]["geometry"]["coordinates"]) == 21


def test_segment_cap_is_typed_rejection() -> None:
    from app.services.moving_object.builder import SegmentCapExceeded
    # 30 two-fix segments: even pairs span 2 h (gap break), odd pairs 60 s.
    rows = []
    t = T0
    for i in range(60):
        rows.append(("A", t.isoformat(), float(i) * 0.01, 0))
        t = t + (timedelta(hours=2) if i % 2 == 0 else timedelta(minutes=1))
    pts, _ = _pts(rows)
    from app.services.moving_object import builder as b
    orig = b.MAX_TRAJECTORY_SEGMENTS
    b.MAX_TRAJECTORY_SEGMENTS = 10
    try:
        with pytest.raises(SegmentCapExceeded):
            b.build_entity_trajectories(pts, SegmentationConfig(),
                                        IngestConfig())
    finally:
        b.MAX_TRAJECTORY_SEGMENTS = orig


# --- dwell ------------------------------------------------------------------


def test_static_noise_produces_one_dwell() -> None:
    fc, truth = static_noise_oracle()
    pts, _ = ingest_point_collection(fc["features"], IngestConfig())
    from app.services.moving_object.contracts import DwellConfig
    build = detect_dwells(pts, DwellConfig(radius_m=25.0, min_dwell_s=120,
                                           min_points=3))
    assert len(build.events) == truth["dwell_count"]
    ev = build.events[0]
    assert ev.duration_s >= truth["dwell_min_duration_s"]
    assert ev.extent_from_anchor_m <= truth["dwell_extent_within_m"]
    assert ev.n_points >= 3


def test_dwell_below_min_duration_is_near_miss() -> None:
    rows = [
        ("A", "2026-01-01T00:00:00Z", 0, 0),
        ("A", "2026-01-01T00:01:00Z", 0.0001, 0),
        ("A", "2026-01-01T00:02:00Z", 0.0002, 0),
    ]  # 120 s exactly; set threshold higher
    pts, _ = _pts(rows)
    from app.services.moving_object.contracts import DwellConfig
    build = detect_dwells(pts, DwellConfig(radius_m=50, min_dwell_s=3600,
                                           min_points=3))
    assert not build.events
    assert build.diagnostics.near_miss_runs >= 1


def test_sparse_drift_splits_runs_and_discloses() -> None:
    # Cluster of two fixes within 50 m, then two isolated fixes ~90 m and
    # ~180 m away: runs split per thresholds; no auto-merge.
    rows = [
        ("A", "2026-01-01T00:00:00Z", 0.0, 0),
        ("A", "2026-01-01T00:01:00Z", 0.0001, 0),  # ~9 m from anchor
        ("A", "2026-01-01T00:02:00Z", 0.001, 0),   # ~90 m: new run
        ("A", "2026-01-01T00:03:00Z", 0.002, 0),   # ~180 m: new run
    ]
    pts, _ = _pts(rows)
    from app.services.moving_object.contracts import DwellConfig
    build = detect_dwells(pts, DwellConfig(radius_m=50, min_dwell_s=30,
                                           min_points=2))
    assert build.diagnostics.total_runs == 3
    assert len(build.events) == 1  # only the 2-fix cluster qualifies
    assert build.diagnostics.near_miss_runs == 2
    assert "no auto-merge" in build.events[0].note


def test_moving_trajectory_has_no_dwells() -> None:
    fc, _ = straight_line_oracle()
    pts, _ = ingest_point_collection(fc["features"], IngestConfig())
    from app.services.moving_object.contracts import DwellConfig
    build = detect_dwells(pts, DwellConfig())
    assert not build.events


# --- quality ----------------------------------------------------------------


def test_quality_report_separates_diagnostics() -> None:
    fc, truth = zero_time_oracle()
    pts, diag = ingest_point_collection(fc["features"], IngestConfig())
    build = build_entity_trajectories(pts, SegmentationConfig(), IngestConfig())
    from app.services.moving_object.quality import diagnose
    report = diagnose(pts, diag, build, IngestConfig())
    assert report.break_reason_counts.get(BREAK_ZERO_DT_CONFLICT) == 1
    assert report.overall_sampling is not None
    assert report.overall_sampling.degenerate_intervals == 1
    assert any("contradiction" in n for n in report.notes)


def test_cleaning_proposal_is_preview_only() -> None:
    rows = [
        ("A", "2026-01-01T00:00:00Z", 0, 0),
        ("A", "2026-01-01T00:00:00Z", 0.5, 0),
        ("A", "2026-01-01T00:01:00Z", 1, 0),
    ]
    feats = []
    for entity, t_iso, lon, lat in rows:
        feats.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": {"entity_id": entity, "timestamp": t_iso,
                           "accuracy_m": 30.0},
        })
    pts, diag = ingest_point_collection(feats, IngestConfig())
    from app.services.moving_object.quality import CleaningProposalConfig, propose_cleaning
    prop = propose_cleaning(
        pts, diag, IngestConfig(),
        CleaningProposalConfig(drop_accuracy_above_m=20.0,
                               duplicate_time_policy="keep_first"),
    )
    assert prop.rows_dropped_low_accuracy == 3
    assert prop.rows_dropped_duplicates == 1
    # all rows carry accuracy 30 > 20 → distinct-row union drops all 3
    assert prop.accepted_after == 0
    assert prop.accepted_before == 3
    assert "NEW ref" in " ".join(prop.notes)
    # preview did NOT mutate the caller's points
    assert len(pts) == 3
