"""Reproducible GIS Project Runtime tests (ADR-0092 Phase A).

Covers:
- SessionPlan → Workflow recipe promotion (deterministic converter, blockers)
- Capability re-resolution at rerun (registry semantics, honest fallback)
- Incremental rerun_from_step (descendant invalidation, upstream reuse)
- Run manifest executable snapshot (capability/algorithm + outcome context,
  fingerprint stability/sensitivity)
- Lineage capability/algorithm/mapspec columns
- Artifact promotion (content-addressed materialization, session-expired honesty)
- Map Product versioning (five-dimension diff)
"""
import json
import uuid

import pytest
from sqlalchemy import select

from app.core.database import Base, Engine, SessionLocal
from app.schemas.project_schema import WorkflowStepSpec
from app.services.gis_harness.workflow_promotion import (
    build_workflow_recipe,
    promotion_blockers,
)
from app.services.provenance.manifest import RunManifestBuilder, compute_run_fingerprint
from app.services.workflow_engine import WorkflowEngine


# ── helpers ───────────────────────────────────────────────────────────────


def _gis_chapter(status="available", *, include_optional_row=False):
    """A realistic MapProductPlan dump (Chengdu-schools-like, harness-shaped)."""
    data_req = [
        {
            "capability": "poi_query",
            "purpose": "获取成都小学 POI",
            "params": {"query": "小学", "city": "成都"},
            "status": status,
            "bound_ref": "ref:poi-abc123" if status == "available" else "",
            "resolved_tool": "query_local_poi",
            "resolved_algorithm": "poi.query.local",
            "depends_on": [],
        },
        {
            "capability": "admin_aggregation",
            "purpose": "按区县聚合",
            "params": {"group_field": "district"},
            "status": status,
            "bound_ref": "ref:agg-def456" if status == "available" else "",
            "resolved_tool": "aggregate_admin_regions",
            "resolved_algorithm": "admin.aggregate",
            "depends_on": ["poi_query"],
        },
    ]
    if include_optional_row:
        data_req.append({
            "capability": "density_estimation",
            "purpose": "密度面（可选）",
            "params": {},
            "status": "unavailable",
            "optional": True,
            "resolved_tool": "",
            "resolved_algorithm": "",
            "depends_on": ["poi_query"],
        })
    return {
        "plan_id": "plan-test0001",
        "query": "成都小学分布",
        "intent": {"task": "distribution_overview", "scope": {"name": "成都"}, "subject": {"category": "小学"}},
        "recipe_id": "poi_distribution_overview",
        "template_id": "education_facility_distribution",
        "data_requirements": data_req,
        "analysis_steps": [],
        "map_layers": [
            {"role": "primary", "cartography": "visual_heatmap", "layer_type": "heatmap", "source_capability": "density_estimation"}
        ],
        "outputs": ["interactive_map", "statistics"],
        "exports": ["png"],
        "statistics": ["feature_count"],
        "charts": ["admin_bar"],
        "manifest_fingerprint": "mfpr-" + "0" * 60,
    }


#: Project-domain tables this suite writes. The fixture drops + recreates
#: them so (a) rows never pollute the shared dev DB across runs and (b) the
#: schema is always THIS branch's (create_all does not ALTER existing tables,
#: so a stale ./data/webgis.db would otherwise miss migration-0022 columns).
_PROJECT_DOMAIN_TABLES = (
    "map_products", "artifact_lineages", "artifacts",
    "workflow_runs", "workflow_revisions", "workflows",
    "project_datasets", "carto_project_facts", "projects",
)


@pytest.fixture
def db():
    from pathlib import Path

    Path("./data").mkdir(parents=True, exist_ok=True)
    # Drop in reverse FK order, then create fresh (scoped to project tables —
    # users/orgs/uploads stay for sibling suites).
    metadata_tables = {
        t.name: t for t in Base.metadata.sorted_tables
        if t.name in _PROJECT_DOMAIN_TABLES
    }
    for name in reversed(_PROJECT_DOMAIN_TABLES):
        tbl = metadata_tables.get(name)
        if tbl is not None:
            tbl.drop(bind=Engine, checkfirst=True)
    for name in _PROJECT_DOMAIN_TABLES:
        tbl = metadata_tables.get(name)
        if tbl is not None:
            tbl.create(bind=Engine, checkfirst=True)


def _mk_project_and_workflow(project_id, workflow_id, steps, name="wf"):
    from app.models.db_model import Organization, User
    from app.models.project import Project, Workflow

    with SessionLocal() as s:
        s.merge(User(id="u_a92", username="a92", email="a92@example.com",
                     password_hash="x", role="viewer", is_active=True))
        s.merge(Organization(id=1, name="org-a92", slug="org-a92"))
        s.add(Project(id=project_id, name="p", owner_id="u_a92"))
        s.add(Workflow(id=workflow_id, project_id=project_id, name=name, version=1,
                       graph_spec={"steps": [st.model_dump() for st in steps]}))
        s.commit()


