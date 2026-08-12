"""Regression test for DATA-08 (selectin N+1 in project listing paths).

list_project_artifacts returned N Artifact ORM rows whose upload_record / layer
relationships are lazy="selectin" and lineages / parent_lineages are
lazy="select" — serializing N artifacts fired ~N×(1 selectin + 2 select)
queries. With explicit selectinload, the total query count is constant.
"""
import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models.db_model import Organization
from app.models.project import Project, Artifact, Workflow, WorkflowRun
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
def project_with_artifacts(db_session):
    db = db_session
    org = Organization(id=1, name="org", slug="org")
    db.add(org)
    db.commit()

    proj = Project(id="proj_1", name="p", org_id=1, status="active")
    db.add(proj)
    db.commit()

    for i in range(20):
        db.add(Artifact(
            id=f"art_{i}",
            project_id=proj.id,
            name=f"a{i}",
            artifact_type="dataset",
            storage_ref="ref:x",
        ))
    db.commit()
    return proj


def test_list_project_artifacts_query_count_is_constant(db_session, project_with_artifacts):
    """DATA-08: listing N artifacts must not fire O(N) relationship queries."""
    db = db_session
    proj = project_with_artifacts

    (artifacts, total), n_queries = _count_queries(
        db, lambda: ProjectService.list_project_artifacts(db, proj.id)
    )
    # 服务现在返回分页形状 (items, total)
    assert len(artifacts) == 20
    assert total == 20
    # 20 artifacts × ~3 lazy relationship queries each would be ~60+ queries.
    # With selectinload: 1 (artifacts) + 4 (selectin batches) = 5 total.
    assert n_queries <= 8, (
        f"DATA-08 regression: listing 20 artifacts fired {n_queries} queries "
        f"(expected ≤8 with batched selectinload)"
    )


def test_list_project_workflows_and_runs_eager_loaded(db_session, project_with_artifacts):
    db = db_session
    proj = project_with_artifacts

    wf = Workflow(
        id="wf_1", project_id=proj.id, name="w", version=1,
        graph_spec={"steps": []},
    )
    db.add(wf)
    db.commit()
    for i in range(10):
        db.add(WorkflowRun(
            id=f"run_{i}", workflow_id=wf.id, workflow_version=1, status="completed",
        ))
    db.commit()

    (runs, total), n_queries = _count_queries(
        db, lambda: ProjectService.list_workflow_runs(db, proj.id)
    )
    assert len(runs) == 10
    assert total == 10
    # Constant query count regardless of N (auth check + join query + 2 selectin
    # batches + project auth overhead). 10 runs × 2 lazy relationship queries
    # would be ~20+ queries.
    assert n_queries <= 8, (
        f"DATA-08 regression: listing 10 workflow runs fired {n_queries} queries"
    )
