"""
Harness Runner - CLI Execution Tool for Pi GIS Agent Evaluation Suite
"""
import logging
from typing import Any, Dict, List

from app.lib.harness.evaluator import HarnessEvaluator
from app.lib.harness.pi_agent_harness import PiAgentHarness

logger = logging.getLogger(__name__)


def run_benchmark_scenario(
    scenario_id: str,
    expected_tools: List[str],
    ideal_step_count: int,
    simulated_tool_calls: List[Dict[str, Any]],
    simulated_tool_results: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """
    Run a simulated benchmark scenario and return evaluation results.

    :param scenario_id: Identifier for the benchmark test scenario.
    :param expected_tools: List of tool names expected to be called.
    :param ideal_step_count: Ideal number of steps.
    :param simulated_tool_calls: List of tool call dicts ({id, name, arguments}).
    :param simulated_tool_results: List of tool result dicts ({id, name, result, is_error}).
    :return: Benchmark evaluation summary dictionary.
    """
    harness = PiAgentHarness(session_id=scenario_id)

    # 1. Record simulated calls & results
    for call in simulated_tool_calls:
        harness.record_tool_call(
            tool_call_id=call["id"],
            name=call["name"],
            arguments=call.get("arguments", {})
        )

    for res in simulated_tool_results:
        harness.record_tool_result(
            tool_call_id=res["id"],
            name=res["name"],
            result=res.get("result", {}),
            is_error=res.get("is_error", False),
            error_msg=res.get("error_msg")
        )

    # 2. Compute 5-dimensional metrics
    metrics = harness.evaluate_all(
        expected_tools=expected_tools,
        ideal_step_count=ideal_step_count
    )

    # 3. Evaluate quality gate thresholds
    evaluator = HarnessEvaluator()
    result = evaluator.evaluate_session(metrics)
    report = evaluator.generate_markdown_report(scenario_id, result)

    # 4. ADR-0159 P1 落库钩子：HarnessEvaluator 产出处 → 质量事实库（eval lane）。
    #    只写账本，不改判定；账本故障不影响评估返回值。
    try:
        from app.services.cartography_metrics_store import record_quality_run_sync

        record_quality_run_sync(
            lane="eval",
            source="harness_evaluator",
            session_id=scenario_id,
            scene_id=scenario_id,
            passed=bool(result.get("overall_passed")),
            gate_scores={
                str(name): score for name, score in (metrics or {}).items()
                if isinstance(score, (int, float)) and not isinstance(score, bool)
            },
            summary={"overall_passed": bool(result.get("overall_passed"))},
        )
    except Exception:  # noqa: BLE001 — 账本绝不反噬评估
        logger.debug("quality fact store: eval-lane record failed", exc_info=True)

    return {
        "scenario_id": scenario_id,
        "metrics": metrics,
        "evaluation": result,
        "report": report
    }


if __name__ == "__main__":
    # Sample dry run
    sample_res = run_benchmark_scenario(
        scenario_id="sample_spatial_stats",
        expected_tools=["st_dbscan"],
        ideal_step_count=1,
        simulated_tool_calls=[{"id": "c1", "name": "st_dbscan", "arguments": {"eps1": 500}}],
        simulated_tool_results=[{"id": "c1", "name": "st_dbscan", "result": {"success": True}, "is_error": False}]
    )
    print(sample_res["report"])
