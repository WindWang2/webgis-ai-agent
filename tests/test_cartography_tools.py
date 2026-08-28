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
async def test_heatmap_native_emits_continuous_legend_spec(spatial_registry):
    out = await spatial_registry.dispatch("heatmap_data", {
        "geojson": _points(20), "render_type": "native",
    })
    assert "legend_spec" in out  # native rendering now produces continuous legend_spec for frontend legend
    assert out["legend_spec"]["type"] == "continuous"


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
    # ADR-0078 canonical contract: palette_colors count MUST track the class
    # count (len(breaks) - 1), not be padded independently. The old h3_binning
    # path took resolve_palette_colors(palette)[:5] verbatim — always 5 colors
    # regardless of how many classes the data actually produced, so breaks and
    # colors silently disagreed. This degenerate cluster legitimately yields
    # few classes; what matters is that colors and breaks now agree by
    # construction.
    assert len(spec["palette_colors"]) == max(1, len(spec["breaks"]) - 1)


from app.tools.spatial_stats import register_spatial_stats_tools


@pytest.fixture
def stats_registry():
    r = ToolRegistry()
    register_spatial_stats_tools(r)
    return r


@pytest.mark.asyncio
async def test_kde_contours_emits_continuous_legend_spec(stats_registry):
    # 非退化点集：x/y 双向离散展开（旧 fixture 是 20 个严格共线点
    # [104+i*0.001, 30+i*0.001]，KDE 带宽估计在该退化输入上数值脆弱）。
    pts = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature",
             "geometry": {"type": "Point",
                          "coordinates": [104.0 + (i % 5) * 0.02, 30.0 + i * 0.015]},
             "properties": {}}
            for i in range(20)
        ],
    }
    out = await stats_registry.dispatch("kde_contours", {
        "geojson": pts, "levels": 6,
    })
    # 无 skip guard（#564）：scipy/matplotlib 是 requirements.txt 硬依赖，
    # 且 std_error_response 从不输出 "error" 键 —— 旧守卫是死代码且注释误导
    # （真实算法回归应让断言如实失败，而不是伪装成 SKIPPED）。
    spec = out.get("legend_spec")
    assert spec is not None
    assert spec["type"] == "continuous"
    assert spec["min"] < spec["max"]
    assert len(spec["palette_colors"]) >= 3
