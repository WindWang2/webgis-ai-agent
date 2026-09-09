"""平台能力绑定契约 conformance（Quality W2b）。

15 个 platform.* 能力（status=planned）的绑定契约测试：每个能力一个
节点，钉住三件事 ——
1. capability 已注册且状态诚实为 planned（不伪装 native 分析能力）；
2. 对应 platform.* 绑定算法存在、候选非空，且候选全部是真实注册工具；
3. 每个候选工具的描述符**显式声明**该 capability（声明/绑定双向一致，
   工具改名/移除/声明漂移都会在这里红）。

节点 id 被 app/lib/gis/algorithms/platform.py 的 conformance_tests
引用，受 registry 的 AST 节点级存在性校验约束。
"""
from __future__ import annotations

import pytest

# (capability_id, 绑定候选工具) —— 与 app/lib/gis/algorithms/platform.py
# 逐字对齐（测试侧冗余一份是刻意的：双向漂移都能被抓住）。
PLATFORM_BINDINGS = {
    "map_viewport_control": [
        "fly_to_location", "set_map_view", "reset_map_view",
        "zoom_to_bbox", "zoom_to_layer", "webgis_view_set"],
    "layer_display_control": [
        "alias_layer", "apply_layer_filter", "display_layer",
        "finalize_display", "remove_layer", "reorder_layer",
        "set_layer_status", "switch_base_layer",
        "update_layer_appearance"],
    "map_annotation_measurement": [
        "add_marker", "clear_annotations", "measure_area",
        "measure_distance", "query_map_features"],
    "thematic_cartography": [
        "apply_layer_style", "apply_template", "combine_map_theme",
        "control_floating_chart", "create_3d_extrusion_map",
        "create_thematic_map", "list_templates", "webgis_component_update",
        "webgis_layout_set", "webgis_map_combine", "webgis_layer_upsert",
        "webgis_layer_remove", "webgis_map_intent", "webgis_map_product"],
    "map_export_publishing": [
        "export_batch_maps", "export_thematic_map",
        "webgis_cartography_status", "webgis_compile_maplibre",
        "webgis_project_init", "webgis_runtime_validate", "webgis_validate"],
    "geocoding": [
        "geocode", "geocode_cn", "batch_geocode_cn",
        "reverse_geocode", "reverse_geocode_cn"],
    "local_data_query": [
        "get_local_osm_catalog", "get_local_stats_catalog",
        "query_local_osm", "query_local_yearbook",
        "query_osm_buildings", "query_osm_roads"],
    "data_source_pipeline": [
        "aggregate_dataset", "attribute_filter", "connect_data_source",
        "describe_dataset", "inspect_data_source", "manage_analysis_asset",
        "plan_data_query", "query_dataset", "refresh_data_source",
        "search_spatial_catalog"],
    "plan_workflow_orchestration": [
        "cancel_execution_run", "execute_execution_plan", "execute_plan",
        "get_execution_run", "get_plan_status", "propose_plan",
        "rerun_workflow", "save_plan_as_workflow", "validate_execution_plan"],
    "scenario_simulation": [
        "spatial_decision_v2", "spatial_reasoning", "what_if_simulate"],
    "meta_tool_surface": [
        "create_new_skill", "deep_explore", "list_available_tools",
        "refresh_skill_surface", "spawn_subagent", "web_search"],
    "report_charting": [
        "generate_analysis_report", "generate_chart",
        "generate_monitoring_report"],
    "dataset_profiling_quality": [
        "audit_spatial_quality", "profile_dataset",
        "profile_dataset_semantics", "repair_spatial_dataset",
        "suggest_analysis_patterns"],
    "crs_transformation": [
        "reproject_coordinates", "transform_coordinates"],
    "temporal_filtering": [
        "temporal_filter"],
    "directional_distribution_analysis": [
        "standard_deviational_ellipse"],
}

# 平台绑定声明的全集（防工具在域间漂移：同一工具只能声明一个平台能力）
_ALL_DECLARED = sorted(t for members in PLATFORM_BINDINGS.values() for t in members)


@pytest.fixture(scope="module")
def live_registry():
    from app.tools import init_tools
    from app.tools.registry import ToolRegistry

    reg = ToolRegistry()
    init_tools(reg)
    return reg


