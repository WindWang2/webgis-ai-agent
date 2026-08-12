"""Reviewer-driven regression tests — lock in the P1/P2 fixes from the review
swarm so the bugs they caught cannot return:

  * run fingerprint stays deterministic when inter-step data (random ref_ids)
    flows through ``input_bindings`` (P1: args leaked into the fingerprint).
  * downstream ``consumers`` traversal is correct & tenant-filtered (was untested).
  * lineage read is level-batched on a WIDE graph (not just a deep one).
  * soft-detach excludes a dataset from lists AND makes resume detect drift.
  * resume after the live workflow is edited uses the FROZEN snapshot.
  * migration: unique (workflow_id, revision_no) + CHECK preservation on all
    altered tables (not just workflow_runs).
"""
import asyncio
import uuid

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models.db_model import Organization
from app.models.project import (
    Project, Artifact, ArtifactLineage,
)
from app.schemas.project_schema import (
    DatasetAttach, WorkflowCreate, WorkflowGraphSpec, WorkflowStepSpec,
)
from app.services.project_service import ProjectService
from app.services.workflow_engine import WorkflowEngine
from app.services.lineage_service import LineageService


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


def _seed(db, name="p", org_id=1):
    db.add(Organization(id=org_id, name=f"o{org_id}", slug=f"o{org_id}"))
    db.commit()
    proj = Project(id=f"proj_{uuid.uuid4().hex[:6]}", name=name, org_id=org_id, status="active")
    db.add(proj)
    db.commit()
    return proj


class _Reg:
    """Stores outputs so resume can reconstruct
    can fail one tool."""

    def __init__(self, fail_on=None):
        self.fail_on = fail_on
        self.version = "1.0#cv1"

    def tool_version(self, n):
        return self.version

    async def dispatch(self, name, args, session_id=None):
        if name == self.fail_on:
            raise RuntimeError(f"{name} boom")
        from app.services.session_data import session_data_manager
        payload = {"produced_by": name, "feature_count": 3}
        ref = await session_data_manager.store(session_id, payload, prefix=name) if session_id else f"ref:{name}"
        return {"success": True, "ref_id": ref, "feature_count": 3, "bbox": [0, 0, 1, 1]}


def _wf(db, proj_id, steps, name="wf"):
    return ProjectService.save_workflow(db, proj_id, WorkflowCreate(
        name=name, graph_spec=WorkflowGraphSpec(steps=[WorkflowStepSpec(**s) for s in steps])))


def _run(coro):
    return asyncio.run(coro)


# ── P1 #2: fingerprint stable with inter-step data flow ─────────────────────

def test_run_fingerprint_stable_with_inter_step_bindings(db_session):
    """A 2-step workflow where s2 consumes s1's output via input_bindings.
    s1 yields a fresh random ref_id each run, but run_fingerprint must NOT
    depend on it (resolved args are excluded from the stable projection)."""
    db = db_session
    proj = _seed(db)
    wf = _wf(db, proj.id, [
        {"step_id": "s1", "tool_name": "t_a", "dependencies": []},
        {"step_id": "s2", "tool_name": "t_b", "dependencies": ["s1"],
         "input_bindings": {"data": "step_s1.result"}},
    ])
    run1 = _run(WorkflowEngine.execute_workflow_run(
        db=db, workflow_id=wf.id, tool_registry=_Reg(),
        expected_project_id=proj.id, session_id="sess"))
    assert run1.status == "completed"
    replay = _run(WorkflowEngine.replay_run(
        db=db, prior_run_id=run1.id, tool_registry=_Reg(), mode="exact",
        expected_project_id=proj.id, session_id="sess"))
    assert run1.run_fingerprint is not None
    assert run1.run_fingerprint == replay.run_fingerprint, (
        "run fingerprint must be stable across replays even when random ref_ids "
        "flow through inter-step input_bindings"
    )


# ── consumers traversal correctness + tenant filter ──────────────────────────

