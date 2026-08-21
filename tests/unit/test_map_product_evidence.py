"""MapProductEvidence / MapProductCompleteness 评估面测试。

不变量：缺产品证据 ≠ PASS（not_applicable_exempt 只在宽松策略下成立；
require_map_product=True 时缺证据按策略失败）。
"""

from app.lib.harness.evidence import MapProductEvidence
from app.lib.harness.evaluator import HarnessEvaluator
from app.lib.harness.pi_agent_harness import PiAgentHarness


def _base_evidence_result(with_product=None, metrics=None):
    return {
        "metrics": metrics or {},
        "evidence": [
            {
                "run_id": "r1", "session_id": "s1", "turn_id": "", "tool_call_id": "t1",
                "tool_name": "webgis_map_product", "duration_ms": 5,
                "is_error": False, "error_msg": "", "refs": [],
                "mapspec_validity": None, "map_actions": [],
                "map_product": with_product,
            },
        ],
        "interaction": {"issued": 0},
        "cartography": {},
    }


class TestMapProductEvidenceDataclass:
    def test_from_result_none_without_key(self):
        assert MapProductEvidence.from_result({}) is None
        assert MapProductEvidence.from_result(None) is None
        assert MapProductEvidence.from_result({"other": 1}) is None

    def test_from_result_transcribes_bounded(self):
        raw = {
            "map_product_evidence": {
                "intent_resolution": {"task": "distribution_overview"},
                "recipe_selection": {"selected": "poi_distribution_overview"},
                "recipe_eligibility": {"eligible": True},
                "fallback_decisions": [
                    {"from_element": f"e{i}", "reason_code": "X"} for i in range(30)
                ],
                "component_selection": [f"c{i}" for i in range(40)],
                "map_product_completeness": {"complete": True},
            }
        }
        ev = MapProductEvidence.from_result(raw)
        assert ev is not None
        assert ev.intent_resolution["task"] == "distribution_overview"
        assert len(ev.fallback_decisions) <= 16
        assert len(ev.component_selection) <= 32
        dumped = ev.to_dict()
        assert dumped["completeness"]["complete"] is True


class TestEvaluatorMapProductDimension:
    def test_exempt_without_product_evidence(self):
        evaluator = HarnessEvaluator()
        result = _base_evidence_result(with_product=None)
        gate = evaluator.evaluate_evidence(result, require_evaluated=False)
        check = gate["checks"]["MapProductCompleteness"]
        assert check["evaluated"] is False
        assert check["reason"] == "not_applicable_exempt"
        assert check["passed"] is True

    def test_policy_fail_when_required_and_missing(self):
        evaluator = HarnessEvaluator()
        result = _base_evidence_result(with_product=None)
        gate = evaluator.evaluate_evidence(result, require_evaluated=False,
                                           require_map_product=True)
        check = gate["checks"]["MapProductCompleteness"]
        assert check["passed"] is False
        assert check["reason"] == "not_evaluated_policy_fail"

    def test_evaluated_pass_with_complete_product(self):
        evaluator = HarnessEvaluator()
        product = MapProductEvidence(
            completeness={"complete": True},
        )
        result = _base_evidence_result(
            with_product=product.to_dict(), metrics={"MapProductCompleteness": 100.0},
        )
        gate = evaluator.evaluate_evidence(result, require_evaluated=False)
        check = gate["checks"]["MapProductCompleteness"]
        assert check["evaluated"] is True
        assert check["passed"] is True and check["reason"] == "evaluated"

    def test_evaluated_fail_with_incomplete_product(self):
        evaluator = HarnessEvaluator()
        product = MapProductEvidence(completeness={"complete": False})
        result = _base_evidence_result(
            with_product=product.to_dict(), metrics={"MapProductCompleteness": 0.0},
        )
        gate = evaluator.evaluate_evidence(result, require_evaluated=False)
        check = gate["checks"]["MapProductCompleteness"]
        assert check["evaluated"] is True
        assert check["passed"] is False

    def test_evaluate_session_skips_absent_product_metric(self):
        evaluator = HarnessEvaluator()
        # V2 五维调用方（无产品维度）行为不变
        gate = evaluator.evaluate_session({
            "ToolChoiceAccuracy": 100.0, "MapSpecValidity": 100.0,
            "CursorResolutionRate": 100.0, "StepEfficiency": 100.0,
            "ErrorRecoveryRate": 100.0,
        })
        assert "MapProductCompleteness" not in gate["checks"]
        assert gate["overall_passed"] is True


class TestHarnessProductMetric:
    def test_compute_completeness_from_tool_results(self):
        h = PiAgentHarness(session_id="s1")
        assert h.has_map_product_evidence() is False
        assert h.compute_map_product_completeness() == 0.0

        h.record_tool_result(
            "t1", "webgis_map_product",
            {"map_product_evidence": {"map_product_completeness": {"complete": True}}},
        )
        assert h.has_map_product_evidence() is True
        assert h.compute_map_product_completeness() == 100.0

    def test_incomplete_product_counts_against_metric(self):
        h = PiAgentHarness(session_id="s2")
        h.record_tool_result(
            "t1", "webgis_map_product",
            {"map_product_evidence": {"map_product_completeness": {"complete": True}}},
        )
        h.record_tool_result(
            "t2", "webgis_map_product",
            {"map_product_evidence": {"map_product_completeness": {"complete": False}}},
        )
        assert h.compute_map_product_completeness() == 50.0

    def test_evaluate_all_emits_metric_only_with_evidence(self):
        h = PiAgentHarness(session_id="s3")
        metrics = h.evaluate_all([], 1)
        assert "MapProductCompleteness" not in metrics
        h.record_tool_result(
            "t1", "webgis_map_product",
            {"map_product_evidence": {"map_product_completeness": {"complete": True}}},
        )
        metrics = h.evaluate_all([], 1)
        assert metrics["MapProductCompleteness"] == 100.0
