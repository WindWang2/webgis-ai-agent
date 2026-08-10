"""Regression tests for DATA-10 (deep-audit round 3):

execute_workflow_run previously held ONE DB session/transaction across the
entire multi-step tool loop — the pooled connection stayed checked out for the
whole run (pool exhaustion under concurrency) and a mid-loop failure committed
ALL prior steps' artifacts as an indistinguishable partial batch. Now each
step commits its artifact + lineage before the next tool dispatch, and a
failure rolls back only the current step.
"""
import uuid

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models.db_model import Organization
from app.models.project import Project, Workflow, WorkflowRun, Artifact
from app.services.workflow_engine import WorkflowEngine
from app.schemas.project_schema import WorkflowStepSpec


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


def _seed_workflow(db, name="wf", steps_data=None):
    org = Organization(id=1, name="org", slug="org")
    db.add(org)
    db.commit()
    proj = Project(id="proj_1", name="p", org_id=1, status="active")
    db.add(proj)
    db.commit()
    wf = Workflow(
        id=f"wf_{uuid.uuid4().hex[:8]}",
        project_id=proj.id,
        name=name,
        version=1,
        graph_spec={"steps": steps_data or []},
    )
    db.add(wf)
    db.commit()
    return wf


class _FakeRegistry:
    """Minimal ToolRegistry stand-in whose dispatch returns dicts or raises."""

    def __init__(self, results=None, fail_on=None):
        self.results = results or {}
        self.fail_on = fail_on
        self.calls = []

    async def dispatch(self, tool_name, tool_args):
        self.calls.append(tool_name)
        if self.fail_on == tool_name:
            raise RuntimeError(f"tool {tool_name} exploded")
        return self.results.get(tool_name, {"success": True, "ref_id": f"ref:{tool_name}"})


def test_workflow_commits_each_step_and_produces_artifacts(db_session):
    """After a successful 2-step run, both artifacts + lineage must be durable
    (committed per-step) and the run completed."""
    db = db_session
    wf = _seed_workflow(db, steps_data=[
        {"step_id": "s1", "tool_name": "buffer_analysis", "args_template": {"dist": 100}, "input_bindings": {}, "dependencies": []},
        {"step_id": "s2", "tool_name": "clip", "args_template": {}, "input_bindings": {"source": "step_s1.result"}, "dependencies": ["s1"]},
    ])
    registry = _FakeRegistry(results={
        "buffer_analysis": {"success": True, "ref_id": "ref:b1"},
        "clip": {"success": True, "ref_id": "ref:c1"},
    })

    import asyncio

    run = asyncio.run(WorkflowEngine.execute_workflow_run(
        db=db, workflow_id=wf.id, tool_registry=registry,
    ))
    assert run.status == "completed"
    assert len(registry.calls) == 2

    artifacts = db.execute(select(Artifact)).scalars().all()
    assert len(artifacts) == 2, "both step artifacts must be persisted"


def test_failed_step_rolls_back_only_current_step(db_session):
    """When step 2 fails, step 1's artifact must remain durable (per-step
    commit) while step 2's partial artifact must NOT be persisted; run=failed."""
    db = db_session
    wf = _seed_workflow(db, steps_data=[
        {"step_id": "s1", "tool_name": "buffer_analysis", "args_template": {}, "input_bindings": {}, "dependencies": []},
        {"step_id": "s2", "tool_name": "explode", "args_template": {}, "input_bindings": {}, "dependencies": ["s1"]},
    ])
    registry = _FakeRegistry(results={"buffer_analysis": {"success": True, "ref_id": "ref:b1"}}, fail_on="explode")

    import asyncio

    run = asyncio.run(WorkflowEngine.execute_workflow_run(
        db=db, workflow_id=wf.id, tool_registry=registry,
    ))
    assert run.status == "failed"
    assert "exploded" in (run.error_message or "")

    # Step 1's artifact was committed before the failure; step 2 never added one.
    artifacts = db.execute(select(Artifact)).scalars().all()
    assert len(artifacts) == 1, (
        f"DATA-10 regression: expected only step-1 artifact, got {len(artifacts)}"
    )
    assert artifacts[0].name.endswith("s1_output")