def _assert_binding(cap_id: str, live_registry) -> None:
    from app.lib.gis.algorithm_registry import get_algorithm_registry
    from app.lib.gis.capability_registry import get_capability_registry

    cap_reg = get_capability_registry()
    algo_reg = get_algorithm_registry()

    # 1) capability 诚实 planned
    assert cap_reg.has(cap_id), f"capability {cap_id} 未注册"
    assert cap_reg.get(cap_id).status == "planned", (
        f"{cap_id}: 平台能力必须保持 planned（算法级契约未建立）")

    # 2) 绑定算法存在、候选非空且全部真实注册
    algo_id = f"platform.{cap_id}"
    assert algo_reg.has(algo_id), f"{algo_id} 未注册"
    algo = algo_reg.get(algo_id)
    assert algo.tool_candidates, f"{algo_id} 无候选"
    missing = [t for t in algo.tool_candidates if not live_registry.has(t)]
    assert not missing, f"{algo_id} 候选未注册: {missing}"

    # 3) 每个候选工具显式声明该 capability（双向一致）
    undeclared = [
        t for t in algo.tool_candidates
        if cap_id not in live_registry.descriptor(t).capabilities
    ]
    assert not undeclared, f"{algo_id} 候选未声明 capability {cap_id}: {undeclared}"

    # 测试侧冗余清单与描述符一致（防两处漂移）
    assert sorted(algo.tool_candidates) == sorted(PLATFORM_BINDINGS[cap_id])


def test_platform_capability_map_viewport_control(live_registry):
    _assert_binding("map_viewport_control", live_registry)


def test_platform_capability_layer_display_control(live_registry):
    _assert_binding("layer_display_control", live_registry)


def test_platform_capability_map_annotation_measurement(live_registry):
    _assert_binding("map_annotation_measurement", live_registry)


def test_platform_capability_thematic_cartography(live_registry):
    _assert_binding("thematic_cartography", live_registry)


def test_platform_capability_map_export_publishing(live_registry):
    _assert_binding("map_export_publishing", live_registry)


def test_platform_capability_geocoding(live_registry):
    _assert_binding("geocoding", live_registry)


def test_platform_capability_local_data_query(live_registry):
    _assert_binding("local_data_query", live_registry)


def test_platform_capability_data_source_pipeline(live_registry):
    _assert_binding("data_source_pipeline", live_registry)


def test_platform_capability_plan_workflow_orchestration(live_registry):
    _assert_binding("plan_workflow_orchestration", live_registry)


def test_platform_capability_scenario_simulation(live_registry):
    _assert_binding("scenario_simulation", live_registry)


def test_platform_capability_meta_tool_surface(live_registry):
    _assert_binding("meta_tool_surface", live_registry)


def test_platform_capability_report_charting(live_registry):
    _assert_binding("report_charting", live_registry)


def test_platform_capability_dataset_profiling_quality(live_registry):
    _assert_binding("dataset_profiling_quality", live_registry)


def test_platform_capability_crs_transformation(live_registry):
    _assert_binding("crs_transformation", live_registry)


def test_platform_capability_temporal_filtering(live_registry):
    _assert_binding("temporal_filtering", live_registry)


def test_platform_capability_directional_distribution_analysis(live_registry):
    _assert_binding("directional_distribution_analysis", live_registry)


def test_platform_bindings_are_exhaustive_and_disjoint(live_registry):
    """绑定清单互不重叠（同一工具只归属一个平台能力），且每个绑定
    工具在 live registry 恰好声明一个平台 capability（原生 GIS 能力
    声明不受此约束）。"""
    seen: dict[str, str] = {}
    for cap_id, members in PLATFORM_BINDINGS.items():
        for t in members:
            assert t not in seen, f"{t} 同时出现在 {seen[t]} 与 {cap_id}"
            seen[t] = cap_id

    declared = [
        t for t in _ALL_DECLARED
        if any(c in PLATFORM_BINDINGS
               for c in live_registry.descriptor(t).capabilities)
    ]
    assert sorted(declared) == _ALL_DECLARED, (
        f"平台能力声明漂移: {sorted(set(declared) ^ set(_ALL_DECLARED))}")


def test_platform_capabilities_never_native_and_resolver_refuses():
    """planned 平台能力绝不允许被解析器当可执行分析派发。"""
    from app.lib.gis.algorithm_resolver import get_algorithm_resolver
    from app.lib.gis.capabilities import iter_capability_packs

    planned = {
        c.id for pack in iter_capability_packs() for c in pack
        if c.id in PLATFORM_BINDINGS
    }
    assert planned == set(PLATFORM_BINDINGS), "平台能力必须全部 planned"
    resolver = get_algorithm_resolver()
    for cap_id in sorted(planned):
        resolution = resolver.resolve(cap_id)
        assert resolution.status == "unavailable", (
            f"{cap_id} 被解析为 {resolution.status}（planned 不得派发）")
        assert resolution.reason == "capability_planned"
