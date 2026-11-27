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
    "app.services.gis_harness.knowledge_tools": "inspection",
    "app.services.gis_harness.tools": "planning",      # 名字覆盖细分
    "app.tools.advanced_spatial": "analysis",           # 名字覆盖细分
    "app.tools.annotation": "map_mutation",             # 量测是检视，名字覆盖
    "app.tools.catalog_discovery_tools": "inspection",  # F07 只读发现面
    "app.tools.cartography": "rendering",               # export_* 名字覆盖
    "app.tools.cartography_tools": "map_mutation",      # webgis_* 细分
    "app.tools.change_detection": "analysis",
    "app.tools.chart": "rendering",
    "app.tools.chinese_maps": "data_access",
    "app.tools.coord_transform": "transformation",
    "app.tools.dasymetric_tools": "analysis",       # 面插值重分配（量守恒分析）
    "app.tools.data_discovery": "inspection",           # 目录/画像/lineage 只读发现
    "app.tools.task_data_supply_tools": "data_access",  # GOAL-02 任务供给（发现/覆盖/计划只读+受控物化）
    "app.tools.data_fabric_tools": "data_access",
    # GOAL-02：服务发现只读面（inspect_data_service）+ 受控读取/物化（读 =
    # data_access；瓦片上图名字覆盖 map_mutation 在模块内声明）。
    "app.tools.ogc_service_tools": "data_access",
    "app.tools.ingest_tools": "data_access",            # 入库（detect→profile→register）
    "app.tools.data_preparation_tools": "data_access",  # 数据准备（inspect→propose→prepare→bind）
    "app.tools.raster_tools_cog": "transformation",     # COG 转换
    "app.tools.explorer_tools": "data_access",
    "app.tools.geocoding": "data_access",
    # ADR-0198：GeoAI 可提示分割/语义映射/embedding 是分析产出
    #（prompt artifact 检视是只读，名字覆盖）
    "app.tools.geoai_tools": "analysis",
    # ADR-0217：GISAction dry-run 编译校验（零执行零状态变更）
    "app.tools.gis_action_tools": "inspection",
    # ADR-0285：选择 scope 只读验证，不隐式执行分析或变更地图。
    "app.tools.interaction_scope_tools": "inspection",
    # ADR-0214 D6：组件发现/替换规划只读（apply 落盘名字覆盖 map_mutation）
    "app.tools.composition_tools": "inspection",
    # ADR-0215：证据复核/重验证回执（bind_mission 恢复上下文，名字覆盖）
    "app.tools.context_revalidation_tools": "inspection",
    # ADR-0260 D7：编译链经 MapSpecLifecycleEngine CAS 提交 —— mutation 面
    "app.tools.map_plan_tools": "map_mutation",
    # RS Temporal Cube：时序立方体对齐/融合/特征（describe 检视名字覆盖）
    "app.tools.rs_cube_tools": "analysis",
    # ADR-0199：多尺度场景 —— plan 只读投影（set 事务写入名字覆盖）
    "app.tools.scene_tools": "inspection",
    # G3D04：三维编排 —— 能力探测只读；representation/相机/分析名字覆盖细分
    "app.tools.scene_orchestration_tools": "inspection",
    "app.tools.geocompute_tools": "analysis",           # 执行平面；validate/run 查询名字覆盖
    "app.tools.layer_manager": "map_mutation",
    # 分析流程编排（goal-02）：plan/recompute/run/status 四工具 —— 编排层
    # 归 analysis（消费既有算子，不自建算法面）。
    "app.tools.analysis_workflow_tools": "analysis",
    # 方法实验（02 任务）：inspect/plan/run/status/compare/adopt + 三个
    # 评价原语 —— 科学比较编排归 analysis（消费既有算子与注册表）。
    "app.tools.method_experiment_tools": "analysis",
    "app.tools.science_eval_primitives": "analysis",
    # 批量图册导出（atlas-batch #1871）：export_atlas —— 出版导出面。
    "app.tools.atlas_export_tools": "export",
    # Track B（M14）：图层浏览/聚合只读工具面 —— 数据获取/检视，非突变
    "app.tools.layer_browse_tools": "data_access",
    # GOAL 01：空间语义契约检视（inspect_spatial_semantics）—— 只读检视面
    "app.tools.spatial_semantics_tools": "inspection",
    "app.tools.skill_library_tools": "inspection",  # gis_skill_* 只读检索/检视
    "app.tools.local_admin": "data_access",
    "app.tools.local_osm": "data_access",
    "app.tools.local_stats": "data_access",
    "app.tools.map_view": "map_mutation",
    "app.tools.map_operations_tools": "map_mutation",   # ADR-0278 表现层操作事务
    "app.tools.scale_profile_tools": "map_mutation",   # GOAL 04 多尺度表达档案
    "app.tools.meta_tools": "debug_internal",
    "app.tools.modelops_tools": "analysis",
    "app.tools.monitoring_report": "export",
    "app.tools.nature_resources": "analysis",
    "app.tools.network_tools": "analysis",
    "app.tools.osm": "data_access",
    "app.tools.point_pattern_tools": "analysis",        # 点格局/时空交互统计
    "app.tools.sampling_tools": "analysis",             # Science V6 抽样
    "app.tools.ecology_tools": "analysis",              # Science V6 生态适宜性/景观指标
    "app.tools.plan_mode": "planning",
    "app.tools.project_tools": "debug_internal",        # workflow/audit 基建
    "app.tools.remote_sensing": "data_access",          # fetch_*；指数分析覆盖
    "app.tools.report": "export",
    "app.tools.skills": "debug_internal",
    "app.tools.skill_surface_refresh": "debug_internal",  # ADR-0100 技能面刷新
    "app.tools.spatial": "analysis",                    # heatmap/query 覆盖
    "app.tools.spatial_decision_tools": "analysis",
    "app.tools.spatial_reasoning": "analysis",
    "app.tools.semantic_tools": "analysis",             # 语义画像/模式建议
    "app.tools.flow_tools": "analysis",                 # OD 流向构建
    "app.tools.spatial_stats": "analysis",
    "app.tools.subagent": "debug_internal",
    "app.tools.templates": "rendering",                 # 模板/主题组合
    "app.tools.temporal_tools": "analysis",
    "app.tools.moving_object_tools": "analysis",  # GOAL-20261127-02 轨迹域
    "app.tools.science_temporal_tools": "analysis",  # science-v5 物候/立方体/异常
    "app.tools.temporal_view_tools": "rendering",  # GOAL 03：时态视图控制（MapSpec temporal 写面/时间轴驱动）
    "app.tools.terrain_analysis": "analysis",
    "app.tools.terrain_hydrology": "analysis",
    "app.tools.terrain_measurements": "analysis",
    "app.tools.upload_tools": "inspection",
    "app.tools.workspace_tools": "data_access",         # workspace 持久化 save/restore
    "app.tools.web_crawler": "data_access",       # 网络 POI 采集（数据获取通道）
    "app.tools.what_if_rules": "analysis",
    "app.tools.what_if_simulate": "analysis",
    # ADR-0192：时空动态仿真推演（物理机制驱动的多时相分析）
    "app.tools.simulation_tools": "analysis",
    # G34：纸面版式与图册编排（作者决定写 MapSpec.layout.print_layout）
    "app.tools.print_layout_tools": "map_mutation",
    # G45：可审阅坐标操作作者面（候选/锁定/预览/执行/结果同族 —— 派生重投影
    # 是几何变换；status/cancel 运行控制仍属本域，不再细分）
    "app.tools.coordinate_operations_tools": "transformation",
    # 两源数据关联与空间富集（durable 执行产派生表/富集图层 —— 分析产出）
    "app.tools.data_join_authoring_tools": "analysis",
    # 项目源数据刷新（检查更新/执行刷新/采纳/回滚候选 —— 数据获取生命周期）
    "app.tools.project_data_refresh_tools": "data_access",
    # 多图联合筛选与持久探索视图（facet 统计/条件求值 —— 探索分析面）
    "app.tools.linked_exploration_tools": "analysis",
    # 拓扑工作台（规则集配置/检查运行/发现/修复预览 —— 拓扑分析）
    "app.tools.topology_workbench_tools": "analysis",
    # Dorling  Cartogram 专题表达（plan/run/apply 渲染产出；inspect/status 只读、
    # export 导出名字覆盖）
    "app.tools.cartogram_tools": "rendering",
    # 任务简报（draft/update/freeze 编制成品；preflight/inspect 只读名字覆盖）
    "app.tools.brief_tools": "export",
    # 数据批量导入（计划/预检/提交/回执 —— 数据获取通道）
    "app.tools.data_import_tools": "data_access",
    # 时序动画媒体（真实渲染编码产物；capabilities 只读、result 取回名字覆盖）
    "app.tools.temporal_media_tools": "rendering",
    # 符号资产与符号图层（apply/unbind 写地图状态；list/inspect/preview 只读覆盖）
    "app.tools.cartographic_asset_tools": "map_mutation",
    # 地理配准（plan/execute 几何变换；inspect/preview/status/report 只读覆盖）
    "app.tools.georeferencing_tools": "transformation",
    # 字段计算工作台（apply 计算产出；catalog/preview/status/result 只读覆盖）
    "app.tools.field_compute_tools": "analysis",
    # 栅格采样（execute 采样分析；plan/preview/status/results 只读覆盖）
    "app.tools.raster_sampling_tools": "analysis",
    # 图幅/帧产品（plan/bind/update/unbind 写地图产品状态；status 只读覆盖）
    "app.tools.frame_product_tools": "map_mutation",
    # 制图设计规范（goal-carto-design-series）：默认 map_mutation，只读面用细分
    "app.tools.design_spec_tools": "map_mutation",
    # 地图系列作者计划（同一 goal WP3）
    "app.tools.map_series_tools": "map_mutation",
    # 系列出版生产（同一 goal WP5；durable job 提交 → 产物）
    "app.tools.series_production_tools": "export",
    # 字段语义契约（draft/assess/result 语义分析；inspect 只读覆盖）
    "app.tools.field_contracts_tools": "analysis",
    # 矢量变更提案（create 提案编制 → 数据变换域；list/diff/rebase 只读覆盖）
    "app.tools.feature_proposal_tools": "transformation",
    # 数据交付（submit 成品交付；capabilities/preview 只读覆盖）
    "app.tools.data_delivery_tools": "export",
    # 栅格表达式（apply 计算；describe/probe 只读、export 导出覆盖）
    "app.tools.raster_expression_tools": "analysis",
    # 目标修复执行编排（start/cancel 运行控制；preview 只读覆盖）
    "app.tools.goal_repair_tools": "analysis",
    # 技能任务（内部技能作业编排 —— 调试内部面）
    "app.tools.skill_job_tools": "debug_internal",
    # 专题样式创作（inspect/preview 只读、apply 写状态名字覆盖）
    "app.tools.thematic_authoring_tools": "rendering",
    # 图符资格检查和候选布局预览是只读操作；apply 可提交 MapSpec。
    "app.tools.diagram_symbol_tools": "inspection",
    # 双变量可靠性评估（apply 评估；inspect/preview 只读覆盖）
    "app.tools.bivariate_reliability_tools": "analysis",
    # 会话模型/预算状态（只读状态面）
    "app.tools.session_model_tools": "inspection",
    # 比例尺表达（inspect 只读、apply 写状态名字覆盖）
    "app.tools.scale_portrayal_tools": "rendering",
    # 注记创作（inspect 只读、author 写地图名字覆盖）
    "app.tools.label_authoring_tools": "rendering",
    # 矢量编辑（commit 变换名字覆盖既有；模块默认补 provision/lineage）
    "app.tools.feature_edit_tools": "transformation",
    # 选择/详情只读检视
    "app.tools.selection_tools": "inspection",
    # 能力生态扩展算子（分区条件分位统计/网络覆盖一致性 —— 分析）
    "app.tools.capability_ecosystem_tools": "analysis",
    # 视觉情境证据只读检视
    "app.tools.visual_context_tools": "inspection",
    # 用户决策（G29 运行中交互前门 —— 非 GIS 功能面；planning 面由审计
    # 测试钉死为计划族，交互编排归内部面）
    "app.tools.user_decision_tools": "debug_internal",
    # 混合地图导出
    "app.tools.mixed_map_export_tools": "export",
    # 本轮上下文装配回执（只读）
    "app.tools.turn_context_tools": "inspection",
}

