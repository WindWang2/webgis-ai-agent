"""统一 turn 错误分类（H03 error_taxonomy）全表测试。

契约：
- 映射表覆盖 legacy 工具面/turn 级 + Pi bridge 私有词表全量；
- 未知值诚实降级（tool 层 → tool_error，turn 层 → internal_error），
  绝不猜测成可重试类；
- additive：只提供 error_class 词表，不改任何既有 failure_class。
"""
from __future__ import annotations

import pytest

from app.services.chat.error_taxonomy import (
    RETRYABLE_ERROR_CLASSES,
    TurnErrorClass,
    classify_turn_exception,
    error_class_for_tool_failure,
    error_class_for_turn_failure,
    is_retryable,
)


class TestToolFailureMapping:
    @pytest.mark.parametrize(
        "failure_class,expected",
        [
            ("validation", "tool_error"),
            ("missing_ref", "tool_error"),
            ("empty_result", "tool_error"),  # 工具面空结果 ≠ turn 级空补全
            ("no_data", "tool_error"),
            ("tool_unavailable", "tool_error"),
            ("auth", "policy_reject"),
            ("resource_limit", "policy_reject"),
            ("transient_network", "transport_error"),
            ("cancelled", "agent_abort"),
            ("internal", "internal_error"),
        ],
    )
    def test_planning_failure_class_table(self, failure_class, expected):
        assert error_class_for_tool_failure(failure_class) == expected

    def test_none_is_tool_error(self):
        # step 语境确认是工具失败；未细分 ≠ 未知错误
        assert error_class_for_tool_failure(None) == "tool_error"
        assert error_class_for_tool_failure("") == "tool_error"

    def test_unknown_forward_compatible_is_tool_error(self):
        # 未来新增 FailureClass 值不炸映射；细语义仍由 failure_class 携带
        assert error_class_for_tool_failure("brand_new_class") == "tool_error"


class TestTurnFailureMapping:
    @pytest.mark.parametrize(
        "failure_class,expected",
        [
            # legacy turn 级
            ("turn_timeout", "turn_timeout"),
            ("no_progress", "no_progress"),
            ("empty_result", "empty_result"),
            ("max_rounds", "max_rounds"),
            ("chat_stream_exception", "internal_error"),
            ("turn_failure", "internal_error"),
            ("cancelled", "agent_abort"),
            # Pi bridge 词表
            ("pi_aborted", "agent_abort"),
            ("pi_turn_budget", "turn_timeout"),
            ("pi_stall", "provider_error"),
            ("pi_process_died", "transport_error"),
            ("process_died", "transport_error"),
            ("pi_send_error", "transport_error"),
            ("pi_unclassified_error", "internal_error"),
            ("pi_turn_error", "internal_error"),
            # pi_abort_<source> 家族
            ("pi_abort_user", "agent_abort"),
            ("pi_abort_session_deleted", "agent_abort"),
        ],
    )
    def test_turn_level_table(self, failure_class, expected):
        assert error_class_for_turn_failure(failure_class) == expected

    def test_none_and_unknown_are_internal(self):
        assert error_class_for_turn_failure(None) == "internal_error"
        assert error_class_for_turn_failure("") == "internal_error"
        assert error_class_for_turn_failure("mystery") == "internal_error"


class TestRetryable:
    @pytest.mark.parametrize(
        "error_class,retryable",
        [
            ("transport_error", True),
            ("retryable_disconnect", True),
            ("provider_error", True),
            # 预算/熔断类刻意不可自动重试：重发同任务只会复现失败
            ("turn_timeout", False),
            ("no_progress", False),
            ("max_rounds", False),
            ("agent_abort", False),
            ("policy_reject", False),
            ("internal_error", False),
            ("tool_error", False),
        ],
    )
    def test_retryable_table(self, error_class, retryable):
        assert is_retryable(error_class) is retryable

    def test_retryable_set_matches_enum(self):
        assert RETRYABLE_ERROR_CLASSES <= {c.value for c in TurnErrorClass}


class TestClassifyTurnException:
    def test_session_clearing_is_policy_reject(self):
        from app.services.chat.execution_engine import SessionClearingError

        assert classify_turn_exception(SessionClearingError("busy")) == "policy_reject"

    def test_unknown_exception_is_internal(self):
        assert classify_turn_exception(ValueError("x")) == "internal_error"
        assert classify_turn_exception(RuntimeError("x")) == "internal_error"

    def test_base_exception_cancel_is_abort(self):
        import asyncio

        assert classify_turn_exception(asyncio.CancelledError()) == "agent_abort"

    def test_never_raises(self):
        # 传不可分类对象也不得抛（分类器自身的健壮性契约）
        assert classify_turn_exception(None) in {c.value for c in TurnErrorClass}
