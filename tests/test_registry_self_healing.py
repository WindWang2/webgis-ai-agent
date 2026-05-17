import pytest
from app.tools.registry import ToolRegistry, tool
from app.lib.geoprocessing.interface import GeoAnalysisResult

@pytest.fixture
def registry():
    return ToolRegistry()

@pytest.mark.asyncio
async def test_dispatch_geo_analysis_result(registry):
    @tool(registry, name="spatial_analysis", description="Spatial analysis tool")
    def spatial_analysis(param: str):
        return GeoAnalysisResult(
            success=True,
            data={"value": 42},
            summary=f"Processed {param}"
        )

    result = await registry.dispatch("spatial_analysis", {"param": "test"})
    assert isinstance(result, dict)
    assert result["success"] is True
    assert result["summary"] == "Processed test"
    assert result["data"] == {"value": 42}

@pytest.mark.asyncio
async def test_dispatch_value_error_self_healing(registry):
    @tool(registry, name="failing_tool", description="Fails with ValueError")
    def failing_tool(field: str):
        if field == "pop":
            raise ValueError("Field 'pop' not found. Available fields are: ['name', 'id'].")
        return {"success": True}

    result = await registry.dispatch("failing_tool", {"field": "pop"})
    assert result["success"] is False
    assert "correction_hint" in result
    assert "Available fields are" in result["correction_hint"]
    assert "ValueError" in result["error_type"]

@pytest.mark.asyncio
async def test_dispatch_key_error_self_healing(registry):
    @tool(registry, name="key_error_tool", description="Fails with KeyError")
    def key_error_tool():
        data = {"a": 1}
        return data["b"]

    result = await registry.dispatch("key_error_tool", {})
    assert result["success"] is False
    assert "correction_hint" in result
    assert "KeyError" in result["error_type"]
    assert "Please check the tool parameters" in result["correction_hint"]
