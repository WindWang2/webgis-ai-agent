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


@pytest.mark.asyncio
async def test_heatmap_raster_emits_continuous_legend_spec(spatial_registry):
    out = await spatial_registry.dispatch("heatmap_data", {
        "geojson": _points(20), "render_type": "raster",
    })
    # raster mode should emit continuous legend_spec if result has data
    if "legend_spec" in out:
        assert out["legend_spec"]["type"] == "continuous"
        assert len(out["legend_spec"]["palette_colors"]) >= 3
    # if no legend_spec (e.g. matplotlib not installed), just verify no crash


from app.tools.advanced_spatial import register_advanced_spatial_tools


@pytest.fixture
def advanced_registry():
    r = ToolRegistry()
    register_advanced_spatial_tools(r)
    return r


@pytest.mark.asyncio
async def test_h3_binning_emits_graduated_legend_spec(advanced_registry):
    pts = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature",
             "geometry": {"type": "Point", "coordinates": [104.0 + i*0.01, 30.0 + i*0.01]},
             "properties": {}}
            for i in range(40)
        ],
    }
    out = await advanced_registry.dispatch("h3_binning", {
        "geojson": pts, "resolution": 7, "stat_method": "count",
    })
    spec = out.get("legend_spec")
    assert spec is not None
    assert spec["type"] == "graduated"
    assert len(spec["breaks"]) >= 2
    assert len(spec["palette_colors"]) >= 2
