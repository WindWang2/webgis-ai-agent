"""WP3 region-event tests: decision table, holes, dateline, honesty."""
from __future__ import annotations

import pytest

from app.services.moving_object.contracts import (
    IngestConfig,
    RegionEventConfig,
)
from app.services.moving_object.events import (
    RegionGeometryError,
    analyze_region_events,
    event_features,
    load_regions,
    region_summaries,
)
from app.services.moving_object.ingest import ingest_point_collection
from app.services.moving_object.oracles import (
    dateline_oracle,
    hole_oracle,
    sparse_region_skip_oracle,
)


def _ingest(fc):
    pts, diag = ingest_point_collection(fc["features"], IngestConfig())
    assert diag.accepted == len(fc["features"])
    return pts


def _square_region(rid="SQ", x0=0.0, y0=0.0, x1=10.0, y1=10.0):
    return {
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "properties": {"region_id": rid},
            "geometry": {"type": "Polygon", "coordinates": [[
                [x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0],
            ]]},
        }],
    }


def test_enter_exit_brackets_are_inferred_intervals() -> None:
    fc = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": {"type": "Point",
            "coordinates": [-5.0, 5.0]},
         "properties": {"entity_id": "T", "timestamp": "2026-01-01T00:00:00Z"}},
        {"type": "Feature", "geometry": {"type": "Point",
            "coordinates": [5.0, 5.0]},
         "properties": {"entity_id": "T", "timestamp": "2026-01-01T00:10:00Z"}},
        {"type": "Feature", "geometry": {"type": "Point",
            "coordinates": [15.0, 5.0]},
         "properties": {"entity_id": "T", "timestamp": "2026-01-01T00:20:00Z"}},
    ]}
    pts = _ingest(fc)
    regions = load_regions(_square_region(), RegionEventConfig(regions_ref="r"))
    analysis = analyze_region_events(
        pts, regions, RegionEventConfig(regions_ref="r"))
    kinds = [ev.event_type for ev in analysis.events]
    assert kinds == ["enter", "exit"]
    enter, exit_ = analysis.events
    # enter: observed inside at 00:10; crossing happened in (00:00, 00:10]
    assert enter.inferred is True
    assert enter.t_exact is None
    assert enter.t_interval_start == "2026-01-01T00:00:00+00:00"
    assert enter.t_interval_end == "2026-01-01T00:10:00+00:00"
    # exit: last observed inside at 00:10 → bracket [00:10, 00:20)
    assert exit_.inferred is True
    assert exit_.t_interval_start == "2026-01-01T00:10:00+00:00"
    assert exit_.t_interval_end == "2026-01-01T00:20:00+00:00"
    assert exit_.sequence == 2


def test_first_fix_inside_is_disclosed_enter() -> None:
    fc = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": {"type": "Point",
            "coordinates": [5.0, 5.0]},
         "properties": {"entity_id": "T", "timestamp": "2026-01-01T00:00:00Z"}},
    ]}
    pts = _ingest(fc)
    regions = load_regions(_square_region(), RegionEventConfig(regions_ref="r"))
    analysis = analyze_region_events(
        pts, regions, RegionEventConfig(regions_ref="r"))
    assert [ev.event_type for ev in analysis.events] == ["enter"]
    ev = analysis.events[0]
    assert ev.inferred is True
    assert ev.t_interval_start is None  # state began before observations
    assert ev.t_interval_end == "2026-01-01T00:00:00+00:00"
    assert ev.from_row is None


def test_boundary_rule_closed_counts_edge_inside_open_excludes() -> None:
    fc = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": {"type": "Point",
            "coordinates": [0.0, 5.0]},  # exactly on west edge
         "properties": {"entity_id": "T", "timestamp": "2026-01-01T00:00:00Z"}},
    ]}
    pts = _ingest(fc)
    cfg_closed = RegionEventConfig(regions_ref="r", boundary_rule="closed")
    regions = load_regions(_square_region(), cfg_closed)
    a_closed = analyze_region_events(pts, regions, cfg_closed)
    assert a_closed.events[0].event_type == "enter"

    cfg_open = RegionEventConfig(regions_ref="r", boundary_rule="open")
    regions_open = load_regions(_square_region(), cfg_open)
    a_open = analyze_region_events(pts, regions_open, cfg_open)
    assert a_open.events == []
    # rule disclosed in notes
    assert any("boundary" in n for n in a_open.notes)


