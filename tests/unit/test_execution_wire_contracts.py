"""Production serializers keep wire compatibility and reject type mistakes."""
import hashlib
from pathlib import Path
from types import SimpleNamespace
import subprocess
import sys

from pydantic import TypeAdapter
import pytest

from app.contracts.execution import ExecutionBundle, RunSnapshot
from app.contracts.modelops import CandidateSetRecord, PublishedArtifact, VramSnapshot
from app.contracts.simulation import CoverageDiff, GeometrySummary
from app.contracts.workflow import BindingRecord, TransitionRecord, WorkflowDAG
from app.lib.modelops.candidates import MaskCandidate, MaskCandidateSet
from app.services.geocompute.plan import (
    ExecutionNode, ExecutionPlan, ExecutionRun, ExecutionRunStatus,
    NodeCategory, NodeEvidence,
)
from app.services.geocompute.reproducibility import build_execution_bundle
from app.services.geocompute.run_evidence import build_snapshot
from app.services.gis_harness.workflow_v4.typed_dag import (
    TypedPort, TypedWorkflowGraph, TypedWorkflowNode,
)
from app.services.modelops.artifacts import publish_raster_artifact
from app.services.modelops.scheduling import GpuDeviceInfo, VramLedger
from app.services.simulation.whatif.spatial_diff_engine import GeometryDiff, diff_layers
from app.services.workflow_runtime.contracts import (
    BindingVerdict, NodeRuntimeState, PortViolation, StateTransitionRecord,
)


def test_workflow_producers_match_nested_wire_contracts() -> None:
    graph = TypedWorkflowGraph(nodes=[TypedWorkflowNode(
        node_id="source", kind="data_input", role="roads",
        outputs=[TypedPort(name="data", artifact_type="feature_collection")],
    )]).to_bounded_dict()
    assert TypeAdapter(WorkflowDAG).validate_python(graph) == graph
    assert graph["nodes"][0]["side_effect"] == "derived_external"
    binding = BindingVerdict(
        node_id="source", ok=False, action="blocked",
        violations=[PortViolation(port="data", code="CRS_CLASS_MISMATCH")],
    ).to_bounded_dict()
    assert TypeAdapter(BindingRecord).validate_python(binding) == binding
    transition = StateTransitionRecord(
        revision=2, from_state="READY", to_state="RUNNING",
    ).to_bounded_dict()
    assert TypeAdapter(TransitionRecord).validate_python(transition) == transition
    assert transition["rev"] == 2


def test_existing_pydantic_extension_fields_are_unchanged() -> None:
    # Static contracts are not installed as JSON validators on persisted rows.
    value = NodeRuntimeState(
        instance_id="i", node_id="n",
        binding={"vendor_extension": {"nested": [1, True]}},
        reuse={"legacy_key": "kept"},
    )
    dumped = value.model_dump()
    assert dumped["binding"] == {"vendor_extension": {"nested": [1, True]}}
    assert dumped["reuse"] == {"legacy_key": "kept"}


def test_run_snapshot_and_bundle_preserve_existing_fields() -> None:
    plan = ExecutionPlan(plan_id="p", nodes=[ExecutionNode(
        node_id="n", category=NodeCategory.FILTER, operation="filter",
    )])
    run = ExecutionRun(
        run_id="r", plan_id=plan.plan_id, plan_fingerprint=plan.graph_fingerprint(),
        status=ExecutionRunStatus.COMPLETED,
        evidence={"n": NodeEvidence(status="completed", rows_emitted=3)},
    )
    snapshot = build_snapshot(run)
    assert TypeAdapter(RunSnapshot).validate_python(snapshot) == snapshot
    assert snapshot["evidence"]["n"]["rows_emitted"] == 3
    bundle = build_execution_bundle(plan, run, runtime_manifest_fingerprint="test")
    assert TypeAdapter(ExecutionBundle).validate_python(bundle) == bundle
    assert bundle["nodes"][0]["category"] == "filter"


def test_modelops_observations_keep_concrete_numeric_fields() -> None:
    candidates = MaskCandidateSet(candidates=(MaskCandidate(index=0, score=0.9),))
    record = candidates.as_dict()
    assert TypeAdapter(CandidateSetRecord).validate_python(record) == record
    ledger = VramLedger(
        devices=(GpuDeviceInfo(index=0, total_mem_bytes=1024),),
        fallback_budget_bytes=1024,
    )
    reservation = ledger.acquire("cuda", 0, bytes_needed=12, run_id="r")
    snapshot = ledger.snapshot()
    assert TypeAdapter(VramSnapshot).validate_python(snapshot) == snapshot
    assert snapshot["used"] == {"cuda:0": 12}
    ledger.release(reservation)
    cached: PublishedArtifact = {"path": "cached.tif", "data_object_id": None}
    assert TypeAdapter(PublishedArtifact).validate_python(cached) == cached


