"""
Unit tests for CompositeMapSpecBuilder and combine_map_theme tool
"""
from app.schemas.map_component_slots import (
    BasemapSlot,
    SymbologySlot,
    ThematicSlot,
    LayoutSlot,
    ViewportSlot,
)
from app.services.mapspec.composite_builder import (
    CompositeMapSpecBuilder,
)
from app.tools.templates import combine_map_theme


def test_composite_builder_default_assembly():
    builder = CompositeMapSpecBuilder()
    mapspec = builder.assemble({}, layer_id="layer_1")

    assert mapspec["version"] == "1.0"
    assert mapspec["basemap"]["providerId"] == "carto-positron"
    assert len(mapspec["layers"]) == 1
    assert mapspec["layers"][0]["id"] == "layer_1"


def test_composite_builder_custom_slots():
    builder = CompositeMapSpecBuilder()
    builder.with_basemap(BasemapSlot(provider_id="carto-dark"))
    builder.with_symbology(SymbologySlot(mode="single", color="#ff0000", radius=8.0))
    builder.with_thematic(ThematicSlot(variant="choropleth", field="population", palette="Viridis"))
    builder.with_layout(LayoutSlot(paper_size="A3", orientation="portrait"))
    builder.with_viewport(ViewportSlot(center=[116.4, 39.9], zoom=12.0))

    mapspec = builder.assemble({})

    assert mapspec["basemap"]["providerId"] == "carto-dark"
    assert mapspec["view"]["center"] == [116.4, 39.9]
    assert mapspec["view"]["zoom"] == 12.0
    assert mapspec["layout"]["paperSize"] == "A3"
    # Thematic now emits legend_spec (consumed by frontend), not dead "thematic" key
    assert mapspec["layers"][0]["legend_spec"]["field"] == "population"
    assert mapspec["layers"][0]["legend_spec"]["palette"] == "Viridis"


def test_composite_builder_preset_combinations():
    builder = CompositeMapSpecBuilder()
    mapspec = builder.assemble({"preset": "cyber_dark"})

    assert mapspec["basemap"]["providerId"] == "carto-dark"
    # cyber_dark → tmpl_ly_dark_report，其定义就是 A3 横向（template_schema.py）。
    # 早期实现里该预设映射到 A4 版式，制图统一（#337）后收敛为 A3。
    assert mapspec["layout"]["paperSize"] == "A3"


def test_combine_map_theme_tool_execution():
    res = combine_map_theme(
        preset="academic_research",
        layer_id="test_admin"
    )

    assert res["status"] == "composite_map_assembled"
    assert res["layer_id"] == "test_admin"
    assert "mapspec" in res
    assert res["mapspec"]["basemap"]["providerId"] == "carto-positron"


def test_composite_builder_line_geometry_paint():
    builder = CompositeMapSpecBuilder()
    builder.with_symbology(SymbologySlot(
        mode="single",
        geometry="LineString",
        color="#00ff00",
        stroke_width=3.0,
        fill_opacity=0.9
    ))
    mapspec = builder.assemble({}, layer_id="roads")

    layer = mapspec["layers"][0]
    assert layer["type"] == "line"
    assert layer["paint"]["line-color"] == "#00ff00"
    assert layer["paint"]["line-width"] == 3.0
    assert layer["paint"]["line-opacity"] == 0.9

