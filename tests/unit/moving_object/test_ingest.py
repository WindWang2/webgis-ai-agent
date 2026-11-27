"""WP1 ingest tests: normalization, ordering, duplicates, tz, diagnostics."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.services.moving_object.contracts import (
    DuplicateTimePolicy,
    IngestConfig,
    MovingObjectError,
    SortPolicy,
)
from app.services.moving_object.ingest import (
    group_by_entity,
    ingest_point_collection,
    parse_event_time,
)
from zoneinfo import ZoneInfo


def _fc(*features):
    return {"type": "FeatureCollection", "features": list(features)}


def _feat(entity_id, t, lon, lat, **props):
    p = {"entity_id": entity_id, "timestamp": t}
    p.update(props)
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [lon, lat]},
        "properties": p,
    }


def test_ingest_sorts_stably_by_time_then_input_order() -> None:
    fc = _fc(
        _feat("A", "2026-01-01T00:02:00Z", 2, 0),
        _feat("A", "2026-01-01T00:00:00Z", 0, 0),
        _feat("A", "2026-01-01T00:00:00Z", 0.5, 0),   # same time, later row
        _feat("A", "2026-01-01T00:01:00Z", 1, 0),
    )
    cfg = IngestConfig()
    pts, diag = ingest_point_collection(fc["features"], cfg)
    assert diag.accepted == 4
    assert diag.out_of_order_rows == 1  # counted on input order
    times = [p.t.isoformat() for p in pts]
    assert times == sorted(times)
    # stable tie-break by input order for equal timestamps
    pair = [p for p in pts if p.t.isoformat().startswith("2026-01-01T00:00")]
    assert pair[0].lon == 0.0 and pair[1].lon == 0.5
    assert pair[0].order < pair[1].order


def test_ingest_keep_input_policy_discloses_out_of_order() -> None:
    fc = _fc(
        _feat("A", "2026-01-01T00:02:00Z", 2, 0),
        _feat("A", "2026-01-01T00:00:00Z", 0, 0),
    )
    pts, diag = ingest_point_collection(fc["features"], IngestConfig(
        sort_policy=SortPolicy.KEEP_INPUT,
    ))
    assert diag.out_of_order_rows == 1
    assert any("keep_input" in n for n in diag.notes)


def test_duplicate_time_policy_keep_first_and_last() -> None:
    feats = [
        _feat("A", "2026-01-01T00:00:00Z", 0, 0),
        _feat("A", "2026-01-01T00:00:00Z", 1, 0),
        _feat("A", "2026-01-01T00:00:00Z", 2, 0),
        _feat("A", "2026-01-01T00:01:00Z", 3, 0),
    ]
    pts, diag = ingest_point_collection(feats, IngestConfig(
        duplicate_time_policy=DuplicateTimePolicy.KEEP_FIRST,
    ))
    assert diag.duplicate_time_pairs == 2
    assert len(pts) == 2
    assert pts[0].lon == 0.0

    pts, diag = ingest_point_collection(feats, IngestConfig(
        duplicate_time_policy=DuplicateTimePolicy.KEEP_LAST,
    ))
    assert len(pts) == 2
    assert pts[0].lon == 2.0


def test_keep_all_counts_duplicates_without_dropping() -> None:
    feats = [
        _feat("A", "2026-01-01T00:00:00Z", 0, 0),
        _feat("A", "2026-01-01T00:00:00Z", 0, 0),
    ]
    pts, diag = ingest_point_collection(feats, IngestConfig())
    assert len(pts) == 2
    assert diag.duplicate_time_pairs == 1


def test_naive_time_assumed_timezone_disclosed() -> None:
    feats = [_feat("A", "2026-01-01T08:00:00", 0, 0)]  # naive Beijing wall time
    pts, diag = ingest_point_collection(feats, IngestConfig(
        assumed_timezone="Asia/Shanghai",
    ))
    assert diag.naive_time_rows == 1
    assert pts[0].t == datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    assert any("Asia/Shanghai" in n for n in diag.notes)


def test_dst_fold_zero_semantics_documented() -> None:
    # US spring-forward gap: 2026-03-08 02:30 does not exist locally;
    # zoneinfo fold=0 maps it to EST offset (-5) → 07:30Z. Documented,
    # disclosed, deterministic.
    t, naive = parse_event_time("2026-03-08T02:30:00", ZoneInfo("America/New_York"))
    assert naive is True
    assert t == datetime(2026, 3, 8, 7, 30, tzinfo=timezone.utc)
    # Ambiguous fall-back: 2026-11-01 01:30 fold=0 → EDT (-4) → 05:30Z.
    t, _ = parse_event_time("2026-11-01T01:30:00", ZoneInfo("America/New_York"))
    assert t == datetime(2026, 11, 1, 5, 30, tzinfo=timezone.utc)


def test_epoch_seconds_and_millis() -> None:
    t, naive = parse_event_time(1_700_000_000, ZoneInfo("UTC"))
    assert naive is False
    assert t == datetime(2023, 11, 14, 22, 13, 20, tzinfo=timezone.utc)
    t, _ = parse_event_time(1_700_000_000_000, ZoneInfo("UTC"))
    assert t == datetime(2023, 11, 14, 22, 13, 20, tzinfo=timezone.utc)


def test_row_level_rejections_counted_not_silent() -> None:
    feats = [
        _feat(None, "2026-01-01T00:00:00Z", 0, 0),          # missing id
        _feat("A", "garbage", 1, 0),                        # bad time
        _feat("A", "2026-01-01T00:00:00Z", 200, 0),         # bad lon
        {"type": "Feature", "geometry": {"type": "LineString",
            "coordinates": [[0, 0], [1, 1]]},
         "properties": {"entity_id": "A", "timestamp": "2026-01-01T00:00:00Z"}},
        _feat("A", "2026-01-01T00:00:00Z", 0, 0),
    ]
    pts, diag = ingest_point_collection(feats, IngestConfig())
    assert diag.accepted == 1
    assert diag.missing_entity_id == 1
    assert diag.unparseable_time == 1
    assert diag.missing_or_degenerate_geometry == 2
    assert diag.empty_after_ingest is False


def test_missing_entity_id_when_all_invalid_leaves_empty() -> None:
    feats = [_feat(None, "2026-01-01T00:00:00Z", 0, 0)]
    pts, diag = ingest_point_collection(feats, IngestConfig())
    assert diag.accepted == 0
    assert diag.empty_after_ingest is True


def test_declared_source_row_field_becomes_identity() -> None:
    feats = [
        _feat("A", "2026-01-01T00:00:00Z", 0, 0, source_row="GPS-9901"),
        _feat("A", "2026-01-01T00:01:00Z", 1, 0, source_row="GPS-9902"),
    ]
    pts, diag = ingest_point_collection(feats, IngestConfig(
        source_row_field="source_row",
    ))
    assert [p.source_row_id for p in pts] == ["GPS-9901", "GPS-9902"]
    assert not any("row:" in n for n in diag.notes)


def test_missing_declared_source_row_value_rejects_row() -> None:
    feats = [
        _feat("A", "2026-01-01T00:00:00Z", 0, 0, source_row="GPS-1"),
        _feat("A", "2026-01-01T00:01:00Z", 1, 0),  # missing declared field
    ]
    pts, diag = ingest_point_collection(feats, IngestConfig(
        source_row_field="source_row",
    ))
    assert diag.accepted == 1


def test_over_cap_input_raises_typed() -> None:
    from app.services.moving_object.contracts import MAX_INPUT_ROWS
    with pytest.raises(MovingObjectError):
        ingest_point_collection([{}] * (MAX_INPUT_ROWS + 1), IngestConfig())


def test_invalid_timezone_typed_rejection() -> None:
    with pytest.raises(MovingObjectError):
        ingest_point_collection([], IngestConfig(assumed_timezone="Not/AZone"))


def test_group_by_entity_keeps_first_appearance_order() -> None:
    feats = [
        _feat("B", "2026-01-01T00:00:00Z", 0, 0),
        _feat("A", "2026-01-01T00:01:00Z", 1, 0),
        _feat("B", "2026-01-01T00:02:00Z", 2, 0),
    ]
    pts, _ = ingest_point_collection(feats, IngestConfig())
    groups = group_by_entity(pts)
    assert [g[0] for g in groups] == ["B", "A"]
    assert len(groups[0][1]) == 2


def test_accuracy_m_preserved_and_invalid_dropped() -> None:
    feats = [
        _feat("A", "2026-01-01T00:00:00Z", 0, 0, accuracy_m=5.0),
        _feat("A", "2026-01-01T00:01:00Z", 1, 0, accuracy_m=-1),   # invalid
        _feat("A", "2026-01-01T00:02:00Z", 2, 0),                  # absent
    ]
    pts, _ = ingest_point_collection(feats, IngestConfig())
    assert pts[0].accuracy_m == 5.0
    assert pts[1].accuracy_m is None
    assert pts[2].accuracy_m is None
