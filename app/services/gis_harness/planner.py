"""MapProductPlanner —— Intent → Recipe → MapProductPlan。

流程（§4 总体目标的中间层）：

    MapRequestIntent
          ↓ recipe selection（确定性）
    Candidate Recipe
          ↓ fetch data（agent 经 ToolDispatchService 执行能力面）
    Spatial Profile（ref descriptor 派生，零全量扫描）
          ↓ Recipe Eligibility（代码侧确定性复检，§17）
    Final Cartography Plan（含 fallback 决策记录）

plan 是**期望产品描述**，不是工具调用脚本；工具解析由 Tool Resolver 按
能力面完成。plan_id 由 (query, recipe_id) 决定性派生 —— 可回放、可 diff。
"""
from __future__ import annotations

import hashlib
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

from app.services.gis_harness.components import (
    CartographyComponent,
    build_default_components,
)
from app.services.gis_harness.intent import MapRequestIntent
from app.services.gis_harness.product_templates import (
    MapProductTemplate,
    get_product_template_registry,
)
from app.services.gis_harness.recipes import (
    CartographyRecipe,
    EligibilityReport,
    FallbackDecision,
    get_recipe_registry,
)

# 能力面 → 具体工具的解析表（Tool Resolver）。能力 id 稳定，工具可替换；
# 未注册能力在 plan 中标记 unavailable（诚实报告，不静默）。
# audit #825: 候选名必须与 ToolRegistry 真实注册名对账（曾有 3/12 能力指向
# 改名后的幽灵工具）；tests/unit/test_capability_registry_parity.py 全量锁定。
CAPABILITY_TOOLS: Dict[str, List[str]] = {
    "poi_query": ["query_local_poi", "search_poi", "query_osm_poi"],
    "admin_boundary_query": ["get_local_admin_boundary"],
    "admin_aggregation": ["spatial_aggregate"],
    "point_profile": ["spatial_stats", "webgis_source_profile"],
    "density_surface": ["heatmap_data"],
    "kde_density": ["kde_contours", "kde_surface"],
    "hotspot": ["hotspot_analysis"],
    "category_breakdown": ["spatial_stats"],
    "proximity_buffer": ["buffer_analysis"],
    "service_area": ["isochrone_analysis", "service_area_simple"],
    "raster_source": ["fetch_dem"],
    # 格网聚合：H3 六边形优先，渔网兜底（模型库 aggregate_grid 的执行面）
    "grid_binning": ["h3_binning", "fishnet_grid"],
    # administrative_choropleth 的 optional_analysis（audit #825 补映射）
    "analytical_density": ["kde_contours", "heatmap_data", "spatial_aggregate"],
}


def resolve_tool_for_capability(
    capability: str,
    available_tools: Optional[Any] = None,
) -> Optional[str]:
    """把能力 id 解析为当前注册表里真实存在的工具名。"""
    candidates = CAPABILITY_TOOLS.get(capability) or []
    if available_tools is None:
        return candidates[0] if candidates else None
    for name in candidates:
        if name in available_tools:
            return name
    return None


class DataRequirement(BaseModel):
    capability: str
    purpose: str = ""
    params: Dict[str, Any] = Field(default_factory=dict)
    status: Literal["pending", "available", "unavailable"] = "pending"
    bound_ref: str = ""
    resolved_tool: str = ""


class AnalysisStep(BaseModel):
    capability: str
    purpose: str = ""
    status: Literal["pending", "done", "skipped", "unavailable"] = "pending"
    bound_ref: str = ""
    resolved_tool: str = ""


class PlannedLayer(BaseModel):
    role: Literal["primary", "secondary", "reference"] = "secondary"
    layer_type: str = "circle"
    cartography: str = "point_overlay"
    source_capability: str = ""
    layer_id: str = ""
    bound_ref: str = ""
    enabled: bool = True
    note: str = ""


