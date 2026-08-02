"""
Unit tests for CompositeMapSpecBuilder and combine_map_theme tool
"""
import pytest
from app.schemas.map_component_slots import (
    BasemapSlot,
    SymbologySlot,
    ThematicSlot,
    LayoutSlot,
    ViewportSlot,
)
from app.services.mapspec.composite_builder import (
    CompositeMapSpecBuilder,
    PRESET_COMBINATIONS,
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
    assert mapspec["layers"][0]["thematic"]["field"] == "population"
    assert mapspec["layers"][0]["thematic"]["palette"] == "Viridis"


def test_composite_builder_preset_combinations():
    builder = CompositeMapSpecBuilder()
    mapspec = builder.assemble({"preset": "cyber_dark"})

    assert mapspec["basemap"]["providerId"] == "carto-dark"
    assert mapspec["layout"]["paperSize"] == "A4"


def test_combine_map_theme_tool_execution():
    res = combine_map_theme(
        preset="academic_research",
        layer_id="test_admin"
    )

    assert res["status"] == "composite_map_assembled"
    assert res["layer_id"] == "test_admin"
    assert "mapspec" in res
    assert res["mapspec"]["basemap"]["providerId"] == "carto-positron"
