"""F05 / ADR-0215 — Reuse identity: fingerprint-fed reuse queries.

Invariants under test:
- the working context's accepted authoritative tokens reach
  ``find_reuse_candidates`` (request-level liveness fires on drift);
- reconciliation learns tokens (never drifts on learning), detects
  authority-side drift the MapSpec diff cannot see, and adopts the new
  token while emitting the fail-closed invalidation event;
- unknown/unresolved datasets degrade honestly (no fingerprints, never a
  guessed value); method keys are never guessed from bare recipe ids.
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
import app.models.project  # noqa: F401 — ProjectDataset tables
import app.models.project_knowledge  # noqa: F401 — knowledge entries
from app.services.gis_context.reuse_identity import (
    reconcile_dataset_fingerprints,
    reuse_query_from_context,
)
from app.services.gis_context.working_context import (
    BasisDataset,
    GISWorkingContext,
    WorkingBasis,
)
from app.services.project_knowledge.contract import (
    EK_ARTIFACT,
    KnowledgeUpsert,
    RefTag,
    REL_DERIVED_FROM,
    VERDICT_EXACT,
    VERDICT_RECOMPUTE_PARTIAL,
)
from app.services.project_knowledge.retrieval import find_reuse_candidates
from app.services.project_knowledge import store as ks


def _wc(fingerprint: str = "", recipe: str = "choropleth_v1") -> GISWorkingContext:
    return GISWorkingContext(
        mission_id="msn-reuse",
        org_id="org-1",
        project_id="prj-1",
        basis=WorkingBasis(
            aoi_bbox=[102.9, 30.5, 104.5, 31.5],
            time_period="2024",
            recipe_id=recipe,
            datasets=[BasisDataset(
                ref_id="ds-1", alias="schools",
                content_revision="rev-1", role="source",
                version_fingerprint=fingerprint,
                authority_id="ds-1" if fingerprint else "",
            )],
        ),
    )


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    session = factory()
    yield session
    session.close()


@pytest.fixture()
def seeded(db):
    """One active artifact entry derived from dataset ds-1 (token sha-1).

    The artifact authority row is real (liveness probes it); the head
    content sha matches the projection's version_token."""
    from app.models.project import Artifact, ProjectDataset

    db.add(ProjectDataset(
        id="ds-1", project_id="prj-1", name="schools",
        source_type="upload", version_fingerprint="sha-1",
    ))
    db.add(Artifact(
        id="art-1", project_id="prj-1", name="schools map",
        artifact_type="map", content_fingerprint="art-sha-a",
    ))
    db.commit()
    row = ks.upsert_entry(db, KnowledgeUpsert(
        org_id="org-1",
        project_id="prj-1",
        entity_kind=EK_ARTIFACT,
        authority_store="artifact",
        authority_id="art-1",
        subject="2024 学校分布专题图",
        version_token="art-sha-a",
        summary="区级学校点位聚合",
        bbox=[102.0, 30.0, 105.0, 32.0],
        temporal_label="2024",
        method_key="capability:choropleth",
        refs=[RefTag(
            relation=REL_DERIVED_FROM, authority="project_dataset",
            id="ds-1", token="sha-1",
        )],
    ))
    db.commit()
    assert row is not None
    return row


def _query_and_verdicts(db, wc, fingerprints=None):
    query = reuse_query_from_context(wc, dataset_fingerprints=fingerprints)
    return find_reuse_candidates(
        db, org_id="org-1", project_id="prj-1", query=query)


def test_exact_reuse_with_matching_fingerprints(db, seeded):
    """Fingerprint + AOI + temporal + method all confirm → exact."""
    wc = _wc(fingerprint="sha-1", recipe="capability:choropleth")
    candidates = _query_and_verdicts(db, wc)
    assert candidates, "candidate expected"
    assert candidates[0].verdict == VERDICT_EXACT
    assert any("指纹一致" in r or "一致" in r for r in candidates[0].reasons)


def test_authority_drift_blocks_exact(db, seeded):
    """Accepted token sha-1, authority moved to sha-2 → no candidate stays
    exact (request_input_stale fires from the *claimed* fingerprints)."""
    from app.models.project import ProjectDataset

    ds = db.get(ProjectDataset, "ds-1")
    ds.version_fingerprint = "sha-2"
    db.commit()
    wc = _wc(fingerprint="sha-1", recipe="capability:choropleth")
    candidates = _query_and_verdicts(db, wc)
    assert candidates
    assert all(c.verdict != VERDICT_EXACT for c in candidates)
    assert any(
        any(c.startswith("request_input_stale") for c in cand.stale_causes)
        or cand.verdict == VERDICT_RECOMPUTE_PARTIAL
        for cand in candidates
    )