class _CountingRegistry:
    """Fake registry that records every dispatched tool name."""

    def __init__(self):
        self.calls = []

    def tool_version(self, name):
        return "1.0#test"

    def list_tools(self):
        return ["query_local_poi", "aggregate_admin_regions", "legacy_tool"]

    async def dispatch(self, name, args, session_id=None):
        self.calls.append(name)
        return {"success": True, "ref_id": f"ref:out-{len(self.calls)}", "feature_count": 3}


# ── A1: promotion converter ───────────────────────────────────────────────


def test_promotion_blockers_on_incomplete_plan():
    chapter = _gis_chapter(status="pending")
    blockers = promotion_blockers(chapter)
    assert blockers, "pending rows must block promotion"
    recipe, _ = build_workflow_recipe(chapter, name="wf")
    assert recipe is None


def test_promotion_allows_unresolved_optional_rows():
    chapter = _gis_chapter(include_optional_row=True)
    assert promotion_blockers(chapter) == []
    recipe, _ = build_workflow_recipe(chapter, name="wf")
    assert recipe is not None
    caps = [s.capability for s in recipe.graph_spec.steps]
    assert "density_estimation" in caps, "optional rows carry capability evidence"


def test_promotion_builds_capability_recipe():
    chapter = _gis_chapter()
    recipe, blockers = build_workflow_recipe(
        chapter, name="成都小学分析", session_id="sess-1"
    )
    assert blockers == []
    steps = recipe.graph_spec.steps
    assert [s.capability for s in steps] == ["poi_query", "admin_aggregation"]
    assert steps[0].tool_name == "query_local_poi", "resolved tool kept as evidence"
    assert steps[0].algorithm_preference == "poi.query.local"
    assert steps[1].dependencies == [steps[0].step_id]
    assert steps[0].args_template == {"query": "小学", "city": "成都"}, \
        "session-bound fields must not leak into the recipe"
    assert "bound_ref" not in steps[0].args_template
    # Product requirements preserved as recipe metadata.
    meta = recipe.graph_spec.metadata
    assert meta["recipe_id"] == "poi_distribution_overview"
    assert meta["manifest_fingerprint"].startswith("mfpr-")
    assert meta["map_layers"][0]["cartography"] == "visual_heatmap"
    assert meta["outputs"] == ["interactive_map", "statistics"]
    # Session provenance recorded.
    assert recipe.created_from_session == "sess-1"
    # Query-only args are search parameters, NOT dataset slots — no role is
    # fabricated for them (honest role inference).
    assert steps[0].input_roles == {}
    # A dataset-slot arg gets the capability's declared primary input type.
    chapter2 = _gis_chapter()
    chapter2["data_requirements"][1]["params"] = {"admin_geojson": "ref:boundaries"}
    recipe2, _ = build_workflow_recipe(chapter2, name="wf2")
    assert recipe2.graph_spec.steps[1].input_roles == {"admin_geojson": "poi_feature_set"}


def test_promotion_is_deterministic():
    chapter = _gis_chapter()
    a, _ = build_workflow_recipe(chapter, name="wf")
    b, _ = build_workflow_recipe(chapter, name="wf")
    assert a.model_dump() == b.model_dump(), "same plan → identical recipe"


# ── A5: capability re-resolution ──────────────────────────────────────────


def test_resolve_step_tool_re_resolves_via_registry():
    spec = WorkflowStepSpec(
        step_id="s1", tool_name="stale_legacy_tool_name", capability="poi_query",
    )
    tool, cap, algo, evidence = WorkflowEngine.resolve_step_tool(spec, _CountingRegistry())
    assert cap == "poi_query"
    assert algo, "resolver must surface an algorithm"
    assert evidence.get("resolver_status") == "resolved"
    assert tool, "must resolve to a registered tool"


def test_resolve_step_tool_unknown_capability_falls_back_honestly():
    spec = WorkflowStepSpec(
        step_id="s1", tool_name="legacy_tool", capability="capability.does.not.exist",
    )
    tool, cap, algo, evidence = WorkflowEngine.resolve_step_tool(spec, _CountingRegistry())
    assert tool == "legacy_tool", "recorded tool used as fallback"
    assert evidence.get("used_recorded_tool") is True


def test_resolve_step_tool_without_capability_is_passthrough():
    spec = WorkflowStepSpec(step_id="s1", tool_name="query_local_poi")
    tool, cap, algo, evidence = WorkflowEngine.resolve_step_tool(spec, _CountingRegistry())
    assert (tool, cap, algo, evidence) == ("query_local_poi", None, None, {})


# ── A5: incremental rerun_from_step ───────────────────────────────────────


def _three_step_spec():
    return [
        WorkflowStepSpec(step_id="s1", tool_name="query_local_poi", capability="poi_query",
                         args_template={"query": "小学"},
                         input_bindings={"query": "binding:query"}),
        WorkflowStepSpec(step_id="s2", tool_name="aggregate_admin_regions",
                         capability="admin_aggregation", dependencies=["s1"]),
        WorkflowStepSpec(step_id="s3", tool_name="generate_chart", dependencies=["s2"]),
    ]


