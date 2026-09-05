"""ADR-0101 Wave 5: 上下文预算管理器 + 有界投影测试。

锁定语义：
- 预算规划确定性（保留输出、tool schema 硬上限比例、未知窗口保守回退）；
- 度量分类（system/user/history/tools）与违规/警告 reason codes；
- tool schema 永远不能吃满上下文（TOOL_SCHEMA_MAX_FRACTION 红线）；
- 投影原语有界且确定性（bound_text/bound_lines/layer/ref 投影）；
- ProjectionCache 指纹命中/失效、有界、线程内一致性。
"""
import pytest

from app.services.chat.context_budget import (
    Category,
    BudgetItem,
    measure_assembled_context,
    measure_components,
    plan_budget,
)
from app.services.chat.context_projections import (
    ProjectionCache,
    bound_lines,
    bound_text,
    content_fingerprint,
    project_layer_inventory,
    project_ref_list,
)


# ---------------------------------------------------------------------------
# 预算规划
# ---------------------------------------------------------------------------

def test_plan_budget_reserves_output():
    plan = plan_budget(context_window=100_000, max_output_tokens=16_384)
    # 保留 = max(16384, 100000*0.25=25000) = 25000
    assert plan.reserved_output == 25_000
    assert plan.usable == 75_000
    # 12k 绝对上限生效（min(75_000*0.35, 12_288)）
    assert plan.category_budgets["TOOL_SCHEMAS"] == 12_288


def test_plan_budget_tool_schema_never_eats_context():
    plan = plan_budget(context_window=8_000, max_output_tokens=2_000)
    assert plan.category_budgets["TOOL_SCHEMAS"] <= plan.usable * 0.35
    # §22 红线：工具 schema 预算 + 保留输出 ≤ 窗口
    assert plan.usable + plan.reserved_output <= 8_000


def test_plan_budget_unknown_window_conservative():
    plan = plan_budget(context_window=0, max_output_tokens=16_384)
    assert plan.context_window == 8_192  # 保守窗口，宁可误报
    # 保留输出被钳到窗口一半 → 可用空间不会因大 max_output 清零
    assert plan.reserved_output == 4_096
    assert plan.usable == 4_096


def test_plan_budget_deterministic():
    a = plan_budget(100_000, 16_384).as_dict()
    b = plan_budget(100_000, 16_384).as_dict()
    assert a == b


# ---------------------------------------------------------------------------
# 组件度量
# ---------------------------------------------------------------------------

def test_measure_components_classifies_and_detects_violations():
    plan = plan_budget(context_window=10_000, max_output_tokens=1_000)
    items = [
        BudgetItem(category=Category.SYSTEM_INSTRUCTIONS, name="sys", text="x" * 400),
        BudgetItem(category=Category.TOOL_SCHEMAS, name="tools", text="y" * 999_999),
    ]
    rep = measure_components(items, plan)
    assert rep.by_category["TOOL_SCHEMAS"] > rep.by_category["SYSTEM_INSTRUCTIONS"]
    assert any(v.startswith("over_limit:tools") for v in rep.violations)


def test_measure_history_hard_limit():
    plan = plan_budget(context_window=100_000, max_output_tokens=8_000)
    big_history = "轮次内容。" * 5_000  # 远超 6000
    rep = measure_assembled_context(
        [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "老消息"},
            {"role": "assistant", "content": big_history},
            {"role": "user", "content": "最新问题"},
        ],
        context_window=100_000,
        max_output_tokens=8_000,
    )
    assert rep.by_category.get("HISTORY", 0) > 0
    assert any(v.startswith("over_limit:history") for v in rep.violations)


def test_measure_over_budget_flagged_not_silent():
    plan = plan_budget(context_window=1_000, max_output_tokens=500)
    items = [BudgetItem(category=Category.HISTORY, name="history", text="字" * 50_000)]
    rep = measure_components(items, plan)
    assert rep.over_budget is True
    assert any(v.startswith("over_budget:total") for v in rep.violations)


