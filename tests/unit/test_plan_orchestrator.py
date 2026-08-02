"""Unit tests for AgentPlanOrchestrator."""
import pytest
from app.services.chat.plan_orchestrator import (
    AgentPlanOrchestrator,
    Plan,
    PlanStep,
    parse_plan,
    should_plan,
)
from app.tools.registry import ToolRegistry


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
