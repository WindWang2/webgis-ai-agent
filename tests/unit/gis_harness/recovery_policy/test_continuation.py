"""M5 多轮继续测试 —— 重试指令：保持已验证产物、只重试失败子图、
跨会话/未知归属 ref fail-closed。
"""
from __future__ import annotations

import pytest

from app.services.gis_harness.recovery_policy.continuation import (
    resolve_retry_directive,
)

_ROWS = [
    {"capability": "ingest_schools", "status": "complete",
     "bound_ref": "ref:data-a"},
    {"capability": "buffer_analysis", "status": "failed",
     "bound_ref": None},
    {"capability": "heatmap_render", "status": "pending",
     "bound_ref": None},
]

_LAST_REC = {
    "tool": "buffer_analysis",
    "diagnostic": {
        "reason": "transient_timeout", "disposition": "retryable",
        "retry": {"attempts": 1, "max_attempts": 2, "retry_allowed": True}},
    "decision": {"auto_retry": False},
}


def _directive(**kw):
    defaults = dict(
        progress_rows=_ROWS,
        last_recovery=_LAST_REC,
        chapter={"workflow_contract": {"workflow_id": "wf-1"}},
        user_ref_hints=[],
        ref_owner_sessions=None,
    )
    defaults.update(kw)
    return resolve_retry_directive(**defaults)


class TestSelectiveRetry:
    def test_only_failed_rows_are_retry_targets(self):
        d = _directive()
        assert d.retry_capabilities == ["buffer_analysis"]
        assert "ingest_schools" not in d.retry_capabilities

    def test_verified_rows_are_preserved(self):
        d = _directive()
        assert d.preserved_capabilities == ["ingest_schools"]
        assert d.preserved_refs == ["ref:data-a"]

    def test_no_failures_no_directive_actions(self):
        d = _directive(progress_rows=[
            {"capability": "x", "status": "complete", "bound_ref": "ref:1"}])
        assert d.retry_capabilities == []
        assert d.verdict == "nothing_to_retry"

    def test_budget_exhausted_needs_user(self):
        rec = dict(_LAST_REC)
        rec["diagnostic"] = dict(_LAST_REC["diagnostic"])
        rec["diagnostic"]["retry"] = {
            "attempts": 2, "max_attempts": 2, "retry_allowed": False}
        d = _directive(last_recovery=rec)
        assert d.verdict == "budget_exhausted"
        assert d.retry_capabilities == []  # 不自动指向重试

    def test_workflow_chapter_suggests_recompute_seam(self):
        d = _directive()
        assert "webgis_analysis_workflow_recompute" in d.suggested_tools

    def test_terminal_reason_blocks_retry(self):
        rec = dict(_LAST_REC)
        rec["diagnostic"] = {
            "reason": "cancelled", "disposition": "terminal",
            "retry": None}
        d = _directive(last_recovery=rec)
        assert d.verdict == "not_retryable"


class TestRefFailClosed:
    def test_cross_session_ref_fail_closed(self):
        d = _directive(
            user_ref_hints=["ref:data-foreign"],
            ref_owner_sessions={"ref:data-foreign": "sess-OTHER"})
        assert d.verdict == "fail_closed"
        assert d.fail_closed_reasons
        assert "ref:data-foreign" in d.fail_closed_reasons[0]

    def test_unknown_owner_ref_fail_closed(self):
        """归属不可证明 → fail-closed（绝不猜）。"""
        d = _directive(
            user_ref_hints=["ref:data-unknown"],
            ref_owner_sessions=None)
        assert d.verdict == "fail_closed"

    def test_same_session_ref_allowed(self):
        d = _directive(
            user_ref_hints=["ref:data-a"],
            ref_owner_sessions={"ref:data-a": "sess-current"},
            current_session_id="sess-current")
        assert d.verdict != "fail_closed"

    def test_no_ref_hints_open(self):
        d = _directive(user_ref_hints=[])
        assert d.verdict != "fail_closed"


class TestContract:
    def test_bounded_and_deterministic(self):
        d1 = _directive().to_bounded_dict()
        d2 = _directive().to_bounded_dict()
        assert d1 == d2
        assert set(d1) == {
            "verdict", "retry_capabilities", "preserved_capabilities",
            "preserved_refs", "suggested_tools", "fail_closed_reasons",
            "directive_line"}

    def test_directive_line_compact(self):
        line = _directive().directive_line
        assert len(line) <= 240
        assert "retry" in line

    def test_never_raises(self):
        d = resolve_retry_directive(
            progress_rows=None, last_recovery="garbage", chapter=42,
            user_ref_hints=None, ref_owner_sessions="x")
        assert d is not None


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
