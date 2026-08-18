"""verdict_summary: bounded LLM rendering + injection policy of the harness verdict.

注入策略的两条硬守卫：
- 指纹守卫：verdict 只在与其 MapSpec 代（指纹一致）时才可注入，跨代/缺指纹
  一律不注入（无法验证就不当证据用）；
- 噪音守卫：passed/passed_with_warnings/superseded 与无制图活动的会话不注入。

渲染必须有界：failed checks ≤3、message ≤200 字、整体硬截断。
"""
from app.lib.cartography.verdict_summary import (
    _MAX_MESSAGE_CHARS,
    render_verdict_for_llm,
    should_inject_verdict,
)

FP = "carto-sha256:abc123"


def _review(status="failed_repairable", reason="desired_quality_failed", fp=FP):
    return {
        "session_id": "s1",
        "cartography": {
            "status": status,
            "termination_reason": reason,
            "mapspec_fingerprint": fp,
            "checks": [],
            "repair_attempts": [],
        },
        "gate": {},
        "overall_passed": False,
    }


class TestShouldInjectVerdict:
    def test_none_or_malformed_review_never_injects(self):
        assert should_inject_verdict(None, FP) is False
        assert should_inject_verdict({}, FP) is False
        assert should_inject_verdict({"cartography": "oops"}, FP) is False

    def test_unresolved_failure_with_matching_fingerprint_injects(self):
        assert should_inject_verdict(_review(), FP) is True

    def test_passed_states_skip(self):
        assert should_inject_verdict(_review(status="passed"), FP) is False
        assert should_inject_verdict(
            _review(status="passed_with_warnings"), FP
        ) is False

    def test_superseded_generation_skips(self):
        # 旧代 verdict：用户意图已前进，注入只会误导本 turn。
        assert should_inject_verdict(_review(status="superseded"), FP) is False

    def test_no_cartography_activity_skips(self):
        assert should_inject_verdict(
            _review(reason="no_session_harness"), FP
        ) is False
        assert should_inject_verdict(
            _review(reason="no_mapspec_mutation"), FP
        ) is False

    def test_fingerprint_mismatch_or_missing_never_injects(self):
        assert should_inject_verdict(_review(fp="carto-sha256:other"), FP) is False
        assert should_inject_verdict(_review(fp=None), FP) is False
        assert should_inject_verdict(_review(fp=""), FP) is False

    def test_unknown_current_fingerprint_never_injects(self):
        # 当前指纹不可得 → 无法验证一致性 → 不注入。
        assert should_inject_verdict(_review(), None) is False
        assert should_inject_verdict(_review(), "") is False


class TestRenderVerdictForLlm:
    def test_renders_marker_status_and_tool_hint(self):
        block = render_verdict_for_llm(_review())
        assert block.startswith("[CARTOGRAPHY_VERDICT]")
        assert '"failed_repairable"' in block
        assert '"desired_quality_failed"' in block
        assert "webgis_cartography_status" in block

    def test_failed_checks_capped_at_three_with_clipped_messages(self):
        review = _review()
        review["cartography"]["checks"] = [
            {
                "rule": f"RULE_{i}",
                "status": "fail",
                "message": "x" * (_MAX_MESSAGE_CHARS + 50),
            }
            for i in range(6)
        ] + [{"rule": "PASS_RULE", "status": "pass", "message": "ok"}]
        block = render_verdict_for_llm(review)
        assert block.count('"RULE_') == 3, "only the first 3 failing checks project"
        assert "PASS_RULE" not in block, "passing checks never project"
        assert "…" in block, "over-long message is clipped with an ellipsis"

    def test_not_evaluated_checks_are_included(self):
        review = _review()
        review["cartography"]["checks"] = [
            {"rule": "RUNTIME_OBSERVATION_FRESHNESS", "status": "not_evaluated",
             "message": "No newer session-owned frontend observation exists."}
        ]
        block = render_verdict_for_llm(review)
        assert "RUNTIME_OBSERVATION_FRESHNESS" in block

    def test_suggested_fix_projected_when_present(self):
        review = _review()
        review["cartography"]["checks"] = [
            {
                "rule": "RUNTIME_RESULT_VISIBILITY",
                "status": "fail",
                "message": "visibility differs",
                "suggested_fix": {"operation": "set_runtime_visibility", "layer_id": "eq"},
            }
        ]
        block = render_verdict_for_llm(review)
        assert "set_runtime_visibility" in block

    def test_repair_attempts_projected_to_last_two(self):
        review = _review()
        review["cartography"]["repair_attempts"] = [
            {"iteration": i, "status": "issued", "repairability": "auto_safe"}
            for i in range(1, 5)
        ]
        block = render_verdict_for_llm(review)
        assert '"iteration": 4' in block.replace(", ", ", ")
        assert '"iteration": 3' in block
        assert '"iteration": 1' not in block

    def test_output_hard_bounded(self):
        review = _review()
        review["cartography"]["checks"] = [
            {"rule": f"R{i}", "status": "fail", "message": "y" * 400} for i in range(3)
        ]
        review["cartography"]["repair_attempts"] = [
            {"iteration": 1, "status": "issued", "repairability": "auto_safe"}
        ]
        block = render_verdict_for_llm(review)
        assert len(block) < 2000

    def test_empty_cartography_degrades_to_not_evaluated(self):
        block = render_verdict_for_llm({"session_id": "s1", "overall_passed": False})
        assert '"not_evaluated"' in block
