"""
Unit tests for Workflow Engine, DAG validation, and Run Comparison
"""
import pytest
from app.services.workflow_engine import WorkflowEngine
from app.schemas.project_schema import WorkflowStepSpec, WorkflowRunResponse
from app.models.project import WorkflowRun


def test_dag_validation_valid():
    steps = [
        WorkflowStepSpec(step_id="step_1", tool_name="tool_a", dependencies=[]),
        WorkflowStepSpec(step_id="step_2", tool_name="tool_b", dependencies=["step_1"]),
        WorkflowStepSpec(step_id="step_3", tool_name="tool_c", dependencies=["step_2"]),
    ]
    order = WorkflowEngine.validate_dag(steps)
    assert order == ["step_1", "step_2", "step_3"]


def test_dag_validation_cycle_detection():
    steps = [
        WorkflowStepSpec(step_id="step_1", tool_name="tool_a", dependencies=["step_2"]),
        WorkflowStepSpec(step_id="step_2", tool_name="tool_b", dependencies=["step_1"]),
    ]
    with pytest.raises(ValueError, match="Cycle detected"):
        WorkflowEngine.validate_dag(steps)


def test_compare_runs():
    run_a = WorkflowRun(
        id="run_a",
        workflow_id="wf_1",
        workflow_version=1,
        input_bindings={"aoi": "Haidian"},
        status="completed",
        cost_perf_summary={"total_duration_seconds": 1.2},
    )
    run_b = WorkflowRun(
        id="run_b",
        workflow_id="wf_1",
        workflow_version=1,
        input_bindings={"aoi": "Chaoyang"},
        status="completed",
        cost_perf_summary={"total_duration_seconds": 1.5},
    )

    diff = WorkflowEngine.compare_runs(None, run_a, run_b)
    assert diff["run_a_id"] == "run_a"
    assert diff["run_b_id"] == "run_b"
    assert "aoi" in diff["inputs_changed"]["diff_keys"]
