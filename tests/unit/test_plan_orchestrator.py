"""Unit tests for AgentPlanOrchestrator."""
import pytest

from app.services.chat.plan_orchestrator import (
    MAX_PLAN_STEPS,
    AgentPlanOrchestrator,
    Plan,
    PlanStep,
    parse_plan,
    should_plan,
)
from app.services.planning import FollowUpKind, PlanStatus
from app.services.planning.store import plan_store


def test_parse_plan_valid_json():
    raw_json = """
    {
      "intent": "绘制海淀区公园分布与热力图",
      "domains": ["chinese", "statistics"],
      "steps": [
        {"n": 1, "goal": "查找海淀区 POI", "tool_family": "chinese"},
        {"n": 2, "goal": "生成热力图", "tool_family": "statistics"}
      ]
    }
    """
    plan = parse_plan(raw_json)
    assert plan is not None
    assert plan.intent == "绘制海淀区公园分布与热力图"
    assert plan.domains == ["chinese", "statistics"]
    assert len(plan.steps) == 2
    assert plan.steps[0].tool_family == "chinese"
    assert plan.steps[1].tool_family == "statistics"


def test_parse_plan_invalid_json():
    assert parse_plan("invalid json") is None
    assert parse_plan("{}") is None


def test_should_plan_heuristics():
    # Short followup with active plan -> skip
    assert should_plan("放大一下", [], has_active_plan=True) is False
    # Short followup without active plan -> plan
    assert should_plan("放大一下", [], has_active_plan=False) is True
    # Detailed query -> plan
    assert should_plan("分析北京市朝阳区近三年的 NDVI 植被变化趋势", [], has_active_plan=True) is True


def test_orchestrator_step_advancement():
    orchestrator = AgentPlanOrchestrator()
    session_id = "sess_plan_1"

    plan = Plan(
        intent="热点分析",
        domains=["statistics"],
        steps=[
            PlanStep(n=1, goal="计算热点", tool_family="statistics"),
            PlanStep(n=2, goal="生成地图", tool_family="core"),
        ],
    )
    orchestrator.set_plan(session_id, plan)

    # Mock registry
    class MockRegistry:
        def metadata(self, name):
            if name == "hotspot_analysis":
                return {"domains": ["statistics"]}
            return {"domains": ["core"]}

    reg = MockRegistry()

    # Advance step 1
    step_n = orchestrator.advance_step(session_id, "hotspot_analysis", reg)
    assert step_n == 1
    assert plan.steps[0].done is True

    # Advance step 2
    step_n2 = orchestrator.advance_step(session_id, "webgis_layer_upsert", reg)
    assert step_n2 == 2
    assert plan.steps[1].done is True

    # Clean up
    orchestrator.clear_plan(session_id)
    assert orchestrator.get_plan(session_id) is None


# ─── design-v3：parse_plan 防御（R4） ────────────────────────────


def test_parse_plan_defensive_n_and_tool_family():
    """R4: n=null / "1.0" / "abc" 稳健转 int；非法 tool_family → None（步骤保留）。"""
    raw = (
        '{"intent":"x","domains":["core"],"steps":['
        '{"n":null,"goal":"a","tool_family":"raster"},'
        '{"n":"1.0","goal":"b","tool_family":"rasterz"},'
        '{"n":"abc","goal":"c","tool_family":"chinese"},'
        '{"goal":"d"}]}'
    )
    plan = parse_plan(raw)
    assert plan is not None
    assert [s.n for s in plan.steps] == [1, 1, 3, 4]
    assert plan.steps[0].tool_family == "raster"   # raster ∈ VALID_DOMAINS
    assert plan.steps[1].tool_family is None       # rasterz 非法 → None
    assert plan.steps[2].tool_family == "chinese"
    assert plan.steps[3].tool_family == "core"     # 缺失默认 core


def test_parse_plan_caps_steps_at_max():
    """R4: 步骤数超过 MAX_PLAN_STEPS 时截断，绝不崩溃。"""
    steps = ",".join(
        '{"n":%d,"goal":"g%d","tool_family":"core"}' % (i, i) for i in range(1, 13)
    )
    raw = '{"intent":"x","domains":["core"],"steps":[%s]}' % steps
    plan = parse_plan(raw)
    assert plan is not None
    assert len(plan.steps) == MAX_PLAN_STEPS


