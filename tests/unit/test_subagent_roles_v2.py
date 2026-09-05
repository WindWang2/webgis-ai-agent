"""ADR-0101 Wave 7: 子代理角色库 / 层级预算 / 隔离强化测试。"""
import asyncio

import pytest

from app.services.subagent import select_tools_for_subagent
from app.services.subagent_roles import (
    SUBAGENT_ROLES,
    BudgetExceeded,
    SubagentBudget,
    SubagentRole,
    get_subagent_role,
    wrap_dispatch_with_budget,
)
from app.tools.registry import ToolRegistry


@pytest.fixture()
def reg():
    registry = ToolRegistry()
    registry.register(
        name="search_poi", description="查询 POI",
        func=lambda q: {"success": True}, tier=1, side_effect="cacheable_read",
    )
    registry.register(
        name="cheap_analyze", description="轻量分析",
        func=lambda d: {"success": True}, tier=2, domains=["statistics"],
        side_effect="deterministic_compute",
    )
    registry.register(
        name="webgis_component_update", description="改地图组件",
        func=lambda i: {"success": True}, tier=2, domains=["mapspec"],
        side_effect="state_mutation",
    )
    registry.register(
        name="external_webhook", description="外部副作用",
        func=lambda u: {"success": True}, tier=2, domains=["dataset"],
        side_effect="external_side_effect",
    )
    registry.register(
        name="spawn_subagent", description="递归子代理",
        func=lambda t: {"success": True}, tier=1,
    )
    return registry


# ---------------------------------------------------------------------------
# 角色库
# ---------------------------------------------------------------------------

def test_role_library_complete_and_bounded():
    assert {"data_researcher", "gis_inspector", "scientific_reviewer",
            "cartography_reviewer", "tool_result_verifier",
            "cheap_summarizer"} <= set(SUBAGENT_ROLES)
    for name, role in SUBAGENT_ROLES.items():
        assert role.name == name
        assert role.max_rounds > 0
        assert role.max_tool_calls >= 0
        assert role.max_wall_time_s > 0


def test_get_unknown_role_rejected():
    with pytest.raises(ValueError, match="未知子代理角色"):
        get_subagent_role("ninja_hacker")


def test_summarizer_role_zero_tools():
    assert SUBAGENT_ROLES["cheap_summarizer"].max_tool_calls == 0
    assert SUBAGENT_ROLES["cheap_summarizer"].allow_mutation is False


# ---------------------------------------------------------------------------
# 突变工具过滤（描述符副作用类 → 权限面）
# ---------------------------------------------------------------------------

def test_no_mutation_role_filters_mutation_tools(reg):
    """allow-list 语义：只读类显式声明才保留（review R1 MAJOR）。"""
    subset = select_tools_for_subagent(
        reg, domains=["mapspec", "dataset", "statistics"], allow_mutation=False
    )
    names = {s["function"]["name"] for s in subset}
    assert "webgis_component_update" not in names   # state_mutation
    assert "external_webhook" not in names          # external_side_effect
    assert "search_poi" in names                    # cacheable_read（显式声明）
    assert "cheap_analyze" in names                 # deterministic_compute


def test_mutation_allowed_by_default(reg):
    subset = select_tools_for_subagent(reg, domains=["mapspec", "dataset", "statistics"])
    names = {s["function"]["name"] for s in subset}
    assert "webgis_component_update" in names


def test_spawn_subagent_always_blacklisted(reg):
    for kwargs in ({}, {"allow_mutation": False}, {"extra_tools": ["spawn_subagent"]}):
        subset = select_tools_for_subagent(reg, **kwargs)
        names = {s["function"]["name"] for s in subset}
        assert "spawn_subagent" not in names


def test_tier3_never_via_extra_tools(reg):
    registry = reg
    registry.register(
        name="danger_tool", description="破坏性",
        func=lambda x: {"success": True}, tier=3, side_effect="destructive",
    )
    subset = select_tools_for_subagent(registry, extra_tools=["danger_tool"])
    assert {s["function"]["name"] for s in subset}.isdisjoint({"danger_tool"})


def test_unclassified_side_effect_excluded_for_readonly_role(reg):
    """review R1 MAJOR（fail-closed）：UNCLASSIFIED = 未知，未知不入只读面。
    全库存量工具默认 UNCLASSIFIED —— 描述符富化完成前 no-mutation 角色的
    可见工具为空（诚实的失败方向，见 tool-descriptor.md）。"""
    reg.register(
        name="legacy_tool", description="老工具",
        func=lambda a: {"success": True}, tier=1,
    )
    subset = select_tools_for_subagent(reg, allow_mutation=False)
    assert "legacy_tool" not in {s["function"]["name"] for s in subset}


