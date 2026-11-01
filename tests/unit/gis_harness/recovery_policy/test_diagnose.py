"""诊断构建矩阵测试（GOAL 05 / M1）—— 把原始工具错误/eligibility/
no-progress/MapSpec 未收敛/goal 差距映射到封闭 reason + 六处置。
"""
from __future__ import annotations

import pytest

from app.services.gis_harness.recovery_policy import diagnose
from app.services.gis_harness.recovery_policy.contracts import (
    GISRecoveryReason,
    RecoveryDisposition,
)


def _tool_failure(message: str = "", *, code=None, status=None,
                  error_type=None, tool="webgis_buffer",
                  harness_payload=None, ref_origin_session=None):
    return diagnose.diagnose_tool_failure(
        status=status, code=code, error_type=error_type, message=message,
        tool_name=tool,
        harness_failure=harness_payload if harness_payload is not None else {
            "class": "tool_error", "remediation": "retry_with_backoff",
            "attempts": 0, "max_attempts": 3, "retry_allowed": True},
        session_id="sess-current",
        ref_origin_session=ref_origin_session,
    )


# ── HTTP 语义族：429 / 5xx / 权限 ─────────────────────────────────────────

class TestHttpSemantics:
    @pytest.mark.parametrize("msg", [
        "upstream returned HTTP 429",
        "rate limit exceeded, retry later",
        "Too Many Requests",
        "触发限流",
    ])
    def test_rate_limited(self, msg):
        d = _tool_failure(msg)
        assert d.reason is GISRecoveryReason.RATE_LIMITED
        assert d.disposition is RecoveryDisposition.RETRYABLE
        assert d.failure_class == "timeout"

    @pytest.mark.parametrize("msg", [
        "HTTP 502 Bad Gateway",
        "503 Service Unavailable",
        "internal server error from provider",
        "上游 504",
    ])
    def test_upstream_server_error(self, msg):
        d = _tool_failure(msg)
        assert d.reason is GISRecoveryReason.UPSTREAM_SERVER_ERROR
        assert d.disposition is RecoveryDisposition.RETRYABLE

    @pytest.mark.parametrize("msg", [
        "403 forbidden",
        "permission denied for dataset",
        "unauthorized: token expired",
        "权限不足",
    ])
    def test_permission_denied_needs_user(self, msg):
        d = _tool_failure(msg)
        assert d.reason is GISRecoveryReason.PERMISSION_DENIED
        assert d.disposition is RecoveryDisposition.NEEDS_USER_APPROVAL
        # 权限失败绝不允许盲目自动重试
        assert "retry" not in d.hints or d.retry is None or not d.retry.retry_allowed

    def test_numeric_status_in_error_type(self):
        d2 = diagnose.diagnose_tool_failure(
            status="429", message="", tool_name="t",
            harness_failure={"class": "timeout", "remediation": "retry_with_backoff",
                             "attempts": 0, "max_attempts": 2, "retry_allowed": True},
            session_id="s",
        )
        assert d2.reason is GISRecoveryReason.RATE_LIMITED


# ── ref 语义族 ────────────────────────────────────────────────────────────

class TestRefSemantics:
    def test_cross_session_ref_is_unsafe(self):
        d = _tool_failure("ref not found: ref:data-abc", code="NOT_FOUND",
                          ref_origin_session="sess-OTHER")
        assert d.reason is GISRecoveryReason.CROSS_SESSION_REF
        assert d.disposition is RecoveryDisposition.UNSAFE_TO_RETRY

    def test_same_session_missing_ref_is_retryable_stale(self):
        d = _tool_failure("ref not found: ref:data-abc", code="NOT_FOUND",
                          ref_origin_session="sess-current")
        assert d.reason is GISRecoveryReason.STALE_REF
        assert d.disposition is RecoveryDisposition.RETRYABLE

    def test_output_expired(self):
        d = _tool_failure("output expired", harness_payload={
            "class": "stale_ref", "remediation": "retry",
            "attempts": 0, "max_attempts": 2, "retry_allowed": True})
        assert d.reason is GISRecoveryReason.OUTPUT_EXPIRED
        assert d.disposition is RecoveryDisposition.REPAIRABLE


# ── CRS 族 ────────────────────────────────────────────────────────────────

class TestCrsSemantics:
    def test_plain_crs_error_repairable(self):
        d = _tool_failure("cannot transform to EPSG:3857",
                          harness_payload={"class": "crs_error",
                                           "remediation": "substitute_operator",
                                           "attempts": 0, "max_attempts": 2,
                                           "retry_allowed": True})
        assert d.reason is GISRecoveryReason.CRS_ERROR
        assert d.disposition is RecoveryDisposition.REPAIRABLE

    @pytest.mark.parametrize("msg", [
        "ambiguous CRS: cannot determine source projection",
        "unknown coordinate system",
        "无法确定坐标系",
    ])
    def test_ambiguous_crs_needs_user(self, msg):
        d = _tool_failure(msg,
                          harness_payload={"class": "crs_error",
                                           "remediation": "substitute_operator",
                                           "attempts": 0, "max_attempts": 2,
                                           "retry_allowed": True})
        assert d.reason is GISRecoveryReason.CRS_AMBIGUOUS
        assert d.disposition is RecoveryDisposition.NEEDS_USER_DATA


# ── 不可恢复族 ────────────────────────────────────────────────────────────

