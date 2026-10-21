"""ADR-0219 C5：conflict reason-code matrix —— 全表裁决/未知 fail-closed/回执映射。"""
from __future__ import annotations


import pytest

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

    def test_pass_truncated_registered_as_non_error_skip(self):
        # #1671：binding_pass_truncated 是"绑定通过但引用被截断"的
        # 非错误 skip 回执，必须登记为 skip_disclosed（不得 fail-closed refuse）。
        code = "binding_pass_truncated"
        assert code in REASON_CODES
        resolution = resolve_conflict(code)
        assert resolution.code == code
        assert resolution.action == "skip_disclosed"
        assert resolution.winner == "none"
        assert resolution.disclosure
        assert disclosure_for(code) != f"未注册 reason code: {code}"

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

    @pytest.mark.parametrize("engine_code", [
        "layer_locked",
        "user_wins_presentation",
        "workbench_delta_invalid",
        "workbench_delta_conflict",
        "quality_gate_blocked",
        "UNSUPPORTED_MUTATION_INTENT",
        "HEAL_CONVERGENCE_EXHAUSTED",
        "HEAL_PLAN_EMPTY",
    ])
    def test_known_engine_codes_map_into_matrix(self, engine_code):
        # C5 登记闭环：mapspec 引擎面全部已知错误码映射后必须仍在
        # REASON_CODES 矩阵内（透传 off-vocabulary 码 = 遥测漏类）。
        code = resolve_from_result(
            is_error=True, superseded=False, duplicate=False,
            error_code=engine_code)
        assert code in REASON_CODES, (
            f"引擎码 {engine_code} 映射出矩阵外 code: {code}")

    def test_workbench_delta_invalid_maps_to_registered_generic(self):
        assert resolve_from_result(
            is_error=True, superseded=False, duplicate=False,
            error_code="workbench_delta_invalid") == "binding_engine_refused"

    def test_heal_family_maps_to_registered_generic(self):
        for engine_code in ("HEAL_CONVERGENCE_EXHAUSTED", "HEAL_PLAN_EMPTY",
                            "HEAL_SOMETHING_NEW"):
            assert resolve_from_result(
                is_error=True, superseded=False, duplicate=False,
                error_code=engine_code) == "binding_engine_refused"

    def test_unknown_engine_code_falls_back_to_registered_generic(self):
        code = resolve_from_result(
            is_error=True, superseded=False, duplicate=False,
            error_code="some_future_engine_code")
        assert code == "binding_engine_refused"
        assert code in REASON_CODES
        assert disclosure_for(code) != f"未注册 reason code: {code}"

    def test_missing_engine_code_falls_back_to_registered_generic(self):
        assert resolve_from_result(
            is_error=True, superseded=False, duplicate=False,
            error_code=None) == "binding_engine_refused"

    def test_success_maps_to_none(self):
        assert resolve_from_result(
            is_error=False, superseded=False, duplicate=False, error_code=None) is None