# ---------------------------------------------------------------------------
# 层级预算
# ---------------------------------------------------------------------------

def test_budget_tool_call_cap():
    b = SubagentBudget(max_tool_calls=2, max_heavy_tool_calls=1, max_wall_time_s=60)
    b.check_tool(is_heavy=False)
    b.check_tool(is_heavy=False)
    with pytest.raises(BudgetExceeded):
        b.check_tool(is_heavy=False)


def test_budget_heavy_cap():
    b = SubagentBudget(max_tool_calls=10, max_heavy_tool_calls=1, max_wall_time_s=60)
    b.check_tool(is_heavy=True)
    with pytest.raises(BudgetExceeded):
        b.check_tool(is_heavy=True)
    # 非重工具不受 heavy 上限影响
    b.check_tool(is_heavy=False)


def test_budget_wall_time():
    import time as _t

    b = SubagentBudget(max_tool_calls=10, max_heavy_tool_calls=1, max_wall_time_s=0.05)
    _t.sleep(0.08)
    with pytest.raises(BudgetExceeded):
        b.check_wall_time()


@pytest.mark.asyncio
async def test_dispatch_budget_wrapper_counts_and_raises(reg):
    async def fake_dispatch(tc, session_id, executed_tools=None):
        return {"success": True}

    budget = SubagentBudget(max_tool_calls=2, max_heavy_tool_calls=0, max_wall_time_s=60)
    wrapped = wrap_dispatch_with_budget(fake_dispatch, budget, reg)
    tc = {"function": {"name": "search_poi", "arguments": "{}"}}
    await wrapped(tc, "s1")
    await wrapped(tc, "s1")
    with pytest.raises(BudgetExceeded):
        await wrapped(tc, "s1")


@pytest.mark.asyncio
async def test_dispatch_budget_marks_heavy_via_descriptor(reg):
    async def fake_dispatch(tc, session_id, executed_tools=None):
        return {"success": True}

    from app.services.subagent_roles import SubagentBudget as B

    budget = B(max_tool_calls=10, max_heavy_tool_calls=0, max_wall_time_s=60)
    wrapped = wrap_dispatch_with_budget(fake_dispatch, budget, reg)
    tc_heavy = {"function": {"name": "external_webhook", "arguments": "{}"}}
    with pytest.raises(BudgetExceeded):
        await wrapped(tc_heavy, "s1")  # external_side_effect → 视为重工具
    assert "seen" not in dir()


# ---------------------------------------------------------------------------
# 隔离不变式
# ---------------------------------------------------------------------------

def test_role_constraints_are_intersection_not_union():
    """角色域收紧：调用方给角色外的域 → 交集（不允许经参数越权放宽）。"""
    role = SUBAGENT_ROLES["data_researcher"]  # 域 = chinese/osm/dataset
    caller_domains = ["raster", "chinese"]
    merged = sorted(set(caller_domains) & set(role.allowed_domains))
    assert merged == ["chinese"]
    # 调用方不给域 → 角色域生效
    merged2 = sorted(set(role.allowed_domains))
    assert "raster" not in merged2


@pytest.mark.asyncio
async def test_subengine_engine_not_polluted_by_roles_module():
    """角色模块不引入第二执行体 —— 只导入策略对象。"""
    import app.services.subagent_roles as sr
    assert not hasattr(sr, "ChatEngine")
    assert not hasattr(sr, "SubagentDispatcher")


@pytest.mark.asyncio
async def test_wall_clock_budget_enforced():
    """review R1 MAJOR 回归锁：纯 LLM 子代理（零工具）也受墙钟约束。"""
    from app.services.subagent import SubagentDispatcher

    class _Reg:
        def all_metadata(self):
            return {}

        def get_schemas_subset(self, names):
            return []

    class _SlowEngine:
        def __init__(self):
            self.dispatch_service = None

        async def chat(self, message, session_id):
            while True:
                await asyncio.sleep(0.05)

    dispatcher = SubagentDispatcher(_Reg(), "sess-wall")
    dispatcher._build_sub_engine = lambda subset, rounds: _SlowEngine()

    result = await dispatcher.run(
        task="慢任务", max_rounds=3,
        role=SubagentRole(
            name="slow_probe", title="探针", model_role="subagent_worker",
            max_rounds=3, max_wall_time_s=0.2, max_tool_calls=0,
            max_heavy_tool_calls=0,
        ),
    )
    assert result.success is False
    assert result.error == "budget_exceeded:wall_time"
