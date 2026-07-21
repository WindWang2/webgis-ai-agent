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


# ─── _call_llm real integration tests ────────────────────────────


@pytest.mark.asyncio
async def test_call_llm_returns_valid_result():
    """When LLM returns valid JSON, _call_llm should return it validated."""
    from app.tools.spatial_reasoning import _call_llm
    from app.services.chat.llm_client import LLMConfig

    mock_response = {
        "choices": [{
            "message": {
                "content": '{"type": "spatial_reasoning", "conclusion": "适合选址", '
                           '"reasoning_chain": [{"step": 1, "fact": "规则", "source": "commercial"}], '
                           '"confidence": 0.8, "uncertainty": "有限", "recommendations": ["建议"]}'
            }
        }]
    }

    with patch("app.tools.spatial_reasoning.call_llm", return_value=mock_response):
        result = await _call_llm("system", "user")

    assert result["type"] == "spatial_reasoning"
    assert result["confidence"] == 0.8
    assert len(result["reasoning_chain"]) == 1


@pytest.mark.asyncio
async def test_call_llm_handles_empty_content():
    """When LLM returns empty content, _call_llm should return error result."""
    from app.tools.spatial_reasoning import _call_llm

    mock_response = {"choices": [{"message": {"content": ""}}]}

    with patch("app.tools.spatial_reasoning.call_llm", return_value=mock_response):
        result = await _call_llm("system", "user")

    assert result["confidence"] == 0.0
    assert "LLM 返回空响应" in result["uncertainty"]


@pytest.mark.asyncio
async def test_call_llm_handles_invalid_json():
    """When LLM returns non-JSON text, _call_llm should return error result."""
    from app.tools.spatial_reasoning import _call_llm

    mock_response = {"choices": [{"message": {"content": "This is not JSON at all."}}]}

    with patch("app.tools.spatial_reasoning.call_llm", return_value=mock_response):
        result = await _call_llm("system", "user")

    assert result["confidence"] == 0.0
    assert "无法解析" in result["uncertainty"]


@pytest.mark.asyncio
async def test_call_llm_handles_truncated_json():
    """When LLM returns truncated JSON, _repair_json should fix it."""
    from app.tools.spatial_reasoning import _call_llm

    truncated = '{"type": "spatial_reasoning", "conclusion": "适合", "reasoning_chain": [{"step": 1, "fact": "x", "source": "c"}], "confidence": 0.8, "uncertainty": "有限", "recommendations": ["建议"]'

    mock_response = {"choices": [{"message": {"content": truncated}}]}

    with patch("app.tools.spatial_reasoning.call_llm", return_value=mock_response):
        result = await _call_llm("system", "user")

    assert result["type"] == "spatial_reasoning"
    assert result["confidence"] == 0.8


@pytest.mark.asyncio
async def test_call_llm_handles_markdown_fences():
    """When LLM wraps JSON in markdown code fences, they should be stripped."""
    from app.tools.spatial_reasoning import _call_llm

    fenced = """```json
{"type": "spatial_reasoning", "conclusion": "适合", "reasoning_chain": [{"step": 1, "fact": "x", "source": "c"}], "confidence": 0.8, "uncertainty": "有限", "recommendations": ["建议"]}
```"""

    mock_response = {"choices": [{"message": {"content": fenced}}]}

    with patch("app.tools.spatial_reasoning.call_llm", return_value=mock_response):
        result = await _call_llm("system", "user")

    assert result["type"] == "spatial_reasoning"
    assert result["confidence"] == 0.8


@pytest.mark.asyncio
async def test_call_llm_handles_validation_error():
    """When LLM returns structurally invalid JSON, _call_llm should return error."""
    from app.tools.spatial_reasoning import _call_llm

    # Missing required fields
    invalid = '{"type": "spatial_reasoning"}'

    mock_response = {"choices": [{"message": {"content": invalid}}]}

    with patch("app.tools.spatial_reasoning.call_llm", return_value=mock_response):
        result = await _call_llm("system", "user")

    assert result["confidence"] == 0.0


@pytest.mark.asyncio
async def test_call_llm_handles_http_error():
    """When LLM API raises an exception, _call_llm should return error result."""
    from app.tools.spatial_reasoning import _call_llm

    with patch("app.tools.spatial_reasoning.call_llm", side_effect=RuntimeError("API down")):
        result = await _call_llm("system", "user")

    assert result["confidence"] == 0.0
    assert "失败" in result["uncertainty"] or "调用失败" in result["uncertainty"]


# ─── _parse_llm_json helper tests ────────────────────────────────


def test_parse_llm_json_valid():
    from app.tools.spatial_reasoning import _parse_llm_json
    assert _parse_llm_json('{"a": 1}') == {"a": 1}


def test_parse_llm_json_with_markdown_fences():
    from app.tools.spatial_reasoning import _parse_llm_json
    fenced = "```json\n{\"a\": 1}\n```"
    assert _parse_llm_json(fenced) == {"a": 1}


def test_parse_llm_json_truncated():
    from app.tools.spatial_reasoning import _parse_llm_json
    # _repair_json only adds closing brackets/braces, not incomplete values
    truncated = '{"a": 1'
    result = _parse_llm_json(truncated)
    # repair adds the missing closing brace
    assert result is not None
    assert result.get("a") == 1


def test_parse_llm_json_invalid():
    from app.tools.spatial_reasoning import _parse_llm_json
    assert _parse_llm_json("not json at all") is None


# ─── _error_result helper tests ──────────────────────────────────


def test_error_result_structure():
    from app.tools.spatial_reasoning import _error_result
    result = _error_result("test error")
    assert result["type"] == "spatial_reasoning"
    assert result["confidence"] == 0.0
    assert "test error" in result["uncertainty"]
    assert isinstance(result["recommendations"], list)
    assert len(result["recommendations"]) > 0