def test_parse_plan_accepts_registry_declared_domains():
    """registry 声明的 domain（不在 DOMAIN_KEYWORDS 里）也合法。"""
    from app.tools.registry import ToolRegistry

    reg = ToolRegistry()
    reg.register("custom_tool", "custom", func=lambda **_: {}, tier=2, domains=["custom_domain"])
    raw = '{"intent":"x","domains":["custom_domain"],"steps":[{"n":1,"goal":"a","tool_family":"custom_domain"}]}'
    plan = parse_plan(raw, registry=reg)
    assert plan is not None
    assert plan.domains == ["custom_domain"]
    assert plan.steps[0].tool_family == "custom_domain"


# ─── design-v3：should_plan + followup_kind ─────────────────────


def test_should_plan_with_followup_kind():
    # new_goal → 必须规划（即使短消息含追问词——"换成查路线" 场景 8）
    assert should_plan("换成查路线", [], has_active_plan=True, followup_kind=FollowUpKind.new_goal) is True
    # style_change / continuation + 活跃计划 → 跳过
    assert should_plan("换成蓝色", [], has_active_plan=True, followup_kind=FollowUpKind.style_change) is False
    assert should_plan("继续", [], has_active_plan=True, followup_kind=FollowUpKind.continuation) is False
    # 无活跃计划时 style_change 回落到旧启发式 → 规划
    assert should_plan("换个颜色", [], has_active_plan=False, followup_kind=FollowUpKind.style_change) is True
    # unclear + 活跃计划 → 旧长度/关键词启发式
    assert should_plan("放大一下", [], has_active_plan=True, followup_kind=FollowUpKind.unclear) is False
    # followup_kind=None → 与旧版完全一致
    assert should_plan("放大一下", [], has_active_plan=True) is False
    assert should_plan("放大一下", [], has_active_plan=False) is True


# ─── design-v3：advance_step 修复（R1/R6/R7） ───────────────────


def test_core_wildcard_removed_style_tool_does_not_tick_core_step():
    """R1: 样式工具（domains=[]）不再通过 "core" 通配打勾 core 步骤。"""
    orchestrator = AgentPlanOrchestrator()
    session_id = "sess_core_wildcard"

    plan = Plan(
        intent="制图",
        domains=["core"],
        steps=[PlanStep(n=1, goal="加图层", tool_family="core")],
    )
    orchestrator.set_plan(session_id, plan)

    class StyleRegistry:
        def metadata(self, name):
            return {"domains": []}  # 样式/视图类工具不声明任何 domain

    assert orchestrator.advance_step(session_id, "webgis_layer_upsert", StyleRegistry()) is None
    assert plan.steps[0].done is False
    orchestrator.clear_plan(session_id)


def test_advance_step_normalizes_legacy_tool_name():
    """R7: 遗留名（set_layer_style）规范化后匹配 canonical 工具 domain。"""
    orchestrator = AgentPlanOrchestrator()
    session_id = "sess_legacy_name"

    plan = Plan(
        intent="制图",
        domains=["core"],
        steps=[PlanStep(n=1, goal="加图层", tool_family="core")],
    )
    orchestrator.set_plan(session_id, plan)

    class LegacyRegistry:
        def metadata(self, name):
            # canonical 工具 webgis_layer_upsert 无 domain，但这里给它 core 声明
            return {"domains": ["core"]}

    assert orchestrator.advance_step(session_id, "set_layer_style", LegacyRegistry()) == 1
    assert plan.steps[0].done is True
    orchestrator.clear_plan(session_id)


# ─── design-v3：store 恢复（R5/R10 重启续接） ────────────────────


@pytest.mark.asyncio
async def test_restore_plan_from_store_after_process_restart(monkeypatch):
    """R5/R10: 清空进程 LRU（模拟重启）后，从 store 恢复计划与 done 进度。"""
    from app.services.chat import planner as planner_mod
    from app.services.chat.llm_client import LLMConfig

    # 全量套件里别的测试可能注入过全局 ToolRegistry；这里显式禁用能力校验，
    # 保证测试与注册表状态无关（tool_family 保持 parse 结果）。
    monkeypatch.setattr("app.services.chat.plan_orchestrator._get_registry", lambda: None)

    orch = AgentPlanOrchestrator()
    sid = "sess-restart-1"
    cfg = LLMConfig(base_url="http://x", model="m", api_key="k")

    async def fake_call_llm(_cfg, _messages, _tools=None):
        return {"choices": [{"message": {"content":
            '{"intent":"恢复测试","domains":["statistics","report"],"steps":['
            '{"n":1,"goal":"热点","tool_family":"statistics"},'
            '{"n":2,"goal":"报告","tool_family":"report"}]}'}}]}

    monkeypatch.setattr(planner_mod, "call_llm", fake_call_llm)
    plan = await orch.make_plan(cfg, sid, "分析热点", "[环境感知]")
    assert plan is not None

    class Reg:
        def metadata(self, name):
            return {"domains": ["statistics"]}

    assert orch.advance_step(sid, "hotspot_analysis", Reg()) == 1
    await orch.flush(sid)

    # 模拟重启：全新 orchestrator 实例（空 LRU），从 store 恢复
    fresh = AgentPlanOrchestrator()
    restored = await fresh.restore_plan(sid)
    assert restored is not None
    assert restored.intent == "恢复测试"
    assert restored.steps[0].done is True
    assert restored.steps[1].done is False
    await plan_store.clear(sid)


