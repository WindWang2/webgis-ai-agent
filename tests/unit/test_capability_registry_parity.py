"""Capability/Algorithm registry parity with the live ToolRegistry (audit #825).

The guard's object has moved from the planner's static CAPABILITY_TOOLS dict
to the GIS Algorithm Registry (app/lib/gis). CAPABILITY_TOOLS is now a
**derived view** over the registry; this suite locks:

1. every native algorithm's tool candidates are really-registered tools;
2. every capability declared by any recipe (preferred/optional) has at least
   one algorithm with a live tool candidate;
3. the derived CAPABILITY_TOOLS view covers exactly the recipe vocabulary;
4. plan_from_intent marks unresolvable capabilities unavailable when the
   caller passes the registry view;
5. core capabilities resolve to real tools through the AlgorithmResolver.
"""

import pytest


@pytest.fixture(scope="module")
def registry_names():
    from app.tools.registry import ToolRegistry
    from app.tools import init_tools

    reg = ToolRegistry()
    init_tools(reg)
    return set(reg.list_tools())


def test_every_algorithm_tool_candidate_is_a_registered_tool(registry_names):
    from app.lib.gis.algorithm_registry import get_algorithm_registry

    dead = []
    for aid in get_algorithm_registry().all_ids:
        algo = get_algorithm_registry().get(aid)
        if algo is None or algo.runtime_status != "native":
            continue
        missing = [t for t in algo.tool_candidates if t not in registry_names]
        if missing:
            dead.append((aid, missing))
    assert not dead, f"algorithm tool candidates not in registry: {dead}"


def test_every_recipe_capability_resolves_to_a_live_tool(registry_names):
    from app.lib.gis.algorithm_resolver import get_algorithm_resolver
    from app.services.gis_harness.recipes import get_recipe_registry

    resolver = get_algorithm_resolver()
    missing = set()
    for rid in get_recipe_registry().all_ids:
        recipe = get_recipe_registry().get(rid)
        for cap in (recipe.preferred_analysis or []) + (recipe.optional_analysis or []):
            resolution = resolver.resolve(cap, available_tools=registry_names)
            if resolution.status != "resolved":
                missing.add((rid, cap))
    assert not missing, f"recipe capabilities without a live tool: {sorted(missing)}"


def test_derived_capability_tools_view_matches_recipe_vocabulary(registry_names):
    from app.services.gis_harness.planner import capability_tool_map
    from app.services.gis_harness.recipes import get_recipe_registry

    derived = capability_tool_map()
    recipe_caps = set()
    for rid in get_recipe_registry().all_ids:
        recipe = get_recipe_registry().get(rid)
        recipe_caps.update(recipe.preferred_analysis or [])
        recipe_caps.update(recipe.optional_analysis or [])
    unmapped = recipe_caps - set(derived)
    assert not unmapped, f"recipe capabilities missing from derived view: {sorted(unmapped)}"
    # 派生视图里的每个候选都必须是真实工具（不再是手写字典的幽灵名）
    dead = {
        cap: [t for t in tools if t not in registry_names]
        for cap, tools in derived.items()
        if any(t not in registry_names for t in tools)
    }
    assert not dead, f"derived capability tool candidates not registered: {dead}"


def test_registry_validation_clean_with_live_tools(registry_names):
    from app.services.gis_harness.registry_validation import validate_gis_library

    issues = validate_gis_library(available_tools=registry_names)
    assert issues == [], f"cross-registry validation issues: {issues}"


def test_core_capabilities_resolve_to_real_tools(registry_names):
    from app.lib.gis.algorithm_resolver import get_algorithm_resolver

    resolver = get_algorithm_resolver()
    for cap in ("poi_query", "admin_boundary_query", "raster_source",
                "admin_aggregation", "kde_density", "hotspot", "grid_binning"):
        resolution = resolver.resolve(cap, available_tools=registry_names)
        assert resolution.status == "resolved", f"{cap} must resolve: {resolution.reason}"
        assert resolution.tool in registry_names


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
