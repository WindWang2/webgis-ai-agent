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


from app.tools.spatial import register_spatial_tools


@pytest.fixture
def spatial_registry():
    r = ToolRegistry()
    register_spatial_tools(r)
    return r


def _points(n: int):
    return {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature",
             "geometry": {"type": "Point", "coordinates": [i * 0.001, i * 0.001]},
             "properties": {}}
            for i in range(n)
        ],
    }


@pytest.mark.asyncio
async def test_heatmap_native_no_legend_spec(spatial_registry):
    out = await spatial_registry.dispatch("heatmap_data", {
        "geojson": _points(20), "render_type": "native",
    })
    assert "legend_spec" not in out  # native rendering produces no discrete legend


@pytest.mark.asyncio
async def test_heatmap_grid_emits_continuous_legend_spec(spatial_registry):
    out = await spatial_registry.dispatch("heatmap_data", {
        "geojson": _points(20), "render_type": "grid",
    })
    assert out.get("legend_spec", {}).get("type") == "continuous"
    spec = out["legend_spec"]
    assert "min" in spec and "max" in spec
    assert len(spec["palette_colors"]) >= 3
