
import pytest
from app.tools.registry import ToolRegistry
from app.tools.spatial import register_spatial_tools
from app.tools.advanced_spatial import register_advanced_spatial_tools
from app.tools.layer_manager import register_layer_management_tools

def test_new_tools_registration():
    registry = ToolRegistry()
    register_spatial_tools(registry)
    register_advanced_spatial_tools(registry)
    register_layer_management_tools(registry)
    
    tools = registry.list_tools()
    assert "zonal_stats" in tools
    assert "idw_interpolation" in tools
    assert "query_map_features" in tools
    assert "apply_layer_filter" in tools

def test_query_map_features_output():
    registry = ToolRegistry()
    register_spatial_tools(registry)
    
    # Mocking the call
    result = registry.dispatch_sync("query_map_features", {"location": [121.5, 31.2], "buffer_m": 20})
    assert result["command"] == "query_features"
    assert result["location"] == [121.5, 31.2]
    assert result["buffer_m"] == 20

def test_apply_layer_filter_output():
    registry = ToolRegistry()
    register_layer_management_tools(registry)
    
    # Mocking session_id
    result = registry.dispatch_sync("apply_layer_filter", {"layer_ref": "ref:abc", "expression": "pop > 100"}, session_id="test_session")
    assert result["command"] == "APPLY_LAYER_FILTER"
    assert result["params"]["layer_id"] == "ref:abc"
    assert result["params"]["filter"] == "pop > 100"

def test_zonal_stats_registration():
    registry = ToolRegistry()
    register_advanced_spatial_tools(registry)
    
    tool_spec = next(t for t in registry.get_schemas() if t["function"]["name"] == "zonal_stats")
    assert "geojson" in tool_spec["function"]["parameters"]["properties"]
    assert "raster_path" in tool_spec["function"]["parameters"]["properties"]
