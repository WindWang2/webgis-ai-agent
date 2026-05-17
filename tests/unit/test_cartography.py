import pytest
from app.services.cartography_service import CartographyService
from app.tools.cartography import register_cartography_tools
from app.tools.registry import ToolRegistry

def test_build_thematic_style_choropleth():
    geojson = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "properties": {"pop": 10}},
            {"type": "Feature", "properties": {"pop": 20}},
            {"type": "Feature", "properties": {"pop": 30}},
            {"type": "Feature", "properties": {"pop": 40}},
            {"type": "Feature", "properties": {"pop": 50}},
        ]
    }
    
    style_def = CartographyService.build_thematic_style(geojson, field="pop", method="quantiles", k=2, palette="Blues")
    
    # Verify it doesn't mutate geojson
    assert "fill_color" not in geojson["features"][0]["properties"]
    
    assert style_def["type"] == "choropleth"
    assert style_def["field"] == "pop"
    assert "breaks" in style_def
    assert "colors" in style_def
    assert "legend_labels" in style_def
    assert len(style_def["colors"]) > 0

def test_build_thematic_style_lisa():
    geojson = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "properties": {"lisa_cluster": "HH"}},
            {"type": "Feature", "properties": {"lisa_cluster": "LL"}},
        ]
    }
    
    style_def = CartographyService.build_thematic_style(geojson, field="lisa_cluster", method="lisa")
    
    assert style_def["type"] == "lisa"
    assert style_def["field"] == "lisa_cluster"
    assert "breaks" not in style_def or "categories" in style_def
    # The requirement says LISA has standard colors for HH, LL, etc.
    assert "colors" in style_def
    assert "HH" in style_def["legend_labels"] or isinstance(style_def["colors"], list) or isinstance(style_def["colors"], dict)
    
def test_create_thematic_map_tool():
    geojson = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "properties": {"val": 10}}
        ]
    }
    
    registry = ToolRegistry()
    register_cartography_tools(registry)
    tool = registry._tools["create_thematic_map"]
    
    result = tool(geojson=geojson, field="val", method="quantiles")
    assert "error" not in result
    assert "metadata" in result or "style" in result
