"""
HarnessEvaluator - 5-Dimensional Evaluation Metric Calculator & Quality Gate.

V2 gate policy（HARNESS-V2）：缺失证据不等于成功。
- MapSpecValidity / CursorResolutionRate 在 V2 harness 下从真实证据计算；无证据
  返回 0.0 而非 100.0，因此缺证据的 run 会真正 fail gate（除非显式豁免）。
- 新增 evaluate_evidence()：消费 evaluate_with_evidence() 的结构化结果，对每个
  维度给出 score / target / passed / evaluated（区分"未评估"与"失败"）。
"""
import logging
from typing import Any, Dict

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
}

# V3 交互维度：无交互证据（issued == 0）时在 gate 中豁免/严格受 require_interaction
# 控制；同步 evaluate_session 对缺失的交互维度直接跳过（V2 调用方行为不变）。
INTERACTION_METRICS = frozenset({
    "InteractionEvidenceCoverage",
    "MapCommandExecutionSuccessRate",
    "InteractionStateConvergenceRate",
    "InteractionRecoveryRate",
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
            if actual_score is None and metric_name in INTERACTION_METRICS:
                # V3 新增维度：无交互证据的 run 不参与同步 gate（等价 evaluate_evidence
                # 的 not_applicable_exempt）。V2 调用方只传 5 维时行为不变。
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
        had_ref = any(len(e.get("refs", [])) > 0 for e in evidence)
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
        }

        checks: Dict[str, Dict[str, Any]] = {}
        all_passed = True
        for metric_name, target in self.thresholds.items():
            evaluated = dims_evaluated[metric_name]
            score = metrics.get(metric_name, 0.0)
            if metric_name in INTERACTION_METRICS:
                if not evaluated and require_interaction:
                    # 要求交互评估但没有任何 issued 动作 → 策略失败。
                    passed = False
                    reason = "not_evaluated_policy_fail"
                elif not evaluated:
                    # 本次 run 无交互（issued == 0）→ 豁免；score 诚实报 0.0。
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