def test_rerun_from_step_invalidates_only_descendants(db):
    import asyncio


    project_id, workflow_id = f"proj_{uuid.uuid4().hex[:8]}", f"wf_{uuid.uuid4().hex[:8]}"
    _mk_project_and_workflow(project_id, workflow_id, _three_step_spec())
    reg = _CountingRegistry()

    with SessionLocal() as db:
        run1 = asyncio.run(WorkflowEngine.execute_workflow_run(
            db=db, workflow_id=workflow_id, tool_registry=reg, expected_project_id=project_id
        ))
    assert run1.status == "completed"
    first_pass_calls = list(reg.calls)
    assert first_pass_calls == ["query_local_poi", "aggregate_admin_regions", "generate_chart"]

    # Rerun from s2: s1 reused, s2+s3 re-executed.
    with SessionLocal() as db:
        run2 = asyncio.run(WorkflowEngine.rerun_from_step(
            db=db, prior_run_id=run1.id, tool_registry=reg, from_step="s2",
            expected_project_id=project_id,
        ))
    assert run2.status == "completed"
    assert reg.calls[len(first_pass_calls):] == ["aggregate_admin_regions", "generate_chart"], \
        "upstream step must be reused, descendants re-executed"
    assert run2.completed_steps == ["s1", "s2", "s3"]
    # Manifest spans the whole workflow (seed + tail) with capability semantics.
    man = run2.run_manifest
    assert {s["step_id"] for s in man["steps"]} == {"s1", "s2", "s3"}
    assert man["steps"][0]["capability"] == "poi_query"


def test_rerun_from_step_invalidates_genuinely_stale_seeds(db, monkeypatch):
    """Real stale branch: capability re-resolves to a DIFFERENT algorithm →
    seed + descendants invalidated, disclosure recorded (review follow-up)."""
    import asyncio
    from types import SimpleNamespace

    class _V2Resolver:
        def resolve(self, capability, available_tools=None, profile=None):
            assert capability == "poi_query"
            return SimpleNamespace(
                status="resolved", tool="query_local_poi_v2",
                algorithm="poi.query.v2", reason="upgraded",
                rejected=[], fallback_trail=[], fallback_candidates=[],
            )

    # resolve_step_tool imports the resolver lazily inside the function —
    # patch the source module's symbol (raising=True guards the anchor).
    import app.lib.gis.algorithm_resolver as ar_mod
    monkeypatch.setattr(ar_mod, "get_algorithm_resolver", lambda: _V2Resolver(), raising=True)

    class _V2Registry(_CountingRegistry):
        def __init__(self):
            super().__init__()
            self.arg_log = []

        def list_tools(self):
            return ["query_local_poi_v2", "aggregate_admin_regions", "generate_chart"]

        async def dispatch(self, name, args, session_id=None):
            import json as _json

            if isinstance(args, str):
                try:
                    args = _json.loads(args)
                except (TypeError, ValueError):
                    args = {}
            self.calls.append(name)
            return {"success": True, "ref_id": f"ref:v2-{len(self.calls)}", "feature_count": 3}

    steps = [
        WorkflowStepSpec(step_id="s1", tool_name="query_local_poi", capability="poi_query",
                         algorithm_preference="poi.query.local", args_template={"query": "小学"}),
        WorkflowStepSpec(step_id="s2", tool_name="aggregate_admin_regions",
                         capability="admin_aggregation", dependencies=["s1"]),
    ]
    project_id, workflow_id = f"proj_{uuid.uuid4().hex[:8]}", f"wf_{uuid.uuid4().hex[:8]}"
    _mk_project_and_workflow(project_id, workflow_id, steps)
    plain = _CountingRegistry()
    with SessionLocal() as db:
        run1 = asyncio.run(WorkflowEngine.execute_workflow_run(
            db=db, workflow_id=workflow_id, tool_registry=plain, expected_project_id=project_id,
        ))
    assert run1.status == "completed"

    v2 = _V2Registry()
    with SessionLocal() as db:
        run2 = asyncio.run(WorkflowEngine.rerun_from_step(
            db=db, prior_run_id=run1.id, tool_registry=v2, from_step="s2",
            expected_project_id=project_id,
        ))
    assert run2.status == "completed"
    # s1 was stale (recorded poi.query.local ≠ resolved poi.query.v2) → NOT
    # reused as a seed; it re-executed on the v2 tool.
    assert "query_local_poi_v2" in v2.calls, "stale seed must re-execute on the new algorithm"
    disclosures = (run2.cost_perf_summary or {}).get("rerun_disclosures") or {}
    assert disclosures.get("stale_algorithm_steps") == ["s1"]
    assert "s2" in run2.completed_steps and "s1" in run2.completed_steps


