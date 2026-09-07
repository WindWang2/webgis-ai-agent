"""GIS-aware Context Budgeting V2 测试（ADR-0103 §七）。

Wave 5 的度量/投影语义见 test_context_budget_v2.py；本文件锁定 V2 增量：
- 新 GIS 分区（DATA_PROFILE / ALGORITHM_METADATA / CARTOGRAPHY_METADATA /
  TOOL_RESULTS）及其确定性上限；
- GisBudgetAdvisor：keep/condense/offload_ref/drop 建议（不落刀）、
  可观察报告（分区 token、输出保留、溢出原因）、确定性；
- context_assembler 的 budget_report 携带 gis_advice。
"""
import inspect

from app.services.chat.context_budget import (
    GIS_SECTION_CAPS,
    BudgetItem,
    Category,
    GisBudgetAdvisor,
    plan_budget,
)


def test_v2_categories_exist():
    assert Category.DATA_PROFILE == 12
    assert Category.ALGORITHM_METADATA == 13
    assert Category.CARTOGRAPHY_METADATA == 14
    assert Category.TOOL_RESULTS == 15
    # 裁剪优先级单调：V2 分区先于核心被裁
    assert Category.TOOL_RESULTS > Category.TRACE_SUMMARIES
    assert Category.TOOL_RESULTS > Category.HISTORY


def test_plan_budget_has_gis_section_caps():
    plan = plan_budget(context_window=100_000, max_output_tokens=8_192)
    for name in ("TOOL_RESULTS", "DATA_PROFILE", "ALGORITHM_METADATA",
                 "CARTOGRAPHY_METADATA", "SESSION_PLAN", "MAP_STATE"):
        assert name in plan.category_budgets, name
    assert plan.category_budgets["TOOL_RESULTS"] == int(plan.usable * GIS_SECTION_CAPS["TOOL_RESULTS"])
    assert plan.reserved_output > 0


def test_advisor_offloads_large_tool_results():
    advisor = GisBudgetAdvisor()
    items = [
        BudgetItem(category=Category.TOOL_RESULTS, name="kde_result",
                   est_tokens=12_000),
        BudgetItem(category=Category.USER_PROMPT, name="user", text="成都市热力图"),
    ]
    advice = advisor.advise(items, context_window=32_000, max_output_tokens=4_096)
    actions = {a["item"]: a for a in advice.actions}
    assert "kde_result" in actions
    assert actions["kde_result"]["action"] == "offload_ref"
    assert actions["kde_result"]["over_by"] > 0


def test_advisor_never_touches_user_prompt_or_system():
    advisor = GisBudgetAdvisor()
    huge = 200_000
    items = [
        BudgetItem(category=Category.USER_PROMPT, name="user", est_tokens=huge),
        BudgetItem(category=Category.SYSTEM_INSTRUCTIONS, name="system", est_tokens=huge),
    ]
    advice = advisor.advise(items, context_window=32_000, max_output_tokens=4_096)
    assert advice.actions == []


def test_advisor_condenses_trace_and_metadata():
    advisor = GisBudgetAdvisor()
    items = [
        BudgetItem(category=Category.TRACE_SUMMARIES, name="trace", est_tokens=6_000),
        BudgetItem(category=Category.DATA_PROFILE, name="profile", est_tokens=4_000),
        BudgetItem(category=Category.HISTORY, name="history", est_tokens=2_000),
    ]
    advice = advisor.advise(items, context_window=32_000, max_output_tokens=4_096)
    actions = {a["item"]: a["action"] for a in advice.actions}
    assert actions.get("trace") == "condense"
    assert actions.get("profile") == "condense"
    assert "history" not in actions


def test_advice_deterministic_and_observable():
    advisor = GisBudgetAdvisor()
    items = [
        BudgetItem(category=Category.TOOL_RESULTS, name="big_a", est_tokens=9_000),
        BudgetItem(category=Category.TOOL_RESULTS, name="big_b", est_tokens=2_000),
    ]
    a1 = advisor.advise(items, context_window=32_000, max_output_tokens=4_096).as_dict()
    a2 = advisor.advise(items, context_window=32_000, max_output_tokens=4_096).as_dict()
    assert a1 == a2
    assert a1["reserved_output"] > 0
    assert "by_section" in a1
    assert a1["overflow_reason"] is None or a1["overflow_reason"].startswith(("near_budget", "total"))


def test_assembler_report_carries_gis_advice():
    from app.services.chat import context_assembler as ca

    src = inspect.getsource(ca)
    assert "gis_advice" in src and "GisBudgetAdvisor" in src
