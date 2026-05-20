"""制图工具的 legend_spec 契约测试。"""
import pytest

from app.tools.registry import ToolRegistry
from app.tools.cartography import register_cartography_tools


@pytest.fixture
def registry():
    r = ToolRegistry()
    register_cartography_tools(r)
    return r


@pytest.mark.asyncio
async def test_create_thematic_map_returns_legend_spec(registry):
    gj = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [0, 0]},
             "properties": {"pop": 10.0}},
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [1, 0]},
             "properties": {"pop": 100.0}},
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [2, 0]},
             "properties": {"pop": 500.0}},
        ],
    }
    out = await registry.dispatch("create_thematic_map", {
        "geojson": gj, "field": "pop", "method": "equal_interval", "k": 3,
    })
    assert "legend_spec" in out
    assert out["legend_spec"]["type"] == "graduated"
    assert out["legend_spec"]["field"] == "pop"
    assert "layer_meta" in out
    assert "title" in out["layer_meta"]
    assert "pop" in out["layer_meta"]["title"]  # title contains field name