def test_sparse_skip_yields_cross_only() -> None:
    fc, region, truth = sparse_region_skip_oracle()
    pts = _ingest(fc)
    cfg = RegionEventConfig(regions_ref="r", detect_dwell=False)
    regions = load_regions(region, cfg)
    analysis = analyze_region_events(pts, regions, cfg)
    kinds = [ev.event_type for ev in analysis.events]
    assert kinds == truth["events_expected"]
    cross = analysis.events[0]
    assert cross.t_exact is None
    assert cross.inferred is True
    assert cross.lon is None  # no observed fix position for a cross


def test_cross_disabled_when_detect_cross_false() -> None:
    fc, region, _ = sparse_region_skip_oracle()
    pts = _ingest(fc)
    cfg = RegionEventConfig(regions_ref="r", detect_cross=False)
    regions = load_regions(region, cfg)
    analysis = analyze_region_events(pts, regions, cfg)
    assert analysis.events == []


def test_hole_pass_through_events() -> None:
    fc, region, truth = hole_oracle()
    pts = _ingest(fc)
    cfg = RegionEventConfig(regions_ref="r", detect_dwell=False)
    regions = load_regions(region, cfg)
    analysis = analyze_region_events(pts, regions, cfg)
    kinds = [ev.event_type for ev in analysis.events]
    assert kinds == truth["events_expected"]
    # no cross events fired on this densely sampled run
    assert "cross" not in kinds


def test_region_dwell_span_from_consecutive_inside_fixes() -> None:
    fc = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": {"type": "Point",
            "coordinates": [1.0, 5.0]},
         "properties": {"entity_id": "T", "timestamp": "2026-01-01T00:00:00Z"}},
        {"type": "Feature", "geometry": {"type": "Point",
            "coordinates": [2.0, 5.0]},
         "properties": {"entity_id": "T", "timestamp": "2026-01-01T00:10:00Z"}},
        {"type": "Feature", "geometry": {"type": "Point",
            "coordinates": [15.0, 5.0]},
         "properties": {"entity_id": "T", "timestamp": "2026-01-01T00:20:00Z"}},
    ]}
    pts = _ingest(fc)
    cfg = RegionEventConfig(regions_ref="r", detect_dwell=True,
                            min_dwell_s=300)
    regions = load_regions(_square_region(), cfg)
    analysis = analyze_region_events(pts, regions, cfg)
    kinds = [ev.event_type for ev in analysis.events]
    assert "dwell" in kinds
    dwell = next(ev for ev in analysis.events if ev.event_type == "dwell")
    # dwell span bounds are exact observation times, not inferred
    assert dwell.inferred is False
    assert dwell.t_exact == "2026-01-01T00:00:00+00:00"
    assert dwell.t_interval_end == "2026-01-01T00:10:00+00:00"


def test_region_dwell_below_min_dwell_s_not_emitted() -> None:
    fc = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": {"type": "Point",
            "coordinates": [1.0, 5.0]},
         "properties": {"entity_id": "T", "timestamp": "2026-01-01T00:00:00Z"}},
        {"type": "Feature", "geometry": {"type": "Point",
            "coordinates": [2.0, 5.0]},
         "properties": {"entity_id": "T", "timestamp": "2026-01-01T00:02:00Z"}},
        {"type": "Feature", "geometry": {"type": "Point",
            "coordinates": [15.0, 5.0]},
         "properties": {"entity_id": "T", "timestamp": "2026-01-01T00:04:00Z"}},
    ]}
    pts = _ingest(fc)
    cfg = RegionEventConfig(regions_ref="r", detect_dwell=True,
                            min_dwell_s=600)
    regions = load_regions(_square_region(), cfg)
    analysis = analyze_region_events(pts, regions, cfg)
    assert not any(ev.event_type == "dwell" for ev in analysis.events)
    # but enter/exit still fire
    assert {ev.event_type for ev in analysis.events} == {"enter", "exit"}


def test_time_window_restricts_analysis() -> None:
    fc = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": {"type": "Point",
            "coordinates": [5.0, 5.0]},
         "properties": {"entity_id": "T", "timestamp": "2026-01-01T00:00:00Z"}},
        {"type": "Feature", "geometry": {"type": "Point",
            "coordinates": [15.0, 5.0]},
         "properties": {"entity_id": "T", "timestamp": "2026-01-01T01:00:00Z"}},
    ]}
    from app.services.moving_object.contracts import TimeWindow
    pts = _ingest(fc)
    cfg = RegionEventConfig(regions_ref="r")
    regions = load_regions(_square_region(), cfg)
    window = TimeWindow(
        start=None,
        end=__import__("datetime").datetime(
            2026, 1, 1, 0, 30, tzinfo=__import__("datetime").timezone.utc),
    )
    analysis = analyze_region_events(pts, regions, cfg, window=window)
    # second fix excluded → still inside at end of window; no exit fires
    assert [ev.event_type for ev in analysis.events] == ["enter"]


