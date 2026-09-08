"""Wave-11 wiring tests (audit 08 §5.2 / §6.2 items 1/2/3/4/5).

Locks in the lineage / reproducibility V4 wiring:

  * the built-but-orphaned geocompute execution bundle is built at run
    completion; verdict + payload-free lineage projection reach the run read
    endpoints (in-memory AND terminal-evidence-snapshot fallback);
  * workflow runs carry a reproducibility classification in the manifest
    OUTCOME block — and the run fingerprint is UNCHANGED by it;
  * provenance args are redacted + bounded at write time: a ``password`` arg
    and inline GeoJSON never land in DB rows (regression);
  * the compiled runtime manifest captures the numeric backend (python/GEOS/
    PROJ/GDAL/libs) and folds it into its fingerprint (drift guard sees env
    changes);
  * ``lineage_inputs`` are populated at plan construction from provable source
    identity in node parameters (and never affect the semantic fingerprint).
"""
from __future__ import annotations

import asyncio
import json
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models.project import ArtifactLineage, Project, ProjectDataset, WorkflowRun
from app.services.geocompute import (
    ExecutionNode,
    ExecutionPlan,
    NodeCategory,
)
from app.services.geocompute.executor import GeoExecutionEngine, owner_scope_for
from app.services.geocompute.reproducibility import (
    CONDITIONALLY_REPRODUCIBLE,
    REPRODUCIBLE,
    bundle_verdict_block,
    build_execution_bundle,
    lineage_projection,
)
from app.services.provenance.manifest import (
    RunManifestBuilder,
    compute_run_fingerprint,
    redact_provenance_args,
)

_PAYLOAD_KEYS = ('"features"', '"geojson"', '"geometry"', '"coordinates"')


def _assert_payload_free(obj, *, where: str) -> None:
    dumped = json.dumps(obj, default=str)
    for banned in _PAYLOAD_KEYS:
        assert banned not in dumped, f"{where}: payload key {banned} leaked"


def _fc(n: int = 4) -> list[dict]:
    return [
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [116.0 + i * 0.01, 39.0]},
            "properties": {"kind": "a" if i % 2 == 0 else "b", "v": i},
        }
        for i in range(n)
    ]


def _filter_node(**kw) -> ExecutionNode:
    params = kw.pop("parameters", None) or {
        "predicate": {"op": "eq", "field": "kind", "value": "a"},
        "features": _fc(),
    }
    return ExecutionNode(node_id=kw.pop("node_id", "f1"),
                         category=kw.pop("category", NodeCategory.FILTER),
                         parameters=params, **kw)


# ── §6.2.1: execution bundle wired at geocompute run completion ────────────


