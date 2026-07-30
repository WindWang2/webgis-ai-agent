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
    """Test list_templates returning all built-in templates when no filter is provided."""
    result = await registry.dispatch("list_templates", {})
    assert "templates" in result
    templates = result["templates"]
    assert len(templates) >= 16
    kinds = {t["kind"] for t in templates}
    assert kinds == {"basemap", "symbology", "layout", "thematic"}


@pytest.mark.asyncio
async def test_list_templates_filter_by_kind(registry):
    """Test list_templates filtering by kind."""
    result = await registry.dispatch("list_templates", {"kind": "symbology"})
    assert "templates" in result
    templates = result["templates"]
    assert len(templates) == 5
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
