"""planner 单元测试 — 门控、计划解析、make_plan。"""
import pytest

from app.services.chat.planner import should_plan


@pytest.mark.parametrize("message,has_plan,expected", [
    ("分析成都市三甲医院的空间分布并做热点检测", False, True),   # 长复杂请求 → 规划
    ("换个颜色", True, False),                                   # 短追问 + 有计划 → 跳过
    ("再放大点", True, False),                                   # 短追问 + 有计划 → 跳过
    ("画个热力图", False, True),                                  # 短但无计划 → 规划
    ("成都和重庆两个城市的人口对比", False, True),                  # 长请求 → 规划
    ("隐藏这个图层", True, False),                                # 短追问词 + 有计划 → 跳过
])
def test_should_plan_gate(message, has_plan, expected):
    assert should_plan(message, [], has_plan) is expected


def test_short_followup_word_without_plan_still_plans():
    """无活跃计划时，即使是追问词也要规划（没有上文可承接）。"""
    assert should_plan("换个颜色", [], has_active_plan=False) is True


from app.services.chat.planner import Plan, PlanStep, parse_plan


def test_parse_plan_valid():
    raw = '''{
      "intent": "分析成都医院分布",
      "domains": ["chinese", "statistics"],
      "steps": [
        {"n": 1, "goal": "锁定成都边界", "tool_family": "chinese"},
        {"n": 2, "goal": "H3 聚合", "tool_family": "statistics"}
      ]
    }'''
    plan = parse_plan(raw)
    assert isinstance(plan, Plan)
    assert plan.intent == "分析成都医院分布"
    assert plan.domains == ["chinese", "statistics"]
    assert len(plan.steps) == 2
    assert plan.steps[0] == PlanStep(n=1, goal="锁定成都边界", tool_family="chinese")
    assert plan.steps[0].done is False


def test_parse_plan_strips_code_fence():
    raw = '```json\n{"intent":"x","domains":["core"],"steps":[]}\n```'
    plan = parse_plan(raw)
    assert plan is not None
    assert plan.intent == "x"


def test_parse_plan_filters_invalid_domains():
    raw = '{"intent":"x","domains":["chinese","nonsense"],"steps":[]}'
    plan = parse_plan(raw)
    assert plan is not None
    assert plan.domains == ["chinese"]  # 越界 domain 被丢弃


def test_parse_plan_malformed_json_returns_none():
    assert parse_plan("not json at all") is None
    assert parse_plan("") is None


def test_parse_plan_missing_fields_returns_none():
    assert parse_plan('{"intent":"x"}') is None          # 缺 domains/steps
    assert parse_plan('{"domains":[],"steps":[]}') is None  # 缺 intent
