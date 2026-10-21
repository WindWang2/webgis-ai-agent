"""Explorer — quality 引擎评分边界（P4 补强 E5：degraded 联动与分数钳制）。"""
from __future__ import annotations

from datetime import datetime, timedelta

from app.services.explorer.quality_engine import QualityEngine


def _engine() -> QualityEngine:
    return QualityEngine()


def test_temporal_score_fresh_is_high_and_decays() -> None:
    now = datetime(2026, 1, 1)
    fresh = _engine().calc_temporal_score("osm", now)
    stale = _engine().calc_temporal_score("osm", now - timedelta(days=3650))
    assert 0.0 <= fresh <= 1.0 and 0.0 <= stale <= 1.0
    assert fresh >= stale


def test_thematic_score_in_range() -> None:
    score = _engine().calc_thematic_score("poi_restaurant", "餐饮美食", "火锅店搜索")
    assert 0.0 <= score <= 1.0


def test_spatial_score_identical_bbox_is_high() -> None:
    bbox = "116.0,39.6,116.8,40.3"
    score = _engine().calc_spatial_score(bbox, bbox)
    assert score >= 0.9
    disjoint = _engine().calc_spatial_score(bbox, "0.0,0.0,0.1,0.1")
    assert 0.0 <= disjoint < 0.5


def test_field_score_range() -> None:
    a = _engine().calc_field_score(["name", "address", "lat", "lon"], "address")
    assert 0.0 <= a <= 1.0


def test_precision_score_counts_only_resolved() -> None:
    geocoded = [
        {"_geocode_status": "ok"},
        {"_geocode_status": "ok"},
        {"_geocode_status": "failed"},
    ]
    score = _engine().calc_precision_score(geocoded)
    assert 0.0 <= score <= 1.0
    empty = _engine().calc_precision_score([])
    assert empty == 0.0
