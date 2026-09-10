"""工具注册初始化"""
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)

# 延迟导入，避免模块加载时触发 heavy 依赖
_TOOL_MODULES = [
    ("app.tools.geocoding", "register_geocoding_tools"),
    ("app.tools.osm", "register_osm_tools"),
    ("app.tools.spatial", "register_spatial_tools"),
    ("app.tools.advanced_spatial", "register_advanced_spatial_tools"),
    ("app.tools.layer_manager", "register_layer_management_tools"),
    ("app.tools.remote_sensing", "register_rs_tools"),
    ("app.tools.chart", "register_chart_tools"),
    ("app.tools.cartography", "register_cartography_tools"),
    ("app.tools.map_view", "register_map_view_tools"),
    ("app.tools.annotation", "register_annotation_tools"),
    ("app.tools.nature_resources", "register_nature_resource_tools"),
    ("app.tools.upload_tools", "register_upload_tools"),
    ("app.tools.web_crawler", "register_crawler_tools"),
    ("app.tools.chinese_maps", "register_chinese_map_tools"),
    ("app.tools.local_admin", "register_local_admin_tools"),
    ("app.tools.local_osm", "register_local_osm_tools"),
    ("app.tools.local_stats", "register_local_stats_tools"),
    ("app.tools.spatial_stats", "register_spatial_stats_tools"),
    # Science V6：空间抽样工具面（Goal 07 新域，独立模块防并发冲突）
    ("app.tools.sampling_tools", "register_sampling_tools"),
    ("app.tools.point_pattern_tools", "register_point_pattern_tools"),
    ("app.tools.terrain_analysis", "register_terrain_tools"),
    ("app.tools.raster_tools_cog", "register_raster_cog_tools"),
    # ("app.tools.interpolation_network", "register_interpolation_network_tools"), # Deleted in v3.4 refactor
    ("app.tools.report", "register_report_tools"),
    ("app.tools.change_detection", "register_change_detection_tools"),
    ("app.tools.monitoring_report", "register_monitoring_report_tools"),
    ("app.tools.skills", "register_skill_tools"),
    # ADR-0100: 如实的技能面刷新（注册表层可热刷；native 面冻结于 spawn）。
    ("app.tools.skill_surface_refresh", "register_skill_surface_refresh"),
    ("app.tools.explorer_tools", "register_explorer_tools"),
    ("app.tools.coord_transform", "register_coord_transform_tools"),
    ("app.tools.coord_transform", "register_epsg_transform_tools"),
    ("app.tools.plan_mode", "register_plan_mode_tools"),
    ("app.tools.subagent", "register_subagent_tools"),
    ("app.tools.meta_tools", "register_meta_tools"),
    ("app.tools.cartography_tools", "register_mapspec_cartography_tools"),
    ("app.tools.templates", "register_template_tools"),
    ("app.services.gis_harness.tools", "register_gis_harness_tools"),
    # Epic 11：方法知识只读工具面（classify/qualify/rank/explain/plan/query）
    ("app.services.gis_harness.knowledge_tools", "register_knowledge_tools"),
    ("app.tools.project_tools", "register_project_tools"),
    ("app.tools.network_tools", "register_network_tools"),
    ("app.tools.temporal_tools", "register_temporal_tools"),
    ("app.tools.spatial_decision_tools", "register_spatial_decision_tools"),
    ("app.tools.data_fabric_tools", "register_data_fabric_tools"),
    ("app.tools.data_discovery", "register_data_discovery_tools"),
    ("app.tools.workspace_tools", "register_workspace_tools"),
    ("app.tools.ingest_tools", "register_ingest_tools"),
    ("app.tools.semantic_tools", "register_semantic_tools"),
    ("app.tools.flow_tools", "register_flow_tools"),
    ("app.tools.dasymetric_tools", "register_dasymetric_tools"),
    ("app.tools.geocompute_tools", "register_geocompute_tools"),
    # science-v5 W8：物候/时空立方体/时间异常工具面（独立模块防并发冲突）
    ("app.tools.science_temporal_tools", "register_science_temporal_tools"),
    ("app.tools.modelops_tools", "register_modelops_tools"),
]



def init_tools(registry: "ToolRegistry") -> None:
    """在 lifespan 启动时注册所有工具，失败单个工具不会阻塞其他工具。"""
    import importlib

    for module_name, func_name in _TOOL_MODULES:
        try:
            mod = importlib.import_module(module_name)
            register_func = getattr(mod, func_name)
            register_func(registry)
        except Exception as e:
            logger.warning(f"[ToolInit] Failed to load {module_name}.{func_name}: {e}")

    # 加载动态技能 (app/skills/*.py)
    try:
        from app.tools.skills import load_skills
        load_skills(registry)
    except Exception as e:
        logger.warning(f"[ToolInit] Failed to load dynamic skills: {e}")

    # #556: rebuild list_available_tools' domain vocabulary from the FINAL
    # registry (meta_tools registered before network/temporal/data_fabric, so
    # its registration-time snapshot missed those domains). Best-effort: a
    # failure must not block startup.
    try:
        from app.tools.meta_tools import refresh_list_available_tools_args
        refresh_list_available_tools_args(registry)
    except Exception as e:
        logger.warning(f"[ToolInit] Failed to refresh list_available_tools args: {e}")

    logger.info(f"[ToolInit] Registered {len(registry.list_tools())} tools")
