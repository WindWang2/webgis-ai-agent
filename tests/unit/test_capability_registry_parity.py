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

    areg = get_algorithm_registry()
    dead = []
    for aid in areg.all_ids:
        algo = areg.get(aid)
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


def test_purpose_named_tools_backmap_to_capabilities():
    """#1075(D-3): purpose-named 工具与数据访问工具必须在 capability 反查图
    中可见；shortest_path 能力解析到最短路径工具族（此前指向 isochrone 族
    与不存在的 nearest_facility）。"""
    from app.tools import init_tools
    from app.tools.registry import ToolRegistry
    from app.lib.gis.algorithm_registry import get_algorithm_registry

    reg = ToolRegistry()
    init_tools(reg)
    t2c = get_algorithm_registry().tool_to_capability()
    expected = {
        "network_shortest_path": "shortest_path",
        "network_closest_facility": "shortest_path",
        "raster_calculator": "raster_source",
        "spatiotemporal_hotspot": "temporal_trend",
        "temporal_aggregate": "temporal_trend",
        "temporal_raster": "temporal_trend",
        "get_admin_division": "admin_boundary_query",
        "search_poi_around": "poi_query",
        "search_poi_polygon": "poi_query",
    }
    for tool, cap in expected.items():
        assert t2c.get(tool) == cap, f"{tool} 应反查到 {cap}，实际 {t2c.get(tool)}"
    # 全部真实工具名必须存在（无指向不存在工具的候选残留）
    tools = set(reg.list_tools())
    areg = get_algorithm_registry()
    for algo_id in areg.all_ids:
        algo = areg.get(algo_id)
        for cand in algo.tool_candidates:
            assert cand in tools, f"算法 {algo.id} 的候选 {cand} 不是真实工具"


def test_temporal_trend_capability_registered():
    """#1075(D-10): temporal_trend capability 就位 —— temporal.* 算法不再
    挂到 spatial_interpolation 上。"""
    from app.lib.gis.capability_registry import get_capability_registry
    from app.lib.gis.algorithm_registry import get_algorithm_registry

    assert get_capability_registry().get("temporal_trend") is not None
    algo = get_algorithm_registry().get("temporal.trend")
    assert algo is not None and algo.capabilities == ["temporal_trend"]


def test_tool_to_capability_cached_and_invalidated():
    """#1076(D-8): 反查索引缓存 —— 二次调用零重建；register 后失效重建。"""
    from app.lib.gis.algorithm_registry import get_algorithm_registry, AlgorithmDescriptor

    reg = get_algorithm_registry()
    m1 = reg.tool_to_capability()
    m2 = reg.tool_to_capability()
    assert m1 is m2, "静态注册表的派生索引应缓存复用"
    reg.register(AlgorithmDescriptor(
        id="cache.invalidate_probe", name="probe", category="network_analysis",
        capabilities=["shortest_path"],
        output_artifact_type="line_feature_set",
        tool_candidates=["network_shortest_path"],
    ))
    m3 = reg.tool_to_capability()
    assert m3 is not m1, "register 后缓存必须失效"
    # 清理探针
    reg._by_id.pop("cache.invalidate_probe")
    reg._by_capability.get("shortest_path", []).remove("cache.invalidate_probe")
    reg._tool_to_capability_cache = None
