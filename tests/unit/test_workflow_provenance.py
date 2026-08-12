"""Reproducibility / provenance test matrix (spec §8–§28).

Covers:
  * deterministic dataset fingerprint (§8/§9)
  * immutable workflow revision + exact replay after a graph edit (§10/§18)
  * run snapshot reproducibility + run fingerprint determinism (§11/§25)
  * truthful artifact metadata — raster not geojson, non-WGS84 not EPSG:4326 (§13)
  * partial-run semantics — completed_steps + resume (§16/§19)
  * stale input → resume rejected (§17/§19)
  * authorization — replay/resume re-authorize, cross-project blocked (§21)
  * run comparison surfaces real diffs (§23)
"""
import asyncio
import uuid

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models.db_model import Organization
from app.models.project import Project, ProjectDataset, Workflow, Artifact
from app.schemas.project_schema import DatasetAttach, WorkflowCreate, WorkflowGraphSpec, WorkflowStepSpec
from app.services.project_service import ProjectService
from app.services.workflow_engine import WorkflowEngine
from app.services.provenance import compute_graph_fingerprint


# ── fixtures ────────────────────────────────────────────────────────────────

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


def _seed_org_project(db, name="p", org_id=1):
    db.add(Organization(id=org_id, name=f"org{org_id}", slug=f"org{org_id}"))
    db.commit()
    proj = Project(id=f"proj_{uuid.uuid4().hex[:6]}", name=name, org_id=org_id, status="active")
    db.add(proj)
    db.commit()
    return proj


class FakeRegistry:
    """Stores outputs in the session store so resume can reconstruct them."""

    def __init__(self, results=None, fail_on=None, version="1.0#cv1"):
        self.results = results or {}
        self.fail_on = fail_on
        self.version = version
        self.calls = []

    def tool_version(self, name):
        return self.version

    async def dispatch(self, name, args, session_id=None):
        self.calls.append(name)
        if name == self.fail_on:
            raise RuntimeError(f"tool {name} exploded")
        base = self.results.get(name)
        if base is not None:
            return base
        # Default: a Fetch-on-Demand vector descriptor, with a reconstructable
        # payload stored under a real ref so resume can read it back.
        payload = {"produced_by": name, "feature_count": 3, "bbox": [0, 0, 1, 1]}
        ref = f"ref:{name}-{uuid.uuid4().hex[:6]}"
        if session_id:
            from app.services.session_data import session_data_manager
            ref = await session_data_manager.store(session_id, payload, prefix=name)
        return {"success": True, "ref_id": ref, "feature_count": 3, "bbox": [0, 0, 1, 1]}


def _run(coro):
    return asyncio.run(coro)


def _make_workflow(db, project_id, steps, name="wf"):
    wf_data = WorkflowCreate(
        name=name,
        graph_spec=WorkflowGraphSpec(steps=[WorkflowStepSpec(**s) for s in steps]),
    )
    return ProjectService.save_workflow(db, project_id, wf_data)


# ── §8/§9 dataset fingerprint ───────────────────────────────────────────────

def test_dataset_fingerprint_deterministic_and_content_sensitive(db_session):
    db = db_session
    proj = _seed_org_project(db)
    d1 = ProjectService.attach_dataset(db, proj.id, DatasetAttach(
        name="a", source_type="upload", source_ref="up_1", crs="EPSG:4326"))
    d2 = ProjectService.attach_dataset(db, proj.id, DatasetAttach(
        name="a-copy", source_type="upload", source_ref="up_1", crs="EPSG:4326"))
    d3 = ProjectService.attach_dataset(db, proj.id, DatasetAttach(
        name="b", source_type="upload", source_ref="up_2", crs="EPSG:4326"))
    assert d1.version_fingerprint == d2.version_fingerprint, "same evidence ⇒ same fp"
    assert d1.version_fingerprint != d3.version_fingerprint, "different ref ⇒ different fp"
    assert ":" not in (d1.version_fingerprint or "") and len(d1.version_fingerprint) == 64


def test_dataset_fingerprint_schema_order_invariant(db_session):
    db = db_session
    proj = _seed_org_project(db)
    d1 = ProjectService.attach_dataset(db, proj.id, DatasetAttach(
        name="a", source_type="upload", source_ref="up", crs="EPSG:4326",
        schema_profile={"b": 1, "a": 2}))
    d2 = ProjectService.attach_dataset(db, proj.id, DatasetAttach(
        name="a2", source_type="upload", source_ref="up", crs="EPSG:4326",
        schema_profile={"a": 2, "b": 1}))
    assert d1.version_fingerprint == d2.version_fingerprint


# ── §10/§18 immutable revision + exact replay ───────────────────────────────

