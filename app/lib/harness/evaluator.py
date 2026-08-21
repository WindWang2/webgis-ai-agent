"""
HarnessEvaluator - 5-Dimensional Evaluation Metric Calculator & Quality Gate.

V2 gate policy（HARNESS-V2）：缺失证据不等于成功。
- MapSpecValidity / CursorResolutionRate 在 V2 harness 下从真实证据计算；无证据
  返回 0.0 而非 100.0，因此缺证据的 run 会真正 fail gate（除非显式豁免）。
- 新增 evaluate_evidence()：消费 evaluate_with_evidence() 的结构化结果，对每个
  维度给出 score / target / passed / evaluated（区分"未评估"与"失败"）。
"""
import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Default Acceptance Thresholds
DEFAULT_THRESHOLDS = {
    "ToolChoiceAccuracy": 90.0,
    "MapSpecValidity": 95.0,
    "CursorResolutionRate": 100.0,
    "StepEfficiency": 80.0,
    "ErrorRecoveryRate": 80.0,
    # V3 新增（additive）：地图交互闭环指标阈值（design §5）。
    "InteractionEvidenceCoverage": 100.0,
    "MapCommandExecutionSuccessRate": 95.0,
    "InteractionStateConvergenceRate": 90.0,
    "InteractionRecoveryRate": 100.0,
    # GIS Harness 产品维度（additive）：产品组装完整度阈值。
    "MapProductCompleteness": 80.0,
}

# V3 交互维度：无交互证据（issued == 0）时在 gate 中豁免/严格受 require_interaction
# 控制；同步 evaluate_session 对缺失的交互维度直接跳过（V2 调用方行为不变）。
INTERACTION_METRICS = frozenset({
    "InteractionEvidenceCoverage",
    "MapCommandExecutionSuccessRate",
    "InteractionStateConvergenceRate",
    "InteractionRecoveryRate",
})

# GIS Harness 产品维度：无产品证据（未跑 webgis_map_product 等）时豁免；
# 有证据则按完整度阈值裁决。缺证据绝不为 PASS（not_evaluated_policy_fail
# 只在 require_map_product=True 且仍无证据时出现）。
PRODUCT_METRICS = frozenset({
    "MapProductCompleteness",
})