@pytest.mark.parametrize("deduped", [False, True])
def test_successful_raster_publication_preserves_all_wire_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, deduped: bool,
) -> None:
    from app.services.lakehouse import raster_object

    payload = b"fixture raster bytes"
    path = tmp_path / "result.tif"
    path.write_bytes(payload)
    grid = {
        "width": 2, "height": 3, "crs": "EPSG:4326",
        "transform": [1.0, 0.0, 0.0, 0.0, -1.0, 3.0],
        "dtype": "uint8", "band_count": 1, "block_shape": [3, 2],
        "nodata": None,
    }
    monkeypatch.setattr(raster_object, "grid_identity_from_path", lambda _: grid)
    monkeypatch.setattr(raster_object, "_chunk_checksums", lambda *_: None)
    monkeypatch.setattr(raster_object, "publish_data_object", lambda *args, **kwargs: SimpleNamespace(
        data_object_id="raster-1", manifest_location="manifest.json", deduped=deduped,
    ))
    # Exercise the actual lakehouse success branch and the ModelOps adapter.
    artifact = publish_raster_artifact(
        path, owner_scope={"session_id": "session-1"}, source_refs=[],
        producer={"capability": "test"},
    )
    assert artifact == {
        "published": True, "data_object_id": "raster-1",
        "content_sha256": hashlib.sha256(payload).hexdigest(),
        "manifest": "manifest.json", "deduped": deduped,
        "grid": grid, "chunk_checksums": False,
    }
    adapter = TypeAdapter(PublishedArtifact)
    assert adapter.validate_python(artifact) == artifact
    assert adapter.dump_python(artifact) == artifact


def test_spatial_summary_contracts_keep_null_population_evidence() -> None:
    summary = GeometryDiff(layer_id="roads").summary()
    assert TypeAdapter(GeometrySummary).validate_python(summary) == summary
    diffs = diff_layers({"facility": {"type": "FeatureCollection", "features": []}}, {})
    coverage = diffs["_coverage"]
    assert isinstance(coverage, dict)
    assert TypeAdapter(CoverageDiff).validate_python(coverage) == coverage
    assert coverage["baseline_coverage_population"] is None
    assert coverage["gained_area_m2"] == 0.0


def test_wire_contracts_have_positive_and_negative_static_consumers(tmp_path: Path) -> None:
    fixture = tmp_path / "wire_consumer.py"
    fixture.write_text('''from app.contracts.workflow import WorkflowDAG, InstancePatch
from app.contracts.execution import SnapshotSummary
from app.contracts.modelops import InferenceProgress, PublishedArtifact
from app.services.workflow_runtime.contracts import StateTransitionRecord
valid_dag: WorkflowDAG = {"nodes": [{"node_id": "n", "optional": True}], "edges": []}
valid_patch: InstancePatch = {"status": "succeeded", "terminal_at": None}
valid_progress: InferenceProgress = {"stage": "infer", "run_id": "r", "tiles_done": 2}
valid_artifact: PublishedArtifact = {"published": True, "deduped": True, "data_object_id": "raster-1"}
bad_dag: WorkflowDAG = {"nodes": [{"optional": "yes"}]}
bad_patch: InstancePatch = {"terminal_at": "yesterday"}
bad_progress: InferenceProgress = {"stage": "unknown", "run_id": "r"}
bad_snapshot: SnapshotSummary = {"run_id": "r", "status": "completed", "source": "live", "created_at": None}
transition = StateTransitionRecord(revision=1, from_state="READY", to_state="RUNNING").to_bounded_dict()
transition["rev"] = "one"
''', encoding="utf-8")
    completed = subprocess.run(
        [sys.executable, "-m", "mypy", "--follow-imports=silent",
         "--ignore-missing-imports", "--no-incremental", "--show-error-codes",
         "--cache-dir", str(tmp_path / "cache"), str(fixture)],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True, text=True, check=False,
    )
    errors = [line for line in completed.stdout.splitlines() if ": error:" in line]
    assert completed.returncode == 1, completed.stdout + completed.stderr
    expected_lines = [
        line_no for line_no, line in enumerate(fixture.read_text().splitlines(), 1)
        if line.startswith(("bad_", 'transition["rev"]'))
    ]
    assert len(errors) == len(expected_lines) == 5, completed.stdout + completed.stderr
    assert all(
        any(f":{line_no}: error:" in error for error in errors)
        for line_no in expected_lines
    ), completed.stdout + completed.stderr
    assert all("[typeddict-item]" in line or "[list-item]" in line for line in errors), errors
