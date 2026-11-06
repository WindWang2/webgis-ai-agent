"""Analysis Workflow 族模板 —— 5 个高需求多步 GIS 工作流族的步骤蓝图。

红线（对齐 workflow_families 的「确定性投影、不复制」纪律）：

- 步骤只**引用**既有注册工具（tool_name 必须真实存在——``validate_families``
  在注册面可用时编译期校验，悬空引用 fatal）；
- 数据角色/重算维度引用 workflow_schema 词表；科学前置引用
  scientific_preconditions 注册表（``grid_identity_match`` 等既有 id）；
- 不复制算子语义：单位合同/前置声明是对既有工具行为的**声明**，
  权威仍在工具 apply_contract 与算法层。

五族（对应任务书 M4–M6）：

1. ``admin_facility_distribution``  POI→行政区空间聚合→计数；密度仅显式分母
   （area 密度合法——几何自足；per-capita 缺分母 typed skipped）。
2. ``service_accessibility``        路网等时圈→边界求交→覆盖统计；
   无路网 typed blocked（绝无「直线 15 分钟」冒充；buffer 替代须披露差异）。
3. ``hazard_exposure``              危险源缓冲→受体求交→暴露统计（阈值警示）。
4. ``temporal_change``              双期矢量差分→变化面积/百分比（年份对齐门）。
5. ``statistical_zones``            H3 网格聚合→率/热点（分母与多重比较披露）。
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Tuple

from app.services.gis_harness.analysis_workflows.contracts import (
    UNIT_VOCABULARY,
    AnalysisStepSpec,
    AnalysisWorkflowContract,
    DataRoleBinding,
    ScienceContext,
    validate_workflow_contract,
)
from app.services.gis_harness.workflow_schema import DataRoleRequirement

#: 族 id 词表（稳定契约；进入 typed blocked 码与测试断言）。
FAMILY_IDS = (
    "admin_facility_distribution",
    "service_accessibility",
    "hazard_exposure",
    "temporal_change",
    "statistical_zones",
    "raster_zonal_summary",
    "network_proximity",
)

_FAMILY_LABELS: Dict[str, str] = {
    "admin_facility_distribution": "行政区设施分布分析",
    "service_accessibility": "公共服务可达性分析",
    "hazard_exposure": "风险暴露分析",
    "temporal_change": "多期区域变化分析",
    "statistical_zones": "自适应统计分区分析",
    "raster_zonal_summary": "栅格指数分区汇总分析",
    "network_proximity": "最近设施网络距离分析",
}


def _step(step_id: str, tool: str, inputs: Dict[str, str], *,
          capability: str = "", params: Optional[Dict[str, Any]] = None,
          units: Optional[Dict[str, str]] = None,
          deps: Tuple[str, ...] = (), preconds: Tuple[str, ...] = (),
          outputs: Tuple[str, ...] = (), fallback: str = "",
          optional: bool = False,
          semantic_contract: Optional[Dict[str, str]] = None,
          ) -> AnalysisStepSpec:
    return AnalysisStepSpec(
        step_id=step_id, tool_name=tool, capability=capability,
        role_inputs=dict(inputs), step_params=dict(params or {}),
        unit_contract=dict(units or {}), depends_on=deps,
        preconditions=preconds, output_roles=outputs,
        fallback=fallback, optional=optional,
        semantic_contract=dict(semantic_contract or {}),
    )


# ── 族模板（steps 蓝图 + 角色需求投影）───────────────────────────────────

_STEPS: Dict[str, Tuple[AnalysisStepSpec, ...]] = {
    "admin_facility_distribution": (
        # 计数主步骤：intersects 多方属约定（点在边界上可计入多个区）——
        # 由本族 required_disclosure 显式披露，验收断言「总数=分区合计」
        # 在该约定下允许 >，且必须与披露一致（不静默放大分母）。
        _step("zones_count", "spatial_aggregate",
              {"points": "role:subject", "polygons": "role:boundary"},
              capability="admin_aggregation",
              params={"count_field": "facility_count"},
              semantic_contract={"points": "geometry_kind:point"},
              outputs=("aggregate_zones",)),
        # 面积密度：几何自足分母（km²），无需外部数据，合法；
        # 单位合同钉死（denominator_kind=area 的输出单位约定）。
        _step("area_density", "spatial_aggregate",
              {"points": "role:subject", "polygons": "role:boundary"},
              capability="admin_aggregation",
              params={"denominator_kind": "area"},
              deps=("zones_count",),
              semantic_contract={"points": "geometry_kind:point"},
              outputs=("density_zones",)),
        # 每万人密度：分母 = 带人口字段的区面层（role:denominator 绑定，
        # qualifier.field 指明字段名）。缺失 → typed skipped，
        # 绝不拿 area 密度或原始计数冒充（ADR-0099）。
        _step("per_capita_rate", "spatial_aggregate",
              {"points": "role:subject", "polygons": "role:denominator"},
              capability="admin_aggregation",
              params={"denominator_kind": "field", "denominator": "population"},
              deps=("zones_count",),
              semantic_contract={"points": "geometry_kind:point"},
              optional=True,
              outputs=("rate_zones",)),
    ),
    "service_accessibility": (
        # ADR-0282（断点2）：族默认 exclude_unreachable=True——脱离路网的
        # 设施 5m 占位面不进覆盖统计（计数进 stats，部分失败显性化）；
        # 需要占位面原样输出的调用方以参数覆盖回 False（工具默认保持
        # master 行为）。
        _step("isochrone", "isochrone_network",
              {"network_layer": "role:network", "facilities": "role:subject"},
              capability="service_area",
              params={"travel_time": 15.0, "mode": "walking",
                      "exclude_unreachable": True},
              units={"travel_time": "min"},
              semantic_contract={"facilities": "geometry_kind:point"},
              outputs=("service_area",)),
        _step("coverage_zones", "overlay_analysis",
              {"layer_a": "step:isochrone", "layer_b": "role:boundary"},
              capability="geometry_overlay",
              params={"how": "intersection"},
              deps=("isochrone",),
              outputs=("coverage_zones",)),
        _step("coverage_population", "spatial_aggregate",
              {"points": "role:population", "polygons": "step:coverage_zones"},
              capability="admin_aggregation",
              deps=("coverage_zones",),
              optional=True,
              outputs=("covered_population",)),
    ),
    # 链③（ADR-0282 D4）：最近设施网络距离 → 行政汇总。
    # subject=需求点（incidents，如居民点/小区），facility=服务设施（医院/
    # 消防站）；汇总 rate = Σtotal_cost / 路线数 = 区均网络成本（均值语义，
    # aggregate_with_denominator 的 count 分母通道——不是伪装率）。
    "network_proximity": (
        _step("nearest_facility", "network_closest_facility",
              {"network": "role:network", "incidents": "role:subject",
               "facilities": "role:facility"},
              capability="closest_facility",
              params={"profile": "driving", "number_to_find": 1},
              semantic_contract={"incidents": "geometry_kind:point",
                                 "facilities": "geometry_kind:point"},
              outputs=("nearest_routes",)),
        _step("accessibility_summary", "spatial_aggregate",
              {"points": "step:nearest_facility", "polygons": "role:boundary"},
              capability="admin_aggregation",
              params={"denominator_kind": "count",
                      "numerator_field": "total_cost"},
              deps=("nearest_facility",),
              outputs=("district_accessibility",)),
    ),
    "hazard_exposure": (
        _step("hazard_buffer", "buffer_analysis",
              {"geojson": "role:hazard"},
              capability="geometry_buffer",
              params={"distance": 1000.0, "unit": "m"},
              units={"distance": "m"},
              outputs=("hazard_zone",)),
        # 暴露清单：受体点 within 危险源缓冲区（spatial_join 语义——
        # overlay(polygon×point) 因 keep_geom_type 丢点几何，不是本步骤
        # 的正确算子）。M:N 基数（重叠缓冲区内一受体多行）由族披露声明。
        _step("exposure_intersect", "spatial_join",
              {"left_layer": "role:receptor",
               "right_layer": "step:hazard_buffer"},
              capability="spatial_join",
              params={"join_type": "inner", "predicate": "within"},
              deps=("hazard_buffer",),
              outputs=("exposed_receptors",)),
    ),
    "temporal_change": (
        # 矢量双期差分：target − baseline；orchestrator 的 temporal 门只裁决
        # 年份画像（存在/顺序/不同——AWF_TEMPORAL_MISMATCH）；CRS/覆盖范围
        # 对齐由内层工具的 CRS 纪律与执行层把关，本层不重复实现。
        _step("change_diff", "overlay_analysis",
              {"layer_a": "role:target_time", "layer_b": "role:baseline"},
              capability="geometry_overlay",
              params={"how": "difference"},
              outputs=("change_areas",)),
        # 变化面积/百分比：把差分面回投到目标期做占比（identity 保留
        # 目标期完整几何 + 差分标记），统计口径由 stats 投影披露。
        _step("change_share", "overlay_analysis",
              {"layer_a": "role:target_time", "layer_b": "step:change_diff"},
              capability="geometry_overlay",
              params={"how": "identity"},
              deps=("change_diff",),
              outputs=("change_share_zones",)),
    ),
    "statistical_zones": (
        _step("grid_stats", "h3_binning",
              {"geojson": "role:subject"},
              capability="grid_binning",
              params={"resolution": 8, "stat_method": "count"},
              preconds=("point_support_required",),
              # point_support_required 前置的执行期对偶（R1 评审 F-5：
              # 声明接线——非点几何在执行期被 typed 拒而非静默质心化）
              semantic_contract={"geojson": "geometry_kind:point"},
              outputs=("grid_zones",)),
        # 网格率（ADR-0282 断点1 修复）：真率语义走显式分母通道——
        # 分母=网格内 subject 计数（grid_stats 输出的 count 字段，网格身份
        # 一致由 grid_identity_match 前置保证），分子=网格内 population 计数
        # → rate=人口/设施（人均服务负荷；设施/人口为其倒数，numerator/
        # denominator 两列都在，消费方可显式取用）。不再是改名计数。
        _step("grid_rate", "spatial_aggregate",
              {"points": "role:population", "polygons": "step:grid_stats"},
              capability="admin_aggregation",
              params={"denominator_kind": "field", "denominator": "count"},
              deps=("grid_stats",),
              preconds=("grid_identity_match",),
              optional=True,
              outputs=("rate_grid",)),
        _step("hotspot", "hotspot_analysis",
              {"geojson": "step:grid_stats"},
              capability="hotspot",
              params={"value_field": "count"},
              deps=("grid_stats",),
              optional=True,
              outputs=("hotspot_zones",)),
    ),
    # 链②（ADR-0282 D4）：栅格指数 → 行政分区统计 → 矢量结果。
    # subject 是 raster 角色（role_kind="raster"——ref/路径直通，不水合）；
    # 掩膜语义由 zonal 的窗口读隐式完成（逐 zone 只读自己的像素窗+多边形
    # 掩膜），不引入独立 mask 工具；nodata 以 valid_pixel_coverage 逐 zone
    # 披露（raster_ops 断点 9 修复）。
    "raster_zonal_summary": (
        _step("resample", "raster_resample",
              {"raster_path": "role:subject"},
              capability="raster_resample",
              params={"target_resolution": None, "target_crs": None,
                      "resampling": "bilinear", "resolution_unit": "crs"},
              optional=True,
              outputs=("resampled_raster",)),
        _step("zonal_stats", "zonal_stats",
              {"geojson": "role:boundary",
               "raster_path": "step:resample"},
              capability="zonal_statistics",
              deps=("resample",),
              outputs=("zonal_zones",)),
    ),
}

#: 各族数据角色需求（复用 DataRoleRequirement 词表模型；required+block
#: 的角色缺失 → orchestrator typed blocked）。
_ROLE_REQUIREMENTS: Dict[str, List[DataRoleRequirement]] = {
    "admin_facility_distribution": [
        DataRoleRequirement(role="subject", required=True,
                            geometry_kinds=["point"],
                            missing_policy="block",
                            note="分析主体 POI（如学校）"),
        DataRoleRequirement(role="boundary", required=True,
                            geometry_kinds=["polygon"],
                            missing_policy="block",
                            note="行政边界（聚合骨架）"),
        DataRoleRequirement(role="denominator", required=False,
                            missing_policy="degrade",
                            note="人口分母——缺失时 per-capita 步骤 typed skipped，只给计数/面积密度"),
    ],
    "service_accessibility": [
        DataRoleRequirement(role="subject", required=True,
                            geometry_kinds=["point"],
                            missing_policy="block",
                            note="设施点"),
        DataRoleRequirement(role="network", required=True,
                            geometry_kinds=["line", "network"],
                            missing_policy="block",
                            note="真实路网——缺失时不得给等时数值结论（AWF_NO_NETWORK_FOR_ACCESSIBILITY）"),
        DataRoleRequirement(role="boundary", required=True,
                            geometry_kinds=["polygon"],
                            missing_policy="block"),
        DataRoleRequirement(role="population", required=False,
                            missing_policy="degrade",
                            note="覆盖人口——缺失时只给覆盖面积/占比"),
    ],
    "hazard_exposure": [
        DataRoleRequirement(role="hazard", required=True, missing_policy="block",
                            note="危险源"),
        DataRoleRequirement(role="receptor", required=True, missing_policy="block",
                            note="承灾体（建筑/人口/资产）"),
    ],
    "temporal_change": [
        DataRoleRequirement(role="baseline", required=True, missing_policy="block",
                            note="基期数据（须带时间标识）"),
        DataRoleRequirement(role="target_time", required=True, missing_policy="block",
                            note="目标期数据（须带时间标识）"),
    ],
    "statistical_zones": [
        DataRoleRequirement(role="subject", required=True,
                            geometry_kinds=["point"], missing_policy="block"),
        DataRoleRequirement(role="population", required=False,
                            missing_policy="degrade",
                            note="网格分母——缺失时只给计数网格，不给率"),
    ],
    # 链②：subject 为栅格主题（role_kind=raster——DataRoleBinding 的
    # role_kind 由本族 build 时按 geometry_kinds=["raster"] 自动铺默认）。
    "raster_zonal_summary": [
        DataRoleRequirement(role="subject", required=True,
                            geometry_kinds=["raster"], missing_policy="block",
                            note="栅格指数图层（NDVI/DEM/降雨等——ref 或本地路径）"),
        DataRoleRequirement(role="boundary", required=True,
                            geometry_kinds=["polygon"], missing_policy="block",
                            note="统计分区（行政区/流域等面域）"),
    ],
    "network_proximity": [
        DataRoleRequirement(role="subject", required=True,
                            geometry_kinds=["point"], missing_policy="block",
                            note="需求点（incidents——居民点/小区/事故点）"),
        DataRoleRequirement(role="facility", required=True,
                            geometry_kinds=["point"], missing_policy="block",
                            note="服务设施点（医院/消防站/学校）"),
        DataRoleRequirement(role="network", required=True,
                            geometry_kinds=["line", "network"],
                            missing_policy="block",
                            note="真实路网——缺失时不得给网络距离结论"),
        DataRoleRequirement(role="boundary", required=True,
                            geometry_kinds=["polygon"], missing_policy="block",
                            note="汇总分区（行政区）"),
    ],
}

#: 各族风险与披露（进入契约 risk_notes / 必披露面）。
_FAMILY_NOTES: Dict[str, Tuple[str, ...]] = {
    "admin_facility_distribution": (
        "intersects 多方属：点落在共边界上会计入多个相邻区——区计数合计可大于去重总数；"
        "结论必须与该约定一致，不得把合计当独立设施数",
    ),
    "service_accessibility": (
        "等时圈语义=路网 Dijkstra 可达（速度表近似）；无路网时可用 buffer 替代但科学含义不同"
        "（欧氏距离≠通达时间），替代结果必须带 proxy 披露",
    ),
    "hazard_exposure": (
        "缓冲半径是暴露筛查代理——真实影响场随危险源类型/地形/气象变化；"
        "阈值结论应给敏感性区间而非单值断言",
        "暴露清单是 M:N 空间连接（重叠缓冲区内一个受体出现多行）——"
        "受体计数不得把清单行数当独立受体数（需按受体 id 去重口径披露）",
    ),
    "temporal_change": (
        "双期差分要求年份/CRS/覆盖范围对齐；分类口径不一致时面积变化不可比"
        "（必须 typed 拒绝而非静默差分）",
    ),
    "statistical_zones": (
        "网格率要求分子分母同一网格身份（grid_identity_match）；"
        "热点检验受多重比较影响——小样本/低计数网格的显著性须按伪 p 披露",
    ),
    "raster_zonal_summary": (
        "zonal 统计的掩膜语义=逐 zone 像素窗+多边形掩膜（隐式掩膜，非独立"
        "mask 步骤）；nodata 以逐 zone valid_pixel_coverage 披露——覆盖率低的"
        "分区均值不可与全有效分区同权解读",
        "栅格重采样会改变像元聚合尺度（bilinear 平滑/nearest 保序）——"
        "默认直接统计原始栅格；仅显式 target_resolution 启用重采样，"
        "分辨率严格使用目标 CRS 单位；分辨率变化即统计口径变化，进入步骤指纹触发重算",
    ),
    "network_proximity": (
        "最近设施距离=路网 Dijkstra 通行成本（默认 travel_time 权重）——"
        "不是欧氏距离；速度表近似与端点吸附容差由算法层披露",
        "跨区路线在 intersects 约定下会计入其经过的每个区（M:N 多方属）——"
        "区均成本合计不可当全区独立均值直接平均",
        "无匹配路线的需求点由 unmatched_demand_count/ids 披露，"
        "区均成本仅描述匹配需求；工作流保持 partial，不作为完整总体成品",
    ),
}


def family_exists(family: str) -> bool:
    return family in FAMILY_IDS


def family_label(family: str) -> str:
    return _FAMILY_LABELS.get(family, family)


def role_requirements(family: str) -> List[DataRoleRequirement]:
    """族角色需求（只读投影；调用方不得原地改）。"""
    return list(_ROLE_REQUIREMENTS.get(family, []))


def build_contract(
    family: str,
    *,
    workflow_id: str,
    bindings: Optional[Dict[str, DataRoleBinding]] = None,
    recipe_refs: Tuple[str, ...] = (),
    params_overrides: Optional[Dict[str, Dict[str, Any]]] = None,
    science: Optional[ScienceContext] = None,
    include_optional_steps: bool = True,
) -> AnalysisWorkflowContract:
    """族模板 → 契约实例（确定性：同 family+bindings+overrides 同契约）。

    params_overrides：调用方参数覆盖（如 travel_time=30）——仅允许覆盖
    模板中已存在的参数键（防拼错参数名 fail-open 跑默认值）。
    """
    if family not in FAMILY_IDS:
        raise KeyError(family)
    # data_contracts 自动铺满族角色需求（未绑定角色 = 空 binding，由
    # orchestrator 裁决 typed blocked/skipped）；调用方绑定显式覆盖。
    contracts: Dict[str, DataRoleBinding] = {
        req.role: DataRoleBinding(role=req.role)
        for req in _ROLE_REQUIREMENTS.get(family, [])
    }
    contracts.update(dict(bindings or {}))
    # ADR-0282 D3：角色需求的 geometry_kinds 表达 raster/table 载荷类型时，
    # **模板占位绑定**（无 ref）铺默认 role_kind，让契约自带类型事实；
    # 调用方显式绑定不覆写——role_kind 与需求不符由 orchestrator plan 门
    # typed 裁决（调用方声明的事实优先，绝不静默改写）。
    for req in _ROLE_REQUIREMENTS.get(family, []):
        binding = contracts.get(req.role)
        if binding is None or binding.ref or binding.role_kind != "vector":
            continue
        kinds = set(req.geometry_kinds or ())
        if kinds and kinds <= {"raster", "table"}:
            binding.role_kind = next(iter(kinds))
    steps: List[AnalysisStepSpec] = []
    overrides = params_overrides or {}
    pruned_role_fallbacks: Dict[str, str] = {}
    for base in _STEPS[family]:
        explicit_resample = (base.step_id == "resample" and "target_resolution" in overrides.get(base.step_id, {}))
        if (base.optional and not include_optional_steps) or (base.step_id == "resample" and not explicit_resample):
            # ADR-0282 D4：optional 步剔除 → 记录其 role: 输入的首个角色，
            # 供下游 step: 引用回接（rewire——契约 DAG 保持闭合且语义等价：
            # 下游直接消费原始角色而非被剔除的中间产物）。
            for source in base.role_inputs.values():
                if source.startswith("role:"):
                    pruned_role_fallbacks[base.step_id] = source
                    break
            continue
        step = base.model_copy(deep=True)
        step_over = overrides.get(step.step_id)
        if step_over:
            unknown = set(step_over) - set(step.step_params)
            if unknown:
                raise ValueError(
                    f"步骤 {step.step_id} 参数覆盖越界: {sorted(unknown)}"
                    f"（合法键: {sorted(step.step_params)}）")
            # P2-2：单位类参数覆盖必须联动 unit_contract——否则证据面（envelope
            # /receipt 的 units）声称旧单位而计算用新单位（声明与计算脱节）。
            if "unit" in step_over and step.unit_contract:
                new_unit = str(step_over["unit"])
                if new_unit not in UNIT_VOCABULARY:
                    raise ValueError(
                        f"步骤 {step.step_id} 单位越界: {new_unit}"
                        f"（词表: {UNIT_VOCABULARY}）")
                step.unit_contract = {
                    param: new_unit for param in step.unit_contract}
            step.step_params.update(step_over)
        if step.step_id == "resample":
            import math
            resolution = step.step_params.get("target_resolution")
            if (isinstance(resolution, bool) or not isinstance(resolution, (int, float))
                    or not math.isfinite(resolution) or resolution <= 0):
                raise ValueError("resample requires a finite positive target_resolution in target CRS units")
            if step.step_params.get("resolution_unit") != "crs":
                raise ValueError("workflow resample resolution_unit must be crs (target CRS units)")
        steps.append(step)
    # optional 步骤的依赖若被剔除，须同步修剪（契约 DAG 仍须闭合）
    present = {s.step_id for s in steps}
    for step in steps:
        step.depends_on = tuple(d for d in step.depends_on if d in present)
        for param, source in list(step.role_inputs.items()):
            if not source.startswith("step:"):
                continue
            upstream = source.removeprefix("step:")
            if upstream not in present and upstream in pruned_role_fallbacks:
                step.role_inputs[param] = pruned_role_fallbacks[upstream]
    # ADR-0282 D4（R1 评审 F-2 修复）：denominator_kind=field 的步骤，分母
    # 字段名**只**从 role:denominator 绑定的 qualifier.field 解析（该角色的
    # 语义就是"带分母字段的图层"）——其它角色的 qualifier（subject 的
    # field、population 的 fields）绝不可劫持分母列名（评审实测复现：
    # population.fields 声明曾致 grid_rate 假 skip、subject.field 曾把
    # per_capita 分母静默改列）。无画像时保持模板默认（行为兼容 master）。
    # fields 清单的 plan 期校验在 orchestrator（同样只认 denominator 角色）。
    for step in steps:
        if step.step_params.get("denominator_kind") != "field":
            continue
        binding = contracts.get("denominator")
        if binding is None or not binding.ref:
            continue
        field = binding.qualifier.get("field")
        if isinstance(field, str) and field:
            step.step_params["denominator"] = field
    return AnalysisWorkflowContract(
        workflow_id=workflow_id,
        family=family,
        label_zh=family_label(family),
        recipe_refs=recipe_refs,
        steps=tuple(steps),
        data_contracts=contracts,
        science_context=science or ScienceContext(),
        risk_notes=_FAMILY_NOTES.get(family, ()),
    )


def validate_families(
    tool_exists: Callable[[str], bool],
) -> List[str]:
    """编译期全族校验（悬空工具引用 fatal；返回警告列表）。

    在注册面可用的测试/启动钩子里调用；工具注册缺失 = 模板腐烂，
    绝不让它静默落到 run 期才报 AWF_TOOL_UNREGISTERED。
    """
    warnings: List[str] = []
    for family in FAMILY_IDS:
        contract = build_contract(family, workflow_id=f"awf.__selfcheck__.{family}")
        warnings.extend(validate_workflow_contract(
            contract, tool_exists=tool_exists, family_exists=family_exists))
    return warnings


def visualization_hints(family: str) -> Tuple[str, ...]:
    """结果元数据级制图建议（只作参考；裁决权威在 converter/planner）。"""
    return {
        "admin_facility_distribution": (
            "aggregate_zones → administrative_choropleth（计数）",
            "density_zones → administrative_choropleth（面积密度）",
        ),
        "service_accessibility": (
            "coverage_zones → polygon_overlay（覆盖区）",
            "isochrone → service_area_outline",
        ),
        "hazard_exposure": (
            "hazard_zone → buffer_outline",
            "exposed_receptors → exposure_inventory",
        ),
        "temporal_change": (
            "change_areas → bitemporal_change",
        ),
        "statistical_zones": (
            "grid_zones → h3_hexbin",
            "hotspot_zones → hotspot_significance",
        ),
        "raster_zonal_summary": (
            "zonal_zones → administrative_choropleth（指数均值/分区统计）",
        ),
        "network_proximity": (
            "nearest_routes → flow_od_arc / line（需求点→设施路线）",
            "district_accessibility → administrative_choropleth（区均网络成本）",
        ),
    }.get(family, ())
