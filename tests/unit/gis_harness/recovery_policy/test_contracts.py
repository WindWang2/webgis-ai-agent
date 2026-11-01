"""GISRecoveryDiagnostic.v1 契约测试（GOAL 05 / M1）。

钉死：词表封闭（reason ↔ registry 双射；reason → 既有 HarnessFailureClass
合法值）、六处置封闭、全字段有界、确定性（同输入同输出 —— 零时钟/零 IO）、
TDS harness_failure payload 消费形状。
"""
from __future__ import annotations

import pytest

from app.services.gis_harness.failure_taxonomy import HarnessFailureClass
from app.services.gis_harness.recovery_policy.contracts import (
    DIAGNOSTIC_SCHEMA,
    DiagnosticAttribution,
    GISRecoveryDiagnostic,
    GISRecoveryReason,
    RECOVERY_DISPOSITIONS,
    REASON_REGISTRY,
    RecoveryDisposition,
    RetryBudgetSnapshot,
)

_HARNESS_CLASSES = {c.value for c in HarnessFailureClass}


def _make(**kw) -> GISRecoveryDiagnostic:
    return GISRecoveryDiagnostic(
        reason=kw.pop("reason", GISRecoveryReason.TRANSIENT_TIMEOUT),
        attribution=kw.pop(
            "attribution", DiagnosticAttribution(session_id="s1")),
        **kw,
    )


# ── 词表封闭 ─────────────────────────────────────────────────────────────

class TestVocabularyClosure:
    def test_reason_registry_is_exactly_the_reason_enum(self):
        """registry 键集 == 枚举全集（不多不少）—— 新码必须登记。"""
        assert set(REASON_REGISTRY) == {r.value for r in GISRecoveryReason}

    def test_every_reason_maps_to_existing_harness_class(self):
        """诊断层不发明粗类：每个 reason 映射到既有 11 类之一。"""
        for reason, entry in REASON_REGISTRY.items():
            assert entry.harness_class in _HARNESS_CLASSES, reason

    def test_every_reason_has_valid_disposition_and_hints(self):
        valid_hints = {
            "retry", "retry_with_backoff", "fallback_tool",
            "substitute_operator", "requery_profile", "replan", "reobserve",
            "ask_user_data", "owner_confirmation", "abort_with_disclosure",
        }
        for reason, entry in REASON_REGISTRY.items():
            assert isinstance(entry.disposition, RecoveryDisposition), reason
            assert entry.hints, reason
            assert set(entry.hints) <= valid_hints, reason

    def test_dispositions_are_exactly_six(self):
        assert {d.value for d in RecoveryDisposition} == RECOVERY_DISPOSITIONS
        assert len(RECOVERY_DISPOSITIONS) == 6

    def test_every_harness_class_has_at_least_one_reason(self):
        """11 个既有粗类都能被诊断层表达（无盲区）。"""
        covered = {e.harness_class for e in REASON_REGISTRY.values()}
        assert covered == _HARNESS_CLASSES

    def test_terminal_dispositions_are_documented_closed(self):
        terminal = {
            r for r, e in REASON_REGISTRY.items()
            if e.disposition is RecoveryDisposition.TERMINAL
        }
        # 取消/预算耗尽/无进展是仅有的 terminal 码 —— 其余失败永远留给
        # 修复/用户路径（不允许把未知失败折叠成 terminal 静默放弃）。
        assert terminal == {"cancelled", "budget_exhausted", "no_progress"}


# ── 有界性 ───────────────────────────────────────────────────────────────

class TestBoundedness:
    def test_detail_truncated_to_200(self):
        d = _make(detail="x" * 500)
        assert len(d.to_bounded_dict()["detail"]) <= 200

    def test_evidence_refs_bounded(self):
        d = _make(evidence_refs=[f"e{i}" for i in range(20)])
        assert len(d.to_bounded_dict()["evidence_refs"]) <= 8

    def test_hints_bounded(self):
        d = _make(hints=["retry"] * 10)
        assert len(d.to_bounded_dict()["hints"]) <= 4

    def test_attribution_strings_bounded(self):
        a = DiagnosticAttribution(
            session_id="s" * 200, tool_name="t" * 200, ref="r" * 200)
        payload = a.to_bounded_dict()
        assert all(len(v) <= 64 for v in payload.values())


# ── 确定性与消费形状 ─────────────────────────────────────────────────────

class TestDeterminismAndPayload:
    def test_schema_version_pinned(self):
        assert DIAGNOSTIC_SCHEMA == "gis_recovery_diagnostic.v1"

    def test_same_input_same_payload(self):
        kw = dict(
            reason=GISRecoveryReason.RATE_LIMITED,
            detail="upstream 429",
            attribution=DiagnosticAttribution(
                session_id="s1", tool_name="webgis_geocode"),
            retry=RetryBudgetSnapshot(
                attempts=1, max_attempts=2, retry_allowed=True),
            evidence_refs=["trace:TOOL_RESULTS"],
        )
        d1 = GISRecoveryDiagnostic(**kw).to_bounded_dict()
        d2 = GISRecoveryDiagnostic(**kw).to_bounded_dict()
        assert d1 == d2

    def test_from_harness_failure_payload_carries_budget(self):
        d = GISRecoveryDiagnostic.from_harness_failure_payload(
            {"class": "timeout", "remediation": "retry_with_backoff",
             "attempts": 2, "max_attempts": 2, "retry_allowed": False},
            reason=GISRecoveryReason.TRANSIENT_TIMEOUT,
            attribution=DiagnosticAttribution(session_id="s1"),
        )
        assert d.retry is not None
        assert d.retry.attempts == 2
        assert d.retry.retry_allowed is False

    def test_from_harness_failure_payload_defaults_budget_absent(self):
        d = GISRecoveryDiagnostic.from_harness_failure_payload(
            {}, reason=GISRecoveryReason.UNKNOWN_FAILURE,
            attribution=DiagnosticAttribution(session_id="s1"),
        )
        assert d.retry is None

    def test_payload_keys_closed(self):
        d = _make()
        assert set(d.to_bounded_dict()) == {
            "schema_version", "reason", "disposition", "failure_class",
            "detail", "attribution", "retry", "evidence_refs", "hints",
        }

    def test_disposition_consistent_with_registry(self):
        d = _make(reason=GISRecoveryReason.PERMISSION_DENIED)
        assert d.disposition is RecoveryDisposition.NEEDS_USER_APPROVAL


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
