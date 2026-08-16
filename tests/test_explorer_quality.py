"""Explorer quality engine tests"""
from datetime import datetime, timedelta, timezone
from app.services.explorer.quality_engine import QualityEngine


def test_temporal_score_education():
    engine = QualityEngine()
    # Data from 14 months ago, education type (lambda=0.03)
    published = datetime.now(timezone.utc) - timedelta(days=14 * 30)
    score = engine.calc_temporal_score("education", published)
    # exp(-0.03 * 14) ≈ 0.657
    assert 0.65 <= score <= 0.67


def test_temporal_score_poi():
    engine = QualityEngine()
    published = datetime.now(timezone.utc) - timedelta(days=14 * 30)
    score = engine.calc_temporal_score("poi", published)
    # exp(-0.30 * 14) ≈ 0.015
    assert score < 0.05


def test_temporal_score_default():
    """Unknown data type falls back to default lambda (0.10)"""
    engine = QualityEngine()
    published = datetime.now(timezone.utc) - timedelta(days=14 * 30)
    score = engine.calc_temporal_score("restaurant", published)
    # exp(-0.10 * 14) ≈ 0.247
    assert 0.20 <= score <= 0.30


def test_spatial_score_full_overlap():
    engine = QualityEngine()
    score = engine.calc_spatial_score(
        "39.9,116.2,40.1,116.4",  # data bbox
        "39.9,116.2,40.1,116.4",  # target bbox (same)
    )
    assert score == 1.0


def test_spatial_score_partial_overlap():
    engine = QualityEngine()
    score = engine.calc_spatial_score(
        "39.0,116.0,40.0,117.0",  # data: partial area
        "39.9,116.2,40.1,116.4",  # target: Haidian district (inside data)
    )
    assert 0.0 < score < 1.0


def test_field_score_complete():
    engine = QualityEngine()
    score = engine.calc_field_score(
        expected_fields=["name", "address", "lat", "lon"],
        actual_fields=["name", "address", "lat", "lon", "level"],
    )
    assert score == 1.0


def test_field_score_partial():
    engine = QualityEngine()
    score = engine.calc_field_score(
        expected_fields=["name", "address", "lat", "lon"],
        actual_fields=["name", "address"],
    )
    assert score == 0.5


def test_temporal_score_naive_datetime_does_not_crash():
    """Issue #482: a naive published_at (e.g. from a source that skipped the
    adapter's UTC normalization) must not raise TypeError in the scoring
    path — it is treated as UTC per house convention."""
    engine = QualityEngine()
    published = datetime(2024, 3, 15)  # naive
    score = engine.calc_temporal_score("education", published)
    assert 0.0 <= score <= 1.0


def test_temporal_score_naive_equals_aware_utc():
    """A naive datetime and its UTC-aware equivalent must score identically
    (naive is interpreted as UTC, not local time)."""
    engine = QualityEngine()
    naive = datetime(2024, 3, 15)
    aware = naive.replace(tzinfo=timezone.utc)
    assert engine.calc_temporal_score("education", naive) == engine.calc_temporal_score(
        "education", aware
    )


def test_temporal_score_future_timestamp_is_clamped_sane():
    """Adversarial: a publish time in the future (clock skew / bad data) must
    produce a bounded score, not an error or an explosion."""
    engine = QualityEngine()
    future = datetime.now(timezone.utc) + timedelta(days=365)
    score = engine.calc_temporal_score("education", future)
    assert 0.0 <= score <= 1.0
