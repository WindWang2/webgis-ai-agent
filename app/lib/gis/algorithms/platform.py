"""平台域算法包（Quality W2b）—— 平台工具面的绑定契约描述符。

语义（诚实边界）：本域的「算法」是**工具面绑定契约** —— 候选工具是
已注册、可执行的真实工具（runtime_status="native" 指工具面已实现且
绑定契约被 conformance 测试钉住：候选必须真实注册、且显式声明对应
capability）。对应 capability 的 status 保持 **planned**：算法级参数
契约 / 科学元数据 / 分析语义尚未建立，规划器与 AlgorithmResolver 对
planned 能力一律不可派发（resolver 对非 native 能力显式 unavailable）。

新增平台能力 = capability（capabilities/platform.py）+ 绑定描述符
（本模块）+ 绑定 conformance 节点（tests/unit/gis/test_platform_capabilities.py），
三处同增，缺一会被 registry validate / conformance AST 门拒绝。
"""
from __future__ import annotations

from typing import List

from app.lib.gis.algorithm_registry import AlgorithmDescriptor

_CONF = "tests/unit/gis/test_platform_capabilities.py::"


def _platform_binding(cap_id: str, name: str, tools: List[str],
                      assumptions_extra: str = "") -> AlgorithmDescriptor:
    assumptions = [
        "绑定契约：每个候选工具已在 ToolRegistry 注册，且其描述符显式"
        "声明本能力（conformance 节点逐能力钉住，漂移即红）",
    ]
    if assumptions_extra:
        assumptions.append(assumptions_extra)
    return AlgorithmDescriptor(
        id=f"platform.{cap_id}", name=name,
        capabilities=[cap_id], category="platform",
        tool_candidates=list(tools),
        conformance_tests=[f"{_CONF}test_platform_capability_{cap_id}"],
        runtime_status="native",
        cpu_cost="low", memory_cost="low", io_cost="low",
        preferred_execution_policy="INLINE",
        deterministic=True, random_seed_policy="deterministic",
        crs_class="CRS_AGNOSTIC",
        algorithm_family="platform_surface",
        assumptions=assumptions,
        limitations=[
            "能力语义 planned：算法级参数契约/科学元数据尚未建立；"
            "planned 能力不进入分析派发（resolver 对非 native 能力 unavailable）",
        ],
    )


ALGORITHMS: List[AlgorithmDescriptor] = [
    _platform_binding(
        "map_viewport_control", "地图视口控制（工具面绑定契约）",
        ["fly_to_location", "set_map_view", "reset_map_view",
         "zoom_to_bbox", "zoom_to_layer", "webgis_view_set"],
    ),
    _platform_binding(
        "layer_display_control", "图层显示控制（工具面绑定契约）",
        ["alias_layer", "apply_layer_filter", "display_layer",
         "finalize_display", "remove_layer", "reorder_layer",
         "set_layer_status", "switch_base_layer",
         "update_layer_appearance"],
    ),
    _platform_binding(
        "map_annotation_measurement", "地图标注与量测（工具面绑定契约）",
        ["add_marker", "clear_annotations", "measure_area",
         "measure_distance", "query_map_features"],
    ),
    _platform_binding(
        "thematic_cartography", "专题制图与样式（工具面绑定契约）",
        ["apply_layer_style", "apply_template", "combine_map_theme",
         "control_floating_chart", "create_3d_extrusion_map",
         "create_thematic_map", "list_templates", "webgis_component_update",
         "webgis_layout_set", "webgis_map_combine", "webgis_layer_upsert",
         "webgis_layer_remove", "webgis_map_intent", "webgis_map_product"],
    ),
    _platform_binding(
        "map_export_publishing", "地图导出发布（工具面绑定契约）",
        ["export_batch_maps", "export_thematic_map",
         "webgis_cartography_status", "webgis_compile_maplibre",
         "webgis_project_init", "webgis_runtime_validate", "webgis_validate"],
    ),
    _platform_binding(
        "geocoding", "地理编码（工具面绑定契约）",
        ["geocode", "geocode_cn", "batch_geocode_cn",
         "reverse_geocode", "reverse_geocode_cn"],
        assumptions_extra="外部 provider 的可用性由各工具的类型化降级契约保证"
                          "（未配置 key → 结构化错误，见 conformance）",
    ),
    _platform_binding(
        "local_data_query", "本地数据目录与查询（工具面绑定契约）",
        ["get_local_osm_catalog", "get_local_stats_catalog",
         "query_local_osm", "query_local_yearbook",
         "query_osm_buildings", "query_osm_roads"],
    ),
    _platform_binding(
        "data_source_pipeline", "数据源管道（工具面绑定契约）",
        ["aggregate_dataset", "attribute_filter", "connect_data_source",
         "describe_dataset", "inspect_data_source", "manage_analysis_asset",
         "plan_data_query", "query_dataset", "refresh_data_source",
         "search_spatial_catalog"],
    ),
    _platform_binding(
        "plan_workflow_orchestration", "计划与工作流编排（工具面绑定契约）",
        ["cancel_execution_run", "execute_execution_plan", "execute_plan",
         "get_execution_run", "get_plan_status", "propose_plan",
         "rerun_workflow", "save_plan_as_workflow", "validate_execution_plan"],
    ),
    _platform_binding(
        "scenario_simulation", "情景推演（工具面绑定契约）",
        ["spatial_decision_v2", "spatial_reasoning", "what_if_simulate"],
    ),
    _platform_binding(
        "meta_tool_surface", "元工具面（工具面绑定契约）",
        ["create_new_skill", "deep_explore", "list_available_tools",
         "refresh_skill_surface", "spawn_subagent", "web_search"],
    ),
    _platform_binding(
        "report_charting", "报告与图表（工具面绑定契约）",
        ["generate_analysis_report", "generate_chart",
         "generate_monitoring_report"],
    ),
    _platform_binding(
        "dataset_profiling_quality", "数据画像与质量（工具面绑定契约）",
        ["audit_spatial_quality", "profile_dataset",
         "profile_dataset_semantics", "repair_spatial_dataset",
         "suggest_analysis_patterns"],
    ),
    _platform_binding(
        "crs_transformation", "坐标系转换（工具面绑定契约）",
        ["reproject_coordinates", "transform_coordinates"],
    ),
    _platform_binding(
        "temporal_filtering", "时间筛选（工具面绑定契约）",
        ["temporal_filter"],
    ),
    _platform_binding(
        "directional_distribution_analysis", "方向分布分析（工具面绑定契约）",
        ["standard_deviational_ellipse"],
    ),
]