def _art(db, project_id, name):
    a = Artifact(id=f"art_{uuid.uuid4().hex[:6]}", project_id=project_id,
                 name=name, artifact_type="analysis")
    db.add(a)
    db.commit()
    return a


def test_consumers_traversal_multihop(db_session):
    db = db_session
    proj = _seed(db)
    a = _art(db, proj.id, "a")
    b = _art(db, proj.id, "b")
    c = _art(db, proj.id, "c")
    LineageService.record_lineage(db, b.id, "t", parent_artifact_ids=[a.id])   # a -> b
    LineageService.record_lineage(db, c.id, "t", parent_artifact_ids=[b.id])   # b -> c
    graph = LineageService.get_lineage_graph(db, a.id, max_depth=5, project_id=proj.id)
    consumer_ids = {e["consumer_artifact_id"]: e["depth"] for e in graph["consumers"]}
    assert consumer_ids == {b.id: 1, c.id: 2}, consumer_ids


def test_consumers_cross_tenant_filtered(db_session):
    db = db_session
    p1 = _seed(db, "p1", org_id=1)
    db.add(Organization(id=2, name="o2", slug="o2"))
    db.commit()
    p2 = Project(id=f"proj_{uuid.uuid4().hex[:6]}", name="p2", org_id=2, status="active")
    db.add(p2)
    db.commit()
    a = _art(db, p1.id, "a")
    foreign = _art(db, p2.id, "x")
    # Directly inject a cross-tenant consumer edge (a -> foreign).
    db.add(ArtifactLineage(id=f"lin_{uuid.uuid4().hex[:6]}", artifact_id=foreign.id,
                           parent_artifact_id=a.id, producing_tool="leak"))
    db.commit()
    graph = LineageService.get_lineage_graph(db, a.id, max_depth=5, project_id=p1.id)
    assert foreign.id not in {e["consumer_artifact_id"] for e in graph["consumers"]}, (
        "cross-tenant consumer must be filtered"
    )


def test_lineage_wide_graph_is_one_query(db_session):
    """1 root → 100 direct children: level-batched read must be ~1 lineage-table
    query, not 100 (a per-node impl would issue ≥100)."""
    db = db_session
    proj = _seed(db)
    root = _art(db, proj.id, "root")
    for i in range(100):
        c = _art(db, proj.id, f"c{i}")
        LineageService.record_lineage(db, c.id, "t", parent_artifact_ids=[root.id], commit=False)
    db.commit()

    queries = {"n": 0}

    @event.listens_for(db.bind, "before_execute")
    def _c(conn, clause, *a, **k):
        txt = str(clause)
        if "artifact_lineages" in txt and txt.lstrip().lower().startswith("select"):
            queries["n"] += 1
    try:
        graph = LineageService.get_lineage_graph(db, root.id, max_depth=5, project_id=proj.id)
    finally:
        event.remove(db.bind, "before_execute", _c)
    assert queries["n"] <= 3, f"wide graph must be O(1) lineage queries, got {queries['n']}"
    assert len(graph["consumers"]) == 100


# ── soft-detach: list exclusion + resume drift ───────────────────────────────

def test_soft_detach_excludes_and_drifts_resume(db_session):
    db = db_session
    proj = _seed(db)
    ds = ProjectService.attach_dataset(db, proj.id, DatasetAttach(
        name="d", source_type="upload", source_ref="up_1", crs="EPSG:4326"))
    assert ds.version_fingerprint

    wf = _wf(db, proj.id, [
        {"step_id": "s1", "tool_name": "t_a", "dependencies": []},
        {"step_id": "s2", "tool_name": "t_b", "dependencies": ["s1"]},
    ])
    run1 = _run(WorkflowEngine.execute_workflow_run(
        db=db, workflow_id=wf.id, tool_registry=_Reg(fail_on="t_b"),
        expected_project_id=proj.id, session_id="sess"))
    assert run1.completed_steps == ["s1"]
    assert ds.id in (run1.input_dataset_fingerprints or {})

    # Detach → active list excludes it (soft tombstone, INV-DEL1).
    assert ProjectService.detach_dataset(db, proj.id, ds.id) is True
    listed = ProjectService.list_project_datasets(db, proj.id)[0]
    assert all(row.id != ds.id for row in listed)

    # The detached dataset is no longer in the active set → current fingerprints
    # differ from the run's captured set → resume detects drift and rejects.
    with pytest.raises(ValueError, match="fingerprints changed"):
        _run(WorkflowEngine.resume_run(
            db=db, prior_run_id=run1.id, tool_registry=_Reg(),
            expected_project_id=proj.id, session_id="sess"))


