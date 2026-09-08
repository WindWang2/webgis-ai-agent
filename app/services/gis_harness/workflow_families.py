"""Workflow Family / Composite / Scenario —— Recipe V3 分层组合（Goal V3）。

把平铺 recipe 升级为四层结构，覆盖靠**组合**生成而不是无上限复制：

    Atomic Recipe      既有 CartographyRecipe（164 个，行为不变）
    ───────────────── 以下为本模块新增层 ─────────────────
    Workflow Family    同一 (domain, workflow_family) 的 recipe 簇 ——
                       从 RecipeRegistry **派生**（不另建第二事实源），
                       聚合成员的 capability / 数据角色 / 义务 / 制图；
    Composite Recipe   跨族组合：base 产品族 + 条件并入的 supporting 层
                       （数据资格/语义信号满足才并入，如显著性热点）；
    Scenario Template  场景模板：主体/范围/本体任务信号 → 家族偏好 +
                       按数据资格排序的制图候选 + 终验期望 + minimal 兜底。

红线：

- Family 是 RecipeRegistry 的确定性投影：成员变化 → family 自动变化；
  Composite / Scenario 只引用既有 recipe / family / 本体任务 id，全部经
  registry_validation 与 validate_families 编译期校验，悬空引用 fatal；
- 不复制算法实现：组合层只声明「何时并入谁」，能力并集由成员导出；
- planned 能力/未注册 recipe 不得进入组合（校验期拦下）；
- 全部确定性：同 registry 同投影，零 LLM、零 I/O。
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel

#: 分层组合版本（进入指纹）。
FAMILY_LAYER_VERSION = 3


class WorkflowFamily(BaseModel):
    """workflow family：recipe 簇的确定性投影（聚合成员事实，不新造事实）。"""
    family_id: str                       # "<domain>.<workflow_family>" 或 "seed.<recipe_id>"
    domain: str
    label: str
    description: str = ""
    member_recipe_ids: Tuple[str, ...] = ()
    # 家族的 intent task 集合（成员并集；与 ontology family_triggers 联动）
    intent_tasks: Tuple[str, ...] = ()
    # V3：关联本体任务 id（自动链接：成员 intent_tasks ∩ 任务 family_triggers）
    ontology_tasks: Tuple[str, ...] = ()
    # 成员聚合投影（注册期计算）
    capabilities: Tuple[str, ...] = ()
    map_models: Tuple[str, ...] = ()
    data_roles: Tuple[str, ...] = ()
    obligations: Tuple[str, ...] = ()
    keywords_zh: Tuple[str, ...] = ()
    keywords_en: Tuple[str, ...] = ()
    is_seed_family: bool = False


class CompositeRecipe(BaseModel):
    """跨族组合：base + 条件并入的 supporting 层（组合生成覆盖，不复制）。"""
    composite_id: str
    label_zh: str
    label_en: str = ""
    description: str = ""
    base_recipe_id: str                  # 主产品族：verdict/模板语义的承载者
    supporting_recipe_ids: Tuple[str, ...] = ()   # 条件并入层
    trigger_ontology_tasks: Tuple[str, ...] = ()  # 语义触发（本体任务 id）
    trigger_cartography: Tuple[str, ...] = ()     # 显式形态信号触发
    # supporting 层并入的数据资格条件（资格状态词表；空 = 跟随 base 一并产出）
    supporting_requires_states: Tuple[str, ...] = ()
    disclosures: Tuple[str, ...] = ()


class ScenarioTemplate(BaseModel):
    """场景模板：语义信号 → 家族/制图候选/终验期望/minimal 兜底。"""
    scenario_id: str
    label_zh: str
    label_en: str = ""
    description: str = ""
    # 匹配信号（全部命中才激活；空集 = 永不激活，校验拦截）
    match_subjects: Tuple[str, ...] = ()      # 主体词（zh 子串，如 小学/医院）
    match_ontology_tasks: Tuple[str, ...] = ()
    match_scope_level: Tuple[str, ...] = ()   # city/district/...
    # 规划偏好
    preferred_families: Tuple[str, ...] = ()
    candidate_recipes: Tuple[str, ...] = ()   # 有序制图候选（数据资格裁决取位）
    # 终验期望（final map verification 消费）
    expected_components: Tuple[str, ...] = ()
    expects_chart: bool = False
    expects_admin_context: bool = False
    # minimal 兜底（Fallback V3 的场景侧声明）
    minimal_disclosure: str = ""


# ── Family 派生（RecipeRegistry 的确定性投影）────────────────────────────


def build_families_from_registry(recipe_registry: Any) -> List[WorkflowFamily]:
    """从 RecipeRegistry 派生 family 投影（同 registry 同输出）。

    - V2 recipe：按 (workflow.domain, workflow.workflow_family) 聚簇；
    - V1 seed：无 workflow —— 每个 seed 自成单成员 family（seed.<id>），
      它们本来就是产品族；强行为家族会伪造第二事实源。
    """
    groups: Dict[Tuple[str, str], Dict[str, Any]] = {}
    seed_groups: Dict[str, Dict[str, Any]] = {}
    for rid in recipe_registry.all_ids:
        recipe = recipe_registry.get(rid)
        if recipe is None:
            continue
        wf = recipe.workflow
        if wf is None:
            g = seed_groups.setdefault(rid, {
                "domain": "seed", "label": rid, "members": [], "tasks": set(),
            })
            g["members"].append(rid)
            g["tasks"].update(recipe.intent_tasks)
            continue
        key = (wf.domain or "general", wf.workflow_family or rid)
        g = groups.setdefault(key, {
            "domain": key[0], "label": key[1], "members": [], "tasks": set(),
        })
        g["members"].append(rid)
        g["tasks"].update(recipe.intent_tasks)

    ontology = None
    try:
        from app.services.gis_harness.gis_ontology import get_task_ontology
        ontology = get_task_ontology()
    except Exception:  # noqa: BLE001 - ontology 缺席时不链接（纯派生层）
        ontology = None

    families: List[WorkflowFamily] = []
    for key in sorted(groups):
        g = groups[key]
        members = sorted(g["members"])
        tasks = sorted(g["tasks"])
        caps: set = set()
        models: set = set()
        roles: set = set()
        obligations: set = set()
        kw_zh: set = set()
        kw_en: set = set()
        for rid in members:
            recipe = recipe_registry.get(rid)
            assert recipe is not None
            wf = recipe.workflow
            assert wf is not None
            caps.update(recipe.preferred_analysis)
            caps.update(recipe.optional_analysis)
            for caps_list in (recipe.task_optional_analysis or {}).values():
                caps.update(caps_list)
            if recipe.primary_cartography:
                models.add(recipe.primary_cartography)
            models.update(recipe.secondary_cartography)
            for req in wf.data_roles:
                roles.add(req.role)
            for obl in wf.obligations:
                obligations.add(obl.obligation_id)
            kw_zh.update(wf.keywords_zh)
            kw_en.update(wf.keywords_en)
        linked: set = set()
        if ontology is not None:
            for tid in ontology.all_ids:
                desc = ontology.get(tid)
                assert desc is not None
                if set(desc.family_triggers) & set(tasks):
                    linked.add(tid)
        families.append(WorkflowFamily(
            family_id=f"{key[0]}.{key[1]}",
            domain=key[0],
            label=key[1],
            description=f"派生自 {len(members)} 个 recipe 的 workflow family",
            member_recipe_ids=tuple(members),
            intent_tasks=tuple(tasks),
            ontology_tasks=tuple(sorted(linked)),
            capabilities=tuple(sorted(caps)),
            map_models=tuple(sorted(models)),
            data_roles=tuple(sorted(roles)),
            obligations=tuple(sorted(obligations)),
            keywords_zh=tuple(sorted(kw_zh)),
            keywords_en=tuple(sorted(kw_en)),
        ))
    for rid in sorted(seed_groups):
        g = seed_groups[rid]
        linked: set = set()
        if ontology is not None:
            for tid in ontology.all_ids:
                desc = ontology.get(tid)
                assert desc is not None
                if set(desc.family_triggers) & g["tasks"]:
                    linked.add(tid)
        families.append(WorkflowFamily(
            family_id=f"seed.{rid}",
            domain="seed",
            label=rid,
            description="V1 seed 产品族（单成员 family，行为与历史一致）",
            member_recipe_ids=(rid,),
            intent_tasks=tuple(sorted(g["tasks"])),
            ontology_tasks=tuple(sorted(linked)),
            is_seed_family=True,
        ))
    return families


# ── Composite 登记表（审定工件；引用必须真实存在）────────────────────────

COMPOSITE_RECIPES: Tuple[CompositeRecipe, ...] = (
    CompositeRecipe(
        composite_id="composite.point_distribution_context",
        label_zh="点分布 + 行政上下文", label_en="Point distribution with admin context",
        description="主体点分布为主产品，行政边界作为上下文底图层；边界数据在场才并入。",
        base_recipe_id="poi_distribution_overview",
        supporting_recipe_ids=("administrative_choropleth",),
        trigger_ontology_tasks=("distribution.point_distribution",),
        supporting_requires_states=("eligible", "transform_required"),
        disclosures=("行政边界为上下文表达，不承载聚合统计。",),
    ),
    CompositeRecipe(
        composite_id="composite.density_screening_chain",
        label_zh="密度筛查链（视觉→显著性）", label_en="Density screening chain",
        description="视觉密度为主，仅当统计前提满足时并入 Gi* 显著性热点层，"
                    "防止「任何分布都上热力图/热点」的过度分析。",
        base_recipe_id="density_visual_overview",
        supporting_recipe_ids=("getis_ord_hotspot_significance",),
        trigger_ontology_tasks=("distribution.point_distribution",
                                "spatial_statistics.hotspot_significance"),
        supporting_requires_states=("eligible",),
        disclosures=("显著性热点仅在统计前提满足时并入；视觉密度不构成显著性结论。",),
    ),
    CompositeRecipe(
        composite_id="composite.admin_aggregation_report",
        label_zh="行政聚合统计报告", label_en="Admin aggregation report",
        description="行政聚合填色 + 排名图表的成组产品（数量/占比对比场景）。",
        base_recipe_id="administrative_choropleth",
        supporting_recipe_ids=("admin_ranking_stats",),
        trigger_ontology_tasks=("distribution.regional_aggregation",
                                "distribution.ranking_comparison"),
        disclosures=(),
    ),
    CompositeRecipe(
        composite_id="composite.autocorrelation_full",
        label_zh="全局+局部自相关", label_en="Global & local autocorrelation",
        description="先全局判显著，再局部定位聚集；两段共用同一权重矩阵事实。",
        base_recipe_id="global_moran_autocorrelation",
        supporting_recipe_ids=("local_moran_lisa",),
        trigger_ontology_tasks=("spatial_statistics.global_autocorrelation",
                                "spatial_statistics.local_cluster"),
        disclosures=("全局不显著时局部聚类仅作探索性参考。",),
    ),
    CompositeRecipe(
        composite_id="composite.interpolation_with_uncertainty",
        label_zh="插值 + 不确定性", label_en="Interpolation with uncertainty",
        description="预测面与不确定性面成对出现，防止把插值结果当确定事实。",
        base_recipe_id="kriging_interpolation_workflow",
        supporting_recipe_ids=("interpolation_uncertainty_map",),
        trigger_ontology_tasks=("interpolation.geostatistical_kriging",
                                "interpolation.uncertainty_surface"),
        disclosures=("插值面必须伴随不确定性/样本密度披露。",),
    ),
    CompositeRecipe(
        composite_id="composite.terrain_suite",
        label_zh="地形套件", label_en="Terrain suite",
        description="坡度/坡向 + 山体阴影 + 等高线的地形综合产品。",
        base_recipe_id="slope_analysis_workflow",
        supporting_recipe_ids=("hillshade_cartography", "contour_map_product"),
        trigger_ontology_tasks=("terrain_hydrology.slope_aspect",
                                "terrain_hydrology.composite_analysis"),
        disclosures=(),
    ),
    CompositeRecipe(
        composite_id="composite.watershed_report",
        label_zh="流域报告", label_en="Watershed report",
        description="流域划分 + 河网提取的水文产品组合。",
        base_recipe_id="watershed_delineation_workflow",
        supporting_recipe_ids=("stream_network_extraction",),
        trigger_ontology_tasks=("terrain_hydrology.watershed",
                                "terrain_hydrology.stream_network"),
        disclosures=(),
    ),
    CompositeRecipe(
        composite_id="composite.change_comparison_product",
        label_zh="变化对比产品", label_en="Change comparison product",
        description="变化检测层 + 前后对比卷帘的成组表达。",
        base_recipe_id="bitemporal_raster_change",
        supporting_recipe_ids=("optical_bitemporal_change",),
        trigger_ontology_tasks=("remote_sensing.change_detection",
                                "cartographic.comparison_map"),
        disclosures=("两期数据源不一致时变化结论含系统误差，必须披露。",),
    ),
    CompositeRecipe(
        composite_id="composite.sar_interpretation_chain",
        label_zh="SAR 解译链", label_en="SAR interpretation chain",
        description="定标 → 去噪 → 解译的雷达产品链；未定标不得进入解译层。",
        base_recipe_id="sar_backscatter_overview",
        supporting_recipe_ids=("sar_calibrated_comparison",),
        trigger_ontology_tasks=("sar.interpretation", "sar.radiometric_calibration"),
        disclosures=("定标参数缺失时仅相对解译。",),
    ),
    CompositeRecipe(
        composite_id="composite.suitability_with_sensitivity",
        label_zh="适宜性 + 敏感性", label_en="Suitability with sensitivity",
        description="MCDA 评价 + 权重敏感性展示，权重假设必须可见。",
        base_recipe_id="suitability_assessment",
        supporting_recipe_ids=("generic_mcda_site_ranking",),
        trigger_ontology_tasks=("decision.suitability", "decision.multi_criteria"),
        disclosures=("未给定权重时等权假设 + 敏感性分析佐证。",),
    ),
    CompositeRecipe(
        composite_id="composite.accessibility_coverage_report",
        label_zh="可达性覆盖报告", label_en="Accessibility coverage report",
        description="服务区/等时圈 + 覆盖率统计的可达性产品组。",
        base_recipe_id="accessibility_analysis",
        supporting_recipe_ids=("facility_coverage_ratio",),
        trigger_ontology_tasks=("network.accessibility", "network.service_area"),
        disclosures=(),
    ),
    CompositeRecipe(
        composite_id="composite.od_flow_report",
        label_zh="OD 流动报告", label_en="OD flow report",
        description="OD 矩阵统计 + 流线地图的流动分析产品组。",
        base_recipe_id="od_flow_overview",
        supporting_recipe_ids=("od_matrix_analysis",),
        trigger_ontology_tasks=("network.od_analysis",),
        disclosures=(),
    ),
)


# ── Scenario 登记表（审定工件；示范场景 + 覆盖面）────────────────────────

SCENARIO_TEMPLATES: Tuple[ScenarioTemplate, ...] = (
    ScenarioTemplate(
        scenario_id="scenario.school_distribution",
        label_zh="学校类设施分布", label_en="School facility distribution",
        description="示范场景（成都小学分布情况）：点分布为主，区域聚合为辅；"
                    "制图形态按数据资格选择，不预设热力图。",
        match_subjects=("小学", "中学", "学校", "幼儿园"),
        match_ontology_tasks=("distribution.point_distribution",),
        candidate_recipes=(
            "poi_distribution_overview", "administrative_choropleth",
            "grid_density_aggregate", "density_visual_overview",
        ),
        preferred_families=("seed.poi_distribution_overview",
                            "seed.administrative_choropleth"),
        expected_components=("title", "legend"),
        expects_chart=True,
        expects_admin_context=True,
        minimal_disclosure="仅输出点位计数与描述统计，不做密度推断。",
    ),
    ScenarioTemplate(
        scenario_id="scenario.medical_access_equity",
        label_zh="医疗可达与公平", label_en="Healthcare access & equity",
        description="可达性 + 分母归一化公平评价；分母缺失禁止公平结论。",
        match_subjects=("医院", "诊所", "医疗", "卫生"),
        match_ontology_tasks=("network.accessibility", "decision.spatial_equity"),
        candidate_recipes=(
            "clinic_coverage_analysis", "healthcare_equity_access",
            "hospital_service_area_stats",
        ),
        expected_components=("title", "legend", "chart_panel"),
        expects_chart=True,
        expects_admin_context=True,
        minimal_disclosure="仅输出设施分布与计数，不下公平性结论。",
    ),
    ScenarioTemplate(
        scenario_id="scenario.flood_risk_exposure",
        label_zh="洪涝风险暴露", label_en="Flood risk exposure",
        description="危险区 × 承灾体暴露；承灾体未确认不得下暴露结论。",
        match_subjects=("内涝", "洪水", "洪涝", "淹没"),
        match_ontology_tasks=("decision.risk_exposure",),
        candidate_recipes=(
            "flood_risk_assessment", "flood_inundation_screen",
            "hazard_exposure_overlay",
        ),
        expected_components=("title", "legend"),
        expects_chart=False,
        expects_admin_context=False,
        minimal_disclosure="仅危险区制图，不做暴露/损失结论。",
    ),
    ScenarioTemplate(
        scenario_id="scenario.site_screening_report",
        label_zh="选址筛查报告", label_en="Site screening report",
        description="多准则选址 + 约束排除 + 敏感性展示的成组产品。",
        match_subjects=("选址", "选点", "布局"),
        match_ontology_tasks=("decision.site_selection", "decision.multi_criteria"),
        candidate_recipes=(
            "generic_mcda_site_ranking", "site_selection",
            "suitability_assessment",
        ),
        expected_components=("title", "legend", "chart_panel"),
        expects_chart=True,
        expects_admin_context=False,
        minimal_disclosure="准则数据不足：仅因子清单与可得性说明。",
    ),
    ScenarioTemplate(
        scenario_id="scenario.land_cover_change",
        label_zh="地表覆盖变化", label_en="Land cover change",
        description="两期覆盖数据的变化检测 + 面积账目对比。",
        match_subjects=("土地利用", "地表覆盖", "覆盖变化", "扩张"),
        match_ontology_tasks=("remote_sensing.change_detection",),
        candidate_recipes=(
            "landcover_change_inventory", "bitemporal_raster_change",
            "change_area_accounting",
        ),
        expected_components=("title", "legend"),
        expects_chart=True,
        expects_admin_context=False,
        minimal_disclosure="仅单期：当前状态展示，不做变化结论。",
    ),
    ScenarioTemplate(
        scenario_id="scenario.air_quality_surface",
        label_zh="空气质量表面", label_en="Air quality surface",
        description="监测站点插值成面 + 站点标注；样本不足时降级站点图。",
        match_subjects=("空气", "空气质量", "污染", "aqi", "pm25"),
        match_ontology_tasks=("interpolation.geostatistical_kriging",
                              "interpolation.deterministic_surface"),
        candidate_recipes=(
            "air_quality_surface", "station_field_interpolation",
            "monitoring_station_coverage",
        ),
        expected_components=("title", "continuous_colorbar"),
        expects_chart=False,
        expects_admin_context=False,
        minimal_disclosure="样本不足：仅站点数值展示，不生成插值面。",
    ),
    ScenarioTemplate(
        scenario_id="scenario.slope_development_constraint",
        label_zh="坡度开发约束", label_en="Slope development constraint",
        description="坡度分级 + 约束分区的用地评价组合。",
        match_subjects=("坡度", "地形", "山地"),
        match_ontology_tasks=("terrain_hydrology.slope_aspect",),
        candidate_recipes=("slope_analysis_workflow", "slope_zoning_constraint"),
        expected_components=("title", "legend"),
        expects_chart=False,
        expects_admin_context=False,
        minimal_disclosure="无 DEM：仅可做矢量地形因子统计。",
    ),
)


class WorkflowFamilyRegistry:
    """family 投影 + composite + scenario 的统一只读注册表（确定性）。"""

    def __init__(self) -> None:
        self._families: List[WorkflowFamily] = []
        self._family_by_id: Dict[str, WorkflowFamily] = {}
        self._composites: Dict[str, CompositeRecipe] = {}
        self._scenarios: Dict[str, ScenarioTemplate] = {}

    def load(self, recipe_registry: Any) -> None:
        self._families = build_families_from_registry(recipe_registry)
        self._family_by_id = {f.family_id: f for f in self._families}
        self._composites = {c.composite_id: c for c in COMPOSITE_RECIPES}
        self._scenarios = {s.scenario_id: s for s in SCENARIO_TEMPLATES}

    # ── family ───────────────────────────────────────────────────────
    @property
    def families(self) -> List[WorkflowFamily]:
        return list(self._families)

    def family(self, family_id: str) -> Optional[WorkflowFamily]:
        return self._family_by_id.get(family_id)

    @property
    def family_count(self) -> int:
        return len(self._families)

    def families_for_domain(self, domain: str) -> List[WorkflowFamily]:
        return [f for f in self._families if f.domain == domain]

    def family_of_recipe(self, recipe_id: str) -> Optional[WorkflowFamily]:
        for f in self._families:
            if recipe_id in f.member_recipe_ids:
                return f
        return None

    # ── composite / scenario ─────────────────────────────────────────
    def composite(self, composite_id: str) -> Optional[CompositeRecipe]:
        return self._composites.get(composite_id)

    @property
    def composites(self) -> List[CompositeRecipe]:
        return [self._composites[k] for k in sorted(self._composites)]

    @property
    def composite_count(self) -> int:
        return len(self._composites)

    def scenario(self, scenario_id: str) -> Optional[ScenarioTemplate]:
        return self._scenarios.get(scenario_id)

    @property
    def scenarios(self) -> List[ScenarioTemplate]:
        return [self._scenarios[k] for k in sorted(self._scenarios)]

    @property
    def scenario_count(self) -> int:
        return len(self._scenarios)

    def match_scenarios(
        self, intent: Any, ontology_matches: Optional[List[str]] = None,
    ) -> List[ScenarioTemplate]:
        """intent → 激活的场景模板（确定性；主体词 + 本体任务 + scope 级）。

        匹配信号：query 命中 match_subjects 任一子串，且（本体任务或
        显式传入的 ontology_matches）命中 match_ontology_tasks 任一。
        scope level 可选加强（不强制）。
        """
        query = str(getattr(intent, "query", "") or "")
        scope_level = str(getattr(getattr(intent, "scope", None),
                                  "level", "") or "")
        task_ids = set(ontology_matches or [])
        activated: List[ScenarioTemplate] = []
        for sid in sorted(self._scenarios):
            s = self._scenarios[sid]
            subject_hit = any(sub and sub in query.lower()
                              for sub in s.match_subjects)
            task_hit = bool(set(s.match_ontology_tasks) & task_ids)
            if subject_hit and task_hit:
                if s.match_scope_level and scope_level not in s.match_scope_level:
                    continue
                activated.append(s)
        return activated

    # ── 校验与指纹 ───────────────────────────────────────────────────
    def validate(self, recipe_registry: Any) -> List[str]:
        """分层引用完整性：composite/scenario 引用必须真实存在。"""
        from app.services.gis_harness.gis_ontology import get_task_ontology

        issues: List[str] = []
        ontology = get_task_ontology()
        # family 投影一致性：成员并集/任务并集与 registry 对账
        for f in self._families:
            for rid in f.member_recipe_ids:
                if recipe_registry.get(rid) is None:
                    issues.append(f"family {f.family_id}: unknown member {rid}")
            for tid in f.ontology_tasks:
                if not ontology.has(tid):
                    issues.append(f"family {f.family_id}: unknown ontology task {tid}")
        for c in self._composites.values():
            if recipe_registry.get(c.base_recipe_id) is None:
                issues.append(f"composite {c.composite_id}: unknown base {c.base_recipe_id}")
            for rid in c.supporting_recipe_ids:
                if recipe_registry.get(rid) is None:
                    issues.append(f"composite {c.composite_id}: unknown supporting {rid}")
            for tid in c.trigger_ontology_tasks:
                if not ontology.has(tid):
                    issues.append(f"composite {c.composite_id}: unknown ontology task {tid}")
            bad = [s for s in c.supporting_requires_states
                   if s not in ("eligible", "transform_required", "degraded")]
            if bad:
                issues.append(f"composite {c.composite_id}: unknown require states {bad}")
        for s in self._scenarios.values():
            if not s.match_subjects and not s.match_ontology_tasks:
                issues.append(f"scenario {s.scenario_id}: 永不激活（无匹配信号）")
            for tid in s.match_ontology_tasks:
                if not ontology.has(tid):
                    issues.append(f"scenario {s.scenario_id}: unknown ontology task {tid}")
            for fid in s.preferred_families:
                if fid not in self._family_by_id:
                    issues.append(f"scenario {s.scenario_id}: unknown family {fid}")
            for rid in s.candidate_recipes:
                if recipe_registry.get(rid) is None:
                    issues.append(f"scenario {s.scenario_id}: unknown candidate recipe {rid}")
            for comp in s.expected_components:
                if comp and comp not in (
                        "title", "legend", "north_arrow", "scale_bar", "attribution",
                        "continuous_colorbar", "chart_panel", "statistics_panel"):
                    issues.append(f"scenario {s.scenario_id}: unknown component {comp}")
            if not s.minimal_disclosure:
                issues.append(f"scenario {s.scenario_id}: 缺 minimal 兜底披露")
        return issues

    def fingerprint(self) -> str:
        payload = {
            "version": FAMILY_LAYER_VERSION,
            "families": [f.model_dump() for f in self._families],
            "composites": [c.model_dump() for c in self.composites],
            "scenarios": [s.model_dump() for s in self.scenarios],
        }
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                               separators=(",", ":"), default=str)
        return hashlib.sha256(canonical.encode("utf-8"), usedforsecurity=False).hexdigest()


_singleton: Optional[WorkflowFamilyRegistry] = None


def get_workflow_family_registry() -> WorkflowFamilyRegistry:
    global _singleton
    if _singleton is None:
        from app.services.gis_harness.recipes import get_recipe_registry
        registry = WorkflowFamilyRegistry()
        registry.load(get_recipe_registry())
        _singleton = registry
    return _singleton


def reset_workflow_family_registry() -> None:
    global _singleton
    _singleton = None


__all__ = [
    "FAMILY_LAYER_VERSION",
    "WorkflowFamily",
    "CompositeRecipe",
    "ScenarioTemplate",
    "COMPOSITE_RECIPES",
    "SCENARIO_TEMPLATES",
    "build_families_from_registry",
    "WorkflowFamilyRegistry",
    "get_workflow_family_registry",
    "reset_workflow_family_registry",
]