class TestExecutionBundleWiring:
    def test_run_completion_builds_verdict_and_lineage(self):
        eng = GeoExecutionEngine(max_workers=1)
        plan = ExecutionPlan(plan_id="p-bundle", nodes=[_filter_node()])
        run = eng.execute_plan(plan)
        assert run.status.value == "completed"
        extras = eng.get_run_extras(run.run_id)
        assert extras, "run completion must build the execution bundle"
        verdict = extras["reproducibility"]
        assert verdict["classification"] == REPRODUCIBLE
        assert verdict["bundle_digest"].startswith("sha256:")
        assert verdict["plan_fingerprint"] == plan.graph_fingerprint()
        lineage = extras["lineage"]
        assert [e["node_id"] for e in lineage] == ["f1"]
        entry = lineage[0]
        assert entry["status"] in {"completed", "reused"}
        _assert_payload_free(lineage, where="engine lineage projection")
        assert "parameters" not in entry

    def test_external_query_run_classified_conditionally(self):
        eng = GeoExecutionEngine(max_workers=1)
        node = ExecutionNode(
            node_id="q", category=NodeCategory.QUERY,
            parameters={"dataset_id": "d1"},
            dataset_fingerprints={"d": "fp-1"},
        )
        run = eng.execute_plan(ExecutionPlan(plan_id="p-ext", nodes=[node]))
        verdict = eng.get_run_extras(run.run_id)["reproducibility"]
        assert verdict["classification"] == CONDITIONALLY_REPRODUCIBLE

    def test_owner_isolation_on_extras(self):
        eng = GeoExecutionEngine(max_workers=1)
        run = eng.execute_plan(ExecutionPlan(
            plan_id="p-own", nodes=[_filter_node(node_id="own1")]))
        assert eng.get_run_extras(run.run_id, owner_scope="u:someone-else") == {}
        assert eng.get_run_extras(run.run_id,
                                  owner_scope=owner_scope_for(None)) != {}

    def test_snapshot_persists_verdict_and_lineage_folded(self, monkeypatch):
        eng = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(eng)
        factory = sessionmaker(bind=eng)
        monkeypatch.setattr(
            "app.services.geocompute.run_evidence.session_factory", factory)

        engine = GeoExecutionEngine(max_workers=1)
        plan = ExecutionPlan(plan_id="p-snap", nodes=[_filter_node(node_id="s1")])
        run = engine.execute_plan(plan)

        from app.models.db_model import GeoComputeRunEvidence

        with factory() as db:
            row = db.query(GeoComputeRunEvidence).filter_by(
                run_id=run.run_id).one()
            snap = row.snapshot
        assert isinstance(snap["run"].get("reproducibility"), dict)
        assert snap["run"]["reproducibility"]["classification"] == REPRODUCIBLE
        assert snap["run"]["reproducibility"]["bundle_digest"].startswith("sha256:")
        assert isinstance(snap.get("lineage"), list)
        _assert_payload_free(snap.get("lineage"), where="snapshot lineage")

    def test_snapshot_fallback_serves_extras_after_restart(self, monkeypatch):
        eng = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(eng)
        factory = sessionmaker(bind=eng)
        monkeypatch.setattr(
            "app.services.geocompute.run_evidence.session_factory", factory)

        engine = GeoExecutionEngine(max_workers=1)
        plan = ExecutionPlan(plan_id="p-restart", nodes=[_filter_node(node_id="r1")])
        run = engine.execute_plan(plan)

        # Simulate a process restart: a fresh engine has no memory of the run.
        fresh = GeoExecutionEngine(max_workers=1)
        replayed = fresh.get_run(run.run_id, owner_scope=owner_scope_for(None))
        assert replayed is not None and replayed.source == "snapshot"
        extras = fresh.get_run_extras(run.run_id, owner_scope=owner_scope_for(None))
        assert extras["reproducibility"]["classification"] == REPRODUCIBLE
        assert [e["node_id"] for e in extras["lineage"]] == ["r1"]
        _assert_payload_free(extras["lineage"], where="snapshot fallback lineage")
        # Others still get nothing.
        assert fresh.get_run_extras(run.run_id, owner_scope="u:other") == {}

    def test_bundle_verdict_block_is_bounded_and_content_addressed(self):
        eng = GeoExecutionEngine(max_workers=1)
        plan = ExecutionPlan(plan_id="p-vb", nodes=[_filter_node(node_id="v1")])
        run = eng.execute_plan(plan)
        bundle = build_execution_bundle(plan, run)
        block = bundle_verdict_block(bundle)
        assert set(block) == {
            "classification", "reason", "bundle_digest", "plan_fingerprint",
            "runtime_manifest_fingerprint", "backend_variant",
        }
        assert bundle_verdict_block(build_execution_bundle(plan, run))["bundle_digest"] \
            == block["bundle_digest"], "same execution ⇒ same bundle digest"
        assert len(json.dumps(block)) < 1000

    def test_lineage_projection_helper_stays_payload_free(self):
        eng = GeoExecutionEngine(max_workers=1)
        node = _filter_node(node_id="pf1")
        plan = ExecutionPlan(plan_id="p-pf", nodes=[node])
        run = eng.execute_plan(plan)
        projection = lineage_projection(plan, run)
        _assert_payload_free(projection, where="lineage_projection")


