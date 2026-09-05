"""Tool functional categories（P8 — Tool Registry Rationalization）。

架构原则（Goal → **Capability** → Algorithm → Tool）：工具数量不是成熟度
指标。本模块给注册表补一层**确定性功能分类**（规则表：模块默认 + 工具名
覆盖），供审计 / 目录披露 / 后续按能力路由使用：

    planning        计划编制与推进（webgis_map_intent / plan_mode 族）
    data_access     数据获取与目录（POI/OSM/地理编码/数据编织/上传）
    analysis        空间/时空分析（统计/聚类/密度/网络/遥感指数）
    transformation  几何/栅格变换（裁剪/重分类/重投影/坐标变换）
    rendering       渲染态产物（热力栅格/图表/专题图生成）
    map_mutation    地图状态突变（图层/视图/组件/标注）
    export          导出成品（报告/专题图导出）
    inspection      只读检视（状态/画像/校验/目录/量测）
    debug_internal  内部/调试（subagent/skill/元工具）

分类是**元数据投影**（不改变注册、不影响 dispatch）；未知模块返回
``uncategorized`` 并由审计测试兜底（新工具落进来必须声明归类）。
"""
from __future__ import annotations

from typing import Dict, List

# 模块默认归类（混合职责模块用 _NAME_OVERRIDES 细分）
_MODULE_DEFAULTS: Dict[str, str] = {
    "app.services.gis_harness.tools": "planning",      # 名字覆盖细分
    "app.tools.advanced_spatial": "analysis",           # 名字覆盖细分
    "app.tools.annotation": "map_mutation",             # 量测是检视，名字覆盖
    "app.tools.cartography": "rendering",               # export_* 名字覆盖
    "app.tools.cartography_tools": "map_mutation",      # webgis_* 细分
    "app.tools.change_detection": "analysis",
    "app.tools.chart": "rendering",
    "app.tools.chinese_maps": "data_access",
    "app.tools.coord_transform": "transformation",
    "app.tools.data_fabric_tools": "data_access",
    "app.tools.explorer_tools": "data_access",
    "app.tools.geocoding": "data_access",
    "app.tools.geocompute_tools": "analysis",           # 执行平面；validate/run 查询名字覆盖
    "app.tools.layer_manager": "map_mutation",
    "app.tools.local_admin": "data_access",
    "app.tools.local_osm": "data_access",
    "app.tools.local_stats": "data_access",
    "app.tools.map_view": "map_mutation",
    "app.tools.meta_tools": "debug_internal",
    "app.tools.monitoring_report": "export",
    "app.tools.nature_resources": "analysis",
    "app.tools.network_tools": "analysis",
    "app.tools.osm": "data_access",
    "app.tools.plan_mode": "planning",
    "app.tools.project_tools": "debug_internal",        # workflow/audit 基建
    "app.tools.remote_sensing": "data_access",          # fetch_*；指数分析覆盖
    "app.tools.report": "export",
    "app.tools.skills": "debug_internal",
    "app.tools.spatial": "analysis",                    # heatmap/query 覆盖
    "app.tools.spatial_decision_tools": "analysis",
    "app.tools.spatial_reasoning": "analysis",
    "app.tools.semantic_tools": "analysis",             # 语义画像/模式建议
    "app.tools.flow_tools": "analysis",                 # OD 流向构建
    "app.tools.spatial_stats": "analysis",
    "app.tools.subagent": "debug_internal",
    "app.tools.templates": "rendering",                 # 模板/主题组合
    "app.tools.temporal_tools": "analysis",
    "app.tools.terrain_analysis": "analysis",
    "app.tools.upload_tools": "inspection",
    "app.tools.web_crawler": "data_access",       # 网络 POI 采集（数据获取通道）
    "app.tools.what_if_rules": "analysis",
    "app.tools.what_if_simulate": "analysis",
}

# 工具名覆盖（模块默认之外的精确归类）
_NAME_OVERRIDES: Dict[str, str] = {
    # gis_harness.tools：计划 2 + 组件突变 1 + 检视 2
    "webgis_map_intent": "planning",
    "webgis_map_product": "planning",
    "webgis_component_update": "map_mutation",
    "webgis_component_catalog": "inspection",
    "webgis_world_state": "inspection",
    # cartography_tools：webgis 面板里检视/校验类
    "webgis_state_get": "inspection",
    "webgis_source_profile": "inspection",
    "webgis_validate": "inspection",
    "webgis_runtime_validate": "inspection",
    "webgis_cartography_status": "inspection",
    # advanced_spatial 中的变换族
    "attribute_filter": "transformation",
    "clip_layer": "transformation",
    "dissolve_layer": "transformation",
    "raster_reclassify": "transformation",
    "raster_calculator": "transformation",
    "raster_resample": "transformation",
    # 变化检测是分析（产出变化栅格，不是几何变换）
    "detect_raster_change": "analysis",
    # spatial 模块：渲染态热力 + 只读查询
    "heatmap_data": "rendering",
    "query_map_features": "inspection",
    # annotation：量测是只读检视
    "measure_distance": "inspection",
    "measure_area": "inspection",
    # cartography：导出族
    "export_thematic_map": "export",
    "export_batch_maps": "export",
    # remote_sensing：指数计算是分析
    "compute_ndvi": "analysis",
    "compute_vegetation_index": "analysis",
    # nature_resources：资产目录是检视
    "list_analysis_assets": "inspection",
    "manage_analysis_asset": "debug_internal",
    # templates：地图组合是渲染；模板列表是检视
    "list_templates": "inspection",
    "webgis_map_combine": "rendering",
    # project_tools：空间质检是检视（只读评估）
    "audit_spatial_quality": "inspection",
    "repair_spatial_dataset": "transformation",
    # geocompute_tools：计划校验与 run 查询是只读检视
    "validate_execution_plan": "inspection",
    "get_execution_run": "inspection",
}

#: 分类有限集合（审计与披露依赖）
TOOL_CATEGORIES: List[str] = [
    "planning",
    "data_access",
    "analysis",
    "transformation",
    "rendering",
    "map_mutation",
    "export",
    "inspection",
    "debug_internal",
]


def classify_tool(name: str, module: str) -> str:
    """确定性归类：名字覆盖 > 模块默认 > uncategorized（审计兜底）。"""
    if name in _NAME_OVERRIDES:
        return _NAME_OVERRIDES[name]
    return _MODULE_DEFAULTS.get(module, "uncategorized")


def build_tool_category_manifest(registry) -> Dict[str, Dict[str, List[str]]]:
    """注册表 → {category: {tools: [...]}} 清单（目录披露/审计输入）。"""
    manifest: Dict[str, Dict[str, List[str]]] = {
        cat: {"tools": []} for cat in TOOL_CATEGORIES
    }
    manifest["uncategorized"] = {"tools": []}
    for name in registry.list_tools():
        func = registry._tools.get(name)
        module = getattr(func, "__module__", "")
        manifest[classify_tool(name, module)]["tools"].append(name)
    for entry in manifest.values():
        entry["tools"].sort()
    return manifest
