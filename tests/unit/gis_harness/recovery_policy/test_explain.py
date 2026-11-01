"""M6 解释面测试 —— what_failed / what_is_safe_to_try / what_needs_you /
what_already_done 四段 + 四类区分（没算好/算好没显示/显示未验证/导出失败）。
"""
from __future__ import annotations

import pytest

from app.services.gis_harness.recovery_policy.explain import (
    build_recovery_explanation,
    classify_situation,
)

_DIAG = {
    "reason": "transient_timeout", "disposition": "retryable",
    "detail": "provider timeout", "failure_class": "timeout",
}
_DECISION = {"auto_retry": False,
             "allowed_actions": ["retry_with_backoff"],
             "required_confirmation": None}


def _explain(diag=None, decision=None, verification=None, directive=None):
    return build_recovery_explanation(
        diagnostic=diag or _DIAG,
        decision=decision or _DECISION,
        goal_verification=verification,
        retry_directive=directive,
    )


class TestFourSections:
    def test_all_four_sections_present(self):
        exp = _explain()
        payload = exp.to_bounded_dict()
        assert set(payload) == {
            "situation", "what_failed", "what_is_safe_to_try",
            "what_needs_you", "what_already_done"}

    def test_what_failed_carries_reason(self):
        exp = _explain()
        assert "transient_timeout" in exp.what_failed

    def test_safe_to_try_from_allowed_actions(self):
        exp = _explain()
        assert "retry_with_backoff" in exp.what_is_safe_to_try

    def test_needs_you_approval(self):
        exp = _explain(diag={"reason": "permission_denied",
                             "disposition": "needs_user_approval",
                             "detail": "403"},
                       decision={"auto_retry": False,
                                 "allowed_actions": ["owner_confirmation"],
                                 "required_confirmation": "owner_write"})
        assert "approval" in exp.what_needs_you.lower()

    def test_needs_you_data(self):
        exp = _explain(diag={"reason": "eligibility_blocked",
                             "disposition": "needs_user_data",
                             "detail": "missing_field:score"},
                       decision={"auto_retry": False,
                                 "allowed_actions": ["ask_user_data"],
                                 "required_confirmation": None})
        assert "data" in exp.what_needs_you.lower()

    def test_already_done_from_verification_and_directive(self):
        exp = _explain(
            verification={"slots": [
                {"slot": "data_coverage", "status": "pass"},
                {"slot": "actual_rendering", "status": "pass"},
                {"slot": "export_delivery", "status": "fail"}]},
            directive={"preserved_capabilities": ["ingest_schools"],
                       "preserved_refs": ["ref:data-a"]})
        assert "data_coverage" in exp.what_already_done
        assert "ref:data-a" in exp.what_already_done

    def test_bounded_lengths(self):
        exp = _explain()
        d = exp.to_bounded_dict()
        assert all(len(str(v)) <= 240 for v in d.values())

    def test_never_raises(self):
        exp = build_recovery_explanation(
            diagnostic=None, decision="x", goal_verification=42,
            retry_directive=None)
        assert exp is not None
        assert exp.situation


class TestSituationClassification:
    """四类区分：computed_incomplete / computed_not_displayed /
    displayed_unverified / export_failed（+ clean）。"""

    def test_computed_incomplete(self):
        assert classify_situation(
            science="fail", rendering="pass", export=None) == \
            "computed_incomplete"

    def test_computed_not_displayed(self):
        assert classify_situation(
            science="pass", rendering="fail", export=None) == \
            "computed_not_displayed"

    def test_displayed_unverified(self):
        assert classify_situation(
            science="not_evaluated", rendering="pass", export=None) == \
            "displayed_unverified"

    def test_export_failed(self):
        assert classify_situation(
            science="pass", rendering="pass", export="fail") == \
            "export_failed"

    def test_clean(self):
        assert classify_situation(
            science="pass", rendering="pass", export="pass") == "clean"

    def test_na_science_clean(self):
        """R2 复审 P1-1：N/A 科学槽（None）+ 渲染 pass → clean。"""
        assert classify_situation(
            science=None, rendering="pass", export=None) == "clean"
        assert classify_situation(
            science=None, rendering="pass", export="pass") == "clean"

    def test_priority_export_over_others(self):
        # 导出失败优先披露（用户显式要求的交付物未兑现）
        assert classify_situation(
            science="fail", rendering="fail", export="fail") == \
            "export_failed"


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