def test_request_input_gone_blocks_reuse(db, seeded):
    """Claimed dataset detached → hard downgrade (not reusable)."""
    from app.models.project import ProjectDataset

    ds = db.get(ProjectDataset, "ds-1")
    ds.detached_at = ds.created_at
    db.commit()
    wc = _wc(fingerprint="sha-1", recipe="capability:choropleth")
    candidates = _query_and_verdicts(db, wc)
    assert all(c.verdict != VERDICT_EXACT for c in candidates)


# ── reconciliation ───────────────────────────────────────────────────────

def test_reconcile_learns_token_without_drift(db, seeded):
    wc = _wc(fingerprint="")
    result = reconcile_dataset_fingerprints(wc, db, project_id="prj-1")
    assert result.mutated is True
    assert result.events == []
    assert result.dataset_fingerprints == {"ds-1": "sha-1"}
    assert wc.basis.datasets[0].version_fingerprint == "sha-1"
    assert wc.basis.datasets[0].authority_id == "ds-1"

    # Idempotent: second pass learns nothing new, mutates nothing.
    result2 = reconcile_dataset_fingerprints(wc, db, project_id="prj-1")
    assert result2.mutated is False
    assert result2.events == []


def test_reconcile_detects_authority_side_drift(db, seeded):
    """Authority re-versioned while MapSpec content_revision unchanged —
    the drift the mapspec-side diff cannot see."""
    wc = _wc(fingerprint="sha-1")
    from app.models.project import ProjectDataset

    ds = db.get(ProjectDataset, "ds-1")
    ds.version_fingerprint = "sha-2"
    db.commit()
    result = reconcile_dataset_fingerprints(wc, db, project_id="prj-1")
    assert len(result.events) == 1
    assert result.events[0]["kind"] == "DATASET_VERSION_CHANGED"
    assert result.events[0]["ref_id"] == "ds-1"
    assert result.mutated is True
    # New token adopted (stops the event re-firing; conclusions staled).
    assert wc.basis.datasets[0].version_fingerprint == "sha-2"


def test_reconcile_resolves_via_alias(db, seeded):
    """ref_id unknown to the authority, alias resolves — the fingerprint is
    recorded under the authority id that actually matched."""
    wc = _wc(fingerprint="")
    wc.basis.datasets[0].ref_id = "mapspec-source-a"
    wc.basis.datasets[0].alias = "ds-1"
    result = reconcile_dataset_fingerprints(wc, db, project_id="prj-1")
    assert result.dataset_fingerprints == {"ds-1": "sha-1"}
    assert wc.basis.datasets[0].authority_id == "ds-1"


def test_reconcile_unresolved_dataset_degrades_honestly(db, seeded):
    wc = _wc(fingerprint="")
    wc.basis.datasets[0].ref_id = "not-a-ds-id"
    wc.basis.datasets[0].alias = "also-unknown"
    result = reconcile_dataset_fingerprints(wc, db, project_id="prj-1")
    assert result.events == []
    assert result.dataset_fingerprints == {}
    assert wc.basis.datasets[0].version_fingerprint == ""


def test_reconcile_authority_gone_is_not_drift(db, seeded):
    wc = _wc(fingerprint="sha-1")
    from app.models.project import ProjectDataset

    ds = db.get(ProjectDataset, "ds-1")
    ds.detached_at = ds.created_at
    db.commit()
    result = reconcile_dataset_fingerprints(wc, db, project_id="prj-1")
    assert result.events == []  # gone ≠ moved; the reuse query reports it
    assert result.dataset_fingerprints == {}


# ── query construction ───────────────────────────────────────────────────

def test_query_construction_full():
    wc = _wc(fingerprint="sha-1", recipe="capability:choropleth")
    query = reuse_query_from_context(wc)
    assert query.bbox == [102.9, 30.5, 104.5, 31.5]
    assert query.temporal_label == "2024"
    assert query.method_key == "capability:choropleth"
    assert query.dataset_fingerprints == {"ds-1": "sha-1"}


def test_query_never_guesses_method():
    wc = _wc(recipe="choropleth_v1")
    assert reuse_query_from_context(wc).method_key is None


def test_query_without_fingerprints():
    wc = _wc(fingerprint="")
    query = reuse_query_from_context(wc)
    assert query.dataset_fingerprints is None
    # Explicit empty dict = fingerprint-less (kill-switch parity).
    assert reuse_query_from_context(wc, dataset_fingerprints={}) \
        .dataset_fingerprints is None