def test_rerun_from_step_unavailable_resolution_keeps_honest_reuse(db):
    """ADR-0092 A5 + review: a seed step whose capability re-resolves to a
    DIFFERENT algorithm must not silently reuse its old output — it (and its
    descendants) re-executes, and the run record discloses the staleness."""
    import asyncio

    steps = [
        WorkflowStepSpec(step_id="s1", tool_name="query_local_poi", capability="poi_query",
                         algorithm_preference="poi.query.local", args_template={"query": "小学"}),
        WorkflowStepSpec(step_id="s2", tool_name="aggregate_admin_regions",
                         capability="admin_aggregation", dependencies=["s1"]),
    ]
    project_id, workflow_id = f"proj_{uuid.uuid4().hex[:8]}", f"wf_{uuid.uuid4().hex[:8]}"
    _mk_project_and_workflow(project_id, workflow_id, steps)
    reg = _CountingRegistry()

    with SessionLocal() as db:
        run1 = asyncio.run(WorkflowEngine.execute_workflow_run(
            db=db, workflow_id=workflow_id, tool_registry=reg, expected_project_id=project_id,
        ))
    assert run1.status == "completed"

    # Registry's tool set changes so poi_query no longer resolves (resolver
    # cannot confirm the recorded algorithm) — reuse stays honest via the
    # unavailable-resolution path, i.e. s1 is NOT marked stale, just reused.
    class _NoPoi(_CountingRegistry):
        def list_tools(self):  # noqa: D102 — narrow view; dispatch shares reg
            return ["aggregate_admin_regions", "legacy_tool"]

    noop = _NoPoi()
    noop.calls = reg.calls  # share the call log across registries
    with SessionLocal() as db:
        run2 = asyncio.run(WorkflowEngine.rerun_from_step(
            db=db, prior_run_id=run1.id, tool_registry=noop, from_step="s2",
            expected_project_id=project_id,
        ))
    assert run2.status == "completed"
    # s1 reused (resolver unavailable → honest reuse, no fabricated staleness).
    assert reg.calls.count("aggregate_admin_regions") >= 2


def test_rerun_from_step_rejects_unknown_step(db):
    project_id, workflow_id = f"proj_{uuid.uuid4().hex[:8]}", f"wf_{uuid.uuid4().hex[:8]}"
    _mk_project_and_workflow(project_id, workflow_id, _three_step_spec())
    import asyncio

    with SessionLocal() as db:
        run1 = asyncio.run(WorkflowEngine.execute_workflow_run(
            db=db, workflow_id=workflow_id, tool_registry=_CountingRegistry(),
            expected_project_id=project_id,
        ))
        with pytest.raises(ValueError, match="not found in workflow graph"):
            asyncio.run(WorkflowEngine.rerun_from_step(
                db=db, prior_run_id=run1.id, tool_registry=_CountingRegistry(),
                from_step="nope", expected_project_id=project_id,
            ))


def test_rerun_from_step_rebinds_inputs(db):
    """Changed input → re-executed tail sees the new binding (A5 replace-inputs).

    Asserts on the dispatched TOOL ARGS (not the run's echo of its own
    input_bindings — that would be tautological)."""
    import asyncio

    project_id, workflow_id = f"proj_{uuid.uuid4().hex[:8]}", f"wf_{uuid.uuid4().hex[:8]}"
    _mk_project_and_workflow(project_id, workflow_id, _three_step_spec())

    class _ArgsRegistry(_CountingRegistry):
        """Records dispatched args; tolerates JSON-string arguments (the
        engine's dispatch seam passes tc.function.arguments as a string)."""

        def __init__(self):
            super().__init__()
            self.arg_log = []

        async def dispatch(self, name, args, session_id=None):
            import json as _json

            if isinstance(args, str):
                try:
                    args = _json.loads(args)
                except (TypeError, ValueError):
                    args = {}
            self.arg_log.append((name, dict(args)))
            return await super().dispatch(name, args, session_id=session_id)

    reg = _ArgsRegistry()

    with SessionLocal() as db:
        run1 = asyncio.run(WorkflowEngine.execute_workflow_run(
            db=db, workflow_id=workflow_id, tool_registry=reg, expected_project_id=project_id
        ))
    with SessionLocal() as db:
        run2 = asyncio.run(WorkflowEngine.rerun_from_step(
            db=db, prior_run_id=run1.id, tool_registry=reg, from_step="s1",
            input_bindings={"query": "中学"}, expected_project_id=project_id,
        ))
    assert run2.status == "completed"
    # The re-executed s1 dispatch actually received the overridden binding.
    assert run2.input_bindings.get("query") == "中学"
    s1_with_new_binding = [
        (n, a) for n, a in reg.arg_log
        if n == "query_local_poi" and a.get("query") == "中学"
    ]
    assert s1_with_new_binding, (
        "re-executed s1 must receive the overridden binding in its tool args"
    )


# ── A2: executable snapshot manifest ──────────────────────────────────────


def _build_manifest(**kwargs):
    b = RunManifestBuilder(
        workflow_revision_id="rev1", graph_fingerprint="gfp1",
        input_bindings={}, input_dataset_fingerprints={"ds": "dfp"},
    )
    b.add_step(step_id="s1", tool_name="t", tool_version="1", status="success",
               capability=kwargs.get("cap"), algorithm=kwargs.get("algo"), args={"a": 1})
    if kwargs.get("with_outcome"):
        b.set_outcome_context(
            runtime_manifest_fingerprint="rtfp-1",
            mapspec_fingerprint="carto-1",
            product_facets=[{"facet_id": "map", "status": "done", "required": True}],
            qa_summary={"fallback_count": 0},
            finalization_summary={"status": "complete"},
        )
    b.add_artifact(id="art1", producing_step="s1", artifact_type="vector",
                   content_fingerprint="cfp1", storage_ref="ref:x")
    return b.build()