# 工具名覆盖（模块默认之外的精确归类）
_NAME_OVERRIDES: Dict[str, str] = {
    # G34：只读能力/检视/页清单/预览/提议 → inspection；应用 → map_mutation；
    # 导出 → export（分类只是元数据投影，不改变注册与 dispatch）。
    "webgis_print_layout_capabilities": "inspection",
    "webgis_print_layout_inspect": "inspection",
    "webgis_print_layout_pages": "inspection",
    "webgis_print_layout_propose": "inspection",
    "webgis_print_layout_preview": "inspection",
    "webgis_print_layout_apply": "map_mutation",
    "webgis_print_layout_export": "export",
    # G3D04 scene_orchestration_tools：module 默认 inspection，3 个名字覆盖细分
    "apply_scene_representation": "map_mutation",
    "fly_camera": "map_mutation",
    "run_3d_analysis": "analysis",
    # gis_harness.tools：计划 2 + 组件突变 1 + 检视 2
    "webgis_map_intent": "planning",
    "webgis_map_product": "planning",
    "webgis_product_edit": "planning",
    "webgis_map_design_select": "planning",
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
    # feature_edit_tools（/goal 03）：预览/历史只读检视；提交是数据状态突变
    "vector_feature_edit_preview": "inspection",
    "vector_feature_edit_history": "inspection",
    "vector_feature_edit_commit": "transformation",
    # geocompute_tools：计划校验与 run 查询是只读检视
    "validate_execution_plan": "inspection",
    "get_execution_run": "inspection",
    "cancel_execution_run": "map_mutation",             # 运行控制是状态突变
    # workspace_tools：盘点/列表是检视；save/restore 走模块默认 data_access
    "describe_workspace": "inspection",
    "list_workspace_snapshots": "inspection",
    # modelops_tools：模型检视/资源/出处/对比是检视；cancel 是运行控制状态突变
    "modelops_list_models": "inspection",
    "modelops_inspect_model": "inspection",
    "modelops_check_compatibility": "inspection",
    "modelops_estimate_resources": "inspection",
    "modelops_compare_results": "inspection",
    "modelops_inspect_provenance": "inspection",
    "modelops_cancel_inference": "map_mutation",
    # scene_tools（ADR-0199）：plan 是只读确定性决策（证据核实 + 档位建议，无写入）
    "plan_map_scene": "inspection",
    # scene_tools（ADR-0199）：顶层 scene 事务写入是地图状态突变
    "set_map_scene": "map_mutation",
    # geoai_tools：GeoPrompt artifact 检视是只读检视
    "geoai_prompt_artifact_inspect": "inspection",
    # composition_tools（ADR-0214 D6）：契约落盘走 SetLayoutIntent —— 状态突变
    "webgis_apply_composition": "map_mutation",
    # rs_cube_tools：立方体盘点/描述符查询与光学×SAR 配对计划是只读（绝不静默重采样）
    "rs_cube_describe": "inspection",
    "rs_cube_align": "inspection",
    # context_revalidation_tools（ADR-0215）：跨会话续接 = 恢复既有工作上下文
    #（类比 workspace save/restore 的 data_access 归属）
    "webgis_context_bind_mission": "data_access",
    # coordinate_operations_tools：状态/收据只读
    "webgis_coordop_status": "inspection",
    "webgis_coordop_result": "inspection",
    # cartogram_tools：只读检视/状态 + 导出
    "webgis_dorling_cartogram_inspect": "inspection",
    "webgis_dorling_cartogram_status": "inspection",
    "webgis_dorling_cartogram_export": "export",
    # brief_tools：预检/证据检视只读
    "brief_preflight": "inspection",
    "brief_inspect_evidence": "inspection",
    # temporal_media_tools：能力查询只读；result 是产物取回
    "webgis_temporal_media_capabilities": "inspection",
    "webgis_temporal_media_result": "export",
    # cartographic_asset_tools：资产/图层只读面
    "webgis_symbol_asset_list": "inspection",
    "webgis_symbol_layer_inspect": "inspection",
    "webgis_symbol_layer_preview": "inspection",
    # georeferencing_tools：只读面
    "webgis_georef_inspect": "inspection",
    "webgis_georef_preview": "inspection",
    "webgis_georef_status": "inspection",
    "webgis_georef_report": "inspection",
    # field_compute_tools：只读面
    "field_compute_catalog": "inspection",
    "field_compute_preview": "inspection",
    "field_compute_status": "inspection",
    "field_compute_result": "inspection",
    # raster_sampling_tools：只读面
    "webgis_raster_sampling_plan": "inspection",
    "webgis_raster_sampling_preview": "inspection",
    "webgis_raster_sampling_status": "inspection",
    "webgis_raster_sampling_results": "inspection",
    # frame_product_tools：状态只读
    "frame_product_status": "inspection",
    # field_contracts_tools：契约检视只读
    "webgis_field_contract_inspect": "inspection",
    # feature_proposal_tools：只读面
    "vector_change_proposal_list": "inspection",
    "vector_change_proposal_diff": "inspection",
    "vector_change_proposal_rebase_preview": "inspection",
    # data_delivery_tools：能力/预览只读
    "delivery_capabilities": "inspection",
    "preview_data_delivery": "inspection",
    # raster_expression_tools：只读面 + 导出
    "webgis_raster_expression_describe": "inspection",
    "webgis_raster_expression_probe": "inspection",
    "webgis_raster_expression_export": "export",
    # goal_repair_tools：修复预览只读
    "webgis_goal_repair_preview": "inspection",
    # thematic_authoring_tools：只读面 + 样式应用写状态
    "webgis_thematic_style_inspect": "inspection",
    "webgis_thematic_style_preview": "inspection",
    "webgis_thematic_style_apply": "map_mutation",
    # bivariate_reliability_tools：只读面
    "webgis_bivariate_reliability_inspect": "inspection",
    "webgis_bivariate_reliability_preview": "inspection",
    # scale_portrayal_tools：检视只读 + 应用写状态
    "webgis_inspect_scale_portrayal": "inspection",
    "webgis_apply_scale_portrayal": "map_mutation",
    "webgis_diagram_symbol_apply": "map_mutation",
    # label_authoring_tools：检视只读 + 注记写地图
    "webgis_label_inspect": "inspection",
    "webgis_label_author": "map_mutation",
    # feature_edit_tools 残余：数据集供给是数据获取；血缘是只读检视
    "vector_dataset_provision": "data_access",
    "vector_feature_lineage": "inspection",
    # 共享拓扑约束面（goal-gis-topology-authoring）：三个只读工具
    "inspect_edit_constraints": "inspection",
    "get_impact": "inspection",
    "revalidate": "inspection",
    # design_spec_tools：规范权威只读面（create/version/apply 写状态走模块默认）
    "webgis_design_spec_list": "inspection",
    "webgis_design_spec_inspect": "inspection",
    "webgis_design_spec_preview": "inspection",
    "webgis_map_design_extract": "inspection",
    "webgis_map_design_recommend": "inspection",
    "webgis_map_design_check_bindings": "inspection",
    # map_series_tools：只读面（create/add_member/propagation_apply 走模块默认）
    "webgis_map_series_list": "inspection",
    "webgis_map_series_inspect": "inspection",
    "webgis_map_series_consistency": "inspection",
    "webgis_map_series_propagation_preview": "inspection",
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