def test_dateline_crossing_track() -> None:
    fc, region, truth = dateline_oracle()
    pts = _ingest(fc)
    cfg = RegionEventConfig(regions_ref="r", detect_dwell=False)
    regions = load_regions(region, cfg)
    analysis = analyze_region_events(pts, regions, cfg)
    kinds = [ev.event_type for ev in analysis.events]
    assert kinds == truth["events_expected"]
    assert truth["no_cross"] and "cross" not in kinds


def test_multipolygon_regions_supported() -> None:
    region = {
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "properties": {"region_id": "TWO-PARTS"},
            "geometry": {"type": "MultiPolygon", "coordinates": [
                [[[0.0, 0.0], [10.0, 0.0], [10.0, 10.0], [0.0, 10.0],
                  [0.0, 0.0]]],
                [[[100.0, 0.0], [110.0, 0.0], [110.0, 10.0],
                  [100.0, 10.0], [100.0, 0.0]]],
            ]},
        }],
    }
    fc = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": {"type": "Point",
            "coordinates": [105.0, 5.0]},
         "properties": {"entity_id": "T", "timestamp": "2026-01-01T00:00:00Z"}},
    ]}
    pts = _ingest(fc)
    cfg = RegionEventConfig(regions_ref="r")
    regions = load_regions(region, cfg)
    analysis = analyze_region_events(pts, regions, cfg)
    assert [ev.event_type for ev in analysis.events] == ["enter"]


def test_invalid_region_geometry_typed_rejection() -> None:
    region = {
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "properties": {"region_id": "BAD"},
            "geometry": {"type": "Point", "coordinates": [0, 0]},
        }],
    }
    with pytest.raises(RegionGeometryError):
        load_regions(region, RegionEventConfig(regions_ref="r"))


def test_missing_region_id_typed_rejection() -> None:
    region = {
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "properties": {},
            "geometry": {"type": "Polygon", "coordinates": [[
                [0, 0], [1, 0], [1, 1], [0, 0]]]},
        }],
    }
    with pytest.raises(RegionGeometryError):
        load_regions(region, RegionEventConfig(regions_ref="r"))


def test_dateline_extent_polygon_rejected() -> None:
    region = {
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "properties": {"region_id": "WORLD"},
            "geometry": {"type": "Polygon", "coordinates": [[
                [-170.0, -10.0], [170.0, -10.0], [170.0, 10.0],
                [-170.0, 10.0], [-170.0, -10.0]]]},
        }],
    }
    with pytest.raises(RegionGeometryError):
        load_regions(region, RegionEventConfig(regions_ref="r"))


def test_region_summaries_honest_denominators() -> None:
    fc, region, _ = sparse_region_skip_oracle()
    pts = _ingest(fc)
    cfg = RegionEventConfig(regions_ref="r")
    regions = load_regions(region, cfg)
    analysis = analyze_region_events(pts, regions, cfg)
    rows = region_summaries(analysis)
    assert len(rows) == 1
    row = rows[0]
    assert row.n_enter == 0
    assert row.n_cross == 1
    assert row.entities_zero_hit == 1  # the entity never had an inside fix
    assert row.total_dwell_s == 0.0


def test_summary_by_type_counts() -> None:
    fc = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": {"type": "Point",
            "coordinates": [-5.0, 5.0]},
         "properties": {"entity_id": "T", "timestamp": "2026-01-01T00:00:00Z"}},
        {"type": "Feature", "geometry": {"type": "Point",
            "coordinates": [5.0, 5.0]},
         "properties": {"entity_id": "T", "timestamp": "2026-01-01T00:10:00Z"}},
        {"type": "Feature", "geometry": {"type": "Point",
            "coordinates": [15.0, 5.0]},
         "properties": {"entity_id": "T", "timestamp": "2026-01-01T00:20:00Z"}},
    ]}
    pts = _ingest(fc)
    regions = load_regions(_square_region(), RegionEventConfig(regions_ref="r"))
    analysis = analyze_region_events(
        pts, regions, RegionEventConfig(regions_ref="r"))
    summary = analysis.to_summary()
    assert summary["by_type"] == {"enter": 1, "exit": 1}
    assert summary["inferred_events"] == 2
    assert summary["n_events"] == 2


def test_event_features_bounded_and_typed() -> None:
    fc, region, _ = sparse_region_skip_oracle()
    pts = _ingest(fc)
    regions = load_regions(region, RegionEventConfig(regions_ref="r"))
    analysis = analyze_region_events(pts, regions,
                                     RegionEventConfig(regions_ref="r"))
    feats = event_features(analysis.events)
    # cross events have no observed fix position → no map feature
    assert feats == []
