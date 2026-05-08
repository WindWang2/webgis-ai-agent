"""Tests for spatial reasoning tool."""
import pytest
from unittest.mock import patch

from app.tools.spatial_reasoning import (
    SPATIAL_RULES,
    SpatialReasoningArgs,
    ReasoningStep,
    SpatialReasoningResult,
    _build_system_prompt,
    _build_user_prompt,
    register_spatial_reasoning,
    spatial_reasoning,
)
from app.tools.registry import ToolRegistry


def test_rule_library_loaded():
    """Verify SPATIAL_RULES contains expected categories."""
    expected_categories = {"traffic", "commercial", "urban_planning", "real_estate", "environment"}
    assert set(SPATIAL_RULES.keys()) == expected_categories
    for key in expected_categories:
        assert "category" in SPATIAL_RULES[key]
        assert "rules" in SPATIAL_RULES[key]
        assert len(SPATIAL_RULES[key]["rules"]) > 0


def test_spatial_reasoning_args_validation():
    """Verify args model validates depth levels."""
    valid = SpatialReasoningArgs(query="test", reasoning_depth="brief")
    assert valid.reasoning_depth == "brief"

    valid = SpatialReasoningArgs(query="test", reasoning_depth="standard")
    assert valid.reasoning_depth == "standard"

    valid = SpatialReasoningArgs(query="test", reasoning_depth="deep")
    assert valid.reasoning_depth == "deep"

    with pytest.raises(ValueError):
        SpatialReasoningArgs(query="test", reasoning_depth="invalid")


def test_build_system_prompt_contains_rules():
    """Verify system prompt includes rule content."""
    prompt = _build_system_prompt()
    assert "交通影响规则" in prompt
    assert "商业选址规则" in prompt
    assert "城市规划规则" in prompt
    assert "房地产规则" in prompt
    assert "环境灾害规则" in prompt
    assert "空间规则库" in prompt
    assert "输出要求" in prompt
    assert "置信度标准" in prompt
    # Verify specific rules are present
    assert "暴雨天气道路通行能力下降 20-40%" in prompt
    assert "小学服务半径 500m" in prompt
    assert "地铁站 500m 内房价溢价 +15-25%" in prompt


def test_build_user_prompt_structure():
    """Verify user prompt structures query and context."""
    query = "分析该区域的商业选址"
    context = {"population": 5000, "nearby_stores": 2}
    prompt = _build_user_prompt(query, context, "deep")
    assert query in prompt
    assert "deep" in prompt
    assert "population: 5000" in prompt
    assert "nearby_stores: 2" in prompt
    assert "输出格式示例" in prompt
    assert "spatial_reasoning" in prompt

    # Test brief depth
    prompt_brief = _build_user_prompt(query, {}, "brief")
    assert "简要推理" in prompt_brief


@pytest.mark.asyncio
async def test_spatial_reasoning_tool_output_format():
    """Mock _call_llm and verify output format matches spec."""
    registry = ToolRegistry()
    register_spatial_reasoning(registry)

    mock_result = {
        "type": "spatial_reasoning",
        "conclusion": "测试结论",
        "reasoning_chain": [
            {"step": 1, "fact": "规则事实", "source": "commercial"},
        ],
        "confidence": 0.82,
        "uncertainty": "测试不确定性",
        "recommendations": ["建议1"],
    }

    with patch(
        "app.tools.spatial_reasoning._call_llm",
        return_value=mock_result,
    ):
        result = await registry.dispatch(
            "spatial_reasoning",
            {
                "query": "测试查询",
                "context": {"key": "value"},
                "reasoning_depth": "standard",
            },
        )

    assert isinstance(result, dict)
    assert result["type"] == "spatial_reasoning"
    assert "conclusion" in result
    assert "reasoning_chain" in result
    assert isinstance(result["reasoning_chain"], list)
    assert len(result["reasoning_chain"]) > 0
    assert "confidence" in result
    assert 0.0 <= result["confidence"] <= 1.0
    assert "uncertainty" in result
    assert "recommendations" in result
    assert isinstance(result["recommendations"], list)