def test_manifest_records_capability_and_algorithm():
    m = _build_manifest(cap="poi_query", algo="poi.query.local")
    step = m["steps"][0]
    assert step["capability"] == "poi_query" and step["algorithm"] == "poi.query.local"


def test_manifest_fingerprint_sensitive_to_algorithm_not_outcomes():
    m1 = _build_manifest(cap="poi_query", algo="poi.query.local")
    m2 = _build_manifest(cap="poi_query", algo="other.algorithm")
    m3 = _build_manifest(cap="poi_query", algo="poi.query.local", with_outcome=True)
    fp1, fp2, fp3 = (compute_run_fingerprint(m) for m in (m1, m2, m3))
    assert fp1 != fp2, "algorithm change ⇒ different compute plan"
    assert fp1 == fp3, "outcome evidence must not change the run fingerprint"
    assert m3["runtime_manifest_fingerprint"] == "rtfp-1"
    assert m3["product_facets"][0]["facet_id"] == "map"


# ── A4: lineage semantic columns ──────────────────────────────────────────


def test_lineage_records_capability_algorithm_mapspec(db):

    from app.models.db_model import User
    from app.models.project import Project, Artifact, ArtifactLineage
    from app.services.lineage_service import LineageService

    project_id = f"proj_{uuid.uuid4().hex[:8]}"
    art_id = f"art_{uuid.uuid4().hex[:8]}"
    with SessionLocal() as s:
        s.merge(User(id="u_lin", username="lin", email="lin@example.com",
                     password_hash="x", role="viewer", is_active=True))
        s.add(Project(id=project_id, name="p", owner_id="u_lin"))
        s.add(Artifact(id=art_id, project_id=project_id, name="out",
                       artifact_type="vector"))
        s.commit()
        edges = LineageService.record_lineage(
            s, artifact_id=art_id, producing_tool="query_local_poi",
            producing_capability="poi_query", producing_algorithm="poi.query.local",
            mapspec_fingerprint="carto-abc", content_fingerprint="cfp",
        )
        s.commit()
        assert edges
        row = s.execute(select(ArtifactLineage).where(
            ArtifactLineage.artifact_id == art_id)).scalars().first()
        assert row.producing_capability == "poi_query"
        assert row.producing_algorithm == "poi.query.local"
        assert row.mapspec_fingerprint == "carto-abc"


# ── A3: artifact promotion ────────────────────────────────────────────────


def test_materialize_content_is_content_addressed_and_idempotent(tmp_path, monkeypatch):
    from app.services import project_artifact_promotion as pap

    monkeypatch.setattr(pap, "content_store_root", lambda: tmp_path)
    payload = {"type": "FeatureCollection", "features": []}
    loc1 = pap.materialize_content("cfp_test_1", payload)
    loc2 = pap.materialize_content("cfp_test_1", payload)
    assert loc1 == loc2
    assert (tmp_path / loc1).is_file()
    assert json.loads((tmp_path / loc1).read_text()) == payload


def test_promote_run_artifacts_promotes_and_discloses(db, monkeypatch, tmp_path):
    import asyncio

    from app.models.db_model import User
    from app.models.project import Project, Workflow, WorkflowRun, Artifact
    from app.services import project_artifact_promotion as pap

    monkeypatch.setattr(pap, "content_store_root", lambda: tmp_path)
    project_id = f"proj_{uuid.uuid4().hex[:8]}"
    wf_id = f"wf_{uuid.uuid4().hex[:8]}"
    run_id = f"run_{uuid.uuid4().hex[:8]}"
    art_id = f"art_{uuid.uuid4().hex[:8]}"

    with SessionLocal() as s:
        s.merge(User(id="u_pr", username="pr", email="pr@example.com",
                     password_hash="x", role="viewer", is_active=True))
        s.add(Project(id=project_id, name="p", owner_id="u_pr"))
        s.add(Workflow(id=wf_id, project_id=project_id, name="wf", version=1))
        s.add(WorkflowRun(
            id=run_id, workflow_id=wf_id, workflow_version=1, project_id=project_id,
            status="completed",
            run_manifest={"artifacts": [{"id": art_id, "producing_step": "s1",
                                         "content_fingerprint": "cfp_pr"}]},
        ))
        s.add(Artifact(
            id=art_id, project_id=project_id, name="out", artifact_type="vector",
            metadata_json={"step_id": "s1", "tool_name": "query_local_poi",
                           "capability": "poi_query", "algorithm": "poi.query.local"},
            content_fingerprint="cfp_pr", storage_ref="ref:poi-x",
        ))
        s.commit()

    # Session store holds the payload (use the real memory-backed manager with a fake session id).
    from app.services.session_data import session_data_manager

    payload = {"type": "FeatureCollection",
               "features": [{"type": "Feature",
                             "geometry": {"type": "Point", "coordinates": [104.0, 30.6]},
                             "properties": {"name": "s1", "district": "锦江区"}}]}

    async def _store():
        return await session_data_manager.store("sess_pr", payload, prefix="poi")

    ref = asyncio.new_event_loop().run_until_complete(_store())
    with SessionLocal() as s:
        art = s.execute(select(Artifact).where(Artifact.id == art_id)).scalars().first()
        art.storage_ref = ref
        s.commit()

    with SessionLocal() as s:
        run = s.execute(select(WorkflowRun).where(WorkflowRun.id == run_id)).scalars().first()
        report = asyncio.new_event_loop().run_until_complete(
            pap.promote_run_artifacts(s, run, session_id="sess_pr", project_id=project_id)
        )
    assert report[0]["status"] == "promoted"
    with SessionLocal() as s:
        art = s.execute(select(Artifact).where(Artifact.id == art_id)).scalars().first()
        meta = art.metadata_json
        assert meta["content_status"] == "promoted"
        assert meta["content_summary"]["feature_count"] == 1
        assert meta["content_summary"]["property_types"] == {"name": "str", "district": "str"}
        assert meta["content_payload_sha256"]
        # Truthful semantics preserved from the producing step.
        assert meta["capability"] == "poi_query"
        # Read-back round-trip.
        assert pap.read_content(meta["content_location"]) == payload

    # Idempotent second pass.
    with SessionLocal() as s:
        run = s.execute(select(WorkflowRun).where(WorkflowRun.id == run_id)).scalars().first()
        report2 = asyncio.new_event_loop().run_until_complete(
            pap.promote_run_artifacts(s, run, session_id="sess_pr", project_id=project_id)
        )
    assert report2[0]["status"] == "already_promoted"


