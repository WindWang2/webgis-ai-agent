"""Test GIS-09: Validate duplicate step_ids before topological sort in WorkflowEngine.

Verifies that duplicate step_ids raise an explicit "Duplicate step_id found:"
ValueError rather than a misleading "Cycle detected in Workflow DAG specification".
"""
import pytest

from app.services.workflow_engine import WorkflowEngine, WorkflowStepSpec


def test_workflow_dag_duplicate_step_id_raises_clear_error():
    """Duplicate step IDs must raise explicit Duplicate step_id error, not cycle error."""
    steps = [
        WorkflowStepSpec(step_id="step_load", tool_name="buffer", tool_parameters={}),
        WorkflowStepSpec(step_id="step_load", tool_name="buffer", tool_parameters={}),  # Duplicate!
    ]

    with pytest.raises(ValueError) as exc_info:
        WorkflowEngine.validate_dag(steps)

    err_msg = str(exc_info.value)
    assert "Duplicate step_id found: step_load" in err_msg
    assert "Cycle detected" not in err_msg


def test_workflow_dag_actual_cycle_still_detected():
    """Actual cyclic dependency must still be reported as cycle."""
    steps = [
        WorkflowStepSpec(step_id="step_a", tool_name="tool_a", tool_parameters={}, dependencies=["step_b"]),
        WorkflowStepSpec(step_id="step_b", tool_name="tool_b", tool_parameters={}, dependencies=["step_a"]),
    ]

    with pytest.raises(ValueError) as exc_info:
        WorkflowEngine.validate_dag(steps)

    assert "Cycle detected in Workflow DAG specification" in str(exc_info.value)


def test_workflow_dag_valid_ordering():
    """Valid DAG with distinct step IDs sorts correctly."""
    steps = [
        WorkflowStepSpec(step_id="step_b", tool_name="tool_b", tool_parameters={}, dependencies=["step_a"]),
        WorkflowStepSpec(step_id="step_a", tool_name="tool_a", tool_parameters={}),
    ]

    topo = WorkflowEngine.validate_dag(steps)
    assert topo == ["step_a", "step_b"]
