"""MapProductPlanner —— Intent → Recipe → Capability → Algorithm → Plan。

流程（§4 总体目标的中间层）：

    MapRequestIntent
          ↓ recipe selection（确定性）
    Candidate Recipe
          ↓ CapabilityRegistry / AlgorithmResolver（能力→算法→工具裁决）
    Capability Requirements + Algorithm Selections
          ↓ fetch data（agent 经 ToolDispatchService 执行能力面）
    Spatial Profile（ref descriptor 派生，零全量扫描）
          ↓ Recipe Eligibility + Algorithm applicability 复检（§17）
    Final Cartography Plan（含 fallback 决策记录）

plan 是**期望产品描述**，不是工具调用脚本。planner 是纯领域编排器：
具体工具名归 AlgorithmRegistry（经 resolver 裁决）、图层类型归
MapModelRegistry、模板选择归 TemplateSelector —— 本文件不再持有这些
知识的硬编码表。plan_id 由 (query, recipe_id) 决定性派生。
"""
from __future__ import annotations

import hashlib
import json
import threading
from collections import OrderedDict
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
from app.lib.gis.runtime_manifest import get_runtime_manifest
from app.services.gis_harness.template_catalog import get_template_catalog
from app.services.gis_harness.template_selector import TemplateSelector


# ── 兼容视图（audit #825 的锁定对象迁移到 registry；非第二事实源）──────
#
# CAPABILITY_TOOLS 现在是 AlgorithmRegistry 的**派生视图**：capability →
# 有序算法工具候选。新增算法只需注册 AlgorithmDescriptor，本模块零改动。
# tests/unit/test_capability_registry_parity.py 锁定派生视图与真实
# ToolRegistry / recipe 声明的 parity。
def capability_tool_map() -> Dict[str, List[str]]:
    """capability → 有序工具候选。

    v2(audit R4)：读 Compiled Runtime Manifest 的 O(1) 预排序视图 ——
    AlgorithmRegistry.capability_tool_map() 此前每次调用全量重建，
    plan_orchestrator 每步都调。manifest 编译时已按算法 priority 排序，
    内容与 registry 派生视图一致（同一来源）。
    """
    from app.lib.gis.runtime_manifest import get_runtime_manifest
    return dict(get_runtime_manifest().capability_to_tools)


def resolve_tool_for_capability(
    capability: str,
    available_tools: Optional[Any] = None,
) -> Optional[str]:
    """把能力 id 解析为当前注册表里真实存在的工具名。

    兼容 shim：委托 AlgorithmResolver 裁决（capability → algorithm →
    tool 的唯一裁决点）；available_tools 为 None 时返回首选候选。
    """
    from app.lib.gis.algorithm_resolver import get_algorithm_resolver
    resolution = get_algorithm_resolver().resolve(
        capability, available_tools=available_tools)
    return resolution.tool if resolution.status == "resolved" else None


# 模块级兼容名（DEPRECATED：新代码用 capability_tool_map()）。
# v2(review)：不再 import 时快照 —— 模块级调用会在 manifest 编译（持
# threading.Lock）经 import 链重入 get_runtime_manifest() 时死锁（非重入
# 锁 + 缓存未置的再编译）。PEP 562 惰性属性保持 `from planner import
# CAPABILITY_TOOLS` 兼容，且首次访问才取当前视图（顺带消除快照过期）。
def __getattr__(name: str):
    if name == "CAPABILITY_TOOLS":
        return capability_tool_map()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def layer_type_for_cartography(cartography: str, default: str = "circle") -> str:
    """制图模型 → MapLibre 图层类型。MapModelRegistry 是唯一权威。"""
    from app.lib.cartography.model_library import get_map_model_registry
    model = get_map_model_registry().resolve(cartography)
    return model.maplibre_layer_type if model else default


def _geometry_aware_layer_type(cartography: str, planned_type: str, profile_geom: str) -> str:
    """Resolve a geometry-polymorphic cartography's layer type from real data.

    多态映射收编进 MapModel.geometry_layer_types（audit #832）；
    非多态模型保持计划类型。
    """
    from app.lib.cartography.model_library import get_map_model_registry
    model = get_map_model_registry().resolve(cartography)
    table = model.geometry_layer_types if model else {}
    if not table:
        return planned_type
    return table.get(profile_geom) or planned_type