def test_revision_immutable_and_exact_replay_after_graph_edit(db_session):
    db = db_session
    proj = _seed_org_project(db)
    wf = _make_workflow(db, proj.id, [
        {"step_id": "s1", "tool_name": "t_a", "dependencies": []},
    ])
    v1_fp = compute_graph_fingerprint(wf.graph_spec)

    # Run v1.
    run1 = _run(WorkflowEngine.execute_workflow_run(
        db=db, workflow_id=wf.id, tool_registry=FakeRegistry(),
        expected_project_id=proj.id, session_id="sess"))
    assert run1.status == "completed"
    assert run1.run_manifest["graph_fingerprint"] == v1_fp

    # Edit the graph → publishes revision 2.
    ProjectService.update_workflow(
        db, proj.id, wf.id,
        graph_spec={"steps": [
            {"step_id": "s1", "tool_name": "t_a", "dependencies": []},
            {"step_id": "s2", "tool_name": "t_b", "dependencies": ["s1"]},
        ]},
    )
    v2_fp = compute_graph_fingerprint(db.get(Workflow, wf.id).graph_spec)
    assert v2_fp != v1_fp
    assert len(ProjectService.list_workflow_revisions(db, proj.id, wf.id)) == 2

    # Replay the v1 run in EXACT mode → must use the v1 frozen graph, not v2.
    replayed = _run(WorkflowEngine.replay_run(
        db=db, prior_run_id=run1.id, tool_registry=FakeRegistry(),
        mode="exact", expected_project_id=proj.id, session_id="sess"))
    assert replayed.run_manifest["graph_fingerprint"] == v1_fp, "exact replay must freeze v1 graph"
    assert replayed.run_manifest["graph_fingerprint"] != v2_fp
    # And it must NOT have executed the new s2 step.
    assert {s["step_id"] for s in replayed.run_manifest["steps"]} == {"s1"}


def test_replay_latest_uses_current_graph(db_session):
    db = db_session
    proj = _seed_org_project(db)
    wf = _make_workflow(db, proj.id, [{"step_id": "s1", "tool_name": "t_a", "dependencies": []}])
    run1 = _run(WorkflowEngine.execute_workflow_run(
        db=db, workflow_id=wf.id, tool_registry=FakeRegistry(),
        expected_project_id=proj.id, session_id="sess"))
    ProjectService.update_workflow(
        db, proj.id, wf.id,
        graph_spec={"steps": [
            {"step_id": "s1", "tool_name": "t_a", "dependencies": []},
            {"step_id": "s2", "tool_name": "t_b", "dependencies": ["s1"]},
        ]},
    )
    replayed = _run(WorkflowEngine.replay_run(
        db=db, prior_run_id=run1.id, tool_registry=FakeRegistry(),
        mode="latest", expected_project_id=proj.id, session_id="sess"))
    assert {s["step_id"] for s in replayed.run_manifest["steps"]} == {"s1", "s2"}


# ── §25 run fingerprint determinism ─────────────────────────────────────────

def test_replay_same_run_yields_same_run_fingerprint(db_session):
    db = db_session
    proj = _seed_org_project(db)
    wf = _make_workflow(db, proj.id, [
        {"step_id": "s1", "tool_name": "t_a", "dependencies": []},
    ])
    run1 = _run(WorkflowEngine.execute_workflow_run(
        db=db, workflow_id=wf.id, tool_registry=FakeRegistry(),
        expected_project_id=proj.id, session_id="sess",
        input_bindings={"aoi": "Haidian"}))
    run2 = _run(WorkflowEngine.replay_run(
        db=db, prior_run_id=run1.id, tool_registry=FakeRegistry(),
        mode="exact", expected_project_id=proj.id, session_id="sess"))
    assert run1.run_fingerprint is not None
    assert run1.run_fingerprint == run2.run_fingerprint, "identical reproducible inputs ⇒ same fp"


# ── §13 truthful artifact metadata ──────────────────────────────────────────

def test_artifact_metadata_raster_not_geojson(db_session):
    db = db_session
    proj = _seed_org_project(db)
    wf = _make_workflow(db, proj.id, [{"step_id": "s1", "tool_name": "ndvi", "dependencies": []}])
    reg = FakeRegistry(results={"ndvi": {"success": True, "raster_source": {"bounds": [0, 0, 1, 1]}}})
    _run(WorkflowEngine.execute_workflow_run(
        db=db, workflow_id=wf.id, tool_registry=reg,
        expected_project_id=proj.id, session_id="sess"))
    art = db.execute(select(Artifact)).scalars().first()
    assert art.artifact_type == "raster"
    assert art.format == "raster"
    assert art.crs is None, "raster CRS must be NULL (unknown), not fabricated EPSG:4326"