class TestRunEndpointExposure:
    client = TestClient(__import__("app.main", fromlist=["app"]).app)

    @staticmethod
    def _auth(user_id: str = "wave11-rest") -> dict[str, str]:
        from app.core.auth import create_access_token

        token = create_access_token({"sub": user_id, "role": "editor"})
        return {"Authorization": f"Bearer {token}"}

    def _execute(self, **node_overrides):
        node = {
            "node_id": "f1", "category": "filter",
            "parameters": {
                "predicate": {"op": "eq", "field": "kind", "value": "a"},
                "features": _fc(),
            },
        }
        node.update(node_overrides)
        resp = self.client.post(
            "/api/v1/geocompute/plans/execute",
            json={"plan": {"plan_id": "p-wave11", "nodes": [node]}},
            headers=self._auth(),
        )
        assert resp.status_code == 200, resp.text
        return resp.json()

    def test_execute_and_get_run_return_lineage_and_verdict(self):
        run = self._execute()
        assert run["reproducibility"]["classification"] == REPRODUCIBLE
        assert run["lineage"][0]["node_id"] == "f1"
        _assert_payload_free(run["lineage"], where="execute response lineage")

        got = self.client.get(
            f"/api/v1/geocompute/runs/{run['run_id']}", headers=self._auth())
        assert got.status_code == 200
        data = got.json()
        assert data["reproducibility"]["classification"] == REPRODUCIBLE
        assert data["lineage"][0]["node_id"] == "f1"
        _assert_payload_free(data["lineage"], where="GET run lineage")
        # The projection is payload-free: node parameters (which carried the
        # inline features) must not leak through any lineage entry.
        assert "parameters" not in data["lineage"][0]

    def test_get_run_without_extras_still_ok(self):
        got = self.client.get(
            "/api/v1/geocompute/runs/does-not-exist", headers=self._auth())
        assert got.status_code == 404


# ── §6.2.1: workflow manifest outcome carries the classification ───────────


class TestWorkflowReproducibilityVerdict:
    def _builder(self) -> RunManifestBuilder:
        b = RunManifestBuilder(
            workflow_revision_id="rev1", graph_fingerprint="gfp1",
            input_bindings={}, input_dataset_fingerprints={"ds": "dfp"},
        )
        b.add_step(step_id="s1", tool_name="t", tool_version="1.0#cv1",
                   status="success", args={"a": 1})
        return b

    def test_outcome_key_outside_fingerprint(self):
        plain = self._builder().build()
        with_verdict = self._builder()
        with_verdict.set_outcome_context(
            reproducibility={"classification": REPRODUCIBLE,
                             "basis": ["registry generation pinned"]})
        manifest = with_verdict.build()
        assert manifest["reproducibility"]["classification"] == REPRODUCIBLE
        assert "reproducibility" not in plain
        assert compute_run_fingerprint(manifest) == compute_run_fingerprint(plain), (
            "the reproducibility verdict is outcome evidence and must NOT "
            "change the run fingerprint"
        )

    def test_engine_verdict_taxonomy(self):
        from app.services.workflow_engine import WorkflowEngine

        completed = WorkflowRun(
            id="r1", workflow_id="w", workflow_version=1, status="completed",
            input_dataset_fingerprints={"ds": "dfp"}, graph_snapshot={"steps": []},
        )
        verdict = WorkflowEngine._reproducibility_verdict(
            completed, runtime_manifest_fingerprint="rtfp",
            tool_versions={"t": "1.0#cv1"})
        assert verdict["classification"] == REPRODUCIBLE
        assert 1 <= len(verdict["basis"]) <= 8

        no_pins = WorkflowRun(
            id="r2", workflow_id="w", workflow_version=1, status="completed",
            input_dataset_fingerprints={}, graph_snapshot=None,
        )
        conditional = WorkflowEngine._reproducibility_verdict(
            no_pins, runtime_manifest_fingerprint=None, tool_versions={})
        assert conditional["classification"] == CONDITIONALLY_REPRODUCIBLE
        assert any("registry generation unknown" in b for b in conditional["basis"])
        assert any("content unpinned" in b for b in conditional["basis"])

        failed = WorkflowRun(
            id="r3", workflow_id="w", workflow_version=1, status="failed")
        assert WorkflowEngine._reproducibility_verdict(
            failed, runtime_manifest_fingerprint="rtfp")[
                "classification"] == "source_unavailable"


# ── workflow e2e fixtures (mirror test_provenance_regression) ──────────────


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
    from app.models.db_model import Organization

    db.add(Organization(id=org_id, name=f"o{org_id}", slug=f"o{org_id}"))
    db.commit()
    proj = Project(id=f"proj_{uuid.uuid4().hex[:16]}", name=name,
                   org_id=org_id, status="active")
    db.add(proj)
    db.commit()
    return proj


class _Reg:
    """Stub tool registry: echoes success + a fresh ref per dispatch."""

    version = "1.0#cv1"

    def tool_version(self, n):
        return self.version

    async def dispatch(self, name, args, session_id=None):
        return {"success": True, "ref_id": f"ref:{name}",
                "feature_count": 3, "bbox": [0, 0, 1, 1]}


