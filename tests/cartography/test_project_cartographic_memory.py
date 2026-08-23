"""Project-scoped cartographic memory (ADR-0069 / spec Phase 2).

Acceptance tests for the fact ledger: shared-classification reuse across
sessions, fail-closed conflict semantics, bounded injection, harvest gating
on a passing verdict, and the no-project zero-behaviour-change guarantee.
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
from app.services.cartography.project_memory import (
    MAX_FACTS_PER_PROJECT,
    MEMORY_BLOCK_CHAR_BUDGET,
    MEMORY_MARKER,
    classification_fingerprint,
    get_active_facts,
    get_shared_classification,
    harvest_facts_from_review,
    mark_stale,
    record_fact,
    render_memory_block,
)


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add(Project(id="p1", name="Proj One"))
    session.add(Project(id="p2", name="Proj Two"))
    session.commit()
    try:
        yield session
    finally:
        session.close()


_CLS_V1 = {
    "type": "graduated", "field": "population",
    "breaks": [0, 100, 500, 1000], "class_count": 4,
}
_CLS_V2 = {
    "type": "graduated", "field": "population",
    "breaks": [0, 200, 800], "class_count": 3,
}


def _fp(payload):
    return classification_fingerprint(payload)


# ─── fingerprint semantics ───────────────────────────────────────────────

def test_fingerprint_tracks_classification_not_color():
    # Phase 1's change_palette repair swaps colors only; a recolored scheme
    # is the SAME classification and must not look like a conflict.
    recolored = {**_CLS_V1, "colors": ["#440154", "#31688e", "#35b779", "#fde725"]}
    assert _fp(recolored) == _fp(_CLS_V1)
    # Different breaks are a different scheme.
    assert _fp(_CLS_V2) != _fp(_CLS_V1)


# ─── reuse across sessions (the series-map requirement) ──────────────────

def test_shared_classification_is_reusable_across_sessions(db):
    record_fact(db, "p1", "shared_classification", "population", _CLS_V1,
                fingerprint=_fp(_CLS_V1), validity_tier="SEMANTIC_VALID")
    db.commit()
    # A later, unrelated session in the same project reads the same scheme.
    fact = get_shared_classification(db, "p1", "population")
    assert fact is not None
    assert fact.payload["breaks"] == [0, 100, 500, 1000]
    assert fact.status == "active"


def test_memory_is_project_scoped(db):
    record_fact(db, "p1", "preference", "palette", {"value": "dark"})
    db.commit()
    # ADR-0069 decision 1: no cross-project cartographic craft library.
    assert get_active_facts(db, "p2") == []
    assert get_shared_classification(db, "p2", "population") is None


# ─── conflict semantics (fail-closed) ────────────────────────────────────

def test_conflicting_classification_is_flagged_not_overwritten(db):
    record_fact(db, "p1", "shared_classification", "population", _CLS_V1,
                fingerprint=_fp(_CLS_V1))
    db.commit()
    fact = record_fact(db, "p1", "shared_classification", "population", _CLS_V2,
                      fingerprint=_fp(_CLS_V2))
    db.commit()
    # Held scheme survives; the divergence is explicit and auditable.
    assert fact.status == "conflicted"
    assert fact.payload["breaks"] == [0, 100, 500, 1000]
    assert fact.payload["_conflict"]["incoming_fingerprint"] == _fp(_CLS_V2)
    # A conflicted fact is never injected.
    assert get_shared_classification(db, "p1", "population") is None
    assert get_active_facts(db, "p1") == []


def test_explicit_supersede_upgrades_the_scheme(db):
    record_fact(db, "p1", "shared_classification", "population", _CLS_V1,
                fingerprint=_fp(_CLS_V1))
    db.commit()
    fact = record_fact(db, "p1", "shared_classification", "population", _CLS_V2,
                       fingerprint=_fp(_CLS_V2), supersede=True,
                       validity_tier="SEMANTIC_VALID")
    db.commit()
    assert fact.status == "active"
    assert fact.payload["breaks"] == [0, 200, 800]


def test_reverify_same_fingerprint_stays_active(db):
    record_fact(db, "p1", "shared_classification", "population", _CLS_V1,
                fingerprint=_fp(_CLS_V1))
    db.commit()
    fact = record_fact(db, "p1", "shared_classification", "population", _CLS_V1,
                       fingerprint=_fp(_CLS_V1))
    db.commit()
    assert fact.status == "active"
    # Upsert identity: (project, kind, subject) — not an append-per-turn log.
    assert db.query(CartoProjectFact).count() == 1


def test_unknown_kind_is_rejected(db):
    assert record_fact(db, "p1", "not_a_kind", "x", {"value": 1}) is None
    assert db.query(CartoProjectFact).count() == 0


# ─── stale invalidation (Phase 3 entry point) ────────────────────────────

def test_mark_stale_removes_facts_from_injection(db):
    record_fact(db, "p1", "shared_classification", "population", _CLS_V1,
                fingerprint=_fp(_CLS_V1))
    record_fact(db, "p1", "preference", "palette", {"value": "dark"})
    db.commit()
    assert mark_stale(db, "p1", kinds=["shared_classification"]) == 1
    db.commit()
    kinds = sorted(f.kind for f in get_active_facts(db, "p1"))
    assert kinds == ["preference"]


# ─── bounded ledger + bounded injection ──────────────────────────────────

def test_ledger_is_bounded_per_project(db):
    for i in range(MAX_FACTS_PER_PROJECT + 15):
        record_fact(db, "p1", "preference", f"pref-{i:04d}", {"value": i})
    db.commit()
    assert db.query(CartoProjectFact).filter(
        CartoProjectFact.project_id == "p1"
    ).count() == MAX_FACTS_PER_PROJECT


def test_injection_block_is_bounded_and_labels_priors(db):
    for i in range(120):
        record_fact(db, "p1", "preference", f"pref-{i:03d}",
                    {"value": f"value-{i}-{'x' * 30}"})
    db.commit()
    block = render_memory_block(get_active_facts(db, "p1", limit=120))
    assert len(block) <= MEMORY_BLOCK_CHAR_BUDGET
    assert MEMORY_MARKER in block
    # ADR-0069 decision 2: the block must present itself as a prior, not as
    # this turn's verdict.
    assert "不是本次的评审结论" in block
    assert "省略" in block


def test_empty_and_stale_only_ledger_injects_nothing(db):
    assert render_memory_block([]) == ""
    record_fact(db, "p1", "preference", "palette", {"value": "dark"})
    db.commit()
    mark_stale(db, "p1")
    db.commit()
    assert render_memory_block(get_active_facts(db, "p1")) == ""


def test_shared_classification_renders_first(db):
    record_fact(db, "p1", "recipe_outcome", "choropleth", {"value": "ok"},
                validity_tier="SEMANTIC_VALID")
    record_fact(db, "p1", "preference", "palette", {"value": "dark"})
    record_fact(db, "p1", "shared_classification", "population", _CLS_V1,
                fingerprint=_fp(_CLS_V1))
    db.commit()
    block = render_memory_block(get_active_facts(db, "p1"))
    lines = [line for line in block.splitlines() if line.startswith("- ")]
    assert "共享分类方案" in lines[0]


# ─── harvest gating: memory lags evidence, never leads it ────────────────

_MAPSPEC = {
    "version": "1.0",
    "sources": {"s1": {"type": "geojson", "ref": "ref:geojson-x"}},
    "layers": [{
        "id": "l1", "source": "s1", "type": "fill",
        "legend_spec": {
            "type": "graduated", "field": "population",
            "breaks": [0, 100, 500, 1000],
            "palette_colors": ["#fee5d9", "#fcae91", "#fb6a4a", "#a50f15"],
        },
    }],
}


def test_harvest_writes_only_on_a_passing_verdict(db):
    passing = {"overall_passed": True, "cartography": {"status": "passed"}}
    assert harvest_facts_from_review(db, "p1", _MAPSPEC, passing) == 1
    db.commit()
    fact = get_shared_classification(db, "p1", "population")
    assert fact is not None
    assert fact.payload["breaks"] == [0, 100, 500, 1000]
    # Colors are NOT part of the remembered classification semantics.
    assert "palette_colors" not in fact.payload


@pytest.mark.parametrize("review", [
    None,
    {},
    {"overall_passed": False, "cartography": {"status": "failed_repairable"}},
    {"cartography": {"status": "not_evaluated"}},
    {"cartography": {"status": "repair_exhausted"}},
])
def test_harvest_is_fail_closed_on_non_passing_reviews(db, review):
    assert harvest_facts_from_review(db, "p1", _MAPSPEC, review) == 0
    db.commit()
    assert db.query(CartoProjectFact).count() == 0


def test_harvest_without_project_writes_nothing(db):
    passing = {"overall_passed": True}
    assert harvest_facts_from_review(db, "", _MAPSPEC, passing) == 0
    assert harvest_facts_from_review(db, "p1", {}, passing) == 0
    db.commit()
    assert db.query(CartoProjectFact).count() == 0


def test_harvest_conflict_does_not_clobber_the_project_scheme(db):
    passing = {"overall_passed": True}
    harvest_facts_from_review(db, "p1", _MAPSPEC, passing)
    db.commit()
    drifted = {
        **_MAPSPEC,
        "layers": [{
            **_MAPSPEC["layers"][0],
            "legend_spec": {
                **_MAPSPEC["layers"][0]["legend_spec"],
                "breaks": [0, 250, 900],
            },
        }],
    }
    harvest_facts_from_review(db, "p1", drifted, passing)
    db.commit()
    # The turn's own scheme differs → conflict, not silent replacement.
    assert get_shared_classification(db, "p1", "population") is None
    held = db.query(CartoProjectFact).one()
    assert held.status == "conflicted"
    assert held.payload["breaks"] == [0, 100, 500, 1000]
