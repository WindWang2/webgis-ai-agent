"""Distribution drift & cartographic environment events (spec P3 / ADR-0069).

The embodiment claim being tested: the data world changes independently of the
agent, and the agent is told about it. A refreshed dataset whose distribution
moved invalidates the classification priors derived from the old shape —
including the temporal case (a new time slice must stay comparable with the
project's shared scheme, the core rule for time-series thematic maps).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.core.database import Base
from app.models.project import CartoProjectFact, Project
from app.services.cartography.distribution_drift import (
    DRIFT_RELATIVE_THRESHOLD,
    ENV_CHANGE_CHAR_BUDGET,
    ENV_CHANGE_MARKER,
    detect_distribution_drift,
    distribution_evidence,
    distribution_fingerprint,
    render_env_change_block,
)
from app.services.cartography.project_memory import (
    apply_distribution_drift,
    classification_fingerprint,
    get_active_facts,
    get_pending_env_changes,
    get_shared_classification,
    record_fact,
)
from app.services.spatial_meta_profiler import QUANTILE_POSITIONS, profile_geojson_source


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add(Project(id="p1", name="Proj One"))
    session.commit()
    try:
        yield session
    finally:
        session.close()


def _fc(values, *, nulls=0):
    features = [
        {"type": "Feature",
         "geometry": {"type": "Point", "coordinates": [116.4 + i * 1e-4, 39.9]},
         "properties": {"pop": v}}
        for i, v in enumerate(values)
    ]
    features += [
        {"type": "Feature",
         "geometry": {"type": "Point", "coordinates": [116.5, 39.9]},
         "properties": {"pop": None}}
        for _ in range(nulls)
    ]
    return {"type": "FeatureCollection", "features": features}


# ─── profiler: quantiles + null ratio ────────────────────────────────────

def test_profiler_emits_quantiles_and_null_ratio():
    profile = profile_geojson_source(_fc(list(range(1, 101)), nulls=10))
    field = profile["fields"]["pop"]
    assert len(field["quantiles"]) == len(QUANTILE_POSITIONS)
    # Monotonic, spanning the data range.
    assert field["quantiles"] == sorted(field["quantiles"])
    assert field["quantiles"][0] == 1.0
    assert field["quantiles"][-1] == 100.0
    # median of 1..100 (linear interpolation) is 50.5
    assert field["quantiles"][3] == pytest.approx(50.5, abs=0.01)
    assert field["null_ratio"] == pytest.approx(10 / 110, abs=1e-4)


def test_non_numeric_field_has_no_distribution_evidence():
    fc = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [0, 0]},
         "properties": {"name": "a"}},
    ]}
    field = profile_geojson_source(fc)["fields"]["name"]
    assert "quantiles" not in field
    assert distribution_evidence(field) is None
    assert distribution_fingerprint(field) is None


# ─── drift detection ─────────────────────────────────────────────────────

def _evidence(values, nulls=0):
    return distribution_evidence(
        profile_geojson_source(_fc(values, nulls=nulls))["fields"]["pop"]
    )


def test_identical_distribution_is_stable():
    held = _evidence(list(range(1, 101)))
    verdict = detect_distribution_drift(held, _evidence(list(range(1, 101))))
    assert verdict["evaluated"] is True
    assert verdict["drifted"] is False
    assert verdict["reason"] == "stable"
    assert verdict["max_relative_deviation"] == 0.0


def test_resampled_same_shape_is_not_a_false_positive():
    # Same distribution, different sample size / interleaving: the shape is
    # what matters, so this must NOT be reported as drift.
    held = _evidence(list(range(1, 101)))
    incoming = _evidence([v for v in range(1, 101) for _ in (0, 1)])
    verdict = detect_distribution_drift(held, incoming)
    assert verdict["drifted"] is False


def test_shifted_distribution_drifts():
    held = _evidence(list(range(1, 101)))
    incoming = _evidence([v * 3 for v in range(1, 101)])
    verdict = detect_distribution_drift(held, incoming)
    assert verdict["drifted"] is True
    assert verdict["reason"] == "quantile_shift"
    assert verdict["max_relative_deviation"] > DRIFT_RELATIVE_THRESHOLD


def test_null_ratio_shift_drifts_even_when_shape_holds():
    held = _evidence(list(range(1, 101)))
    incoming = _evidence(list(range(1, 101)), nulls=40)
    verdict = detect_distribution_drift(held, incoming)
    assert verdict["drifted"] is True
    assert verdict["reason"] == "null_ratio_shift"


def test_missing_evidence_is_unevaluable_not_stable():
    # fail-closed: "cannot tell" must never be reported as "no drift".
    verdict = detect_distribution_drift(None, _evidence([1, 2, 3]))
    assert verdict["evaluated"] is False
    assert verdict["drifted"] is False
    assert verdict["reason"] == "insufficient_distribution_evidence"


def test_zero_centred_distribution_does_not_divide_by_zero():
    held = {"quantiles": [0.0] * len(QUANTILE_POSITIONS), "null_ratio": 0.0}
    incoming = {"quantiles": [0.0] * len(QUANTILE_POSITIONS), "null_ratio": 0.0}
    verdict = detect_distribution_drift(held, incoming)
    assert verdict["evaluated"] is True
    assert verdict["drifted"] is False


# ─── ledger integration: drift invalidates classification priors ─────────

_CLS = {"type": "graduated", "field": "pop", "breaks": [1, 25, 50, 100],
        "class_count": 3}


def _seed_project(db, values):
    record_fact(db, "p1", "shared_classification", "pop", _CLS,
                fingerprint=classification_fingerprint(_CLS),
                validity_tier="SEMANTIC_VALID")
    profile = profile_geojson_source(_fc(values))["fields"]["pop"]
    apply_distribution_drift(db, "p1", {"pop": profile})
    db.commit()


def test_first_sighting_records_baseline_without_event(db):
    _seed_project(db, list(range(1, 101)))
    # A first sighting is not a change: no event, and the scheme stays usable.
    assert get_pending_env_changes(db, "p1") == []
    assert get_shared_classification(db, "p1", "pop") is not None


def test_drift_invalidates_scheme_and_emits_event(db):
    _seed_project(db, list(range(1, 101)))
    drifted = profile_geojson_source(_fc([v * 4 for v in range(1, 101)]))["fields"]["pop"]
    events = apply_distribution_drift(db, "p1", {"pop": drifted})
    db.commit()
    assert len(events) == 1
    assert events[0]["subject"] == "pop"
    assert events[0]["invalidated_classifications"] == 1
    # The prior is no longer injectable — breaks are a function of the shape.
    assert get_shared_classification(db, "p1", "pop") is None
    assert get_active_facts(db, "p1") == []


def test_stable_refresh_keeps_the_scheme_active(db):
    _seed_project(db, list(range(1, 101)))
    same = profile_geojson_source(_fc(list(range(1, 101))))["fields"]["pop"]
    events = apply_distribution_drift(db, "p1", {"pop": same})
    db.commit()
    assert events == []
    assert get_shared_classification(db, "p1", "pop") is not None


def test_unevaluable_profile_leaves_state_untouched(db):
    _seed_project(db, list(range(1, 101)))
    events = apply_distribution_drift(db, "p1", {"pop": {"type": "string"}})
    db.commit()
    assert events == []
    assert get_shared_classification(db, "p1", "pop") is not None


def test_env_change_events_are_consumed_once(db):
    _seed_project(db, list(range(1, 101)))
    drifted = profile_geojson_source(_fc([v * 4 for v in range(1, 101)]))["fields"]["pop"]
    apply_distribution_drift(db, "p1", {"pop": drifted})
    db.commit()
    first = get_pending_env_changes(db, "p1")
    db.commit()
    assert len(first) == 1
    # Read-once: the same drift must not nag on every subsequent turn, but the
    # fact stays stale (revival requires re-classification through the gate).
    assert get_pending_env_changes(db, "p1") == []
    stale = db.query(CartoProjectFact).filter(
        CartoProjectFact.kind == "data_profile"
    ).one()
    assert stale.status == "stale"


def test_temporal_slice_incomparable_scheme_is_invalidated(db):
    """Time-series thematic maps: a new slice whose distribution moved cannot
    reuse the project's shared breaks (cross-slice comparability would be a
    lie), so the prior is invalidated and the agent is told."""
    _seed_project(db, [10, 20, 30, 40, 50, 60, 70, 80, 90, 100])
    next_slice = profile_geojson_source(
        _fc([300, 420, 500, 610, 700, 820, 900, 1010, 1100, 1250])
    )["fields"]["pop"]
    events = apply_distribution_drift(db, "p1", {"pop": next_slice})
    db.commit()
    assert len(events) == 1
    assert get_shared_classification(db, "p1", "pop") is None
    block = render_env_change_block(events)
    assert ENV_CHANGE_MARKER in block
    assert "pop" in block
    assert "过期" in block


# ─── bounded injection ───────────────────────────────────────────────────

def test_env_change_block_is_bounded():
    events = [
        {"subject": f"field_{i:03d}", "reason": "quantile_shift", "deviation": 0.42}
        for i in range(80)
    ]
    block = render_env_change_block(events)
    assert len(block) <= ENV_CHANGE_CHAR_BUDGET
    assert "省略" in block


def test_empty_env_change_renders_nothing():
    assert render_env_change_block([]) == ""
    assert render_env_change_block([{"reason": "quantile_shift"}]) == ""
