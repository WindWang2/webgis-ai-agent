"""ADR-0219 C5：conflict reason-code matrix —— 全表裁决/未知 fail-closed/回执映射。"""
from __future__ import annotations


from app.services.gis_harness.representation_binding.conflicts import (
    REASON_CODES,
    disclosure_for,
    resolve_conflict,
    resolve_from_result,
)


class TestMatrix:
    def test_every_registered_code_resolves_to_itself(self):
        for code in REASON_CODES:
            resolution = resolve_conflict(code)
            assert resolution.code == code
            assert resolution.winner in ("user", "agent", "system", "none")
            assert resolution.action in (
                "refuse", "retry_with_fresh_revision", "skip_disclosed",
                "rebase", "apply", "defer_to_user")
            assert resolution.disclosure

    def test_unknown_code_fail_closed_refuse(self):
        resolution = resolve_conflict("totally_unknown")
        assert resolution.action == "refuse"
        assert resolution.winner == "none"

    def test_detail_appended_bounded(self):
        resolution = resolve_conflict("binding_target_locked", detail="roads")
        assert resolution.code == "binding_target_locked"
        assert "[roads]" in resolution.disclosure

    def test_user_wins_refusals(self):
        for code in ("binding_target_locked", "binding_user_wins_presentation",
                     "conflict_finalizer_user_disabled"):
            assert resolve_conflict(code).winner == "user"
            assert resolve_conflict(code).action == "refuse"

    def test_cas_is_retry_not_conflict(self):
        for code in ("binding_stale_revision", "conflict_two_tabs_cas"):
            resolution = resolve_conflict(code)
            assert resolution.action == "retry_with_fresh_revision"
            assert resolution.winner == "none"

    def test_no_silent_last_write_wins(self):
        # 拒绝类动作必须存在披露（无静默裁决）。
        for code in REASON_CODES:
            if resolve_conflict(code).action in ("refuse", "skip_disclosed"):
                assert resolve_conflict(code).disclosure.strip()

    def test_disclosure_for_unknown(self):
        assert "未注册" in disclosure_for("nope")


class TestResultMapping:
    def test_duplicate_maps_to_replay(self):
        assert resolve_from_result(
            is_error=False, superseded=False, duplicate=True,
            error_code=None) == "binding_duplicate_replay"

    def test_superseded_maps_to_stale(self):
        assert resolve_from_result(
            is_error=False, superseded=True, duplicate=False,
            error_code=None) == "binding_stale_revision"

    def test_layer_locked_maps_to_target_locked(self):
        assert resolve_from_result(
            is_error=True, superseded=False, duplicate=False,
            error_code="layer_locked") == "binding_target_locked"

    def test_other_error_passthrough(self):
        assert resolve_from_result(
            is_error=True, superseded=False, duplicate=False,
            error_code="workbench_delta_invalid") == "workbench_delta_invalid"

    def test_success_maps_to_none(self):
        assert resolve_from_result(
            is_error=False, superseded=False, duplicate=False, error_code=None) is None
