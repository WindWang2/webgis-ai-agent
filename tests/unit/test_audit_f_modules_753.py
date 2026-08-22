"""#753 (rest): minimal tests for the remaining zero-test production modules —
meta_tools, explorer.validate_stage, gis_harness.product_templates."""
import asyncio



def test_meta_tools_list_available_tools_domains_are_live():
    """Every advertised domain must return >=1 tool (#556 contract)."""
    from app.tools.registry import ToolRegistry
    from app.tools.meta_tools import register_meta_tools

    reg = ToolRegistry()
    register_meta_tools(reg)

    async def run():
        return await reg.dispatch("list_available_tools", {"domain": "osm"})

    res = asyncio.run(run())
    assert isinstance(res, dict)
    assert res.get("success") is not False


def test_validate_stage_completes_with_progress():
    from app.services.explorer.validate_stage import run_validate_stage

    seen = []

    async def run():
        return await run_validate_stage(
            "task-1", geocoded_ref_id="ref:geojson-1", total_rows=10,
            on_progress=seen.append,
        )

    result = asyncio.run(run())
    assert result.stage == "validate"
    assert result.data["status"] == "completed"
    assert result.data["geocoded_ref_id"] == "ref:geojson-1"
    assert 100 in seen


def test_product_templates_registry_integrity():
    """Seeds load, subject-aware find_for_recipe picks the generic template for
    non-education subjects and the specialized one for 学校 subjects (#719)."""
    from app.services.gis_harness.product_templates import get_product_template_registry

    registry = get_product_template_registry()
    assert registry.all_ids
    generic = registry.find_for_recipe("poi_distribution_overview", subject_category="餐厅")
    assert generic.id == "poi_distribution_overview"
    edu = registry.find_for_recipe("poi_distribution_overview", subject_category="小学")
    assert edu.id == "education_facility_distribution"
