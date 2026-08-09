"""Regression tests for lineage tenant isolation (DATA-01, DATA-07).

DATA-01: get_lineage_graph previously walked parents/consumers without
re-checking each belongs to the caller's project, so a cross-tenant neighbor
artifact leaked via the lineage graph. DATA-07: record_lineage accepted
arbitrary parent_artifact_ids with no project check, creating the cross-project
DAG links. Both are P0 (cross-tenant IDOR).
"""
import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models.db_model import Organization, User
from app.models.project import Project, Artifact
from app.services.lineage_service import LineageService


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:", echo=False)

    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()
    Base.metadata.drop_all(engine)


def _make_project(db, name, org_id):
    proj = Project(id=f"proj_{name}", name=name, org_id=org_id, status="active")
    db.add(proj)
    db.commit()
    return proj


def _make_artifact(db, project_id, name):
    art = Artifact(
        id=f"art_{name}",
        project_id=project_id,
        name=name,
        artifact_type="dataset",
        storage_ref="ref:x",
    )
    db.add(art)
    db.commit()
    return art


def test_record_lineage_rejects_cross_project_parent(db_session):
    """DATA-07: a parent artifact from a different project must not be linked."""
    db = db_session
    org = Organization(id=1, name="org", slug="org")
    db.add(org)
    db.commit()
    proj_a = _make_project(db, "a", org_id=1)
    proj_b = _make_project(db, "b", org_id=1)
    child = _make_artifact(db, proj_a.id, "child")
    foreign_parent = _make_artifact(db, proj_b.id, "foreign_parent")

    # Attempt to link child (proj_a) -> foreign_parent (proj_b).
    records = LineageService.record_lineage(
        db=db,
        artifact_id=child.id,
        producing_tool="buffer_analysis",
        parent_artifact_ids=[foreign_parent.id],
    )
    # The cross-project parent must NOT have been recorded.
    recorded_parents = [r.parent_artifact_id for r in records if r.parent_artifact_id]
    assert foreign_parent.id not in recorded_parents


def test_get_lineage_graph_filters_cross_tenant_neighbors(db_session):
    """DATA-01: a cross-project neighbor must not leak via traversal."""
    db = db_session
    org = Organization(id=1, name="org", slug="org")
    db.add(org)
    db.commit()
    proj_a = _make_project(db, "a", org_id=1)
    proj_b = _make_project(db, "b", org_id=1)
    own_parent = _make_artifact(db, proj_a.id, "own_parent")
    child = _make_artifact(db, proj_a.id, "child")
    foreign_child = _make_artifact(db, proj_b.id, "foreign_child")

    # Manually insert a lineage edge from foreign_child (proj_b) -> child
    # (simulating a pre-existing cross-project link that predates DATA-07).
    from app.models.project import ArtifactLineage
    from datetime import datetime, timezone
    db.add(ArtifactLineage(
        id="lin_cross_1",
        artifact_id=foreign_child.id,
        parent_artifact_id=child.id,
        producing_tool="t",
        created_at=datetime.now(timezone.utc),
    ))
    # And a legitimate same-project edge: child -> own_parent.
    db.add(ArtifactLineage(
        id="lin_ok_1",
        artifact_id=child.id,
        parent_artifact_id=own_parent.id,
        producing_tool="t",
        created_at=datetime.now(timezone.utc),
    ))
    db.commit()

    # Querying child's lineage scoped to proj_a: the legitimate parent should
    # appear, but the cross-project consumer (foreign_child, proj_b) must NOT.
    graph = LineageService.get_lineage_graph(db=db, artifact_id=child.id, project_id=proj_a.id)
    parent_ids = [p["parent_artifact_id"] for p in graph["parents"]]
    consumer_ids = [c["consumer_artifact_id"] for c in graph["consumers"]]
    assert own_parent.id in parent_ids
    assert foreign_child.id not in consumer_ids, (
        "DATA-01 regression: cross-tenant consumer leaked into lineage graph"
    )
