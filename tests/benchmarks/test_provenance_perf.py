"""Deterministic performance bounds for the provenance subsystem (spec §30).

No network, no LLM, no real tools. Marks: ``perf`` (run with ``-m perf``).
Bounds are deliberately generous (the goal is "avoid N+1 / pathological", not
microsecond tuning) and assert the structural guarantees: level-batched lineage
traversal is O(depth), manifest/fingerprint scale near-linearly with steps.
"""
import statistics
import time

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models.db_model import Organization
from app.models.project import Project, Artifact
from app.services.lineage_service import LineageService
from app.services.provenance import (
    RunManifestBuilder,
    compute_dataset_fingerprint,
    compute_graph_fingerprint,
    compute_run_fingerprint,
)

pytestmark = pytest.mark.perf


def _engine():
    eng = create_engine("sqlite:///:memory:")

    @event.listens_for(eng, "connect")
    def _fk(c, _):
        cur = c.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()
    return eng


def _med(fn, n=11):
    samples = []
    for _ in range(n):
        t0 = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - t0) * 1000.0)
    return statistics.median(samples)


def test_fingerprint_dataset_is_fast():
    schema = {f"field_{i}": {"type": "str"} for i in range(50)}
    med = _med(lambda: compute_dataset_fingerprint("upload", "ref:up-1", "EPSG:4326", schema))
    assert med < 5.0, f"dataset fingerprint median {med:.3f}ms > 5ms"


def test_fingerprint_graph_scales_near_linearly():
    def graph(n):
        return {"steps": [{"step_id": f"s{i}", "tool_name": "t", "dependencies": [f"s{i-1}"] if i else []} for i in range(n)]}
    med5 = _med(lambda: compute_graph_fingerprint(graph(5)))
    med20 = _med(lambda: compute_graph_fingerprint(graph(20)))
    assert med20 < 3.0, f"20-step graph fp {med20:.3f}ms"
    assert med20 < (med5 * 8 + 5), "graph fp should scale near-linearly, not explode"


def test_manifest_build_and_run_fingerprint_for_20_steps():
    steps = [{"step_id": f"s{i}", "tool_name": f"t{i}", "tool_version": "1.0#cv1",
              "status": "completed", "args": {"k": i}} for i in range(20)]
    artifacts = [{"id": f"art{i}", "producing_step": f"s{i}", "artifact_type": "vector",
                  "format": "geojson", "crs": None, "content_fingerprint": f"cf{i}", "storage_ref": f"ref:{i}"}
                 for i in range(20)]

    def build():
        b = RunManifestBuilder(workflow_revision_id="r", graph_fingerprint="g",
                               input_bindings={"aoi": "x"}, input_dataset_fingerprints={"d": "f"})
        for s in steps:
            b.add_step(**s)
        for a in artifacts:
            b.add_artifact(**a)
        m = b.build()
        return compute_run_fingerprint(m)

    med = _med(build)
    assert med < 15.0, f"manifest+fp for 20 steps {med:.3f}ms > 15ms"


def _seed_lineage_chain(db, depth):
    db.add(Organization(id=1, name="o", slug="o"))
    db.commit()
    proj = Project(id="proj_b", name="p", org_id=1, status="active")
    db.add(proj)
    db.commit()
    arts = []
    for i in range(depth):
        a = Artifact(id=f"art_{i}", project_id=proj.id, name=f"a{i}", artifact_type="analysis")
        db.add(a)
        arts.append(a)
    db.flush()
    for i in range(1, depth):
        LineageService.record_lineage(db, arts[i].id, "t", parent_artifact_ids=[arts[i - 1].id], commit=False)
    db.commit()
    return proj, arts


def _seed_lineage_fanout(db, edges):
    """One root with `edges` direct children (wide, shallow) to stress batch read."""
    db.add(Organization(id=1, name="o", slug="o"))
    db.commit()
    proj = Project(id="proj_f", name="p", org_id=1, status="active")
    db.add(proj)
    db.commit()
    root = Artifact(id="art_root", project_id=proj.id, name="root", artifact_type="analysis")
    db.add(root)
    db.flush()
    children = []
    for i in range(edges):
        c = Artifact(id=f"art_c{i}", project_id=proj.id, name=f"c{i}", artifact_type="analysis")
        db.add(c)
        children.append(c)
    db.flush()
    for c in children:
        LineageService.record_lineage(db, c.id, "t", parent_artifact_ids=[root.id], commit=False)
    db.commit()
    return proj, root


def test_lineage_traversal_depth_1_5_20_query_count():
    """Level-batched BFS ⇒ lineage-table query count ~ O(depth), not O(nodes)."""
    for depth in (1, 5, 20):
        eng = _engine()
        Base.metadata.create_all(eng)
        db = sessionmaker(bind=eng)()
        try:
            proj, arts = _seed_lineage_chain(db, depth)
            queries = {"n": 0}

            @event.listens_for(eng, "before_execute")
            def _c(conn, clause, *a, **k):
                txt = str(clause)
                if "artifact_lineages" in txt and txt.lstrip().lower().startswith("select"):
                    queries["n"] += 1
            graph = LineageService.get_lineage_graph(db, arts[-1].id, max_depth=depth, project_id=proj.id)
            event.remove(eng, "before_execute", _c)
            assert queries["n"] <= depth + 2, f"depth {depth}: {queries['n']} lineage queries"
            assert len(graph["parents"]) == max(0, depth - 1)
        finally:
            db.close()
            Base.metadata.drop_all(eng)


def test_lineage_traversal_1k_edges_under_bound():
    eng = _engine()
    Base.metadata.create_all(eng)
    db = sessionmaker(bind=eng)()
    try:
        proj, root = _seed_lineage_fanout(db, 1000)
        med = _med(lambda: LineageService.get_lineage_graph(db, root.id, max_depth=5, project_id=proj.id), n=5)
        assert med < 250.0, f"1k-edge traversal {med:.1f}ms > 250ms"
    finally:
        db.close()
        Base.metadata.drop_all(eng)


def test_lineage_traversal_10k_edges_under_bound():
    eng = _engine()
    Base.metadata.create_all(eng)
    db = sessionmaker(bind=eng)()
    try:
        proj, root = _seed_lineage_fanout(db, 10000)
        med = _med(lambda: LineageService.get_lineage_graph(db, root.id, max_depth=5, project_id=proj.id), n=3)
        assert med < 1500.0, f"10k-edge traversal {med:.1f}ms > 1500ms"
    finally:
        db.close()
        Base.metadata.drop_all(eng)
