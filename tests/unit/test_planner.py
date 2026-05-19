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
