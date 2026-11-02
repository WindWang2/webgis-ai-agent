"""RecoveryPolicy 纯决策 + 副作用守卫测试（GOAL 05 / M2）。

钉死安全不变量：
- 读/幂等 + 预算内 → 允许有界自动重试（含退避 schedule，纯计算无 sleep）；
- 状态变更/外部副作用/产物/未分类 → 绝不自动重试；按类别要求既有确认门；
- tier-3 破坏性永不自动重试；
- 预算耗尽/取消/terminal → 零动作（诚实终止）；
- 决策确定性（同输入同输出）。
"""
from __future__ import annotations

import pytest

from app.services.gis_harness.recovery_policy import diagnose
from app.services.gis_harness.recovery_policy.contracts import (
    DiagnosticAttribution,
    GISRecoveryDiagnostic,
    GISRecoveryReason,
)
from app.services.gis_harness.recovery_policy.policy import (
    backoff_schedule_ms,
    decide_recovery,
)
from app.services.gis_harness.recovery_policy.risk import (
    CONSERVATIVE_SIDE_EFFECT_DEFAULT,
    resolve_tool_risk,
)


def _diag(reason, *, disposition_hint=None, attempts=0, max_attempts=3,
          allowed=True):
    return GISRecoveryDiagnostic(
        reason=reason,
        attribution=DiagnosticAttribution(session_id="s1", tool_name="t"),
        retry={"attempts": attempts, "max_attempts": max_attempts,
               "retry_allowed": allowed} if max_attempts else None,
    )


RISK_READ_ONLY = {"side_effect_class": "pure", "tier": 1}
RISK_COMPUTE = {"side_effect_class": "deterministic_compute", "tier": 1}
RISK_MUTATION = {"side_effect_class": "state_mutation", "tier": 1}
RISK_EXTERNAL = {"side_effect_class": "external_side_effect", "tier": 2}
RISK_ARTIFACT = {"side_effect_class": "artifact_creation", "tier": 1}
RISK_TIER3 = {"side_effect_class": "destructive", "tier": 3}
RISK_UNCLASSIFIED = {"side_effect_class": "unclassified", "tier": 1}


# ── 风险解析 ─────────────────────────────────────────────────────────────

class TestRiskResolution:
    def test_descriptor_fields_consumed(self):
        p = resolve_tool_risk("webgis_x", descriptor=RISK_MUTATION)
        assert p.side_effect_class == "state_mutation"
        assert p.tier == 1
        assert p.retry_safe is False

    def test_read_only_retry_safe(self):
        assert resolve_tool_risk(
            "webgis_x", descriptor=RISK_READ_ONLY).retry_safe is True

    def test_unclassified_conservative(self):
        p = resolve_tool_risk("webgis_x", descriptor=RISK_UNCLASSIFIED)
        assert p.retry_safe is not True  # 未知不放宽
        assert p.side_effect_class == CONSERVATIVE_SIDE_EFFECT_DEFAULT or \
            p.side_effect_class == "unclassified"

    def test_no_descriptor_conservative(self):
        p = resolve_tool_risk("webgis_x", descriptor=None)
        assert p.retry_safe is False
        assert p.tier == 0

    def test_real_descriptor_side_effect_field(self):
        """R2 复审 P2-2：真实 ToolDescriptor 字段名是 side_effect。"""
        from app.services.gis_harness.recovery_policy.risk import (
            ToolRiskProfile,
            resolve_tool_risk,
        )
        from app.tools.descriptor import SideEffectClass, ToolDescriptor

        desc = ToolDescriptor(
            name="webgis_query_x", description="d",
            side_effect=SideEffectClass.CACHEABLE_READ, tier=1)
        p = resolve_tool_risk("webgis_query_x", descriptor=desc)
        assert p.side_effect_class == "cacheable_read"
        assert p.retry_safe is True
        mut = ToolDescriptor(
            name="webgis_edit_x", description="d",
            side_effect=SideEffectClass.STATE_MUTATION, tier=1)
        assert resolve_tool_risk(
            "webgis_edit_x", descriptor=mut).retry_safe is False

    def test_tier3_is_destructive(self):
        p = resolve_tool_risk("webgis_x", descriptor=RISK_TIER3)
        assert p.destructive is True


# ── 安全重试矩阵 ─────────────────────────────────────────────────────────