def test_artifact_metadata_non_wgs84_crs_preserved(db_session):
    db = db_session
    proj = _seed_org_project(db)
    wf = _make_workflow(db, proj.id, [{"step_id": "s1", "tool_name": "projected", "dependencies": []}])
    reg = FakeRegistry(results={"projected": {
        "success": True, "ref_id": "ref:x", "feature_count": 5, "crs": "EPSG:32650"}})
    _run(WorkflowEngine.execute_workflow_run(
        db=db, workflow_id=wf.id, tool_registry=reg,
        expected_project_id=proj.id, session_id="sess"))
    art = db.execute(select(Artifact)).scalars().first()
    assert art.crs == "EPSG:32650", "real CRS from the result must be preserved, not overwritten"
    assert art.format == "geojson"


# ── §16/§19 partial-run + resume ────────────────────────────────────────────

def test_partial_failure_records_completed_steps_then_resumes(db_session):
    db = db_session
    proj = _seed_org_project(db)
    wf = _make_workflow(db, proj.id, [
        {"step_id": "s1", "tool_name": "t_a", "dependencies": []},
        {"step_id": "s2", "tool_name": "t_b", "dependencies": ["s1"]},
        {"step_id": "s3", "tool_name": "t_c", "dependencies": ["s2"]},
    ])
    # First run: s2 explodes → partial.
    run1 = _run(WorkflowEngine.execute_workflow_run(
        db=db, workflow_id=wf.id, tool_registry=FakeRegistry(fail_on="t_b"),
        expected_project_id=proj.id, session_id="sess"))
    assert run1.status == "failed"
    assert run1.completed_steps == ["s1"], f"got {run1.completed_steps}"
    assert run1.error_message and "exploded" in run1.error_message

    # Resume with a registry that no longer fails → completes s2,s3.
    resumed = _run(WorkflowEngine.resume_run(
        db=db, prior_run_id=run1.id, tool_registry=FakeRegistry(),
        expected_project_id=proj.id, session_id="sess"))
    assert resumed.status == "completed"
    assert resumed.completed_steps == ["s1", "s2", "s3"], f"got {resumed.completed_steps}"
    # s1's original artifact must NOT have been duplicated.
    arts = db.execute(select(Artifact).where(Artifact.metadata_json["step_id"].as_string() == "s1")).scalars().all()
    assert len(arts) == 1


def test_resume_rejects_when_no_completed_steps(db_session):
    db = db_session
    proj = _seed_org_project(db)
    wf = _make_workflow(db, proj.id, [{"step_id": "s1", "tool_name": "t_a", "dependencies": []}])
    run1 = _run(WorkflowEngine.execute_workflow_run(
        db=db, workflow_id=wf.id, tool_registry=FakeRegistry(fail_on="t_a"),
        expected_project_id=proj.id, session_id="sess"))
    assert run1.status == "failed" and run1.completed_steps == []
    with pytest.raises(ValueError, match="no completed steps"):
        _run(WorkflowEngine.resume_run(
            db=db, prior_run_id=run1.id, tool_registry=FakeRegistry(),
            expected_project_id=proj.id, session_id="sess"))


# ── §17/§19 stale input → resume rejected / full rerun ──────────────────────

def test_resume_rejects_on_stale_input(db_session):
    db = db_session
    proj = _seed_org_project(db)
    ds = ProjectDataset(
        id=f"ds_{uuid.uuid4().hex[:6]}", project_id=proj.id, name="d",
        source_type="upload", source_ref="up_1", crs="EPSG:4326",
        version_fingerprint="fp1")
    db.add(ds)
    db.commit()
    wf = _make_workflow(db, proj.id, [
        {"step_id": "s1", "tool_name": "t_a", "dependencies": []},
        {"step_id": "s2", "tool_name": "t_b", "dependencies": ["s1"]},
    ])
    run1 = _run(WorkflowEngine.execute_workflow_run(
        db=db, workflow_id=wf.id, tool_registry=FakeRegistry(fail_on="t_b"),
        expected_project_id=proj.id, session_id="sess"))
    assert run1.completed_steps == ["s1"]

    # Drift the dataset's content identity (different source_ref → new fingerprint).
    ds.source_ref = "up_CHANGED"
    db.commit()

    with pytest.raises(ValueError, match="fingerprints changed"):
        _run(WorkflowEngine.resume_run(
            db=db, prior_run_id=run1.id, tool_registry=FakeRegistry(),
            expected_project_id=proj.id, session_id="sess"))

    # allow_rerun=True falls back to a full fresh run instead of raising.
    rerun = _run(WorkflowEngine.resume_run(
        db=db, prior_run_id=run1.id, tool_registry=FakeRegistry(),
        expected_project_id=proj.id, session_id="sess", allow_rerun=True))
    assert rerun.status == "completed"


# ── §21 authorization ───────────────────────────────────────────────────────