class MapProductPlan(BaseModel):
    """GIS/制图执行计划（typed / deterministic / replayable）。"""
    plan_id: str
    query: str
    intent: MapRequestIntent
    recipe_id: str
    template_id: str = ""
    data_requirements: List[DataRequirement] = []
    analysis_steps: List[AnalysisStep] = []
    map_layers: List[PlannedLayer] = []
    components: List[CartographyComponent] = []
    statistics: List[str] = []
    charts: List[str] = []
    fallbacks: List[FallbackDecision] = []
    validation: List[str] = []
    exports: List[str] = []
    outputs: List[str] = []
    status: Literal["draft", "finalized"] = "draft"
    completeness: Dict[str, Any] = Field(default_factory=dict)
    eligibility: Dict[str, Any] = Field(default_factory=dict)


def _plan_id(query: str, recipe_id: str) -> str:
    digest = hashlib.sha1(
        f"{query}|{recipe_id}".encode("utf-8"), usedforsecurity=False
    ).hexdigest()[:12]
    return f"plan-{digest}"


# 主专题表达 → 图层类型（模型库 maplibre_layer_type 的镜像）
_CARTOGRAPHY_LAYER_TYPE = {
    "visual_heatmap": "heatmap",
    "density_overview": "heatmap",
    "point_overlay": "circle",
    "simple_point_map": "circle",
    "proportional_symbol": "circle",
    "administrative_choropleth": "fill",
    "categorical_thematic": "fill",
    "proximity_overlay": "fill",
    "raster_surface": "raster",
    "hotspot_overlay": "fill",
    "administrative_aggregation": "fill",
    "aggregate_grid": "fill",
}


