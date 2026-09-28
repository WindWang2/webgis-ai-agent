"""replay + runtime_metrics 消费面（G08）：确定性重放与指标聚合语义。

覆盖入口：replay.py 的 canonical_call_signature / compare_chains /
route_decision_diff / simulate_agent_loop；reliability_corpus 的脚本形态；
runtime_metrics 的四个纯聚合器（routing / execution / gis_correctness /
context）—— 聚合语义用小型确定性 fixture 锁定（含分母诚实性：缺席字段
不计入分母）。
"""
from __future__ import annotations

import pytest

from app.evaluation.replay import (
    ScriptedCall,
    canonical_call_signature,
    compare_chains,
    route_decision_diff,
    simulate_agent_loop,
)
from app.evaluation.runtime_metrics import (
    context_metrics,
    execution_metrics,
    gis_correctness_metrics,
    routing_metrics,
)
from app.lib.runtime.gis_trace import GisTraceChain, Stage


# ── replay ───────────────────────────────────────────────────────────────


def test_canonical_call_signature_order_insensitive_and_discriminating():
    sig = canonical_call_signature("t", {"b": 1, "a": 2})
    assert sig == canonical_call_signature("t", {"a": 2, "b": 1})
    assert sig != canonical_call_signature("t", {"a": 1, "b": 2})
    assert sig != canonical_call_signature("u", {"a": 2, "b": 1})


def test_compare_chains_detects_missing_stages():
    chain_a = GisTraceChain(turn_id="ca")
    chain_a.record(Stage.USER_INTENT, text="x")
    chain_a.record(Stage.TOOL_CALLS, tool="t")
    chain_a.record(Stage.VERIFICATION, verdict="pass")
    chain_b = GisTraceChain(turn_id="cb")
    chain_b.record(Stage.USER_INTENT, text="x")

    comparison = compare_chains(chain_a, chain_b)
    assert comparison.missing_in_b == ["TOOL_CALLS", "VERIFICATION"]
    assert comparison.missing_in_a == []
    assert comparison.completeness_a > comparison.completeness_b


def test_route_decision_diff_reports_only_differing_fields():
    def decision(model_id: str):
        return type(
            "D",
            (),
            {
                "as_dict": staticmethod(
                    lambda: {"role": "execution", "model_id": model_id}
                )
            },
        )()

    assert route_decision_diff(decision("m1"), decision("m1")) == {}
    diff = route_decision_diff(decision("m1"), decision("m2"))
    assert set(diff) == {"model_id"}
    assert diff["model_id"] == {"a": "m1", "b": "m2"}


@pytest.mark.asyncio
async def test_simulate_agent_loop_replays_script_with_invariants():
    from app.tools.registry import ToolRegistry

    registry = ToolRegistry()
    registry.register(
        name="echo_x", description="echo", func=lambda v: {"ok": True, "v": v}
    )
    report = await simulate_agent_loop(
        registry,
        "sess-g08",
        [ScriptedCall(tool="echo_x", arguments={"v": 1})],
    )
    assert report.invariants_held is True
    assert report.violations == []
    assert len(report.steps) == 1


def test_reliability_corpus_scripts_are_replay_shaped():
    from app.evaluation.reliability_corpus import build_reliability_corpus

    cases = build_reliability_corpus()
    assert len(cases) >= 19
    ids = [c.case_id for c in cases]
    assert len(ids) == len(set(ids))
    for case in cases:
        assert case.script, case.case_id
        assert all(isinstance(s, ScriptedCall) for s in case.script), case.case_id


# ── runtime_metrics 聚合语义 ─────────────────────────────────────────────


def test_routing_metrics_fallback_and_failure_rates():
    observations = [
        {"reason_codes": ["fallback"], "failed": False, "latency_s": 1.0},
        {"reason_codes": [], "failed": True, "latency_s": 3.0},
        {"reason_codes": ["primary_cooldown"], "failed": False, "latency_s": 2.0},
    ]
    metrics = routing_metrics(observations).as_dict()
    assert metrics["calls"] == 3
    assert metrics["fallback_rate"] == 0.6667  # round(2/3, 4)
    assert metrics["failure_rate"] == 0.3333
    assert metrics["avg_latency_s"] == pytest.approx(2.0)
    assert routing_metrics([]).as_dict()["calls"] == 0


def test_execution_metrics_aggregation():
    turn_reports = [
        {
            "completed": True,
            "timed_out": False,
            "no_progress": False,
            "repeated_calls": 0,
            "tool_calls": 3,
        },
        {
            "completed": False,
            "timed_out": True,
            "no_progress": True,
            "repeated_calls": 2,
            "tool_calls": 5,
        },
    ]
    metrics = execution_metrics(turn_reports).as_dict()
    assert metrics["turns"] == 2
    assert metrics["completion_rate"] == 0.5
    assert metrics["timeout_rate"] == 0.5
    assert metrics["no_progress_incidence"] == 0.5
    assert metrics["repeated_call_rate"] == 0.5
    assert metrics["avg_tool_count"] == 4.0


def test_gis_correctness_metrics_skips_undeclared_denominators():
    outcomes = [
        {
            "expected_capabilities": ["admin_aggregation"],
            "used_tools": ["spatial_aggregate"],
            "data_qualified": True,
            "final_map_state_pass": True,
        },
        {
            "expected_capabilities": ["admin_aggregation"],
            "used_tools": ["nope"],
            "data_qualified": False,
        },
    ]
    metrics = gis_correctness_metrics(outcomes).as_dict()
    assert metrics["cases"] == 2
    assert metrics["correct_algorithm_family_rate"] == 0.5
    assert metrics["correct_data_qualification_rate"] == 0.5
    # case 2 未声明 final_map_state_pass → 不计入分母（诚实聚合）。
    assert metrics["final_map_state_pass_rate"] == 1.0


def test_context_metrics_overflow_and_token_ratios():
    budget_reports = [
        {
            "by_category": {"TOOL_SCHEMAS": 100, "TOOL_RESULTS": 300},
            "total_est_tokens": 1000,
            "over_budget": False,
            "violations": [],
        },
        {
            "by_category": {"TOOL_SCHEMAS": 800, "TOOL_RESULTS": 100},
            "total_est_tokens": 1000,
            "over_budget": True,
            "violations": ["over_budget:TOOL_SCHEMAS"],
        },
    ]
    metrics = context_metrics(budget_reports).as_dict()
    assert metrics["reports"] == 2
    assert metrics["overflow_rate"] == 0.5
    assert metrics["truncation_incidence"] == 0.5
    assert metrics["schema_token_ratio"] == pytest.approx(0.45)
    assert metrics["result_token_ratio"] == pytest.approx(0.20)