@pytest.mark.asyncio
async def test_restore_terminal_plan_returns_none(monkeypatch):
    """R5: 恢复出的终态计划（全部步骤完成）视为无活跃计划。"""
    from app.services.chat import planner as planner_mod
    from app.services.chat.llm_client import LLMConfig

    # 与注册表状态解耦（全量套件可能注入了全局 registry）
    monkeypatch.setattr("app.services.chat.plan_orchestrator._get_registry", lambda: None)

    orch = AgentPlanOrchestrator()
    sid = "sess-restart-2"
    cfg = LLMConfig(base_url="http://x", model="m", api_key="k")

    async def fake_call_llm(_cfg, _messages, _tools=None):
        return {"choices": [{"message": {"content":
            '{"intent":"x","domains":["statistics"],"steps":[{"n":1,"goal":"热点","tool_family":"statistics"}]}'}}]}

    monkeypatch.setattr(planner_mod, "call_llm", fake_call_llm)
    plan = await orch.make_plan(cfg, sid, "热点", "[环境感知]")
    assert plan is not None

    class Reg:
        def metadata(self, name):
            return {"domains": ["statistics"]}

    assert orch.advance_step(sid, "hotspot_analysis", Reg()) == 1
    await orch.flush(sid)  # 全部步骤 done → canonical status = completed

    fresh = AgentPlanOrchestrator()
    assert await fresh.restore_plan(sid) is None
    await plan_store.clear(sid)


# ─── design-v3：换目标 → 旧计划 superseded ─────────────────────


@pytest.mark.asyncio
async def test_make_plan_supersedes_old_active_plan(monkeypatch):
    """design-v3: 新计划替换旧非终态计划时，旧 canonical 标记 superseded。"""
    from app.services.chat import planner as planner_mod
    from app.services.chat.llm_client import LLMConfig

    orch = AgentPlanOrchestrator()
    sid = "sess-super-1"
    cfg = LLMConfig(base_url="http://x", model="m", api_key="k")
    responses = iter([
        '{"intent":"旧计划","domains":["raster"],"steps":[{"n":1,"goal":"NDVI","tool_family":"raster"}]}',
        '{"intent":"新计划","domains":["network"],"steps":[{"n":1,"goal":"路线","tool_family":"network"}]}',
    ])

    async def fake_call_llm(_cfg, _messages, _tools=None):
        return {"choices": [{"message": {"content": next(responses)}}]}

    monkeypatch.setattr(planner_mod, "call_llm", fake_call_llm)

    old = await orch.make_plan(cfg, sid, "算一下 NDVI", "[环境感知]")
    assert old is not None
    old_canon = await plan_store.load_current(sid)
    old_id = old_canon.plan_id

    new = await orch.make_plan(cfg, sid, "换成查路线", "[环境感知]")
    assert new is not None and new.intent == "新计划"

    hist = await plan_store.get_by_id(sid, old_id)
    assert hist is not None
    assert hist.status == PlanStatus.superseded
    assert (await plan_store.load_current(sid)).plan_id != old_id
    await plan_store.clear(sid)


def test_capability_validation_nulls_family_with_no_registered_tool():
    """design-v3 §capability：tool_family 无任何已注册工具 → 置 None（步骤保留，
    失去打勾能力，避免死步骤）。"""
    from app.tools.registry import ToolRegistry

    reg = ToolRegistry()
    reg.register("hotspot_analysis", "hotspot", func=lambda **_: {},
                 tier=2, domains=["statistics"])
    orch = AgentPlanOrchestrator()
    sid = "sess-cap-1"
    plan = Plan(
        intent="x",
        domains=["statistics", "report"],
        steps=[
            PlanStep(n=1, goal="热点", tool_family="statistics"),
            PlanStep(n=2, goal="报告", tool_family="report"),  # report 无已注册工具
        ],
    )
    orch.set_plan(sid, plan)
    orch._apply_capability_validation(plan, reg)
    assert plan.steps[0].tool_family == "statistics"  # 有工具支撑 → 保留
    assert plan.steps[1].tool_family is None          # 无工具支撑 → None
    orch.clear_plan(sid)