class DataRequirement(BaseModel):
    capability: str
    purpose: str = ""
    params: Dict[str, Any] = Field(default_factory=dict)
    status: Literal["pending", "available", "unavailable"] = "pending"
    bound_ref: str = ""
    resolved_tool: str = ""
    resolved_algorithm: str = ""
    # v3(Phase D)：依赖边（capability id 列表，registry artifact 类型推断）。
    # additive —— 旧持久计划无此字段，plan_graph 读取侧重放推断。
    depends_on: List[str] = Field(default_factory=list)
    optional: bool = False


class AnalysisStep(BaseModel):
    capability: str
    purpose: str = ""
    status: Literal["pending", "done", "skipped", "unavailable"] = "pending"
    bound_ref: str = ""
    resolved_tool: str = ""
    resolved_algorithm: str = ""
    depends_on: List[str] = Field(default_factory=list)
    optional: bool = False


class AlgorithmSelectionRecord(BaseModel):
    """一次 capability → algorithm → tool 裁决的有界证据（§27）。"""

    capability: str
    status: Literal["resolved", "unavailable"]
    algorithm: str = ""
    tool: str = ""
    reason: str = ""
    rejected: List[str] = []
    fallback_trail: List[Dict[str, Any]] = []
    fallback_candidates: List[str] = []


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
    # ── registry 编排证据（有界转录，§27）─────────────────────────────
    algorithm_selections: List[AlgorithmSelectionRecord] = []
    template_selection: Dict[str, Any] = Field(default_factory=dict)
    map_model_selection: List[Dict[str, Any]] = Field(default_factory=list)
    # v2(Phase 4, #1084)：计划编制时的 registry 内容指纹 —— 恢复/续跑时与
    # 当前 manifest 比对，不一致 → STALE_PLAN（旧计划不得静默套用新
    # registry 语义）。空 = 历史计划（不判 stale）。
    manifest_fingerprint: str = ""


def _plan_id(query: str, recipe_id: str) -> str:
    digest = hashlib.sha1(
        f"{query}|{recipe_id}".encode("utf-8"), usedforsecurity=False
    ).hexdigest()[:12]
    return f"plan-{digest}"


class _CompositionRejectedError(Exception):
    """组合校验失败（携带违规明细，供兜底路径记录 evidence）。"""

    def __init__(self, message: str, *, violations: Optional[List[Dict[str, Any]]] = None) -> None:
        super().__init__(message)
        self.violations = violations or []