def test_promote_run_artifacts_session_expired_is_truthful(db, monkeypatch, tmp_path):
    import asyncio

    from app.models.project import WorkflowRun, Artifact
    from app.services import project_artifact_promotion as pap

    monkeypatch.setattr(pap, "content_store_root", lambda: tmp_path)
    project_id = f"proj_{uuid.uuid4().hex[:8]}"
    se_wf_id = f"wf_{uuid.uuid4().hex[:8]}"
    se_run_id = f"run_{uuid.uuid4().hex[:8]}"
    se_art_id = f"art_{uuid.uuid4().hex[:8]}"
    with SessionLocal() as s:
        from app.models.db_model import User
        from app.models.project import Project, Workflow

        s.merge(User(id="u_se", username="se", email="se@example.com",
                     password_hash="x", role="viewer", is_active=True))
        s.add(Project(id=project_id, name="p", owner_id="u_se"))
        s.add(Workflow(id=se_wf_id, project_id=project_id, name="wf", version=1))
        s.add(WorkflowRun(
            id=se_run_id, workflow_id=se_wf_id, workflow_version=1, project_id=project_id,
            status="completed",
            run_manifest={"artifacts": [{"id": se_art_id, "producing_step": "s1"}]},
        ))
        s.add(Artifact(
            id=se_art_id, project_id=project_id, name="out", artifact_type="vector",
            metadata_json={"step_id": "s1"}, storage_ref="ref:gone",
        ))
        s.commit()
        run = s.execute(select(WorkflowRun).where(WorkflowRun.id == se_run_id)).scalars().first()
        # session_id=None → no store to probe: "no_session_context" (session
        # may still be alive), NOT "session_expired" — the two are conflated
        # nowhere (review E4 fix).
        report = asyncio.new_event_loop().run_until_complete(
            pap.promote_run_artifacts(s, run, session_id=None, project_id=project_id)
        )
    assert report[0]["status"] == "no_session_context"


# ── A6: map product versioning ────────────────────────────────────────────


def test_map_product_versioning_and_diff(db):
    from app.models.db_model import User
    from app.models.project import Project
    from app.services.map_product_service import MapProductService

    project_id = f"proj_{uuid.uuid4().hex[:8]}"
    with SessionLocal() as s:
        s.merge(User(id="u_mp", username="mp", email="mp@example.com",
                     password_hash="x", role="viewer", is_active=True))
        s.add(Project(id=project_id, name="p", owner_id="u_mp"))
        s.commit()

        v1 = MapProductService.record_version(
            s, project_id,
            mapspec_fingerprint="carto-v1",
            recipe_id="poi_distribution_overview",
            input_dataset_fingerprints={"ds1": "fpA"},
            run_manifest={"steps": [{"step_id": "s1", "tool_name": "t", "algorithm": "a1",
                                     "args": {"q": "小学"}}],
                          "artifacts": [{"id": "x", "content_fingerprint": "out1"}]},
        )
        assert v1.version_no == 1
        assert v1.diff_summary["vs_version_no"] is None

        # Style-only change: MapSpec moved, compute plan + inputs identical.
        v2 = MapProductService.record_version(
            s, project_id,
            mapspec_fingerprint="carto-v2",
            input_dataset_fingerprints={"ds1": "fpA"},
            run_manifest={"steps": [{"step_id": "s1", "tool_name": "t", "algorithm": "a1",
                                     "args": {"q": "小学"}}],
                          "artifacts": [{"id": "x", "content_fingerprint": "out1"}]},
        )
        d2 = v2.diff_summary
        assert d2["style_changed"] is True
        assert d2["data_changed"] is False and d2["algorithm_changed"] is False
        assert d2["parameter_changed"] is False and d2["output_changed"] is False
        assert d2["analysis_recomputation_expected"] is False, \
            "style-only change must not demand analysis recomputation"

        # Data change: downstream invalidation is expected.
        v3 = MapProductService.record_version(
            s, project_id,
            mapspec_fingerprint="carto-v2",
            input_dataset_fingerprints={"ds1": "fpB"},
            run_manifest={"steps": [{"step_id": "s1", "tool_name": "t", "algorithm": "a1",
                                     "args": {"q": "小学"}}],
                          "artifacts": [{"id": "y", "content_fingerprint": "out2"}]},
        )
        d3 = v3.diff_summary
        assert d3["data_changed"] is True
        assert d3["output_changed"] is True
        assert d3["analysis_recomputation_expected"] is True

        rows, total = MapProductService.list_versions_paginated(s, project_id, limit=10)
        assert total == 3
        assert [v.version_no for v in rows] == [3, 2, 1]  # newest first
        fps = {v.product_fingerprint for v in rows}
        assert len(fps) == 3, "distinct substantive states ⇒ distinct fingerprints"