# ── resume uses the FROZEN snapshot, not a live edit ─────────────────────────

def test_resume_after_workflow_edit_uses_frozen_snapshot(db_session):
    db = db_session
    proj = _seed(db)
    wf = _wf(db, proj.id, [
        {"step_id": "s1", "tool_name": "t_a", "dependencies": []},
        {"step_id": "s2", "tool_name": "t_b", "dependencies": ["s1"]},
    ])
    run1 = _run(WorkflowEngine.execute_workflow_run(
        db=db, workflow_id=wf.id, tool_registry=_Reg(fail_on="t_b"),
        expected_project_id=proj.id, session_id="sess"))
    assert run1.completed_steps == ["s1"]
    frozen_fp = run1.run_manifest["graph_fingerprint"]

    # Edit the live graph (adds a 3rd step). The partial run's snapshot is v1.
    ProjectService.update_workflow(db, proj.id, wf.id, graph_spec={"steps": [
        {"step_id": "s1", "tool_name": "t_a", "dependencies": []},
        {"step_id": "s2", "tool_name": "t_b", "dependencies": ["s1"]},
        {"step_id": "s3", "tool_name": "t_c", "dependencies": ["s2"]},
    ]})

    resumed = _run(WorkflowEngine.resume_run(
        db=db, prior_run_id=run1.id, tool_registry=_Reg(),
        expected_project_id=proj.id, session_id="sess"))
    assert resumed.status == "completed"
    # Resume executed only s2 (from the frozen v1 graph), NOT the new s3.
    assert {s["step_id"] for s in resumed.run_manifest["steps"]} == {"s1", "s2"}
    assert resumed.run_manifest["graph_fingerprint"] == frozen_fp


# ── migration: unique index + CHECKs on every altered table ──────────────────

def test_migration_unique_revision_number_and_checks(tmp_path, monkeypatch):
    import sqlite3
    from alembic import command
    from alembic.config import Config

    db_path = tmp_path / "m.db"
    url = f"sqlite:///{db_path}"
    # env.py reads DATABASE_URL and overrides the config URL — force SQLite so CI's
    # PostgreSQL DATABASE_URL doesn't hijack this isolated migration test.
    monkeypatch.setenv("DATABASE_URL", url)
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")

    con = sqlite3.connect(str(db_path))
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("INSERT INTO organizations (id, name, slug) VALUES (1,'o','o')")
    con.execute("INSERT INTO projects (id, org_id, name, status) VALUES ('p',1,'p','active')")
    con.execute("INSERT INTO workflows (id, project_id, name, version) VALUES ('w','p','w',1)")
    con.execute(
        "INSERT INTO workflow_revisions (id, workflow_id, revision_no, graph_spec, "
        "graph_fingerprint) VALUES ('r1','w',1,'{}','fp')"
    )
    # Unique (workflow_id, revision_no).
    with pytest.raises(sqlite3.IntegrityError):
        con.execute(
            "INSERT INTO workflow_revisions (id, workflow_id, revision_no, graph_spec, "
            "graph_fingerprint) VALUES ('r2','w',1,'{}','fp2')"
        )
    # CHECKs preserved on the other two altered tables.
    with pytest.raises(sqlite3.IntegrityError):
        con.execute(
            "INSERT INTO project_datasets (id, project_id, name, source_type, quality_status) "
            "VALUES ('d','p','d','upload','bogus_status')"
        )
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("INSERT INTO workflows (id, project_id, name, version) VALUES ('w2','p','w2',0)")
    con.close()
