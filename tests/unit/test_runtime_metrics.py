"""Runtime Evaluation Metrics 测试（ADR-0103 §十一）。

golden 语料的检索指标用真实 registry + 真实 golden cases 计算（确定性）；
其余指标族用小型确定性 fixture 锁定边界与聚合语义。
"""
import pytest

from app.evaluation.runtime_metrics import (
    context_metrics,
    execution_metrics,
    gis_correctness_metrics,
    relevant_tools_for_case,
    retrieval_metrics,
    routing_metrics,
)


@pytest.fixture(scope="module")
def registry():
    from app.tools import init_tools
    from app.tools.registry import ToolRegistry

    reg = ToolRegistry()
    init_tools(reg)
    return reg


def test_relevant_tools_from_capabilities(registry):
    tools = relevant_tools_for_case(registry, ["admin_aggregation"])
    assert "spatial_aggregate" in tools
    for t in tools:
        assert int(registry.descriptor(t).tier) < 3


def test_retrieval_metrics_over_golden_corpus(registry):
    from app.evaluation.golden_cases import get_all_cases

    cases = [c for c in get_all_cases() if getattr(c, "expected_capabilities", None)]
    assert cases, "golden corpus should contain capability-labelled cases"
    metrics = retrieval_metrics(registry, cases[:20], k=30)
    d = metrics.as_dict()
    assert d["cases"] > 0
    # 候选注入了 expected_capabilities（capability backfill）→ recall 应显著非零
    assert d["recall_at_k"] > 0.5
    assert 0.0 <= d["precision_at_k"] <= 1.0
    assert abs(d["irrelevant_rate"] - (1.0 - d["precision_at_k"])) < 1e-6


def test_routing_metrics_aggregation():
    obs = [
        {"reason_codes": ["primary_configured"], "failed": False, "latency_s": 1.0},
        {"reason_codes": ["health_skip:m:cooldown", "fallback_first_capable"], "failed": False, "latency_s": 2.0},
        {"reason_codes": [], "failed": True, "latency_s": 3.0},
    ]
    m = routing_metrics(obs).as_dict()
    assert m["calls"] == 3
    assert m["fallback_rate"] == pytest.approx(1 / 3, abs=1e-3)
    assert m["failure_rate"] == pytest.approx(1 / 3, abs=1e-3)
    assert m["avg_latency_s"] == pytest.approx(2.0)
    assert routing_metrics([]).as_dict()["calls"] == 0


def test_execution_metrics_aggregation():
    reports = [
        {"completed": True, "timed_out": False, "no_progress": False, "repeated_calls": 0, "tool_calls": 5},
        {"completed": True, "timed_out": False, "no_progress": True, "repeated_calls": 2, "tool_calls": 12},
        {"completed": False, "timed_out": True, "no_progress": False, "repeated_calls": 0, "tool_calls": 3},
    ]
    m = execution_metrics(reports).as_dict()
    assert m["completion_rate"] == pytest.approx(2 / 3, abs=1e-3)
    assert m["timeout_rate"] == pytest.approx(1 / 3, abs=1e-3)
    assert m["no_progress_incidence"] == pytest.approx(1 / 3, abs=1e-3)
    assert m["repeated_call_rate"] == pytest.approx(1 / 3, abs=1e-3)
    assert m["avg_tool_count"] == pytest.approx((5 + 12 + 3) / 3, abs=1e-2)


def test_gis_correctness_algorithm_family():
    outs = [
        # spatial_aggregate 派生 capability=admin_aggregation → 期望命中
        {"expected_capabilities": ["admin_aggregation"], "used_tools": ["spatial_aggregate"],
         "data_qualified": True, "final_map_state_pass": True},
        {"expected_capabilities": ["kriging"], "used_tools": ["spatial_aggregate"],
         "data_qualified": False, "final_map_state_pass": False},
    ]
    m = gis_correctness_metrics(outs).as_dict()
    assert m["correct_algorithm_family_rate"] == pytest.approx(0.5)
    assert m["correct_data_qualification_rate"] == pytest.approx(0.5)
    assert m["final_map_state_pass_rate"] == pytest.approx(0.5)


def test_context_metrics_from_budget_reports():
    reports = [
        {"total_est_tokens": 1000, "over_budget": False, "violations": [],
         "by_category": {"TOOL_SCHEMAS": 200, "TOOL_RESULTS": 100}},
        {"total_est_tokens": 1000, "over_budget": True, "violations": ["over_budget:total"],
         "by_category": {"TOOL_SCHEMAS": 400, "TOOL_RESULTS": 300}},
    ]
    m = context_metrics(reports).as_dict()
    assert m["overflow_rate"] == 0.5
    assert m["truncation_incidence"] == 0.5
    assert m["schema_token_ratio"] == pytest.approx(0.3)
    assert m["result_token_ratio"] == pytest.approx(0.2)