def test_cross_project_run_blocked(db_session):
    db = db_session
    pa = _seed_org_project(db, "pa", org_id=1)
    pb = Project(id=f"proj_{uuid.uuid4().hex[:6]}", name="pb", org_id=1, status="active")
    db.add(pb)
    db.commit()
    wf = _make_workflow(db, pa.id, [{"step_id": "s1", "tool_name": "t_a", "dependencies": []}])
    with pytest.raises(ValueError, match="does not belong to project"):
        _run(WorkflowEngine.execute_workflow_run(
            db=db, workflow_id=wf.id, tool_registry=FakeRegistry(),
            expected_project_id=pb.id, session_id="sess"))


def test_replay_re_authorizes_project(db_session):
    db = db_session
    pa = _seed_org_project(db, "pa", org_id=1)
    pb = Project(id=f"proj_{uuid.uuid4().hex[:6]}", name="pb", org_id=1, status="active")
    db.add(pb)
    db.commit()
    wf = _make_workflow(db, pa.id, [{"step_id": "s1", "tool_name": "t_a", "dependencies": []}])
    run1 = _run(WorkflowEngine.execute_workflow_run(
        db=db, workflow_id=wf.id, tool_registry=FakeRegistry(),
        expected_project_id=pa.id, session_id="sess"))
    with pytest.raises(ValueError, match="does not belong to project"):
        _run(WorkflowEngine.replay_run(
            db=db, prior_run_id=run1.id, tool_registry=FakeRegistry(),
            mode="exact", expected_project_id=pb.id, session_id="sess"))


def test_resume_re_authorizes_project(db_session):
    db = db_session
    pa = _seed_org_project(db, "pa", org_id=1)
    pb = Project(id=f"proj_{uuid.uuid4().hex[:6]}", name="pb", org_id=1, status="active")
    db.add(pb)
    db.commit()
    wf = _make_workflow(db, pa.id, [
        {"step_id": "s1", "tool_name": "t_a", "dependencies": []},
        {"step_id": "s2", "tool_name": "t_b", "dependencies": ["s1"]},
    ])
    run1 = _run(WorkflowEngine.execute_workflow_run(
        db=db, workflow_id=wf.id, tool_registry=FakeRegistry(fail_on="t_b"),
        expected_project_id=pa.id, session_id="sess"))
    with pytest.raises(ValueError, match="does not belong to project"):
        _run(WorkflowEngine.resume_run(
            db=db, prior_run_id=run1.id, tool_registry=FakeRegistry(),
            expected_project_id=pb.id, session_id="sess"))


# ── §23 run comparison ──────────────────────────────────────────────────────

def test_compare_runs_surfaces_real_diffs(db_session):
    db = db_session
    proj = _seed_org_project(db)
    wf = _make_workflow(db, proj.id, [{"step_id": "s1", "tool_name": "t_a", "dependencies": []}])
    run_a = _run(WorkflowEngine.execute_workflow_run(
        db=db, workflow_id=wf.id, tool_registry=FakeRegistry(version="1.0#cv1"),
        expected_project_id=proj.id, session_id="sess", input_bindings={"aoi": "Haidian"}))
    run_b = _run(WorkflowEngine.execute_workflow_run(
        db=db, workflow_id=wf.id, tool_registry=FakeRegistry(version="2.0#cv3"),
        expected_project_id=proj.id, session_id="sess", input_bindings={"aoi": "Chaoyang"}))

    diff = WorkflowEngine.compare_runs(db, run_a, run_b)
    assert "aoi" in diff["inputs_changed"]["diff_keys"]
    assert diff["revision"]["graph_same"] is True
    assert diff["tool_versions_changed"], "different tool versions must surface"
    assert diff["tool_versions_changed"].get("t_a") == ("1.0#cv1", "2.0#cv3")
    assert diff["run_fingerprint"]["same"] is False


def test_compare_runs_identical_replay_same_fingerprint(db_session):
    db = db_session
    proj = _seed_org_project(db)
    wf = _make_workflow(db, proj.id, [{"step_id": "s1", "tool_name": "t_a", "dependencies": []}])
    run_a = _run(WorkflowEngine.execute_workflow_run(
        db=db, workflow_id=wf.id, tool_registry=FakeRegistry(),
        expected_project_id=proj.id, session_id="sess", input_bindings={"x": 1}))
    run_b = _run(WorkflowEngine.replay_run(
        db=db, prior_run_id=run_a.id, tool_registry=FakeRegistry(),
        mode="exact", expected_project_id=proj.id, session_id="sess"))
    diff = WorkflowEngine.compare_runs(db, run_a, run_b)
    assert diff["run_fingerprint"]["same"] is True
    assert diff["inputs_changed"]["diff_keys"] == []
