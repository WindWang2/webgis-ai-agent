"""Lineage DAG invariants (spec §14, §26, §15):
  * self-cycle rejected at write (INV-LIN1)
  * multi-hop cycle rejected at write (INV-LIN2)
  * intra-project enforcement (INV-LIN3) + tenant filter on traversal
  * input-dataset provenance recorded (INV-LIN4)
  * transaction flag: record_lineage(commit=False) flushes without committing
  * level-batched traversal returns correct multi-hop parents/consumers
"""
import uuid

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models.db_model import Organization
from app.models.project import Project, Artifact, ArtifactLineage
from app.services.lineage_service import LineageService, LineageCycleError


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:")
    @event.listens_for(engine, "connect")
    def _fk(dbapi_con, _):
        cur = dbapi_con.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()
    Base.metadata.drop_all(engine)


def _make_artifact(db, project_id, name="a"):
    art = Artifact(
        id=f"art_{uuid.uuid4().hex[:8]}",
        project_id=project_id,
        name=name,
        artifact_type="analysis",
    )
    db.add(art)
    db.commit()
    return art


def _make_project(db, name="p", org_id=1):
    if org_id:
        exists = db.execute(select(Organization).where(Organization.id == org_id)).scalars().first()
        if not exists:
            db.add(Organization(id=org_id, name=f"org{org_id}", slug=f"org{org_id}"))
            db.commit()
    proj = Project(id=f"proj_{uuid.uuid4().hex[:6]}", name=name, org_id=org_id, status="active")
    db.add(proj)
    db.commit()
    return proj


def test_self_cycle_rejected(db_session):
    db = db_session
    proj = _make_project(db)
    art = _make_artifact(db, proj.id)
    with pytest.raises(LineageCycleError, match="self-cycle"):
        LineageService.record_lineage(
            db=db, artifact_id=art.id, producing_tool="t",
            parent_artifact_ids=[art.id],
        )


def test_multi_hop_cycle_rejected(db_session):
    """A → B → C already recorded; adding C → A must be rejected."""
    db = db_session
    proj = _make_project(db)
    a = _make_artifact(db, proj.id, "a")
    b = _make_artifact(db, proj.id, "b")
    c = _make_artifact(db, proj.id, "c")
    # A <- B <- C  (C's parent is B, B's parent is A)
    LineageService.record_lineage(db, b.id, "t", parent_artifact_ids=[a.id])
    LineageService.record_lineage(db, c.id, "t", parent_artifact_ids=[b.id])
    # Now claim A's parent is C → closes cycle C->B->A->C
    with pytest.raises(LineageCycleError, match="multi-hop cycle"):
        LineageService.record_lineage(db, a.id, "t", parent_artifact_ids=[c.id])


def test_no_false_positive_on_diamond(db_session):
    """A diamond (B,C both depend on A; D depends on B,C) is NOT a cycle."""
    db = db_session
    proj = _make_project(db)
    a = _make_artifact(db, proj.id, "a")
    b = _make_artifact(db, proj.id, "b")
    c = _make_artifact(db, proj.id, "c")
    d = _make_artifact(db, proj.id, "d")
    LineageService.record_lineage(db, b.id, "t", parent_artifact_ids=[a.id])
    LineageService.record_lineage(db, c.id, "t", parent_artifact_ids=[a.id])
    # D has two parents B and C — must not raise.
    LineageService.record_lineage(db, d.id, "t", parent_artifact_ids=[b.id, c.id])
    rows = db.execute(select(ArtifactLineage).where(ArtifactLineage.artifact_id == d.id)).scalars().all()
    assert {r.parent_artifact_id for r in rows} == {b.id, c.id}


def test_cross_project_parent_rejected(db_session):
    db = db_session
    p1 = _make_project(db, "p1", org_id=1)
    p2 = _make_project(db, "p2", org_id=1)
    child = _make_artifact(db, p1.id)
    foreign = _make_artifact(db, p2.id)
    LineageService.record_lineage(
        db=db, artifact_id=child.id, producing_tool="t",
        parent_artifact_ids=[foreign.id],
    )
    # The cross-project parent must have been filtered out → no edge persisted.
    rows = db.execute(
        select(ArtifactLineage).where(ArtifactLineage.artifact_id == child.id)
    ).scalars().all()
    assert all(r.parent_artifact_id != foreign.id for r in rows)