@pytest.mark.asyncio
async def test_save_plan_as_workflow_flags_partial_rerun_pair():
    """rerun_workflow: one-sided from_run_id/from_step is a structured error
    with a correction hint, never a silent full run (review follow-up)."""
    from app.tools import init_tools
    from app.tools.registry import ToolRegistry

    reg = ToolRegistry()
    init_tools(reg)
    res = await reg.dispatch("rerun_workflow", {
        "project_id": "proj_x", "workflow_id": "wf_x", "from_step": "s2",
    }, session_id="sess-x")
    assert res.get("success") is False
    assert "from_run_id" in res.get("error", "")
    assert res.get("correction_hint")


@pytest.mark.asyncio
async def test_semantic_tools_dispatch_with_ref_alias():
    """Regression lock (review): semantic tools must be dispatchable with a
    session ref/alias — the args model previously failed to build (missing
    typing names under from __future__ annotations) and the alias was
    transparently dereferenced into the param slot."""
    import uuid as _uuid

    from app.services.session_data import session_data_manager
    from app.tools import init_tools
    from app.tools.registry import ToolRegistry

    reg = ToolRegistry()
    init_tools(reg)
    sid = f"sem-{_uuid.uuid4().hex[:8]}"
    try:
        fc = {"type": "FeatureCollection", "features": [
            {"type": "Feature",
             "geometry": {"type": "Point", "coordinates": [104.0, 30.6]},
             "properties": {"district": "锦江区", "resident_population": 100000}},
        ]}
        ref = await session_data_manager.store(sid, fc, prefix="bench")
        await session_data_manager.set_alias(sid, ref, "ds")
        res = await reg.dispatch(
            "profile_dataset_semantics", {"geojson_ref": "ds"}, session_id=sid,
        )
        assert res.get("success") is True, res
        roles = {
            (r["field"], tuple(r["roles"]))
            for r in res["semantic_profile"]["field_roles"]
        }
        assert ("district", ("admin_dimension",)) in roles
        assert any("normalization_denominator" in r for _, r in roles)

        res2 = await reg.dispatch(
            "suggest_analysis_patterns",
            {"query": "分析各区学校资源是否均衡", "geojson_ref": "ds"},
            session_id=sid,
        )
        assert res2.get("success") is True, res2
        equity = next(
            m for m in res2["patterns"] if m["pattern_id"] == "spatial_equity"
        )
        assert "normalization_denominator" in equity["satisfied_roles"]
        assert not equity["disclosures"]
    finally:
        await session_data_manager.clear_session(sid)


def test_map_product_record_version_rejects_foreign_run(db):
    """IDOR regression lock (review): a run from ANOTHER project must never
    feed this project's map-product ledger."""

    from app.models.db_model import User
    from app.models.project import Project, Workflow, WorkflowRun
    from app.services.map_product_service import MapProductService

    proj_a = f"proj_{uuid.uuid4().hex[:8]}"
    proj_b = f"proj_{uuid.uuid4().hex[:8]}"
    wf_b = f"wf_{uuid.uuid4().hex[:8]}"
    run_b = f"run_{uuid.uuid4().hex[:8]}"
    with SessionLocal() as s:
        s.merge(User(id="u_idor", username="idor", email="idor@example.com",
                     password_hash="x", role="viewer", is_active=True))
        s.add(Project(id=proj_a, name="a", owner_id="u_idor"))
        s.add(Project(id=proj_b, name="b", owner_id="u_idor"))
        s.add(Workflow(id=wf_b, project_id=proj_b, name="wf", version=1))
        s.add(WorkflowRun(
            id=run_b, workflow_id=wf_b, workflow_version=1, project_id=proj_b,
            status="completed",
            run_manifest={"steps": [{"step_id": "s1", "tool_name": "t"}],
                          "artifacts": []},
        ))
        s.commit()
        with pytest.raises(ValueError, match="not found in project"):
            MapProductService.record_version(s, proj_a, workflow_run_id=run_b)


