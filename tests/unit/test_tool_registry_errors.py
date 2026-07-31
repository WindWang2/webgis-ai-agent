"""Unit tests for ToolRegistry encapsulated error handling & self-healing contract (Candidate #5)."""
import pytest
from app.tools.registry import ToolRegistry, tool
from app.services.tool_dispatch_service import ToolDispatchService, ToolDispatchResult


@pytest.mark.asyncio
async def test_registry_dispatch_captures_reference_resolution_error():
    registry = ToolRegistry()

    @tool(registry, name="test_tool", description="Test tool")
    def test_tool(data: dict):
        return {"success": True, "data": data}

    # Dispatch with an unresolvable reference in arguments
    result = await registry.dispatch("test_tool", {"data": "ref:non_existent_ref_xyz"}, session_id="test_sess_123")

    # Verify that dispatch returned a standardized error dict instead of raising an uncaught ValueError
    assert isinstance(result, dict)
    assert result["success"] is False
    assert result["code"] == "VALIDATION_ERROR"
    assert result["error_type"] == "ValueError"
    assert "无法找到引用数据" in result["message"]
    assert "Reference Resolution Error" in result["correction_hint"]


@pytest.mark.asyncio
async def test_tool_dispatch_service_handles_reference_error_single_path():
    registry = ToolRegistry()

    @tool(registry, name="buffer_tool", description="Buffer tool")
    def buffer_tool(geojson: dict):
        return {"success": True, "geojson": geojson}

    dispatch_service = ToolDispatchService(registry=registry)
    tc = {"id": "tc_1", "function": {"name": "buffer_tool", "arguments": '{"geojson": "ref:missing_layer"}'}}
    executed_tools = set()

    res = await dispatch_service.dispatch(tc, session_id="sess_456", executed_tools=executed_tools)

    assert isinstance(res, ToolDispatchResult)
    assert res.status == "error"
    assert res.geojson_ref is None
    assert "Reference Resolution Error" in res.llm_payload or "无法找到引用" in res.llm_payload