def _wf(db, proj_id, steps, name="wf"):
    from app.schemas.project_schema import (
        WorkflowCreate, WorkflowGraphSpec, WorkflowStepSpec,
    )
    from app.services.project_service import ProjectService

    return ProjectService.save_workflow(
        db, proj_id,
        WorkflowCreate(name=name, graph_spec=WorkflowGraphSpec(
            steps=[WorkflowStepSpec(**s) for s in steps])))


def _run_wf(db, wf, proj, **kw):
    from app.services.workflow_engine import WorkflowEngine

    return asyncio.run(WorkflowEngine.execute_workflow_run(
        db=db, workflow_id=wf.id, tool_registry=_Reg(),
        expected_project_id=proj.id, **kw))


class TestWorkflowManifestEndToEnd:
    def test_run_manifest_carries_verdict_and_stays_stable(self, db_session):
        from app.services.workflow_engine import WorkflowEngine

        db = db_session
        proj = _seed(db)
        wf = _wf(db, proj.id, [
            {"step_id": "s1", "tool_name": "t_a", "dependencies": []},
        ])
        run = _run_wf(db, wf, proj, session_id="sess")
        assert run.status == "completed"
        verdict = run.run_manifest["reproducibility"]
        assert verdict["classification"] in {
            REPRODUCIBLE, CONDITIONALLY_REPRODUCIBLE,
        }
        assert 1 <= len(verdict["basis"]) <= 8

        replay = asyncio.run(WorkflowEngine.replay_run(
            db=db, prior_run_id=run.id, tool_registry=_Reg(), mode="exact",
            expected_project_id=proj.id, session_id="sess"))
        assert replay.run_manifest["reproducibility"] == verdict
        assert run.run_fingerprint == replay.run_fingerprint, (
            "verdict must not churn the run fingerprint across replays"
        )

    def test_pinned_dataset_content_upgrades_classification(self, db_session):
        db = db_session
        proj = _seed(db)
        db.add(ProjectDataset(
            id="ds-fixed", project_id=proj.id, name="d",
            source_type="file", source_ref="bound.csv", crs="EPSG:4326"))
        db.commit()
        wf = _wf(db, proj.id, [
            {"step_id": "s1", "tool_name": "t_a", "dependencies": []},
        ])
        run = _run_wf(db, wf, proj)
        assert run.run_manifest["reproducibility"]["classification"] == REPRODUCIBLE


# ── §6.2.5: write-time redaction of provenance args ────────────────────────

_SECRET = "sup3r-secret-password"
_API_KEY = "ak-live-1234567890"
_BIG = "q" * 600
_REDACT_ARGS = {
    "password": _SECRET,
    "api_key": _API_KEY,
    "query": _BIG,
    "limit": 5,
    "features": _fc(2),
}


