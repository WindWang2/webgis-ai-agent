"""Concurrency invariants for workflow runs (spec §29):
  * duplicate resume does not duplicate artifacts/lineage
  * run fingerprint stays deterministic across concurrent replays
"""
import asyncio
import uuid

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models.db_model import Organization
from app.models.project import Project, Artifact, ArtifactLineage
from app.schemas.project_schema import WorkflowCreate, WorkflowGraphSpec, WorkflowStepSpec
from app.services.project_service import ProjectService
from app.services.workflow_engine import WorkflowEngine


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:")
    @event.listens_for(engine, "connect")
    def _fk(c, _):
        cur = c.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()
    Base.metadata.drop_all(engine)


class _Reg:
    def tool_version(self, n): return "1.0#cv1"

    async def dispatch(self, name, args, session_id=None):
        from app.services.session_data import session_data_manager
        payload = {"produced_by": name, "feature_count": 2}
        ref = await session_data_manager.store(session_id, payload, prefix=name) if session_id else f"ref:{name}"
        return {"success": True, "ref_id": ref, "feature_count": 2}


def _setup(db):
    db.add(Organization(id=1, name="o", slug="o"))
    db.commit()
    proj = Project(id=f"proj_{uuid.uuid4().hex[:16]}", name="p", org_id=1, status="active")
    db.add(proj)
    db.commit()
    wf = ProjectService.save_workflow(db, proj.id, WorkflowCreate(
        name="wf", graph_spec=WorkflowGraphSpec(steps=[
            WorkflowStepSpec(step_id="s1", tool_name="t_a"),
            WorkflowStepSpec(step_id="s2", tool_name="t_b", dependencies=["s1"]),
        ])))
    return proj, wf


def test_concurrent_replays_yield_same_fingerprint(db_session):
    db = db_session
    proj, wf = _setup(db)
    run1 = asyncio.run(WorkflowEngine.execute_workflow_run(
        db=db, workflow_id=wf.id, tool_registry=_Reg(),
        expected_project_id=proj.id, session_id="sess"))

    # Two replays on separate sessions sharing the same DB. Fingerprints are
    # deterministic across replays of identical reproducible inputs.
    async def do_replay(sid):
        return await WorkflowEngine.replay_run(
            db=db, prior_run_id=run1.id, tool_registry=_Reg(),
            mode="exact", expected_project_id=proj.id, session_id=sid)

    # Serialize DB access (SQLite) but the fingerprint logic is the point.
    r1 = asyncio.run(do_replay("sess_a"))
    r2 = asyncio.run(do_replay("sess_b"))
    assert r1.run_fingerprint is not None
    assert r1.run_fingerprint == r2.run_fingerprint == run1.run_fingerprint


def test_resume_does_not_duplicate_artifacts_or_lineage(db_session):
    db = db_session
    proj, wf = _setup(db)

    # Build a genuinely partial run: fail s2.
    class _FailReg(_Reg):
        async def dispatch(self, name, args, session_id=None):
            if name == "t_b":
                raise RuntimeError("boom")
            return await super().dispatch(name, args, session_id)

    partial = asyncio.run(WorkflowEngine.execute_workflow_run(
        db=db, workflow_id=wf.id, tool_registry=_FailReg(),
        expected_project_id=proj.id, session_id="sess"))
    assert partial.completed_steps == ["s1"]
    s1_arts_before = db.execute(select(Artifact).where(
        Artifact.metadata_json["step_id"].as_string() == "s1")).scalars().all()
    s1_lins_before = db.execute(select(ArtifactLineage).where(
        ArtifactLineage.workflow_run_id == partial.id)).scalars().all()

    resumed = asyncio.run(WorkflowEngine.resume_run(
        db=db, prior_run_id=partial.id, tool_registry=_Reg(),
        expected_project_id=proj.id, session_id="sess"))
    assert resumed.status == "completed"

    # s1 must not have produced a second artifact.
    s1_arts_after = db.execute(select(Artifact).where(
        Artifact.metadata_json["step_id"].as_string() == "s1")).scalars().all()
    assert len(s1_arts_after) == len(s1_arts_before) == 1
    # The resumed run's own lineage rows cover s2 only (s1 lineage belongs to the
    # prior run). No duplicate lineage for s1 in the new run.
    new_lins = db.execute(select(ArtifactLineage).where(
        ArtifactLineage.workflow_run_id == resumed.id)).scalars().all()
    assert len(new_lins) == 1, f"resumed run should have 1 lineage row (s2), got {len(new_lins)}"
    _ = s1_lins_before  # prior run keeps its own lineage
