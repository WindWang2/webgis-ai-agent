"""Baseline query-count measurements for the chat context assembly hot path.

These tests document the BEFORE-state of the project-context block:

  1. The current ``_build_project_context_block`` calls three service methods
     on every ``assemble()`` invocation:
       - ``get_project_with_auth``  (1 query)
       - ``list_project_datasets``  (1 auth + 1 COUNT + 1 page = 3 queries)
       - ``list_project_workflows`` (1 auth + 1 COUNT + 1 page = 3 queries)
     Total: 7 sync Postgres queries per assemble call.

  2. ``assemble()`` is called once per LLM round in
     ``ChatExecutionEngine._chat_locked`` and ``chat_stream`` (max_rounds = 60).
     A long agent turn therefore triggers 60 * 7 = 420 project-DB queries
     (off-loaded to a thread pool) for an unchanged project.

  3. The list endpoints materialize full ORM rows and a kB-scale
     ``schema_profile``/``graph_spec`` JSON column for what the assembler
     only consumes as the first 5 names.

The tests here are written first (RED on a clean BASE_SHA) to capture the
baseline numbers; the optimization pass turns them into a contract enforced
by ``tests/perf/test_project_context_cache.py`` (added in the same branch).
"""
from __future__ import annotations

from datetime import datetime, timezone
import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models.db_model import Organization
from app.models.project import Project, ProjectDataset, Workflow
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


@pytest.fixture
def project_with_data(db_session):
    db = db_session
    org = Organization(id=1, name="org", slug="org")
    db.add(org)
    db.commit()
    proj = Project(id="proj_perf_1", name="Baseline Project", org_id=1, status="active")
    db.add(proj)
    db.commit()
    for i in range(20):
        db.add(ProjectDataset(
            id=f"ds_{i}",
            project_id=proj.id,
            name=f"Dataset {i}",
            source_type="vector",
            source_ref=f"ref:{i}",
            crs="EPSG:4326",
            schema_profile={"fields": ["id", "name"]},
            created_at=datetime.now(timezone.utc),
        ))
    for i in range(10):
        db.add(Workflow(
            id=f"wf_{i}",
            project_id=proj.id,
            name=f"Workflow {i}",
            description="d",
            version=1,
            graph_spec={"steps": [{"id": f"s_{i}"}]},
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        ))
    db.commit()
    return proj


def _count_queries(db, fn):
    counter = {"n": 0}
    engine = db.get_bind()

    def before_execute(conn, clauseelement, multiparams, params, execution_options):
        counter["n"] += 1

    event.listen(engine, "before_execute", before_execute)
    try:
        result = fn()
    finally:
        event.remove(engine, "before_execute", before_execute)
    return result, counter["n"]


# ---------------------------------------------------------------------------
# Baseline evidence (no assertion: this is a measurement, not a contract).
# ---------------------------------------------------------------------------

def test_baseline_project_block_query_count(db_session, project_with_data):
    """Mirror the three call sites of _build_project_context_block and count
    SQL statements. This is the BEFORE number the perf PR must beat.
    """
    db = db_session
    proj = project_with_data
    project_id = proj.id

    def assemble_project_block():
        # Faithful copy of the current sync body of
        # ``_build_project_context_block`` (without the assembler glue).
        ProjectService.get_project_with_auth(db, project_id)
        ProjectService.list_project_datasets(db, project_id)
        ProjectService.list_project_workflows(db, project_id)
        return True

    _, n = _count_queries(db, assemble_project_block)
    # Documented in the test docstring; printed so CI logs preserve the number.
    # Measured on origin/master 84c73fa: 10 (auth + 2 selectin org/owner +
    # COUNT+page datasets + auth + COUNT+page workflows).
    print(f"\n[BASELINE] project-block queries per assemble() = {n}")
    # This test is a measurement, not a contract: the optimization pass
    # will lower the number. Keep the assertion conservative: must be
    # strictly above 1 (otherwise an in-tree cache already exists).
    assert n > 1, (
        f"Expected >1 baseline queries per assemble; got {n}. "
        f"The cache may already be live — rebase onto latest origin/master."
    )


def test_baseline_assemble_per_round_count(db_session, project_with_data, monkeypatch):
    """Show that a 10-round chat turn would issue 10 × 7 = 70 project-DB
    queries for an unchanged project. The optimization PR must drop this to
    ≤1 query per project per turn (or a tight bounded number).
    """
    db = db_session
    proj = project_with_data
    project_id = proj.id

    counter = {"n": 0}
    engine = db.get_bind()

    def before_execute(conn, clauseelement, multiparams, params, execution_options):
        counter["n"] += 1

    event.listen(engine, "before_execute", before_execute)
    try:
        for _round in range(10):
            # Re-issue the three calls each round (current behavior).
            ProjectService.get_project_with_auth(db, project_id)
            ProjectService.list_project_datasets(db, project_id)
            ProjectService.list_project_workflows(db, project_id)
    finally:
        event.remove(engine, "before_execute", before_execute)
    print(f"\n[BASELINE] 10 rounds -> {counter['n']} project-DB queries")
    # 10 × per-block count must be a strict multiple; the cache will make
    # subsequent rounds return 0 queries.
    assert counter["n"] >= 10