class TestAutoRetryMatrix:
    def test_read_only_retryable_allows_bounded_retry(self):
        d = _diag(GISRecoveryReason.TRANSIENT_TIMEOUT)
        dec = decide_recovery(d, risk=RISK_READ_ONLY)
        assert dec.auto_retry is True
        assert "retry_with_backoff" in dec.allowed_actions
        assert dec.required_confirmation is None
        assert dec.backoff_ms  # 有 schedule

    def test_rate_limited_backoff_longer_than_timeout(self):
        d = _diag(GISRecoveryReason.RATE_LIMITED)
        dec = decide_recovery(d, risk=RISK_COMPUTE)
        assert dec.auto_retry is True
        assert dec.backoff_ms[0] >= 1000

    def test_state_mutating_never_auto_retry(self):
        d = _diag(GISRecoveryReason.TRANSIENT_TIMEOUT)
        dec = decide_recovery(d, risk=RISK_MUTATION)
        assert dec.auto_retry is False
        assert "retry" not in dec.allowed_actions
        assert dec.required_confirmation == "owner_write"

    def test_state_mutating_with_idempotency_key_allows_bounded(self):
        d = _diag(GISRecoveryReason.TRANSIENT_TIMEOUT)
        dec = decide_recovery(d, risk=RISK_MUTATION,
                              idempotency_key_present=True)
        assert dec.auto_retry is True

    def test_external_side_effect_requires_paid_confirmation(self):
        d = _diag(GISRecoveryReason.UPSTREAM_SERVER_ERROR)
        dec = decide_recovery(d, risk=RISK_EXTERNAL)
        assert dec.auto_retry is False
        assert dec.required_confirmation == "paid_call"

    def test_tier3_destructive_never_auto(self):
        d = _diag(GISRecoveryReason.TOOL_EXECUTION_FAILED)
        dec = decide_recovery(d, risk=RISK_TIER3)
        assert dec.auto_retry is False
        assert dec.required_confirmation == "destructive"
        assert dec.terminal is False

    def test_unclassified_conservative_no_auto_retry(self):
        d = _diag(GISRecoveryReason.TRANSIENT_TIMEOUT)
        dec = decide_recovery(d, risk=RISK_UNCLASSIFIED)
        assert dec.auto_retry is False

    def test_no_risk_profile_conservative(self):
        d = _diag(GISRecoveryReason.TRANSIENT_TIMEOUT)
        dec = decide_recovery(d, risk=None)
        assert dec.auto_retry is False


# ── 终止与预算 ───────────────────────────────────────────────────────────

class TestTerminalAndBudget:
    def test_budget_exhausted_converts_to_abort(self):
        d = _diag(GISRecoveryReason.TRANSIENT_TIMEOUT, attempts=2,
                  max_attempts=2, allowed=False)
        dec = decide_recovery(d, risk=RISK_READ_ONLY)
        assert dec.auto_retry is False
        assert "abort_with_disclosure" in dec.allowed_actions
        assert dec.terminal is True

    def test_cancelled_zero_actions(self):
        d = _diag(GISRecoveryReason.CANCELLED, max_attempts=0)
        dec = decide_recovery(d, risk=RISK_READ_ONLY)
        assert dec.auto_retry is False
        assert dec.terminal is True
        assert "retry" not in dec.allowed_actions

    def test_no_progress_terminal(self):
        d = diagnose.diagnose_no_progress(streak=3)
        dec = decide_recovery(d, risk=RISK_READ_ONLY)
        assert dec.terminal is True
        assert dec.auto_retry is False

    def test_cancel_flag_interrupts_immediately(self):
        d = _diag(GISRecoveryReason.RATE_LIMITED)
        dec = decide_recovery(d, risk=RISK_COMPUTE, cancelled=True)
        assert dec.auto_retry is False
        assert dec.allowed_actions == ()


# ── 用户路径 ─────────────────────────────────────────────────────────────

class TestUserPaths:
    def test_needs_user_data_no_retry(self):
        d = diagnose.diagnose_eligibility(
            state="blocked", reason_code="missing_field:score")
        dec = decide_recovery(d, risk=RISK_READ_ONLY)
        assert dec.auto_retry is False
        assert "ask_user_data" in dec.allowed_actions

    def test_permission_needs_owner_confirmation(self):
        d = _diag(GISRecoveryReason.PERMISSION_DENIED)
        dec = decide_recovery(d, risk=RISK_READ_ONLY)
        assert dec.auto_retry is False
        assert dec.required_confirmation == "owner_write"
        assert "owner_confirmation" in dec.allowed_actions

    def test_cross_session_ref_deny_all_recovery(self):
        d = _diag(GISRecoveryReason.CROSS_SESSION_REF)
        dec = decide_recovery(d, risk=RISK_READ_ONLY)
        assert dec.auto_retry is False
        assert "retry" in dec.denied_actions

    def test_repairable_read_only_repairs_allowed(self):
        d = _diag(GISRecoveryReason.CRS_ERROR)
        dec = decide_recovery(d, risk=RISK_READ_ONLY)
        assert dec.auto_retry is False
        assert "substitute_operator" in dec.allowed_actions


# ── 纯度与确定性 ─────────────────────────────────────────────────────────

class TestPurity:
    def test_deterministic(self):
        d = _diag(GISRecoveryReason.RATE_LIMITED)
        r = RISK_COMPUTE
        assert decide_recovery(d, risk=r).to_bounded_dict() == \
            decide_recovery(d, risk=r).to_bounded_dict()

    def test_backoff_schedule_pure_and_bounded(self):
        assert backoff_schedule_ms("rate_limited") == \
            backoff_schedule_ms("rate_limited")
        assert len(backoff_schedule_ms("rate_limited")) <= 4
        assert backoff_schedule_ms("cancelled") == ()

    def test_to_bounded_dict_keys_closed(self):
        dec = decide_recovery(_diag(GISRecoveryReason.EMPTY_RESULT),
                              risk=RISK_READ_ONLY)
        assert set(dec.to_bounded_dict()) == {
            "reason", "disposition", "auto_retry", "allowed_actions",
            "denied_actions", "denial_reasons", "required_confirmation",
            "backoff_ms", "terminal",
        }


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