class TestProvenanceRedaction:
    def test_redactor_unit_semantics(self):
        out = redact_provenance_args(_REDACT_ARGS)
        assert out["password"] == "[REDACTED]"
        assert out["api_key"] == "[REDACTED]"
        assert out["query"].startswith("[digest:sha256:")
        assert "bytes=600" in out["query"]
        assert "features" not in out  # large payload key dropped
        assert out["limit"] == 5
        # idempotent: applying twice is a no-op
        assert redact_provenance_args(out) == out
        # hard bound: a pathological arg blob is truncated to the 4KB budget —
        # measured on the PERSISTED (JSON-escaped) form, matching the DB column
        blob = {f"k{i}": "v" * 200 for i in range(64)}
        bounded = redact_provenance_args(blob)
        assert len(json.dumps(bounded, ensure_ascii=False)) <= 4096
        assert "[truncated]" in json.dumps(bounded, default=str)

    def test_password_never_lands_in_db_rows(self, db_session):
        db = db_session
        proj = _seed(db)
        wf = _wf(db, proj.id, [{
            "step_id": "s1", "tool_name": "create_data_source",
            "args_template": dict(_REDACT_ARGS), "dependencies": [],
        }])
        run = _run_wf(db, wf, proj)
        assert run.status == "completed"

        db.expire_all()
        persisted = db.execute(
            select(WorkflowRun).where(WorkflowRun.id == run.id)).scalar_one()
        edges = db.execute(
            select(ArtifactLineage).where(
                ArtifactLineage.workflow_run_id == run.id)).scalars().all()
        assert edges

        trace_args = persisted.execution_trace[0]["args"]
        assert trace_args["password"] == "[REDACTED]"
        assert "features" not in trace_args
        assert trace_args["query"].startswith("[digest:sha256:")
        params = edges[0].parameters
        assert params["password"] == "[REDACTED]"
        assert "features" not in params
        assert len(json.dumps(params, ensure_ascii=False, default=str)) <= 4096

        # The regression sweep: no raw secret / api key / oversized value /
        # inline payload anywhere in the persisted provenance rows.
        blobs = {
            "execution_trace": json.dumps(persisted.execution_trace, default=str),
            "run_manifest": json.dumps(persisted.run_manifest, default=str),
            "lineage": json.dumps(
                [e.parameters for e in edges], default=str),
        }
        for where, blob in blobs.items():
            for banned in (_SECRET, _API_KEY, _BIG, "coordinates"):
                assert banned not in blob, f"{where} leaked {banned!r}"
            assert '"features"' not in blob, f"{where} leaked inline payload"

    def test_manifest_step_args_redacted_at_build(self):
        b = RunManifestBuilder(
            workflow_revision_id="r", graph_fingerprint="g",
            input_bindings={}, input_dataset_fingerprints={},
        )
        b.add_step(step_id="s1", tool_name="t", tool_version="1",
                   status="success", args=dict(_REDACT_ARGS))
        args = b.build()["steps"][0]["args"]
        assert args["password"] == "[REDACTED]"
        assert "features" not in args
        assert args["query"].startswith("[digest:sha256:")

    def test_w11_marker_union_redacts_s3_and_signed_urls(self, db_session):
        """round-1 SEC MINOR-1: provenance 的敏感 key 集合 = jobs 脱敏器
        ``SENSITIVE_KEY_PARTS`` 的并集 —— ``s3_access_key`` / ``signed_url``
        一类凭据此前不在窄列表里，值原样落库。现在：值一律 [REDACTED]
        （不可恢复），超长串走 sha256 摘要（存在可验证、内容不可取回）。
        """
        from app.services.jobs.redaction import SENSITIVE_KEY_PARTS
        from app.services.provenance.manifest import _SECRET_KEY_MARKERS

        assert set(_SECRET_KEY_MARKERS) >= set(SENSITIVE_KEY_PARTS), (
            "provenance redaction markers must cover the jobs redactor union")
        for probe in ("s3_access_key", "signed_url", "private_key",
                      "owner_token", "cookie"):
            assert any(m in probe for m in _SECRET_KEY_MARKERS), probe

        s3 = "AKIAIOSFODNN7EXAMPLE"
        signed = "https://blobs.example/x?" + "X-Amz-Signature=" + "f" * 128
        args = {
            "s3_access_key": s3,
            "signed_url": signed,
            "note": "z" * 600,  # 非敏感但超长 → 摘要化
        }
        db = db_session
        proj = _seed(db)
        wf = _wf(db, proj.id, [{
            "step_id": "s1", "tool_name": "create_data_source",
            "args_template": args, "dependencies": [],
        }])
        run = _run_wf(db, wf, proj)
        assert run.status == "completed"

        db.expire_all()
        persisted = db.execute(
            select(WorkflowRun).where(WorkflowRun.id == run.id)).scalar_one()
        edges = db.execute(
            select(ArtifactLineage).where(
                ArtifactLineage.workflow_run_id == run.id)).scalars().all()
        assert edges
        for surface in (persisted.execution_trace[0]["args"], edges[0].parameters):
            assert surface["s3_access_key"] == "[REDACTED]"
            assert surface["signed_url"] == "[REDACTED]"
            assert surface["note"].startswith("[digest:sha256:")
        blob = json.dumps(persisted.execution_trace, default=str)
        assert s3 not in blob and signed not in blob


# ── §6.2.6: numeric backend environment capture ────────────────────────────


