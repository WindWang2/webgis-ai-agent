"""Subagent Budget Class 测试（ADR-0119 决策 D8）。

验收：预算档位词表、交集语义（class 只能收紧）、token 闸、role 显式
档位、usage() 审计留痕、既有 role 数字不被放宽。
"""
import pytest

from app.services.subagent_roles import (
    BUDGET_CLASSES,
    SubagentBudget,
    SUBAGENT_ROLES,
    resolve_budget_class,
)
from app.services.subagent_roles import BudgetExceeded


def test_vocabulary_closed():
    assert set(BUDGET_CLASSES) == {"light", "standard", "heavy", "research"}


def test_resolve_unknown_degrades_to_standard():
    assert resolve_budget_class("nonsense") == BUDGET_CLASSES["standard"]
    assert resolve_budget_class("") == BUDGET_CLASSES["standard"]
    assert resolve_budget_class("HEAVY") == BUDGET_CLASSES["heavy"]


def test_class_never_loosens_role_numbers():
    """交集语义：role 数字 ≤ class 上限时保持 role 数字。"""
    for name, role in SUBAGENT_ROLES.items():
        bc = resolve_budget_class(getattr(role, "budget_class", "standard"))
        assert role.max_tool_calls <= bc["max_tool_calls"] + 1e-9, name
        assert role.max_wall_time_s <= bc["max_wall_time_s"] + 1e-9, name


def test_token_gate_enforced():
    b = SubagentBudget(max_tool_calls=10, max_heavy_tool_calls=2,
                       max_wall_time_s=60.0, max_total_tokens=100,
                       budget_class="light")
    b.add_llm_usage({"prompt_tokens": 60, "completion_tokens": 30})
    b.check_tokens()  # 90 ≤ 100
    b.add_llm_usage({"prompt_tokens": 50, "completion_tokens": 0})
    with pytest.raises(BudgetExceeded):
        b.check_tokens()


def test_token_gate_disabled_when_zero():
    b = SubagentBudget(max_tool_calls=10, max_heavy_tool_calls=2,
                       max_wall_time_s=60.0, max_total_tokens=0)
    b.add_llm_usage({"total_tokens": 10**9})
    b.check_tokens()  # 0 = 不限


def test_usage_carries_budget_class():
    b = SubagentBudget(max_tool_calls=5, max_heavy_tool_calls=1,
                       max_wall_time_s=10.0, max_total_tokens=1000,
                       budget_class="research")
    usage = b.usage()
    assert usage["budget_class"] == "research"
    assert usage["llm_usage"]["reports"] == 0  # 无 provider 回报诚实为 0


def test_builtin_roles_have_known_class():
    for name, role in SUBAGENT_ROLES.items():
        assert getattr(role, "budget_class", "standard") in BUDGET_CLASSES, name
    assert SUBAGENT_ROLES["scientific_reviewer"].budget_class == "light"
