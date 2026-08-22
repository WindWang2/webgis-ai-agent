"""audit #825: capability→tool mapping parity with the live ToolRegistry.

The previous guard sampled 3 capability ids; the table had drifted so that
poi_query / admin_boundary_query / raster_source resolved to ghost tool names
(renamed long ago) and administrative_choropleth declared an unmapped
analytical_density. This suite locks FULL parity:

1. every CAPABILITY_TOOLS candidate is a really-registered tool name;
2. every capability id declared by any recipe (preferred/optional) has an
   entry in CAPABILITY_TOOLS;
3. plan_from_intent marks unresolvable capabilities unavailable when the
   caller passes the registry view.
"""

import pytest


@pytest.fixture(scope="module")
def registry_names():
    from app.tools.registry import ToolRegistry
    from app.tools import init_tools

    reg = ToolRegistry()
    init_tools(reg)
    return set(reg.list_tools())


def test_every_capability_candidate_is_a_registered_tool(registry_names):
    from app.services.gis_harness.planner import CAPABILITY_TOOLS

    dead = {
        cap: [c for c in cands if c not in registry_names]
        for cap, cands in CAPABILITY_TOOLS.items()
        if any(c not in registry_names for c in cands)
    }
    assert not dead, f"capability candidates not in registry: {dead}"


def test_every_recipe_capability_has_a_tool_mapping():
    from app.services.gis_harness.planner import CAPABILITY_TOOLS
    from app.services.gis_harness.recipes import get_recipe_registry

    missing = set()
    for rid in get_recipe_registry().all_ids:
        recipe = get_recipe_registry().get(rid)
        for cap in (recipe.preferred_analysis or []) + (recipe.optional_analysis or []):
            if cap not in CAPABILITY_TOOLS:
                missing.add((rid, cap))
    assert not missing, f"recipe capabilities without tool mapping: {sorted(missing)}"


def test_core_capabilities_resolve_to_real_tools(registry_names):
    from app.services.gis_harness.planner import resolve_tool_for_capability

    for cap in ("poi_query", "admin_boundary_query", "raster_source",
                "admin_aggregation", "kde_density", "hotspot", "grid_binning"):
        resolved = resolve_tool_for_capability(cap, registry_names)
        assert resolved, f"{cap} must resolve against the live registry"
        assert resolved in registry_names


def test_unresolvable_capability_marked_unavailable_in_plan():
    from app.services.gis_harness.intent import resolve_map_request_intent
    from app.services.gis_harness.planner import MapProductPlanner

    intent = resolve_map_request_intent("成都小学的分布情况")
    planner = MapProductPlanner()
    # registry view WITHOUT any poi tool: poi_query must degrade honestly
    plan = planner.plan_from_intent(intent, available_tools={"nonexistent_tool"})
    statuses = {r.capability: r.status for r in plan.data_requirements}
    assert statuses.get("poi_query") == "unavailable"

    # unknown view (None): stays pending, not falsely unavailable
    plan_pending = planner.plan_from_intent(intent)
    statuses_pending = {r.capability: r.status for r in plan_pending.data_requirements}
    assert statuses_pending.get("poi_query") == "pending"
