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
    ("app.tools.spatial_stats", "register_spatial_stats_tools"),
    ("app.tools.terrain_analysis", "register_terrain_tools"),
    # ("app.tools.interpolation_network", "register_interpolation_network_tools"), # Deleted in v3.4 refactor
    ("app.tools.report", "register_report_tools"),
    ("app.tools.change_detection", "register_change_detection_tools"),
    ("app.tools.monitoring_report", "register_monitoring_report_tools"),
    ("app.tools.skills", "register_skill_tools"),
    ("app.tools.explorer_tools", "register_explorer_tools"),
    ("app.tools.coord_transform", "register_coord_transform_tools"),
    ("app.tools.coord_transform", "register_epsg_transform_tools"),
    ("app.tools.plan_mode", "register_plan_mode_tools"),
    ("app.tools.subagent", "register_subagent_tools"),
    ("app.tools.meta_tools", "register_meta_tools"),
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

    logger.info(f"[ToolInit] Registered {len(registry.list_tools())} tools")