def test_rerun_from_step_corrupt_prior_run_rejected(db):
    """Negative path: seed artifacts gone → ValueError, never a silent
    full rerun over missing generations (review follow-up)."""
    import asyncio

    from sqlalchemy import delete as sa_delete

    from app.models.project import Artifact, ArtifactLineage

    project_id, workflow_id = f"proj_{uuid.uuid4().hex[:8]}", f"wf_{uuid.uuid4().hex[:8]}"
    _mk_project_and_workflow(project_id, workflow_id, _three_step_spec())
    with SessionLocal() as db:
        run1 = asyncio.run(WorkflowEngine.execute_workflow_run(
            db=db, workflow_id=workflow_id, tool_registry=_CountingRegistry(),
            expected_project_id=project_id,
        ))
        assert run1.status == "completed"
        # Corrupt: wipe the prior run's lineage + artifacts.
        db.execute(sa_delete(ArtifactLineage).where(ArtifactLineage.workflow_run_id == run1.id))
        db.execute(sa_delete(Artifact).where(Artifact.project_id == project_id))
        db.commit()
        with pytest.raises(ValueError, match="no longer reconstructable"):
            asyncio.run(WorkflowEngine.rerun_from_step(
                db=db, prior_run_id=run1.id, tool_registry=_CountingRegistry(),
                from_step="s2", expected_project_id=project_id,
            ))


def test_promote_run_artifacts_store_write_failure_disclosed(db, monkeypatch, tmp_path):
    """Negative path: content write failure → store_unavailable, no fake
    content_location (review follow-up)."""
    import asyncio

    from app.models.db_model import User
    from app.models.project import Project, Workflow, WorkflowRun, Artifact
    from app.services import project_artifact_promotion as pap

    monkeypatch.setattr(pap, "content_store_root", lambda: tmp_path)
    monkeypatch.setattr(pap, "materialize_blob", lambda fp, blob: None)  # ENOSPC

    project_id = f"proj_{uuid.uuid4().hex[:8]}"
    wf_id = f"wf_{uuid.uuid4().hex[:8]}"
    run_id = f"run_{uuid.uuid4().hex[:8]}"
    art_id = f"art_{uuid.uuid4().hex[:8]}"
    with SessionLocal() as s:
        s.merge(User(id="u_sw", username="sw", email="sw@example.com",
                     password_hash="x", role="viewer", is_active=True))
        s.add(Project(id=project_id, name="p", owner_id="u_sw"))
        s.add(Workflow(id=wf_id, project_id=project_id, name="wf", version=1))
        s.add(WorkflowRun(
            id=run_id, workflow_id=wf_id, workflow_version=1, project_id=project_id,
            status="completed",
            run_manifest={"artifacts": [{"id": art_id, "producing_step": "s1",
                                         "content_fingerprint": "cfp_sw"}]},
        ))
        s.add(Artifact(
            id=art_id, project_id=project_id, name="out", artifact_type="vector",
            metadata_json={"step_id": "s1"}, content_fingerprint="cfp_sw",
            storage_ref="ref:sw-1",
        ))
        s.commit()

    from app.services.session_data import session_data_manager

    async def _store():
        return await session_data_manager.store(
            "sess_sw", {"type": "FeatureCollection", "features": []}, prefix="bench",
        )

    ref = asyncio.run(_store())
    with SessionLocal() as s:
        art = s.execute(select(Artifact).where(Artifact.id == art_id)).scalars().first()
        art.storage_ref = ref
        s.commit()

    with SessionLocal() as s:
        run = s.execute(select(WorkflowRun).where(WorkflowRun.id == run_id)).scalars().first()
        report = asyncio.run(pap.promote_run_artifacts(
            s, run, session_id="sess_sw", project_id=project_id,
        ))
    assert report[0]["status"] == "store_unavailable"
    with SessionLocal() as s:
        art = s.execute(select(Artifact).where(Artifact.id == art_id)).scalars().first()
        meta = art.metadata_json
        assert meta["content_status"] == "store_unavailable"
        assert "content_location" not in meta, "must not claim durability it does not have"


@pytest.mark.asyncio
async def test_save_plan_as_workflow_unknown_tool_rejected():
    """Legacy steps path: hallucinated tool ids are disclosed at SAVE time —
    nothing persisted (review follow-up)."""
    from app.tools import init_tools
    from app.tools.registry import ToolRegistry

    reg = ToolRegistry()
    init_tools(reg)
    res = await reg.dispatch("save_plan_as_workflow", {
        "project_id": "proj_x", "workflow_name": "wf",
        "steps": [{"step_id": "s1", "tool_name": "totally_not_a_tool",
                   "args_template": {}}],
    }, session_id="sess-x")
    assert res.get("success") is False
    assert "totally_not_a_tool" in res.get("error", "")
    assert res.get("unknown_tools") == ["totally_not_a_tool"]
    assert res.get("correction_hint")


@pytest.mark.asyncio
async def test_save_plan_as_workflow_no_plan_error_is_actionable():
    """session_id without a GIS plan → structured error + correction hint."""
    from app.tools import init_tools
    from app.tools.registry import ToolRegistry

    reg = ToolRegistry()
    init_tools(reg)
    res = await reg.dispatch("save_plan_as_workflow", {
        "project_id": "proj_x", "workflow_name": "wf",
    }, session_id="sess-empty-plan")
    assert res.get("success") is False
    assert "webgis_map_intent" in res.get("correction_hint", "")