class HarnessEvaluator:
    """Evaluates telemetry from PiAgentHarness against quality gate thresholds."""

    def __init__(self, thresholds: Dict[str, float] | None = None):
        self.thresholds = {**DEFAULT_THRESHOLDS, **(thresholds or {})}

    def evaluate_session(
        self,
        harness_metrics: Dict[str, float]
    ) -> Dict[str, Any]:
        """
        Evaluate harness metrics against quality gate thresholds.

        :param harness_metrics: Dictionary of computed metric scores.
        :return: Evaluation result containing metrics, thresholds, checks, and overall pass status.
        """
        checks: Dict[str, Dict[str, Any]] = {}
        all_passed = True

        for metric_name, target_threshold in self.thresholds.items():
            actual_score = harness_metrics.get(metric_name)
            if actual_score is None and (
                metric_name in INTERACTION_METRICS or metric_name in PRODUCT_METRICS
            ):
                # V3 新增维度：无交互/产品证据的 run 不参与同步 gate（等价
                # evaluate_evidence 的 not_applicable_exempt）。V2 调用方只传
                # 5 维时行为不变。
                continue
            actual_score = harness_metrics.get(metric_name, 0.0)
            passed = actual_score >= target_threshold
            if not passed:
                all_passed = False

            checks[metric_name] = {
                "score": actual_score,
                "target": target_threshold,
                "passed": passed,
            }

        return {
            "overall_passed": all_passed,
            "metrics": harness_metrics,
            "thresholds": self.thresholds,
            "checks": checks,
        }

    def evaluate_evidence(
        self,
        evidence_result: Dict[str, Any],
        *,
        require_evaluated: bool = True,
        require_interaction: bool = False,
        require_cartography: Optional[bool] = None,
        require_map_product: bool = False,
    ) -> Dict[str, Any]:
        """Evaluate a structured ``evaluate_with_evidence`` result.

        Per gate policy, unevaluated dimensions are treated as FAILURE rather than
        success: ``require_evaluated=True`` (default) makes a dimension with no
        evidence (e.g. zero mutations for MapSpecValidity, zero refs for
        CursorResolutionRate) fail its check. Set ``require_evaluated=False`` to
        instead exempt dimensions that had nothing to evaluate (useful for runs
        that legitimately perform no cartography).

        V3 交互维度：``evaluated = interaction.issued > 0``。默认
        ``require_interaction=False`` —— 无交互证据的 run 交互维度豁免
        （not_applicable_exempt，score 诚实报 0.0 + evaluated:false），V2 调用方
        行为不变。``require_interaction=True`` 严格 —— 未评估的交互维度按
        策略失败处理（issued>0 但无 ACK 时 Coverage=0 天然 fail，无需额外规则）。
        """
        metrics: Dict[str, float] = evidence_result.get("metrics", {})
        evidence = evidence_result.get("evidence", [])

        had_mutation = any(e.get("mapspec_validity") for e in evidence)
        display_mutation = any(
            e.get("tool_name") == "webgis_layer_upsert"
            and e.get("mapspec_validity")
            for e in evidence
        )
        cartography_required = (
            display_mutation if require_cartography is None else require_cartography
        )
        had_ref = any(len(e.get("refs", [])) > 0 for e in evidence)
        had_product = any(e.get("map_product") for e in evidence)
        interaction = evidence_result.get("interaction") or {}
        try:
            issued = int(interaction.get("issued") or 0)
        except (TypeError, ValueError):
            issued = 0
        dims_evaluated = {
            "MapSpecValidity": had_mutation,
            "CursorResolutionRate": had_ref,
            "ToolChoiceAccuracy": True,
            "StepEfficiency": True,
            "ErrorRecoveryRate": True,
            # V3 新增（additive）：evaluated = issued > 0。
            "InteractionEvidenceCoverage": issued > 0,
            "MapCommandExecutionSuccessRate": issued > 0,
            "InteractionStateConvergenceRate": issued > 0,
            "InteractionRecoveryRate": issued > 0,
            # GIS Harness 产品维度（additive）：evaluated = 有产品证据。
            "MapProductCompleteness": had_product,
        }

        checks: Dict[str, Dict[str, Any]] = {}
        all_passed = True
        for metric_name, target in self.thresholds.items():
            evaluated = dims_evaluated[metric_name]
            score = metrics.get(metric_name, 0.0)
            if metric_name in INTERACTION_METRICS or metric_name in PRODUCT_METRICS:
                if not evaluated and (
                    (metric_name in PRODUCT_METRICS and require_map_product)
                    or (metric_name in INTERACTION_METRICS and require_interaction)
                ):
                    # 要求评估但没有任何证据 → 策略失败。
                    passed = False
                    reason = "not_evaluated_policy_fail"
                elif not evaluated:
                    # 本次 run 无该族证据 → 豁免；score 诚实报 0.0。
                    passed = True
                    reason = "not_applicable_exempt"
                else:
                    passed = score >= target
                    reason = "evaluated"
            else:
                if not evaluated and require_evaluated:
                    # No evidence to evaluate → policy: FAIL (not success).
                    passed = False
                    reason = "not_evaluated_policy_fail"
                elif not evaluated and not require_evaluated:
                    # Legitimately nothing to evaluate → exempt.
                    passed = True
                    reason = "not_applicable_exempt"
                else:
                    passed = score >= target
                    reason = "evaluated"
            if not passed:
                all_passed = False
            checks[metric_name] = {
                "score": score,
                "target": target,
                "passed": passed,
                "evaluated": evaluated,
                "reason": reason,
            }

        # Cartographic success is a categorical evidence gate, not another
        # percentage inferred from tool success. MapSpec mutation runs require
        # it by default; callers can explicitly exempt non-cartographic runs.
        cartography = evidence_result.get("cartography") or {}
        cartography_status = str(cartography.get("status") or "not_evaluated")
        cartography_trusted = cartography.get("trusted") is True
        cartography_evaluated = (
            cartography_trusted
            and cartography_status not in ("not_evaluated", "superseded")
        )
        cartography_passed = (
            cartography_trusted
            and cartography_status in ("passed", "passed_with_warnings")
        )
        contradictory = (
            ("evaluated" in cartography
             and bool(cartography.get("evaluated")) != cartography_evaluated)
            or ("passed" in cartography
                and bool(cartography.get("passed")) != cartography_passed)
        )
        if contradictory:
            cartography_evaluated = False
            cartography_passed = False
            cartography_reason = "inconsistent_or_untrusted_evidence"
        elif cartography_passed:
            cartography_reason = "evaluated"
        elif not cartography_required and not cartography_evaluated:
            cartography_passed = True
            cartography_reason = "not_applicable_exempt"
        elif not cartography_evaluated:
            cartography_reason = "not_evaluated_policy_fail"
        else:
            cartography_reason = "evaluated_failure"
        if not cartography_passed:
            all_passed = False
        checks["CartographicQuality"] = {
            "score": 100.0 if cartography_passed and cartography_evaluated else 0.0,
            "target": 100.0,
            "passed": cartography_passed,
            "evaluated": cartography_evaluated,
            "reason": cartography_reason,
            "status": cartography_status,
            "trusted": cartography_trusted,
        }

        all_passed = all(bool(check.get("passed")) for check in checks.values())

        return {
            "overall_passed": all_passed,
            "metrics": metrics,
            "thresholds": self.thresholds,
            "checks": checks,
            "run_id": evidence_result.get("run_id"),
            "session_id": evidence_result.get("session_id"),
        }

    def generate_markdown_report(
        self,
        session_id: str,
        evaluation_result: Dict[str, Any]
    ) -> str:
        """Generate a formatted Markdown evaluation report."""
        overall_status = "✅ PASSED" if evaluation_result["overall_passed"] else "❌ FAILED"
        lines = [
            f"# Pi GIS Agent Evaluation Report: {session_id}",
            "",
            f"**Overall Status**: {overall_status}",
            "",
            "## 5-Dimensional Quality Gate Metrics",
            "",
            "| Metric Name | Score (%) | Target (%) | Status |",
            "|---|---|---|---|",
        ]

        for name, chk in evaluation_result["checks"].items():
            if chk.get("passed"):
                status_icon = "🟢 PASS"
            elif chk.get("reason") == "not_evaluated_policy_fail":
                status_icon = "⚫ NOT EVALUATED"
            else:
                status_icon = "🔴 FAIL"
            lines.append(
                f"| {name} | {chk['score']}% | {chk['target']}% | {status_icon} |"
            )

        lines.extend([
            "",
            "---",
            "*Report generated by Pi GIS Agent Harness Evaluation Suite.*",
        ])

        return "\n".join(lines)
