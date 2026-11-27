"""R1 review closure tests: the behaviors fixed in response to the
independent line-by-line review (REVIEW_R1.md).

- R1-1  typed rejections (region geometry / scale caps / window bounds /
        bad enum) surface as TrajectoryServiceError with distinct codes,
        never raw exceptions (REST face maps them to 4xx, not 500)
- R1-2  product family identity is the stable binding (dataset+fields+
        regions), not the windowed content
- R1-3  dwell runs split on inter-fix gaps > max_gap_s (no fabricated
        30-day dwell from two fixes); region dwell same bound
- R1-4  region summary denominators close: n_entities counts only
        inside-fix entities; n_entities + entities_zero_hit == n_analyzed
- R1-8  zero-path segments are actually excluded from line features
- R1-10 constant speed domain uses distinct-value semantics
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from app.services.moving_object.builder import trajectory_line_features
from app.services.moving_object.contracts import (
    DwellConfig,
    IngestConfig,
    RegionEventConfig,
    SegmentationConfig,
)
from app.services.moving_object.dwell import detect_dwells
from app.services.moving_object.events import (
    analyze_region_events,
    load_regions,
    region_summaries,
)
from app.services.moving_object.ingest import ingest_point_collection
from app.services.moving_object.presentation import compute_speed_domain
from app.services.moving_object.service import (
    TrajectoryProductConfig,
    TrajectoryServiceError,
    preview_trajectory_product,
)
from app.services.session_data import session_data_manager
import uuid


T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _feat(entity, t, lon, lat, **props):
    p = {"entity_id": entity, "timestamp": t}
    p.update(props)
    return {"type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": p}


# ── R1-1 typed rejection codes ───────────────────────────────────────────


def test_bad_enum_string_is_validation_error_not_silent() -> None:
    with pytest.raises(ValidationError):
        TrajectoryProductConfig(dataset="ref:x", duplicate_time_policy="bogus")
    with pytest.raises(ValidationError):
        TrajectoryProductConfig(dataset="ref:x", boundary_rule="sideways")


@pytest.mark.asyncio
async def test_region_geometry_error_maps_to_typed_service_error() -> None:
    sid = f"r1-region-{uuid.uuid4().hex[:8]}"
    fc = {"type": "FeatureCollection", "features": [
        _feat("A", "2026-01-01T00:00:00Z", 0, 0),
        _feat("A", "2026-01-01T00:01:00Z", 0.001, 0),
    ]}
    ref = await session_data_manager.store(sid, fc, prefix="geojson")
    await session_data_manager.set_alias(sid, ref, "pts")
    bad_regions = {"type": "FeatureCollection", "features": [{
        "type": "Feature", "properties": {"region_id": "BAD"},
        "geometry": {"type": "Point", "coordinates": [0, 0]},
    }]}
    zone = await session_data_manager.store(sid, bad_regions, prefix="geojson")
    await session_data_manager.set_alias(sid, zone, "zones")
    with pytest.raises(TrajectoryServiceError) as exc:
        await preview_trajectory_product(sid, TrajectoryProductConfig(
            dataset="pts", regions_ref="zones"))
    assert exc.value.code == "TRAJECTORY_REGION_INVALID"
    await session_data_manager.clear_session(sid)


@pytest.mark.asyncio
async def test_unparseable_window_bound_is_typed_rejection() -> None:
    sid = f"r1-window-{uuid.uuid4().hex[:8]}"
    fc = {"type": "FeatureCollection", "features": [
        _feat("A", "2026-01-01T00:00:00Z", 0, 0),
        _feat("A", "2026-01-01T00:01:00Z", 0.001, 0),
    ]}
    ref = await session_data_manager.store(sid, fc, prefix="geojson")
    await session_data_manager.set_alias(sid, ref, "pts")
    with pytest.raises(TrajectoryServiceError) as exc:
        await preview_trajectory_product(sid, TrajectoryProductConfig(
            dataset="pts", time_start="not-a-time"))
    assert exc.value.code == "TRAJECTORY_WINDOW_INVALID"
    await session_data_manager.clear_session(sid)


@pytest.mark.asyncio
async def test_invalid_timezone_is_typed_rejection_not_500() -> None:
    """R2 follow-up: invalid assumed_timezone (with or without a window)
    must surface as TRAJECTORY_TIMEZONE_INVALID, never a raw 500."""
    sid = f"r1-tz-{uuid.uuid4().hex[:8]}"
    fc = {"type": "FeatureCollection", "features": [
        _feat("A", "2026-01-01T00:00:00Z", 0, 0),
        _feat("A", "2026-01-01T00:01:00Z", 0.001, 0),
    ]}
    ref = await session_data_manager.store(sid, fc, prefix="geojson")
    await session_data_manager.set_alias(sid, ref, "pts")
    with pytest.raises(TrajectoryServiceError) as exc_window:
        await preview_trajectory_product(sid, TrajectoryProductConfig(
            dataset="pts", assumed_timezone="Not/AZone",
            time_start="2026-01-01T00:00:00+00:00"))
    assert exc_window.value.code == "TRAJECTORY_TIMEZONE_INVALID"
    # No window: the rejection comes from ingest and is classified as
    # INPUT_INVALID — still a typed 4xx, never a raw 500 (R2-1 concern).
    with pytest.raises(TrajectoryServiceError) as exc_plain:
        await preview_trajectory_product(sid, TrajectoryProductConfig(
            dataset="pts", assumed_timezone="Not/AZone"))
    assert exc_plain.value.code in (
        "TRAJECTORY_TIMEZONE_INVALID", "TRAJECTORY_INPUT_INVALID")
    await session_data_manager.clear_session(sid)


@pytest.mark.asyncio
async def test_scale_cap_maps_to_typed_service_error(monkeypatch) -> None:
    """Lowering the region-event cap must surface as a typed service error
    (TRAJECTORY_SCALE_EXCEEDED), not a raw MovingObjectError → 500."""
    from app.services.moving_object import events as ev_mod
    sid = f"r1-cap-{uuid.uuid4().hex[:8]}"
    # Many entities each entering the region once: events exceed a low cap.
    feats = []
    for i in range(10):
        feats.append(_feat(f"E{i:02d}", "2026-01-01T00:00:00Z", -1, 0.5))
        feats.append(_feat(f"E{i:02d}", "2026-01-01T00:01:00Z", 0.5, 0.5))
        feats.append(_feat(f"E{i:02d}", "2026-01-01T00:02:00Z", 2, 0.5))
    ref = await session_data_manager.store(
        sid, {"type": "FeatureCollection", "features": feats}, prefix="geojson")
    await session_data_manager.set_alias(sid, ref, "pts")
    region = {"type": "FeatureCollection", "features": [{
        "type": "Feature", "properties": {"region_id": "SQ"},
        "geometry": {"type": "Polygon", "coordinates": [[
            [0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]},
    }]}
    zone = await session_data_manager.store(sid, region, prefix="geojson")
    await session_data_manager.set_alias(sid, zone, "zones")
    orig = ev_mod.MAX_EVENTS
    monkeypatch.setattr(ev_mod, "MAX_EVENTS", 3)
    try:
        with pytest.raises(TrajectoryServiceError) as exc_info:
            await preview_trajectory_product(
                sid, TrajectoryProductConfig(dataset="pts", regions_ref="zones"))
    finally:
        monkeypatch.setattr(ev_mod, "MAX_EVENTS", orig)
    assert exc_info.value.code == "TRAJECTORY_SCALE_EXCEEDED"
    await session_data_manager.clear_session(sid)


# ── R1-2 stable family identity ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_family_identity_is_binding_not_window() -> None:
    from app.services.moving_object.service import _family_id
    f1 = _family_id("ref:geojson-abc", "entity_id", "timestamp", None)
    f2 = _family_id("ref:geojson-abc", "entity_id", "timestamp", None)
    f3 = _family_id("ref:geojson-abc", "entity_id", "timestamp", "ref:geojson-z")
    f4 = _family_id("ref:geojson-other", "entity_id", "timestamp", None)
    assert f1 == f2            # binding identity stable across calls
    assert f1 != f3            # regions binding participates
    assert f1 != f4            # dataset binding participates


# ── R1-3 dwell gap splitting ─────────────────────────────────────────────


def test_dwell_run_splits_on_gap_no_fabricated_long_duration() -> None:
    # Two coincident fixes 30 days apart: without the gap bound this would
    # fabricate a 2,592,000 s dwell.
    t2 = T0 + timedelta(days=30)
    feats = [
        _feat("A", T0.isoformat(), 0, 0),
        _feat("A", t2.isoformat(), 0, 0),
    ]
    pts, _ = ingest_point_collection(feats, IngestConfig())
    build = detect_dwells(pts, DwellConfig(radius_m=50, min_dwell_s=120,
                                           min_points=2, max_gap_s=3600))
    assert not build.events
    assert build.diagnostics.near_miss_runs == 2  # two 1-fix runs


def test_dwell_within_gap_stays_merged() -> None:
    feats = [
        _feat("A", T0.isoformat(), 0, 0),
        _feat("A", (T0 + timedelta(minutes=10)).isoformat(), 0, 0),
        _feat("A", (T0 + timedelta(minutes=20)).isoformat(), 0, 0),
    ]
    pts, _ = ingest_point_collection(feats, IngestConfig())
    build = detect_dwells(pts, DwellConfig(radius_m=50, min_dwell_s=120,
                                           min_points=2, max_gap_s=3600))
    assert len(build.events) == 1
    assert build.events[0].duration_s == 1200.0


def test_region_dwell_splits_on_gap() -> None:
    from app.services.moving_object.contracts import RegionEventConfig
    from app.services.moving_object.events import RegionSet  # noqa: F401
    region = {"type": "FeatureCollection", "features": [{
        "type": "Feature", "properties": {"region_id": "SQ"},
        "geometry": {"type": "Polygon", "coordinates": [[
            [0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]},
    }]}
    feats = [
        _feat("A", T0.isoformat(), 0.5, 0.5),
        _feat("A", (T0 + timedelta(days=30)).isoformat(), 0.5, 0.5),
        _feat("A", (T0 + timedelta(days=60)).isoformat(), 0.5, 0.5),
    ]
    pts, _ = ingest_point_collection(feats, IngestConfig())
    cfg = RegionEventConfig(regions_ref="r", detect_dwell=True,
                            min_dwell_s=600, dwell_max_gap_s=3600)
    regions = load_regions(region, cfg)
    analysis = analyze_region_events(pts, regions, cfg)
    dwells = [e for e in analysis.events if e.event_type == "dwell"]
    assert not dwells  # each 30-day span exceeds the gap bound


# ── R1-4 denominator closure ─────────────────────────────────────────────


def test_region_summary_denominators_close() -> None:
    region = {"type": "FeatureCollection", "features": [{
        "type": "Feature", "properties": {"region_id": "SQ"},
        "geometry": {"type": "Polygon", "coordinates": [[
            [0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]},
    }]}
    # T-in: two fixes inside; C-only: crosses the region between fixes;
    # O-out: never touches it (west of region).
    feats = [
        _feat("T-in", T0.isoformat(), 0.5, 0.5),
        _feat("T-in", (T0 + timedelta(minutes=1)).isoformat(), 0.5, 0.5),
        _feat("C-only", T0.isoformat(), -0.5, 0.5),
        _feat("C-only", (T0 + timedelta(minutes=1)).isoformat(), 1.5, 0.5),
        _feat("O-out", T0.isoformat(), -2.0, 0.5),
        _feat("O-out", (T0 + timedelta(minutes=1)).isoformat(), -1.5, 0.5),
    ]
    pts, _ = ingest_point_collection(feats, IngestConfig())
    cfg = RegionEventConfig(regions_ref="r", detect_dwell=False)
    regions = load_regions(region, cfg)
    analysis = analyze_region_events(pts, regions, cfg)
    rows = region_summaries(analysis)
    assert len(rows) == 1
    row = rows[0]
    assert row.n_entities == 1                       # only T-in had inside fixes
    # T-in's first fix is already inside → disclosed enter; C-only crosses.
    assert row.n_enter == 1 and row.n_cross == 1
    assert row.n_exit == 0
    assert row.entities_zero_hit == 2                # C-only + O-out
    assert row.n_entities + row.entities_zero_hit == analysis.n_entities


# ── R1-8 zero-path segments excluded ─────────────────────────────────────


def test_zero_path_segments_excluded_from_line_features() -> None:
    from app.services.moving_object.builder import build_entity_trajectories
    feats = [
        _feat("A", T0.isoformat(), 0, 0),
        _feat("A", (T0 + timedelta(minutes=1)).isoformat(), 0.001, 0),
        # coincident duplicate-time island: run of 2 with zero path
        _feat("B", T0.isoformat(), 5, 5),
        _feat("B", (T0 + timedelta(hours=2)).isoformat(), 5, 5),
    ]
    pts, _ = ingest_point_collection(feats, IngestConfig())
    build = build_entity_trajectories(pts, SegmentationConfig(), IngestConfig())
    line_feats = trajectory_line_features(build)
    entity_ids = {f["properties"]["entity_id"] for f in line_feats}
    assert "B" not in entity_ids
    assert "A" in entity_ids


# ── R1-10 distinct speed domain ──────────────────────────────────────────


def test_speed_domain_constant_on_identical_speeds() -> None:
    domain, constant = compute_speed_domain([36.0, 36.0, 36.0])
    assert constant is True
    assert domain == [36.0, 36.0]