class TestRuntimeEnvCapture:
    def test_env_block_present_bounded_honest(self):
        from app.lib.gis.runtime_manifest import capture_runtime_env

        env = capture_runtime_env()
        assert len(env) <= 12
        assert env["python"].count(".") == 2
        assert env["python"] != "unknown"
        assert env["shapely"] not in ("", "unknown")
        assert env["geos"] not in ("", "unknown")
        for key in ("proj", "pyproj", "gdal", "numpy"):
            assert isinstance(env[key], str) and env[key]

    def test_env_folded_into_fingerprint_and_drift_guard(self, monkeypatch):
        from app.lib.gis import runtime_manifest as rm

        base = rm.compile_runtime_manifest()
        assert base.runtime_env, "compiled manifest must carry the env block"
        assert base.runtime_env == rm.capture_runtime_env()

        # A numeric-backend upgrade: same registries, new GEOS.
        upgraded_env = {**base.runtime_env, "geos": "9.9.9-fake"}
        monkeypatch.setattr(rm, "capture_runtime_env", lambda: upgraded_env)
        upgraded = rm.compile_runtime_manifest()
        assert upgraded.runtime_env["geos"] == "9.9.9-fake"
        assert upgraded.fingerprint != base.fingerprint, (
            "numeric backend change must change the runtime manifest fingerprint"
        )
        # The stale-plan guard inherits the sensitivity.
        assert upgraded.is_stale_plan(base.fingerprint) is True
        assert base.is_stale_plan(base.fingerprint) is False

        monkeypatch.undo()
        restored = rm.compile_runtime_manifest()
        assert restored.fingerprint == base.fingerprint, (
            "same backend content ⇒ same fingerprint (determinism)"
        )

    def test_env_capture_survives_import_failure(self, monkeypatch):
        import builtins
        from app.lib.gis.runtime_manifest import capture_runtime_env

        real_import = builtins.__import__

        def _boom(name, *a, **kw):
            if name.startswith(("shapely", "pyproj", "rasterio", "numpy")):
                raise ImportError(f"no {name}")
            return real_import(name, *a, **kw)

        monkeypatch.setattr(builtins, "__import__", _boom)
        env = capture_runtime_env()
        assert env["python"] != "unknown"
        for key in ("geos", "shapely", "proj", "pyproj", "gdal", "numpy"):
            assert env[key] == "unknown", "import failure must be disclosed, not fabricated"


# ── §6.2.4: lineage_inputs populated at plan construction ──────────────────


class TestLineageInputsPopulation:
    def test_derived_from_provable_parameter_identity(self):
        from app.services.geocompute.api import build_plan_from_json

        plan = build_plan_from_json({"plan_id": "p", "nodes": [{
            "node_id": "q", "category": "query",
            "parameters": {"dataset_id": "ds-1", "ref_id": "ref-9",
                           "filter_expr": "kind = 'a'"},
        }]})
        links = plan.node_map()["q"].lineage_inputs
        assert [(link.ref_id, link.kind) for link in links] == [
            ("ds-1", "dataset_version"), ("ref-9", "ref"),
        ]
        # Nodes without provable identity stay honestly empty.
        plan2 = build_plan_from_json({"plan_id": "p2", "nodes": [{
            "node_id": "f", "category": "filter",
            "parameters": {"predicate": {"op": "eq"}},
        }]})
        assert plan2.node_map()["f"].lineage_inputs == []

    def test_explicit_lineage_inputs_win(self):
        from app.services.geocompute.api import build_plan_from_json

        plan = build_plan_from_json({"plan_id": "p", "nodes": [{
            "node_id": "q", "category": "query",
            "parameters": {"dataset_id": "ds-1"},
            "lineage_inputs": [{"ref_id": "art-explicit", "kind": "artifact"}],
        }]})
        links = plan.node_map()["q"].lineage_inputs
        assert [(link.ref_id, link.kind) for link in links] == [("art-explicit", "artifact")]

    def test_lineage_inputs_do_not_touch_semantic_fingerprint(self):
        bare = ExecutionNode(node_id="q", category=NodeCategory.QUERY,
                             parameters={"dataset_id": "ds-1"})
        linked = bare.model_copy(update={
            "lineage_inputs": [{"ref_id": "ds-1", "kind": "dataset_version"}]})
        assert bare.semantic_fingerprint() == linked.semantic_fingerprint()

    def test_rest_model_accepts_lineage_inputs(self):
        from app.api.routes.geocompute import ExecutionPlanIn, _plan_from_request

        plan = _plan_from_request(ExecutionPlanIn(**{
            "plan_id": "p-rest",
            "nodes": [{
                "node_id": "q", "category": "query",
                "parameters": {"dataset_id": "ds-1"},
                "lineage_inputs": [{"ref_id": "art-1", "kind": "artifact"}],
            }],
        }))
        links = plan.node_map()["q"].lineage_inputs
        assert [(link.ref_id, link.kind) for link in links] == [("art-1", "artifact")]
