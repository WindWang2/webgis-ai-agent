"""统一 capability policy 入口对抗测试(H05)。

覆盖:决策矩阵(credential_missing/permission_denied/certification_stale/
provider_unhealthy/policy_denied)、fail-closed vs 缺席面放行、错误码
保持既有闸契约、多因优先级。
"""
from __future__ import annotations

from app.lib.capability_policy import (
    CODE_CREDENTIALS_REQUIRED,
    CODE_PERMISSION_DENIED,
    POLICY_OK,
    REASON_CREDENTIAL_MISSING,
    REASON_CERTIFICATION_STALE,
    REASON_PERMISSION_DENIED,
    REASON_POLICY_DENIED,
    REASON_PROVIDER_UNHEALTHY,
    CapabilityPolicyFact,
    evaluate_capability_policy,
)


class TestDecisionMatrix:
    def test_allow_with_no_requirements(self):
        d = evaluate_capability_policy(CapabilityPolicyFact())
        assert d.allowed is True
        assert d.reason_code == POLICY_OK
        assert d.error_code == ""

    def test_credential_missing_is_typed_and_carries_gate_code(self):
        d = evaluate_capability_policy(CapabilityPolicyFact(
            required_credentials=("smtp",),
            credentials_present=(),
        ))
        assert d.allowed is False
        assert d.reason_code == REASON_CREDENTIAL_MISSING
        assert d.error_code == CODE_CREDENTIALS_REQUIRED

    def test_credential_present_passes(self):
        d = evaluate_capability_policy(CapabilityPolicyFact(
            required_credentials=("smtp", "tile"),
            credentials_present=("smtp", "tile", "other"),
        ))
        assert d.allowed is True

    def test_permission_denied(self):
        d = evaluate_capability_policy(CapabilityPolicyFact(
            required_permission="admin_export",
            granted_permissions=("viewer",),
        ))
        assert d.allowed is False
        assert d.reason_code == REASON_PERMISSION_DENIED
        assert d.error_code == CODE_PERMISSION_DENIED

    def test_certification_stale_blocks(self):
        d = evaluate_capability_policy(CapabilityPolicyFact(
            certification_state="stale",
        ))
        assert d.allowed is False
        assert d.reason_code == REASON_CERTIFICATION_STALE

    def test_certification_invalid_blocks(self):
        d = evaluate_capability_policy(CapabilityPolicyFact(
            certification_state="invalid",
        ))
        assert d.allowed is False

    def test_certification_unknown_lenient_for_core(self):
        """核心域缺席面不裁决(unknown 不虚构拒绝)。"""
        d = evaluate_capability_policy(CapabilityPolicyFact(
            certification_state="unknown", fail_closed=False,
        ))
        assert d.allowed is True

    def test_certification_unknown_fails_closed_for_extensions(self):
        d = evaluate_capability_policy(CapabilityPolicyFact(
            certification_state="unknown", fail_closed=True,
        ))
        assert d.allowed is False
        assert d.reason_code == REASON_CERTIFICATION_STALE

    def test_provider_unhealthy(self):
        d = evaluate_capability_policy(CapabilityPolicyFact(
            health_state="open",
        ))
        assert d.allowed is False
        assert d.reason_code == REASON_PROVIDER_UNHEALTHY

    def test_half_open_not_denied_by_health(self):
        """半开是恢复路径:policy 面不拒绝(执法在 bind 断路支)。"""
        d = evaluate_capability_policy(CapabilityPolicyFact(
            health_state="half_open",
        ))
        assert d.allowed is True

    def test_reason_precedence_credential_first(self):
        d = evaluate_capability_policy(CapabilityPolicyFact(
            required_credentials=("smtp",),
            required_permission="admin",
            certification_state="stale",
            health_state="open",
        ))
        assert d.reason_code == REASON_CREDENTIAL_MISSING
        assert list(d.reasons) == [
            REASON_CREDENTIAL_MISSING,
            REASON_PERMISSION_DENIED,
            REASON_CERTIFICATION_STALE,
            REASON_PROVIDER_UNHEALTHY,
        ]

    def test_policy_denied_reason_in_vocabulary(self):
        assert REASON_POLICY_DENIED == "policy_denied"

    def test_to_dict_bounded(self):
        d = evaluate_capability_policy(CapabilityPolicyFact(
            required_credentials=("a", "b", "c"),
            required_permission="x",
        ))
        payload = d.to_dict()
        assert set(payload.keys()) == {"allowed", "reason_code", "error_code", "reasons"}
        assert len(payload["reasons"]) <= 4