class MapProductPlanner:
    """确定性产品规划器（纯函数式，无 LLM 依赖、无 I/O）。"""

    def __init__(self) -> None:
        self.recipes = get_recipe_registry()
        self.templates = get_product_template_registry()

    # ── 阶段 1：intent → draft plan（数据未到手） ────────────────────
    def plan_from_intent(
        self,
        intent: MapRequestIntent,
        template_id: str = "",
        recipe_id: str = "",
        available_tools: Optional[Any] = None,
    ) -> MapProductPlan:
        # recipe_id 显式指定（webgis_map_intent 阶段的推荐/LLM 纠偏）优先——
        # 保证意图阶段与产品阶段用同一份计划（plan 连续性）。
        recipe = self.recipes.get(recipe_id) if recipe_id else None
        if recipe is None:
            candidates = self.recipes.select_candidates(intent)
            recipe = candidates[0] if candidates else None
        if recipe is None:
            # 兜底：没有 task/cartography 命中时按通用 POI 分布 recipe
            recipe = (
                self.recipes.get("poi_distribution_overview")
                or self.recipes.default_recipe()
            )

        template: Optional[MapProductTemplate] = None
        if template_id:
            template = self.templates.get(template_id)
        if template is None and recipe.id:
            template = self.templates.find_for_recipe(
                recipe.id, subject_category=intent.subject.category,
            )

        # simple_view 任务：直接轻量点图产品
        if intent.task == "simple_view":
            template = self.templates.get("simple_poi_view") or template

        plan = MapProductPlan(
            plan_id=_plan_id(intent.query, recipe.id),
            query=intent.query,
            intent=intent,
            recipe_id=recipe.id,
            template_id=template.id if template else "",
            status="draft",
        )

        # 数据需求（能力去重，保持声明顺序）；simple_view 不过度分析——
        # 只保留主数据获取 + 画像，砍掉聚合/密度/热点等衍生分析。
        if intent.task == "simple_view":
            capabilities = [
                c for c in recipe.preferred_analysis
                if c in ("poi_query", "point_profile", "raster_source", "service_area",
                         "proximity_buffer")
            ] or recipe.preferred_analysis[:1]
        else:
            capabilities = list(recipe.preferred_analysis)
            for extra in recipe.optional_analysis:
                if extra not in capabilities:
                    capabilities.append(extra)

        purpose_map = {
            "poi_query": f"{intent.subject.category or '主体'} 要素获取",
            "admin_boundary_query": "行政边界/区划面获取",
            "admin_aggregation": "按行政区聚合统计",
            "point_profile": "数据画像（点数/几何/字段）",
            "kde_density": "核密度分析",
            "hotspot": "热点显著性分析",
            "density_surface": "密度面",
            "service_area": "网络服务区",
            "proximity_buffer": "邻近缓冲",
            "raster_source": "栅格数据源",
            "category_breakdown": "类别构成统计",
            "grid_binning": "H3/渔网格网聚合",
            "analytical_density": "分析密度面/密度聚合",
        }
        for cap in capabilities:
            # audit #825: 兑现模块 docstring —— 调用方传入注册表可见工具时，
            # 解析不到真实工具的能力在 plan 中标记 unavailable（诚实报告）。
            if available_tools is not None and not resolve_tool_for_capability(
                cap, available_tools
            ):
                plan.data_requirements.append(DataRequirement(
                    capability=cap, purpose=purpose_map.get(cap, cap),
                    status="unavailable",
                ))
                plan.analysis_steps.append(AnalysisStep(
                    capability=cap, purpose=purpose_map.get(cap, cap),
                    status="unavailable",
                ))
                continue
            plan.data_requirements.append(DataRequirement(
                capability=cap, purpose=purpose_map.get(cap, cap),
            ))
            plan.analysis_steps.append(AnalysisStep(
                capability=cap, purpose=purpose_map.get(cap, cap),
            ))

        # 图层角色：来自产品模板或 recipe 声明
        if template:
            for role_spec in template.layer_roles:
                plan.map_layers.append(PlannedLayer(
                    role=role_spec.role,  # type: ignore[arg-type]
                    layer_type=role_spec.layer_type,
                    cartography=role_spec.cartography,
                    source_capability=role_spec.source_capability,
                ))
            plan.outputs = list(template.outputs)
            plan.exports = list(template.exports) if not intent.export_intents else list(
                dict.fromkeys(template.exports + intent.export_intents)
            )
        else:
            plan.map_layers.append(PlannedLayer(
                role="primary",
                layer_type=_CARTOGRAPHY_LAYER_TYPE.get(recipe.primary_cartography, "circle"),
                cartography=recipe.primary_cartography,
                source_capability=recipe.preferred_analysis[0] if recipe.preferred_analysis else "",
            ))
            for carto in recipe.secondary_cartography:
                plan.map_layers.append(PlannedLayer(
                    role="secondary",
                    layer_type=_CARTOGRAPHY_LAYER_TYPE.get(carto, "circle"),
                    cartography=carto,
                    source_capability=recipe.preferred_analysis[0] if recipe.preferred_analysis else "",
                ))

        # 统计/图表
        if "statistics" in intent.output_intents:
            plan.statistics = ["feature_count", "admin_summary"]
            if intent.task == "administrative_statistic":
                plan.statistics = ["admin_aggregation", "ranking", "total"]
        if "chart" in intent.output_intents:
            plan.charts = ["category_bar"] if intent.task == "categorical_distribution" else ["admin_bar"]

        plan.validation = list(recipe.validation_rules)
        return plan

    # ── 阶段 2：数据回来后 → eligibility 复检 + 终稿 ─────────────────
    def finalize_with_profile(
        self,
        plan: MapProductPlan,
        profile: Optional[Dict[str, Any]],
        *,
        min_points_default: int = 10,
    ) -> MapProductPlan:
        """Spatial Profile 到手后的确定性复检（§17 反一锤定音）。

        - profile 几何/点数驱动 eligibility；
        - 不合格元素禁用 + fallback 记录（from/to/reason/evidence）；
        - 主专题表达可能因此改变（heatmap → point），组件集随终稿重算。
        """
        recipe = self.recipes.get(plan.recipe_id)
        finalized = plan.model_copy(deep=True)
        if recipe is None:
            finalized.status = "finalized"
            return finalized

        report: EligibilityReport = self.check_recipe_eligibility(
            recipe, profile, min_points_default=min_points_default,
        )
        finalized.eligibility = {
            "recipe_id": recipe.id,
            "eligible": report.eligible,
            "disabled": [d.model_dump() for d in report.disabled],
            "checks": report.checks,
        }
        finalized.fallbacks = list(report.fallbacks)

        disabled_elements = {d.element for d in report.disabled}
        # 主数据几何（点层提升的先决条件——面数据上提升 circle 层是制图空转）
        geom_types = (profile or {}).get("geometryTypes") or []
        profile_geom = "unknown"
        if isinstance(geom_types, list) and geom_types:
            from app.services.gis_harness.recipes import _geometry_category
            profile_geom = _geometry_category(geom_types)

        # 图层级裁决：热力/格网主层被禁 → 降级 + （几何为点时）点层提升。
        # 两种主表达各持独立 recorded 标志：混合模板（热力+格网）同被禁时
        # 各自记录 fallback，不互相吞并。
        heat_fallback_recorded = False
        grid_fallback_recorded = False
        for layer in finalized.map_layers:
            if layer.cartography in ("visual_heatmap", "density_overview"):
                if "visual_heatmap" in disabled_elements or "native_heatmap" in disabled_elements:
                    reason = next(
                        (d for d in report.disabled if d.element in ("visual_heatmap", "native_heatmap")),
                        None,
                    )
                    layer.enabled = False
                    layer.role = "secondary"  # 禁用层不再是 primary（单一 primary 不变式）
                    layer.note = (
                        f"disabled: {reason.reason_code}" if reason else "disabled"
                    )
                    if not heat_fallback_recorded:
                        point_layer = next(
                            (ly for ly in finalized.map_layers
                             if ly.cartography in ("point_overlay", "simple_point_map")
                             and ly.enabled),
                            None,
                        )
                        # 点层提升为 primary（converter 会按真实几何推断图层
                        # 类型——面数据上它落成 fill，不会是空转的 circle）。
                        if point_layer:
                            point_layer.role = "primary"
                        finalized.fallbacks.append(FallbackDecision(
                            from_element="visual_heatmap",
                            to_element="point_distribution",
                            reason_code=reason.reason_code if reason else "INELIGIBLE",
                            evidence={
                                **(reason.evidence if reason else {}),
                                "profile_geometry": profile_geom,
                            },
                        ))
                        heat_fallback_recorded = True
            elif layer.cartography == "aggregate_grid":
                if "aggregate_grid" in disabled_elements or "recipe" in disabled_elements:
                    # 不按 reason_code 过滤：GEOMETRY_NOT_SUPPORTED 与
                    # INSUFFICIENT_POINTS 都要保留真实原因码（此前过滤导致
                    # 几何失配被硬编码误标为 INSUFFICIENT_POINTS）。
                    reason = next(
                        (d for d in report.disabled if d.element in ("aggregate_grid", "recipe")),
                        None,
                    )
                    layer.enabled = False
                    layer.role = "secondary"
                    layer.note = f"disabled: {reason.reason_code}" if reason else "disabled"
                    if not grid_fallback_recorded:
                        point_layer = next(
                            (ly for ly in finalized.map_layers
                             if ly.cartography in ("point_overlay", "simple_point_map")
                             and ly.enabled),
                            None,
                        )
                        if point_layer:
                            point_layer.role = "primary"
                        finalized.fallbacks.append(FallbackDecision(
                            from_element="aggregate_grid",
                            to_element="point_distribution",
                            reason_code=reason.reason_code if reason else "INELIGIBLE",
                            evidence={
                                **(reason.evidence if reason else {}),
                                "profile_geometry": profile_geom,
                            },
                        ))
                        grid_fallback_recorded = True

        # recipe 整体不合格 → 禁用与被禁元素对应的图层并记录 RECIPE_INELIGIBLE；
        # 全部图层被禁时追加点图兜底层（gate 因此可达）。
        if not report.eligible:
            for layer in finalized.map_layers:
                if layer.enabled and layer.cartography in disabled_elements:
                    layer.enabled = False
                    layer.role = "secondary"
                    layer.note = "disabled: RECIPE_INELIGIBLE"
            finalized.fallbacks.append(FallbackDecision(
                from_element=recipe.id,
                to_element="point_distribution",
                reason_code="RECIPE_INELIGIBLE",
                evidence={"disabled": sorted(disabled_elements)},
            ))
            if not any(ly.enabled for ly in finalized.map_layers):
                finalized.map_layers.append(PlannedLayer(
                    role="primary", layer_type="circle", cartography="point_overlay",
                    source_capability="poi_query",
                    note="recipe ineligible — fallback point map",
                ))

        # 终稿组件集：按（回退后的）实际主表达重建 + recipe 声明的附加组件
        primary_layer = next(
            (ly for ly in finalized.map_layers if ly.role == "primary" and ly.enabled),
            None,
        )
        primary_carto = primary_layer.cartography if primary_layer else "point_overlay"
        finalized.components = build_default_components(
            primary_cartography=primary_carto,
            title=self._default_title(plan),
            subtitle=plan.intent.scope.name if plan.intent.scope.name else "",
            report_product=plan.intent.report_product,
            scope_name=plan.intent.scope.name,
            subject_category=plan.intent.subject.category,
            extra_types=recipe.default_components,
        )

        finalized.status = "finalized"
        finalized.completeness = self.assess_completeness(finalized)
        return finalized

    def check_recipe_eligibility(
        self,
        recipe: CartographyRecipe,
        profile: Optional[Dict[str, Any]],
        *,
        min_points_default: int = 10,
    ) -> EligibilityReport:
        from app.services.gis_harness.recipes import check_eligibility

        return check_eligibility(
            recipe, profile=profile, min_points_default=min_points_default,
        )

    def _default_title(self, plan: MapProductPlan) -> str:
        intent = plan.intent
        template = self.templates.get(plan.template_id) if plan.template_id else None
        pattern = template.title_pattern if template and template.title_pattern else "{scope}{subject}分布"
        return pattern.format(
            scope=intent.scope.name or "",
            subject=intent.subject.category or "",
        )

    # ── 完整性评估（Harness evidence 消费面） ────────────────────────
    def assess_completeness(self, plan: MapProductPlan) -> Dict[str, Any]:
        expected_outputs = set(plan.outputs) or {"interactive_map"}
        present: Dict[str, bool] = {}
        # #716: interactive_map means an AUTHORED+BOUND layer exists — planned
        # layers default enabled, so the old planned-layer check reported
        # completeness even when every authoring attempt failed.
        # #784: layer_ids 绑定流没有 primary_ref —— 绑定的已提交图层本身
        # 就是「已授权且已挂载」的证据，bound_ref 不再是必要条件。
        has_bound_layer = any(
            ly.enabled and ly.layer_id for ly in plan.map_layers
        )
        present["interactive_map"] = has_bound_layer
        # #784: data_bound 的判据是「计划元素绑到了已提交图层或 ref 任一」——
        # grid/simple_view 流常只带 layer_ids（无 primary_ref），绑定的图层
        # 本身就是数据到位的证据。
        present["data_bound"] = any(
            ly.bound_ref or ly.layer_id for ly in plan.map_layers
        )
        # #784: 统计维只在该产品族声明了统计输出时适用 —— simple_view 等
        # 轻量产品不该因「没有统计」被记成 missing statistics。
        present["statistics"] = (
            bool(plan.statistics) and any(
                s.status == "done" for s in plan.analysis_steps
            )
        ) if plan.statistics else True
        present["components"] = bool(plan.components)
        present["exports"] = bool(plan.exports) if "export" in expected_outputs else True
        # #784: 未绑定的结构性规划图层（primary/reference）必须可见 ——
        # 此前任何单个绑定图层就满足 interactive_map，缺失的 reference 层
        #（如教育产品的行政区层）是静默的，completeness 对缺层产品假报
        # complete。secondary 点叠加是可选增强（planner 资格降级本身就可能
        # 砍掉它），不因缺席记 missing。
        unbound_planned = [
            ly.cartography or ly.layer_type
            for ly in plan.map_layers
            if ly.enabled and ly.role != "secondary"
            and not (ly.bound_ref or ly.layer_id)
        ]
        present["planned_layers"] = not unbound_planned
        missing = sorted(k for k, v in present.items() if not v)
        return {
            "expected_outputs": sorted(expected_outputs),
            "present": present,
            "missing": missing,
            "unbound_planned_layers": unbound_planned,
            "complete": not missing,
            "fallback_count": len(plan.fallbacks),
        }


__all__ = [
    "MapProductPlan",
    "DataRequirement",
    "AnalysisStep",
    "PlannedLayer",
    "MapProductPlanner",
    "CAPABILITY_TOOLS",
    "resolve_tool_for_capability",
]