class MapProductPlanner:
    """确定性产品规划器（纯函数式，无 LLM 依赖、无 I/O）。"""

    def __init__(self) -> None:
        self.recipes = get_recipe_registry()
        self.templates = get_product_template_registry()
        self.catalog = get_template_catalog()
        self.selector = TemplateSelector(catalog=self.catalog)
        # v2(audit R4)：同一会话里 webgis_map_intent 与 webgis_map_product
        # 对同一 query 各解析一次（加上 finalize_with_profile 共 2-3 次）。
        # plan_from_intent 是纯函数 —— 以 (query, recipe, template,
        # available_tools, manifest 指纹) 为键 memo；registry 内容变化
        # （manifest 指纹变）自动失效。有界 64。
        #
        # v3(audit A2/A3)：planner 实例此前在每个 harness tool call 内新建
        # （tools.py / plan_orchestrator.py），memo 只活在一次调用里 ——
        # intent→product 链跨调用零复用。共享 PlannerRuntime 后 memo 真正
        # 跨调用存活，eviction 路径随之可达：plain dict 的
        # ``popitem(last=False)`` 是 TypeError（v2 遗留 bug，被"每调用新
        # 实例"掩盖），必须 OrderedDict。memo 读写持锁（进程内多线程
        # dispatch 并发规划安全）；命中与存入均深拷贝，调用方可变返回值
        # 不污染 memo 基底。
        self._plan_memo: OrderedDict = OrderedDict()
        self._plan_memo_max = 64
        self._plan_memo_lock = threading.RLock()

    def attached_to_current_registries(self) -> bool:
        """共享 runtime 的 registry 身份守卫（v3 Phase C）。

        ``reset_recipe_registry`` / 模板注册测试会替换 registry 单例——
        共享 planner 持有旧引用时 memo 与裁决会漂移。身份不同即重建。
        """
        return (
            self.recipes is get_recipe_registry()
            and self.templates is get_product_template_registry()
            and self.catalog is get_template_catalog()
        )

    def reset_memo(self) -> None:
        """测试隔离：清空 memo（不重建 planner）。"""
        with self._plan_memo_lock:
            self._plan_memo.clear()

    # ── registry 裁决辅助 ─────────────────────────────────────────────
    def _resolve_capabilities(
        self,
        capabilities: List[str],
        intent: MapRequestIntent,
        *,
        available_tools: Optional[Any] = None,
        profile: Optional[Dict[str, Any]] = None,
        optional_capabilities: Optional[set] = None,
    ) -> tuple[List[DataRequirement], List[AnalysisStep], List[AlgorithmSelectionRecord]]:
        """capability → DataRequirement/AnalysisStep + 裁决证据。"""
        from app.lib.gis.algorithm_resolver import get_algorithm_resolver
        from app.lib.gis.capability_registry import get_capability_registry
        from app.services.gis_harness.plan_graph import infer_dependency_edges
        caps = get_capability_registry()
        resolver = get_algorithm_resolver()
        # v3(Phase D)：registry artifact 类型推断的依赖边（A.output ∩ B.input
        # ⇒ A→B）随行持久化 —— 扁平行即携带依赖序，plan_graph 是其纯投影。
        edges = infer_dependency_edges(capabilities)
        optional_set = optional_capabilities or set()
        requirements: List[DataRequirement] = []
        steps: List[AnalysisStep] = []
        selections: List[AlgorithmSelectionRecord] = []
        subject = intent.subject.category or "主体"
        for cap in capabilities:
            purpose = caps.purpose_for(cap, subject)
            resolution_result = resolver.resolve(
                cap, profile=profile, available_tools=available_tools)
            record = AlgorithmSelectionRecord(
                capability=cap,
                status=resolution_result.status,
                algorithm=resolution_result.algorithm,
                tool=resolution_result.tool,
                reason=resolution_result.reason,
                rejected=list(resolution_result.rejected),
                fallback_trail=[f.model_dump() for f in resolution_result.fallback_trail],
                fallback_candidates=list(resolution_result.fallback_candidates),
            )
            selections.append(record)
            # audit #825: 调用方传入注册表可见工具时，解析不到真实工具的
            # 能力标记 unavailable（诚实报告）；视图未知（None）保持 pending。
            unavailable = (
                available_tools is not None and record.status != "resolved"
            )
            status = "unavailable" if unavailable else "pending"  # type: ignore[assignment]
            deps = edges.get(cap, [])
            optional = cap in optional_set
            requirements.append(DataRequirement(
                capability=cap, purpose=purpose, status=status,
                resolved_tool=record.tool if record.status == "resolved" else "",
                resolved_algorithm=record.algorithm if record.status == "resolved" else "",
                depends_on=deps, optional=optional,
            ))
            steps.append(AnalysisStep(
                capability=cap, purpose=purpose, status=status,  # type: ignore[arg-type]
                resolved_tool=record.tool if record.status == "resolved" else "",
                resolved_algorithm=record.algorithm if record.status == "resolved" else "",
                depends_on=deps, optional=optional,
            ))
        return requirements, steps, selections

    # ── 阶段 1：intent → draft plan（数据未到手） ────────────────────
    def plan_from_intent(
        self,
        intent: MapRequestIntent,
        template_id: str = "",
        recipe_id: str = "",
        available_tools: Optional[Any] = None,
        project_verified: Optional[set] = None,
        use_memo: bool = True,
    ) -> MapProductPlan:
        # v2(R4)：memo 命中直接返回既有 plan（确定性规划器，同输入同输出）。
        # available_tools 参与（工具面变化改变 resolution evidence）；
        # project_verified 参与（#864 项目记忆排序）。测试可用 use_memo=False
        # 绕过。
        #
        # v3(Phase C)：**确定性裁决前置** —— recipe/template 选择是廉价排序
        # （~10 recipe × ~7 模板），先裁决再查 memo，键用裁决结果而非原始
        # 参数。intent 阶段（无显式参数）与 product 阶段（显式回放同一
        # recipe/template，plan 连续性）由此命中同一条目；project_verified
        # 只在改变裁决结果时才分键（裁决相同 ⇒ 输出相同，命中是正确语义）。
        # recipe_id 显式指定（webgis_map_intent 阶段的推荐/LLM 纠偏）优先——
        # 保证意图阶段与产品阶段用同一份计划（plan 连续性）。
        recipe = self.recipes.get(recipe_id) if recipe_id else None
        if recipe is None:
            # #1067(E-12): 回退重选此前不带 project_verified（#864 只修了
            # 主路径）—— 应用 recipe 与 evidence 候选在项目记忆排序场景下分叉。
            candidates = self.recipes.select_candidates(
                intent, project_verified=project_verified
            )
            recipe = candidates[0] if candidates else None
        if recipe is None:
            # 兜底：没有 task/cartography 命中时按通用 POI 分布 recipe
            recipe = (
                self.recipes.get("poi_distribution_overview")
                or self.recipes.default_recipe()
            )

        # 模板选择：显式 template_id 优先（plan 连续性）；否则由
        # TemplateSelector 确定性评分（subject/task/outputs/priority）。
        template: Optional[MapProductTemplate] = None
        selection_dump: Dict[str, Any] = {}
        if template_id:
            template = self.catalog.get_product_template(template_id)
            selection_dump = {
                "status": "selected" if template else "none",
                "template_id": template.id if template else "",
                "decision": {"reason": f"explicit_template_id:{template_id}"},
            }
        if template is None:
            selection = self.selector.select_product(
                intent=intent, recipe_id=recipe.id,
            )
            selection_dump = selection.model_dump()
            if selection.status == "selected":
                template = self.catalog.get_product_template(selection.template_id)

        memo_key = None
        if use_memo:
            try:
                # v2(review R3-P1-3)：intent 全量参与键 —— task/subject/
                # output_intents 等字段都改变 plan 输出，只按 query 键会在
                # 同 query 不同 intent 时返回错误计划（复现于 review）。
                intent_canonical = json.dumps(
                    intent.model_dump(), ensure_ascii=False, sort_keys=True,
                )
                memo_key = (
                    intent_canonical, recipe.id, template.id if template else "",
                    tuple(sorted(available_tools)) if available_tools is not None else None,
                    get_runtime_manifest().fingerprint,
                )
                with self._plan_memo_lock:
                    cached = self._plan_memo.get(memo_key)
                    if cached is not None:
                        return cached.model_copy(deep=True)
            except Exception:  # noqa: BLE001 — memo 失败退直算
                memo_key = None

        plan = MapProductPlan(
            plan_id=_plan_id(intent.query, recipe.id),
            query=intent.query,
            intent=intent,
            recipe_id=recipe.id,
            template_id=template.id if template else "",
            status="draft",
            template_selection=selection_dump,
            manifest_fingerprint=get_runtime_manifest().fingerprint,
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

        requirements, steps, selections = self._resolve_capabilities(
            capabilities, intent, available_tools=available_tools,
            optional_capabilities=set(recipe.optional_analysis))
        plan.data_requirements = requirements
        plan.analysis_steps = steps
        plan.algorithm_selections = selections

        # 图层角色：来自产品模板或 recipe 声明（layer_type 由模型库推导）
        if template:
            for role_spec in template.layer_roles:
                plan.map_layers.append(PlannedLayer(
                    role=role_spec.role,  # type: ignore[arg-type]
                    layer_type=layer_type_for_cartography(role_spec.cartography,
                                                          role_spec.layer_type or "circle"),
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
                layer_type=layer_type_for_cartography(recipe.primary_cartography),
                cartography=recipe.primary_cartography,
                source_capability=recipe.preferred_analysis[0] if recipe.preferred_analysis else "",
            ))
            for carto in recipe.secondary_cartography:
                plan.map_layers.append(PlannedLayer(
                    role="secondary",
                    layer_type=layer_type_for_cartography(carto),
                    cartography=carto,
                    source_capability=recipe.preferred_analysis[0] if recipe.preferred_analysis else "",
                ))
        plan.map_model_selection = self._map_model_evidence(plan.map_layers)

        # 统计/图表
        if "statistics" in intent.output_intents:
            plan.statistics = ["feature_count", "admin_summary"]
            if intent.task == "administrative_statistic":
                plan.statistics = ["admin_aggregation", "ranking", "total"]
        if "chart" in intent.output_intents:
            plan.charts = ["category_bar"] if intent.task == "categorical_distribution" else ["admin_bar"]

        plan.validation = list(recipe.validation_rules)
        if memo_key is not None:
            # 存入即深拷贝：调用方持有返回对象并可变（plan1.data_requirements=[]
            # 不得污染 memo 基底）。
            with self._plan_memo_lock:
                self._plan_memo[memo_key] = plan.model_copy(deep=True)
                while len(self._plan_memo) > self._plan_memo_max:
                    self._plan_memo.popitem(last=False)

        return plan

    def _map_model_evidence(self, layers: List[PlannedLayer]) -> List[Dict[str, Any]]:
        """图层 → MapModel 解析证据（有界：≤ layers 数）。"""
        from app.lib.cartography.model_library import get_map_model_registry
        models = get_map_model_registry()
        evidence: List[Dict[str, Any]] = []
        for layer in layers:
            model = models.resolve(layer.cartography)
            evidence.append({
                "cartography": layer.cartography,
                "map_model": model.id if model else "",
                "layer_type": layer.layer_type,
                "source": "map_model_registry" if model else "fallback_default",
            })
        return evidence

    # ── 阶段 2：数据回来后 → eligibility 复检 + 终稿 ─────────────────
    def finalize_with_profile(
        self,
        plan: MapProductPlan,
        profile: Optional[Dict[str, Any]],
        *,
        min_points_default: int = 10,
        available_tools: Optional[Any] = None,
    ) -> MapProductPlan:
        """Spatial Profile 到手后的确定性复检（§17 反一锤定音）。

        - profile 几何/点数驱动 eligibility；
        - algorithm applicability 复检（resolver 带 profile 重裁决；
          available_tools 传入时与 draft 阶段同视图，evidence 不漂移）；
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

        # algorithm applicability 复检：带 profile 重裁决（不改变
        # DataRequirement 的 available 状态——那是绑定回填的职责；只更新
        # 裁决证据，让 evidence 能解释『为什么这个算法没跑』）。
        if profile is not None:
            capabilities = [r.capability for r in finalized.data_requirements]
            _, _, selections = self._resolve_capabilities(
                capabilities, plan.intent, profile=profile,
                available_tools=available_tools,
                optional_capabilities={
                    r.capability for r in finalized.data_requirements if r.optional
                })
            finalized.algorithm_selections = selections

        disabled_elements = {d.element for d in report.disabled}
        # 主数据几何（点层提升的先决条件——面数据上提升 circle 层是制图空转）
        geom_types = (profile or {}).get("geometryTypes") or []
        profile_geom = "unknown"
        if isinstance(geom_types, list) and geom_types:
            from app.services.gis_harness.recipes import _geometry_category
            profile_geom = _geometry_category(geom_types)

        # audit #832: 几何多态表达的 layer_type 跟随真实数据几何 ——
        # categorical_thematic 计划为 fill 而点数据授权 circle 时，primary
        # 永不绑定、completeness 永远 missing（#784 修复的残留面）。
        for layer in finalized.map_layers:
            resolved = _geometry_aware_layer_type(
                layer.cartography, layer.layer_type, profile_geom)
            if resolved != layer.layer_type:
                layer.note = (
                    layer.note + "; " if layer.note else ""
                ) + f"layer_type {layer.layer_type}->{resolved} by profile geometry"
                layer.layer_type = resolved
        finalized.map_model_selection = self._map_model_evidence(finalized.map_layers)

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
                    role="primary",
                    layer_type=layer_type_for_cartography("point_overlay"),
                    cartography="point_overlay",
                    source_capability="poi_query",
                    note="recipe ineligible — fallback point map",
                ))

        # 终稿组件集：优先走 component_resolver/composer（composition 驱动），
        # 失败回退到 build_default_components（兼容旧路径）。
        primary_layer = next(
            (ly for ly in finalized.map_layers if ly.role == "primary" and ly.enabled),
            None,
        )
        primary_carto = primary_layer.cartography if primary_layer else "point_overlay"
        try:
            from app.services.gis_harness.component_composer import get_component_composer
            from app.services.gis_harness.component_resolver import get_component_resolver
            template = self.templates.get(plan.template_id) if plan.template_id else None
            comp_tmpl_id = (template.composition_template_id if template and template.composition_template_id else "")
            # report_product prefers a composition that provides export_layout/map_border
            if plan.intent.report_product:
                output_target = "pdf"
                # force a report-capable composition（接线模板不满足时改选；
                # 满足 export_layout 必备的接线模板保持不变）
                from app.lib.cartography.composition_templates import get_composition_template_registry
                compo_reg = get_composition_template_registry()

                def _requires_export_layout(c) -> bool:
                    return c is not None and any(
                        s.id == "export_layout" and s.cardinality == "required"
                        for s in c.component_slots
                    )

                wired = compo_reg.get(comp_tmpl_id) if comp_tmpl_id else None
                if not _requires_export_layout(wired):
                    # wired 为 None 时（未接线/未注册 id）同样走自动改选 ——
                    # 报告产品的版面契约优先于具体模板选择。
                    cands = compo_reg.find_for_map_model(primary_carto, "pdf")
                    report_cands = [c for c in cands if _requires_export_layout(c)]
                    if report_cands:
                        comp_tmpl_id = report_cands[0].id
            else:
                output_target = "interactive"
            resolver = get_component_resolver()
            # available_context: statistics/chart if recipe declares those outputs
            ctx: list = []
            if plan.statistics:
                ctx.append("statistics")
            if plan.charts:
                ctx.append("chart")
            selection = resolver.resolve(
                composition_template_id=comp_tmpl_id,
                map_model_id=primary_carto,
                output_target=output_target,
                available_context=ctx,
            )
            title_text = self._default_title(plan)
            subtitle_text = plan.intent.scope.name if plan.intent.scope.name else ""
            # layer binding: primary layer id → legend/colorbar
            layer_bindings: dict = {}
            if primary_layer and primary_layer.layer_id:
                layer_bindings["primary"] = primary_layer.layer_id
            composer = get_component_composer()
            overrides = (template.component_overrides if template else {})  # type: ignore[attr-defined]
            composed = composer.compose(
                selection,
                title_text=title_text,
                subtitle_text=subtitle_text,
                layer_bindings=layer_bindings,
                composition_template_id=selection.composition_template_id,
                overrides=overrides if isinstance(overrides, dict) else {},
            )
            if not composed:
                raise ValueError("empty composition")
            # 组合级校验（conflicts/cardinality/required/forbidden/planned/
            # 孤儿绑定）：error 级违规 → 抛错走 build_default_components 兜底；
            # warning（zone 碰撞等）记入 evidence，QA（semantic_checks）单独报告。
            from app.lib.cartography.composition_validation import validate_component_composition
            validation = validate_component_composition(
                composed,
                composition_template_id=selection.composition_template_id,
                map_model_id=primary_carto,
                layer_ids=[ly.layer_id for ly in finalized.map_layers if ly.layer_id],
                output_target=output_target,
            )
            if not validation.ok:
                raise _CompositionRejectedError(
                    "composition violations: "
                    + "; ".join(
                        f"{v.code}[{v.component_type or v.slot}] {v.detail}"
                        for v in validation.errors
                    ),
                    violations=[v.to_dict() for v in validation.errors],
                )
            finalized.components = composed
            # stash composition evidence
            finalized.template_selection = {
                **finalized.template_selection,
                "composition_template_id": selection.composition_template_id,
                "component_templates": selection.component_templates,
                "composition_warnings": [v.to_dict() for v in validation.warnings],
            }
        except Exception as exc:
            # 组合路径失败 → build_default_components 兜底，但必须留下可追溯
            # evidence（FallbackDecision + template_selection），不静默吞掉。
            reason_code = (
                "COMPOSITION_INVALID" if isinstance(exc, _CompositionRejectedError)
                else "COMPOSITION_ERROR"
            )
            fallback_evidence: Dict[str, Any] = {
                "reason_code": reason_code,
                "error": str(exc)[:500],
            }
            if isinstance(exc, _CompositionRejectedError):
                fallback_evidence["violations"] = exc.violations
            finalized.fallbacks.append(FallbackDecision(
                from_element="composition_template",
                to_element="default_components",
                reason_code=reason_code,
                evidence=fallback_evidence,
            ))
            finalized.template_selection = {
                **finalized.template_selection,
                "composition_fallback": fallback_evidence,
            }
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

    # ── 完整性评估（Harness evidence 消费面）──────────────────────────
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
    "AlgorithmSelectionRecord",
    "PlannedLayer",
    "MapProductPlanner",
    # CAPABILITY_TOOLS 经 PEP 562 __getattr__ 惰性提供，不在模块命名空间（ruff F822 豁免）
    "CAPABILITY_TOOLS",  # noqa: F822
    "capability_tool_map",
    "resolve_tool_for_capability",
    "layer_type_for_cartography",
]