def test_measure_near_budget_warns():
    plan = plan_budget(context_window=1_000, max_output_tokens=200)
    # usable=750；造 ~700 token 历史 → ≥90% 警告且 <100%（CJK 字≈1.5 tok）
    items = [BudgetItem(category=Category.HISTORY, name="history", text="字" * 460)]
    rep = measure_components(items, plan)
    assert not rep.over_budget
    assert rep.warnings


def test_measure_assembled_context_shape():
    rep = measure_assembled_context(
        [
            {"role": "system", "content": "系统指令"},
            {"role": "user", "content": "成都的小学在哪"},
        ],
        tools_payload='{"type":"function","function":{"name":"x"}}',
        context_window=32_000,
        max_output_tokens=4_096,
    )
    assert rep.by_category["SYSTEM_INSTRUCTIONS"] > 0
    assert rep.by_category["USER_PROMPT"] > 0
    assert rep.by_category["TOOL_SCHEMAS"] > 0
    assert rep.over_budget is False
    d = rep.as_dict()
    assert set(d) == {"total_est_tokens", "usable", "over_budget", "violations",
                      "warnings", "by_category"}


# ---------------------------------------------------------------------------
# 有界投影
# ---------------------------------------------------------------------------

def test_bound_text_marks_truncation():
    s = bound_text("a" * 500, 100)
    assert len(s) <= 100
    assert s.endswith("(truncated)")
    assert bound_text("short", 100) == "short"


def test_bound_lines_deterministic_and_discloses():
    lines = [f"line-{i}" for i in range(50)]
    out = bound_lines(lines, max_lines=5)
    assert out.count("\n") == 5
    assert "(45 more omitted)" in out
    assert out == bound_lines(lines, max_lines=5)


def test_project_layer_inventory_bounded():
    layers = {f"layer_{i}": {"type": "geojson", "visible": True} for i in range(40)}
    out = project_layer_inventory(layers, max_layers=10)
    assert out.count("- ") <= 11  # 10 行 + omitted 注
    assert "10 more omitted" in out  # 投影先截 max_layers*2 行再交 bound_lines


def test_project_ref_list_bounded():
    refs = {f"ref:{i}": f"别名{i}" for i in range(50)}
    out = project_ref_list(refs, max_refs=5)
    assert out.count("- ") <= 6


def test_content_fingerprint_stable_and_sensitive():
    a = content_fingerprint({"b": 1, "a": [1, 2]})
    b = content_fingerprint({"a": [1, 2], "b": 1})
    assert a == b
    c = content_fingerprint({"a": [1, 2, 3], "b": 1})
    assert a != c


# ---------------------------------------------------------------------------
# ProjectionCache
# ---------------------------------------------------------------------------

def test_projection_cache_hit_and_invalidation():
    cache = ProjectionCache(max_entries=8, default_ttl_s=60)
    calls = {"n": 0}

    def builder():
        calls["n"] += 1
        return f"proj-v{calls['n']}"

    v1, hit1 = cache.get_or_build("ns", "k1", "fp1", builder)
    v2, hit2 = cache.get_or_build("ns", "k1", "fp1", builder)
    assert hit1 is False and hit2 is True
    assert v1 == v2
    # 指纹变化 → miss 并重建
    v3, hit3 = cache.get_or_build("ns", "k1", "fp2", builder)
    assert hit3 is False and v3 == "proj-v2"
    # 命名空间失效
    cache.invalidate("ns")
    v4, hit4 = cache.get_or_build("ns", "k1", "fp2", builder)
    assert hit4 is False


def test_projection_cache_bounded():
    cache = ProjectionCache(max_entries=4, default_ttl_s=60)
    for i in range(10):
        cache.get_or_build("ns", f"k{i}", "fp", lambda i=i: f"v{i}")
    assert cache.stats()["entries"] <= 4


def test_projection_cache_no_cross_instance_leak():
    a = ProjectionCache()
    b = ProjectionCache()
    a.get_or_build("ns", "k", "fpA", lambda: "va")
    # 指纹表此前是类属性（共享）——回归锁：实例间必须隔离
    v, hit = b.get_or_build("ns", "k", "fpA", lambda: "vb")
    assert hit is False and v == "vb"
