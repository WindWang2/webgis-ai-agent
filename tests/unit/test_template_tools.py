"""
Unit tests for template tools: list_templates & apply_template (Seam 1 dispatch).
"""
import pytest
from app.tools.registry import ToolRegistry
from app.tools.templates import register_template_tools
from app.tools.__init__ import init_tools


@pytest.fixture
def registry():
    """Create a ToolRegistry and register template tools."""
    reg = ToolRegistry()
    register_template_tools(reg)
    return reg


@pytest.mark.asyncio
async def test_list_templates_all(registry):
    """Test list_templates returning all built-in templates when no filter is provided.

    F-FE-TPL: V2 expanded the library to 60+ entries (incl. 22 composites);
    a 20-row default page only samples a subset of kinds, so the test
    explicitly requests a full-library scan.
    """
    result = await registry.dispatch("list_templates", {"limit": 100})
    assert "templates" in result
    templates = result["templates"]
    assert len(templates) >= 70
    kinds = {t["kind"] for t in templates}
    assert kinds == {"basemap", "symbology", "layout", "thematic", "composite"}


@pytest.mark.asyncio
async def test_list_templates_filter_by_kind(registry):
    """Test list_templates filtering by kind.

    F-FE-TPL: V2 expanded the symbology library from 5 to 16 entries; the
    lower bound is 15 to enforce the expansion.
    """
    result = await registry.dispatch("list_templates", {"kind": "symbology"})
    assert "templates" in result
    templates = result["templates"]
    assert len(templates) >= 15
    for t in templates:
        assert t["kind"] == "symbology"


@pytest.mark.asyncio
async def test_list_templates_filter_by_query(registry):
    """Test list_templates filtering by query keyword/name."""
    result = await registry.dispatch("list_templates", {"q": "学术"})
    assert "templates" in result
    templates = result["templates"]
    assert len(templates) >= 2
    for t in templates:
        assert "学术" in t["name"] or "学术" in t.get("description", "") or any("学术" in k for k in t.get("keywords", []))


@pytest.mark.asyncio
async def test_apply_template_symbology_single(registry):
    """Test apply_template with a single mode symbology template."""
    sample_geojson = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]},
                "properties": {"name": "Zone A"}
            }
        ]
    }
    result = await registry.dispatch("apply_template", {
        "template_id": "tmpl_sym_admin_blue",
        "geojson": sample_geojson
    })

    assert "error" not in result
    assert result["status"] == "template_applied"
    assert result["kind"] == "symbology"
    assert result["template_id"] == "tmpl_sym_admin_blue"
    assert "geojson" in result
    assert result["geojson"]["features"][0]["properties"]["fill_color"] == "#3b82f6"


@pytest.mark.asyncio
async def test_apply_template_basemap(registry):
    """Test apply_template with a basemap template ID emitting BASE_LAYER_CHANGE command."""
    result = await registry.dispatch("apply_template", {
        "template_id": "tmpl_bm_positron"
    })

    assert "error" not in result
    assert result["status"] == "template_applied"
    assert result["kind"] == "basemap"
    assert result["command"] == "BASE_LAYER_CHANGE"
    assert result["params"]["providerId"] == "carto-positron"
    assert "vectorStyleUrl" in result["params"]


@pytest.mark.asyncio
async def test_apply_template_layout(registry):
    """Test apply_template with a layout template ID emitting EXPORT_LAYOUT_UPDATE / export_map command."""
    result = await registry.dispatch("apply_template", {
        "template_id": "tmpl_ly_academic"
    })

    assert "error" not in result
    assert result["status"] == "template_applied"
    assert result["kind"] == "layout"
    # Layout must dispatch to the real frontend command (was EXPORT_LAYOUT_UPDATE — fixed)
    assert result["command"] == "export_map"
    assert "params" in result
    assert result["params"]["paperSize"] == "A4"
    assert result["params"]["style"]["fontFamily"] == "Georgia, serif"


@pytest.mark.asyncio
async def test_apply_template_thematic_choropleth(registry):
    """Test apply_template with a thematic choropleth preset template ID."""
    sample_geojson = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "properties": {"pop": 10}},
            {"type": "Feature", "properties": {"pop": 50}},
            {"type": "Feature", "properties": {"pop": 100}},
        ]
    }
    result = await registry.dispatch("apply_template", {
        "template_id": "tmpl_th_pop_choro",
        "field": "pop",
        "geojson": sample_geojson,
    })

    assert "error" not in result
    assert result["status"] == "template_applied"
    assert result["kind"] == "thematic"
    assert result["variant"] == "choropleth"
    assert result["command"] == "create_thematic_map"
    assert result["field"] == "pop"
    assert result["style"] is not None
    assert result["legend_spec"] is not None
    assert result["legend_spec"]["type"] == "graduated"


@pytest.mark.asyncio
async def test_apply_template_thematic_heatmap(registry):
    """Test apply_template with a thematic heatmap preset template ID."""
    result = await registry.dispatch("apply_template", {
        "template_id": "tmpl_th_heatmap",
        "field": "density"
    })

    assert "error" not in result
    assert result["status"] == "template_applied"
    assert result["kind"] == "thematic"
    assert result["variant"] == "heatmap"
    assert result["command"] == "add_native_heatmap"
    assert result["field"] == "density"
    assert result["params"]["radius"] == 30


@pytest.mark.asyncio
async def test_apply_template_unknown_id(registry):
    """Test apply_template with non-existent template_id returns an error."""
    result = await registry.dispatch("apply_template", {"template_id": "tmpl_non_existent"})
    assert "error" in result
    assert "not found" in result["error"].lower()


def test_init_tools_includes_templates():
    """Test that app.tools.__init__.init_tools registers list_templates and apply_template."""
    reg = ToolRegistry()
    init_tools(reg)
    tools = reg.list_tools()
    assert "list_templates" in tools
    assert "apply_template" in tools


def test_no_builtin_symbology_preset_carries_field():
    """Spec invariant (US22-style): field is injected at apply-time, NOT stored in a preset.

    Categorical symbology seeds must not hardcode `field` (only thematic does, and even
    thematic injects it at apply). This guards against regression of the field-in-preset fix.
    """
    from app.schemas.template_schema import SEED_TEMPLATES

    offenders = [
        t["id"]
        for t in SEED_TEMPLATES
        if t.get("kind") == "symbology"
        and t.get("is_builtin")
        and t.get("payload", {}).get("mode") == "categorical"
        and "field" in t.get("payload", {})
    ]
    assert offenders == [], f"builtin categorical symbology presets must not carry `field`: {offenders}"