class TestTerminalSemantics:
    def test_cancelled(self):
        d = _tool_failure("cancelled by user",
                          harness_payload={"class": "cancelled",
                                           "remediation": "abort_with_disclosure",
                                           "attempts": 0, "max_attempts": 0,
                                           "retry_allowed": False})
        assert d.reason is GISRecoveryReason.CANCELLED
        assert d.disposition is RecoveryDisposition.TERMINAL

    def test_budget_exhausted(self):
        d = _tool_failure("budget",
                          harness_payload={"class": "budget_exhausted",
                                           "remediation": "abort_with_disclosure",
                                           "attempts": 3, "max_attempts": 3,
                                           "retry_allowed": False})
        assert d.reason is GISRecoveryReason.BUDGET_EXHAUSTED
        assert d.disposition is RecoveryDisposition.TERMINAL

    def test_no_progress(self):
        d = diagnose.diagnose_no_progress(streak=3, hints=[])
        assert d.reason is GISRecoveryReason.NO_PROGRESS
        assert d.disposition is RecoveryDisposition.TERMINAL


# ── capability / eligibility 族 ───────────────────────────────────────────

class TestCapabilityAndEligibility:
    def test_capability_ineligible(self):
        d = diagnose.diagnose_capability_refusal(code="CAPABILITY_INELIGIBLE")
        assert d.reason is GISRecoveryReason.CAPABILITY_INELIGIBLE
        assert d.disposition is RecoveryDisposition.REPAIRABLE

    def test_provider_unavailable(self):
        d = diagnose.diagnose_capability_refusal(code="PROVIDER_UNAVAILABLE")
        assert d.reason is GISRecoveryReason.PROVIDER_UNAVAILABLE
        assert d.disposition is RecoveryDisposition.REPAIRABLE

    @pytest.mark.parametrize("state,reason,disposition", [
        ("blocked", "eligibility_blocked", RecoveryDisposition.NEEDS_USER_DATA),
        ("transform_required", "eligibility_degraded",
         RecoveryDisposition.REPAIRABLE),
        ("degraded", "eligibility_degraded", RecoveryDisposition.REPAIRABLE),
    ])
    def test_eligibility_states(self, state, reason, disposition):
        d = diagnose.diagnose_eligibility(state=state, reason_code="missing_field:score")
        assert d.reason.value == reason
        assert d.disposition is disposition
        assert "missing_field:score" in d.detail

    def test_eligibility_eligible_returns_none(self):
        assert diagnose.diagnose_eligibility(state="eligible", reason_code="") is None


# ── MapSpec / 渲染观察族 ──────────────────────────────────────────────────

class TestRenderGapSemantics:
    def test_layer_missing_not_converged(self):
        d = diagnose.diagnose_render_gap(
            render_status="RENDER_ISSUES",
            finding_codes=["render_layer_missing"])
        assert d.reason is GISRecoveryReason.MAPSPEC_NOT_CONVERGED
        assert d.disposition is RecoveryDisposition.REPAIRABLE

    def test_stale_observation_reobserve(self):
        d = diagnose.diagnose_render_gap(
            render_status="stale", finding_codes=["render_revision_stale"])
        assert d.reason is GISRecoveryReason.MAPSPEC_NOT_CONVERGED

    def test_source_error_renderer_failure(self):
        d = diagnose.diagnose_render_gap(
            render_status="RENDER_ISSUES", finding_codes=["render_source_error"])
        assert d.reason is GISRecoveryReason.RENDERER_FAILURE

    def test_verified_returns_none(self):
        assert diagnose.diagnose_render_gap(
            render_status="RENDER_VERIFIED", finding_codes=[]) is None


# ── goal 差距族 ───────────────────────────────────────────────────────────

class TestGoalGapSemantics:
    def test_export_gap(self):
        d = diagnose.diagnose_goal_gap({
            "verdict": "partial",
            "missing": ["export:pdf", "chart:histogram"],
        })
        assert d.reason is GISRecoveryReason.GOAL_GAP_EXPORT
        assert d.disposition is RecoveryDisposition.REPAIRABLE

    def test_rendering_gap(self):
        d = diagnose.diagnose_goal_gap({
            "verdict": "partial", "missing": ["render_observation"],
        })
        assert d.reason is GISRecoveryReason.GOAL_GAP_RENDERING

    def test_science_gap(self):
        d = diagnose.diagnose_goal_gap({
            "verdict": "blocked",
            "missing": ["data_qualification", "spatial_validation"],
        })
        assert d.reason is GISRecoveryReason.GOAL_GAP_SCIENCE

    def test_satisfied_returns_none(self):
        assert diagnose.diagnose_goal_gap({
            "verdict": "satisfied", "missing": []}) is None

    def test_absent_report_returns_none(self):
        assert diagnose.diagnose_goal_gap(None) is None


# ── 纯度：绝不抛出 ────────────────────────────────────────────────────────

class TestNeverRaises:
    def test_all_none_inputs(self):
        d = diagnose.diagnose_tool_failure(
            status=None, code=None, error_type=None, message=None,
            tool_name="", harness_failure=None, session_id="")
        assert d.reason is GISRecoveryReason.UNKNOWN_FAILURE

    def test_garbage_harness_payload(self):
        d = diagnose.diagnose_tool_failure(
            message="x", tool_name="t", harness_failure="garbage",
            session_id="s")
        assert d.reason is GISRecoveryReason.UNKNOWN_FAILURE


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
