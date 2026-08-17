"""Regression test for issue #602 (selectin cascade on lineage/artifact read paths).

The hot read paths only consume scalar columns but the model relationships
(ArtifactLineage.artifact / parent_artifact / workflow_run, Artifact.upload_record /
layer) are lazy="selectin" — a load-time strategy that fires one extra IN-query
per batch even when the loaded objects are never used. get_lineage_graph's
level-batched BFS and list_project_artifacts previously triggered ~3-5 extra
queries per batch, so the query count grew with result size.

Fix: noload() on the discarded relationships (same pattern as
get_project_fingerprint). These tests assert the query count stays constant
regardless of how many rows a level / page returns.
"""
import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models.db_model import Organization
from app.models.project import (
    Project, Artifact, ArtifactLineage,
)
from app.services.lineage_service import LineageService
from app.services.project_service import ProjectService


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


def _count_queries(db, fn):
    """Run fn and count SQL statements executed against the session."""
    counter = {"n": 0}

    from sqlalchemy import event as sa_event

    engine = db.get_bind()

    def before_execute(conn, clauseelement, multiparams, params, execution_options):
        counter["n"] += 1

    sa_event.listen(engine, "before_execute", before_execute)
    try:
        result = fn()
    finally:
        sa_event.remove(engine, "before_execute", before_execute)
    return result, counter["n"]


@pytest.fixture
def project_chain(db_session):
    """proj + artifacts art_0 -> art_1 -> art_2 with a lineage edge each.

    art_0 is a root (single lineage row with parent NULL); art_1 and art_2
    each have one parent edge. get_lineage_graph(art_0) then walks three BFS
    levels downstream.
    """
    db = db_session
    org = Organization(id=1, name="org", slug="org")
    db.add(org)
    db.commit()

    proj = Project(id="proj_1", name="p", org_id=1, status="active")
    db.add(proj)
    db.commit()

    for i in range(3):
        db.add(Artifact(id=f"art_{i}", project_id=proj.id, name=f"a{i}",
                        artifact_type="dataset", storage_ref="ref:x"))
    db.commit()

    db.add(ArtifactLineage(id="lin_0", artifact_id="art_0", parent_artifact_id=None,
                           producing_tool="t", workflow_run_id=None))
    db.add(ArtifactLineage(id="lin_1", artifact_id="art_1", parent_artifact_id="art_0",
                           producing_tool="t", workflow_run_id=None))
    db.add(ArtifactLineage(id="lin_2", artifact_id="art_2", parent_artifact_id="art_1",
                           producing_tool="t", workflow_run_id=None))
    db.commit()
    return proj


def test_lineage_graph_query_count_constant_across_bfs_levels(db_session, project_chain):
    """#602: each BFS level must cost one query, not one + 3 selectin cascades."""
    db = db_session
    graph, n_queries = _count_queries(
        db, lambda: LineageService.get_lineage_graph(db, "art_0", max_depth=5, project_id="proj_1")
    )
    assert graph["artifact_id"] == "art_0"
    # 1 upstream level + 3 downstream levels (art_0/art_1/art_2) + 1 tenant
    # check. The pre-fix selectin cascade added 3 queries per non-empty level
    # batch (artifact/parent_artifact/workflow_run), ~14 total.
    assert n_queries <= 6, (
        f"#602 regression: lineage BFS fired {n_queries} queries for 3 levels "
        f"(expected ≤6 with noload; selectin cascade grows with depth)"
    )


def test_lineage_graph_query_count_does_not_scale_with_batch_size(db_session, project_chain):
    """#602: a 30-row level batch must not cost 30×3 extra queries."""
    db = db_session
    for i in range(30):
        db.add(Artifact(id=f"wide_{i}", project_id="proj_1", name=f"w{i}",
                        artifact_type="dataset", storage_ref="ref:x"))
    db.commit()
    for i in range(30):
        db.add(ArtifactLineage(id=f"lin_wide_{i}", artifact_id=f"wide_{i}",
                               parent_artifact_id="art_0", producing_tool="t",
                               workflow_run_id=None))
    db.commit()

    graph, n_queries = _count_queries(
        db, lambda: LineageService.get_lineage_graph(db, "art_0", max_depth=1, project_id="proj_1")
    )
    assert len(graph["consumers"]) == 31  # 1 chain edge (art_1) + 30 wide edges
    # 1 upstream + 1 downstream (30-row batch) + 1 tenant check.
    assert n_queries <= 6, (
        f"#602 regression: 30-row lineage batch fired {n_queries} queries "
        f"(expected ≤6; selectin cascade scales with batch size)"
    )


def test_list_project_artifacts_query_count_does_not_scale_with_page_size(db_session, project_chain):
    """#602: the artifacts list page must not fire upload_record/layer selectins."""
    for i in range(50):
        db_session.add(Artifact(id=f"list_{i}", project_id="proj_1", name=f"l{i}",
                                artifact_type="dataset", storage_ref="ref:x"))
    db_session.commit()

    db = db_session
    (artifacts, total), n_queries = _count_queries(
        db, lambda: ProjectService.list_project_artifacts(db, "proj_1", limit=50)
    )
    assert len(artifacts) == 50
    assert total == 53
    # auth project read (+2 selectin: organization/owner) + count + page row.
    # Pre-fix the page row also fired the upload_record / layer selectin chains,
    # pushing this to ~10+.
    assert n_queries <= 6, (
        f"#602 regression: listing 50 artifacts fired {n_queries} queries "
        f"(expected ≤6 with noload on upload_record/layer)"
    )