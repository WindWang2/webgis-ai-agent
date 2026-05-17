import pytest
from app.tools.registry import ToolRegistry, tool

@pytest.fixture
def registry():
    return ToolRegistry()

@pytest.mark.asyncio
async def test_validation_error_self_healing(registry):
    """验证当缺少必需参数时，ToolRegistry 返回包含 correction_hint 的标准错误。"""
    @tool(registry, name="test_tool", description="A test tool", param_descriptions={"required_param": "A required parameter"})
    def test_tool(required_param: str):
        return {"success": True, "data": required_param}

    # 调用时缺少 required_param
    result = await registry.dispatch("test_tool", {})
    
    assert result["success"] is False
    assert result["code"] == "VALIDATION_ERROR"
    assert "correction_hint" in result
    assert "required_param" in result["message"]
    assert "Validation Error" in result["correction_hint"]
    print(f"\nCaptured correction_hint: {result['correction_hint']}")

@pytest.mark.asyncio
async def test_type_error_self_healing(registry):
    """验证当参数类型不匹配时，ToolRegistry 返回包含 correction_hint 的标准错误。"""
    @tool(registry, name="int_tool", description="Accepts an integer", param_descriptions={"value": "An integer value"})
    def int_tool(value: int):
        return {"success": True, "data": value}

    # 传入字符串而不是整数
    result = await registry.dispatch("int_tool", {"value": "not_an_int"})
    
    assert result["success"] is False
    assert result["code"] == "VALIDATION_ERROR"
    assert "correction_hint" in result
    assert "value" in result["message"]
    assert "Validation Error" in result["correction_hint"]
