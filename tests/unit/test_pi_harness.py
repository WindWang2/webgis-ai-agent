"""
Unit tests for PiAgentHarness, HarnessEvaluator, and harness_runner
"""
from app.lib.harness.evaluator import HarnessEvaluator
from app.lib.harness.pi_agent_harness import PiAgentHarness
from app.tools.harness_runner import run_benchmark_scenario


def test_pi_harness_metric_computation():
    harness = PiAgentHarness(session_id="test_session_1")

    # Record tool call & result
    harness.record_tool_call(
        tool_call_id="call_1",
        name="st_dbscan",
        arguments={"geojson": "ref:geojson:12345", "eps1": 500}
    )
    harness.record_tool_result(
        tool_call_id="call_1",
        name="st_dbscan",
        result={"success": True},
        is_error=False
    )

    metrics = harness.evaluate_all(
        expected_tools=["st_dbscan"],
        ideal_step_count=1
    )

    assert metrics["ToolChoiceAccuracy"] == 100.0
    assert metrics["CursorResolutionRate"] == 100.0
    assert metrics["StepEfficiency"] == 100.0
    assert metrics["ErrorRecoveryRate"] == 100.0


def test_pi_harness_error_recovery_tracking():
    harness = PiAgentHarness(session_id="test_session_2")

    # Step 1: Exception
    harness.record_tool_call("c1", "st_dbscan", {})
    harness.record_tool_result("c1", "st_dbscan", {}, is_error=True, error_msg="Invalid CRS")

    # Step 2: Recovery
    harness.record_tool_call("c2", "st_dbscan", {"crs": "EPSG:4326"})
    harness.record_tool_result("c2", "st_dbscan", {"success": True}, is_error=False)

    metrics = harness.evaluate_all(
        expected_tools=["st_dbscan"],
        ideal_step_count=1
    )

    assert metrics["ErrorRecoveryRate"] == 100.0
    assert metrics["StepEfficiency"] == 50.0  # 1 ideal / 2 actual = 50%


def test_evaluator_quality_gate_checks():
    evaluator = HarnessEvaluator()
    metrics = {
        "ToolChoiceAccuracy": 95.0,
        "MapSpecValidity": 100.0,
        "CursorResolutionRate": 100.0,
        "StepEfficiency": 85.0,
        "ErrorRecoveryRate": 90.0,
    }

    result = evaluator.evaluate_session(metrics)
    assert result["overall_passed"] is True

    report = evaluator.generate_markdown_report("test_session", result)
    assert "# Pi GIS Agent Evaluation Report" in report
    assert "✅ PASSED" in report


def test_run_benchmark_scenario_runner():
    res = run_benchmark_scenario(
        scenario_id="scenario_cartography",
        expected_tools=["combine_map_theme"],
        ideal_step_count=1,
        simulated_tool_calls=[{"id": "c1", "name": "combine_map_theme", "arguments": {"preset": "cyber_dark"}}],
        simulated_tool_results=[{"id": "c1", "name": "combine_map_theme", "result": {"success": True}}]
    )

    assert res["scenario_id"] == "scenario_cartography"
    assert res["evaluation"]["overall_passed"] is True
    assert "ToolChoiceAccuracy" in res["metrics"]