def test_traversal_tenant_isolation_and_multihop(db_session):
    """Level-batched BFS returns multi-hop parents, and project_id filters a
    cross-tenant neighbor that was wired in directly at the DB level."""
    db = db_session
    p1 = _make_project(db, "p1", org_id=1)
    p2 = _make_project(db, "p2", org_id=2)
    a = _make_artifact(db, p1.id, "a")
    b = _make_artifact(db, p1.id, "b")
    foreign = _make_artifact(db, p2.id, "x")
    LineageService.record_lineage(db, b.id, "t", parent_artifact_ids=[a.id])  # a -> b
    # Inject a cross-tenant edge directly (bypassing the service check) to test
    # that the traversal's project_id filter scrubs it on read.
    db.add(ArtifactLineage(
        id=f"lin_{uuid.uuid4().hex[:8]}", artifact_id=b.id, parent_artifact_id=foreign.id,
        producing_tool="leak",
    ))
    db.commit()

    graph = LineageService.get_lineage_graph(db, b.id, max_depth=5, project_id=p1.id)
    parent_ids = {e["parent_artifact_id"] for e in graph["parents"]}
    assert a.id in parent_ids
    assert foreign.id not in parent_ids, "cross-tenant parent must be filtered"


def test_input_dataset_provenance_recorded(db_session):
    """A root edge can carry source_dataset_id (INV-LIN4)."""
    db = db_session
    proj = _make_project(db)
    art = _make_artifact(db, proj.id)
    LineageService.record_lineage(
        db=db, artifact_id=art.id, producing_tool="t", parent_artifact_ids=None,
        source_dataset_id="ds_1", source_dataset_fingerprint="fp1",
        content_fingerprint="cf1",
    )
    row = db.execute(select(ArtifactLineage).where(ArtifactLineage.artifact_id == art.id)).scalar_one()
    assert row.source_dataset_id == "ds_1"
    assert row.source_dataset_fingerprint == "fp1"
    assert row.content_fingerprint == "cf1"


def test_commit_false_does_not_commit(db_session):
    """record_lineage(commit=False) flushes but leaves the transaction open so
    the orchestrator owns the atomic boundary (INV-TX2)."""
    db = db_session
    proj = _make_project(db)
    art = _make_artifact(db, proj.id)
    LineageService.record_lineage(
        db=db, artifact_id=art.id, producing_tool="t", commit=False,
    )
    # The row is flushed (queryable in this session) ...
    assert db.execute(select(ArtifactLineage)).scalars().first() is not None
    # ... but a rollback discards it (no commit happened).
    db.rollback()
    assert db.execute(select(ArtifactLineage)).scalars().first() is None


def test_traversal_performance_is_level_batched(db_session):
    """Depth-20 chain: query count must be O(depth), not O(nodes) (no N+1)."""
    db = db_session
    proj = _make_project(db)
    arts = [_make_artifact(db, proj.id, f"a{i}") for i in range(20)]
    for i in range(1, 20):
        LineageService.record_lineage(
            db, arts[i].id, "t", parent_artifact_ids=[arts[i - 1].id]
        )

    queries = {"n": 0}

    @event.listens_for(db.bind, "before_execute")
    def _count(conn, clause, *a, **k):
        # Count only SELECTs against the lineage table (the traversal's own work),
        # excluding ORM selectin relationship loads on other tables.
        text = str(clause)
        if "artifact_lineages" in text and text.lstrip().lower().startswith("select"):
            queries["n"] += 1

    try:
        graph = LineageService.get_lineage_graph(db, arts[-1].id, max_depth=20, project_id=proj.id)
    finally:
        event.remove(db.bind, "before_execute", _count)
    # A 20-deep chain has 20 levels ⇒ level-batched is ~20 lineage-table queries
    # (one IN per level) + 1 tenant filter. Per-node querying would be ≥20 just for
    # parents; the point is it is O(depth), not O(nodes) round-trips with pop(0).
    assert queries["n"] <= 22, f"expected ~O(depth) lineage queries, got {queries['n']}"
    assert len(graph["parents"]) == 19
