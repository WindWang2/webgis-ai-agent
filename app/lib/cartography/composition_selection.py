"""Composition Selection V7 — 同一数据的多方案组合候选与评分（Goal 08 Phase F）.

``plan_composition``（template_intelligence）回答「给定类目，一个组合怎么
拼」；本模块回答「同一份数据，**有哪些**合理组合、按什么排序」：

    TaskCartographyContext（任务类目/几何/变量/产物/输出 —— 结构化事实）
      → 每个类目：plan_composition（组合模板/槽位）× 候选主表达
        （model_library 几何∩产物硬过滤 + 类目亲和评分）
      → 确定性加权评分（数据契合/类目亲和/变量契合/输出/组件可用性/统计）
      → List[CompositionCandidate]（有界、可解释、可序列化）

知识形态（与本仓库既有纪律一致）：
- 类目 → 主表达亲和是**审定静态表**（同 COMPONENT_SEMANTIC_ROLES /
  TemplateSpec affinity 的性质），不是 per-query 硬编码 —— 「成都学校
  分布」由 (geometry=point, artifacts=[point_feature_set,
  admin_aggregate_table], categories=…) 结构化事实驱动，本模块任何
  路径都不出现 query 字符串（case corpus 负例锁定）；
- 纯函数、确定性、有界；只裁决「用什么组合」，不渲染、不改 MapSpec。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field

#: 变量语义类型（测量层级，决定 classification/color_scheme 契合）。
VariableKind = str  # "none" | "categorical" | "ordinal" | "continuous" | "rate"

#: 候选/理由上界（有界载荷）。
MAX_ALTERNATIVES = 3
_MAX_REASONS = 6
_MODELS_PER_CATEGORY = 3

#: 变量语义 → classification 契合。
_VARIABLE_CLASSIFICATION_FIT: Dict[str, Tuple[str, ...]] = {
    "none": ("none", "categorical"),
    "categorical": ("categorical",),
    "ordinal": ("graduated", "categorical"),
    "continuous": ("graduated", "quantiles", "natural_breaks",
                   "equal_interval", "std_dev", "head_tail"),
    "rate": ("quantiles", "natural_breaks", "std_dev", "graduated",
             "head_tail", "equal_interval"),
}

#: 变量语义 → color_scheme 契合。
_VARIABLE_SCHEME_FIT: Dict[str, Tuple[str, ...]] = {
    "none": ("none", "qualitative"),
    "categorical": ("qualitative",),
    "ordinal": ("sequential", "qualitative"),
    "continuous": ("sequential", "perceptual_uniform"),
    "rate": ("sequential", "perceptual_uniform", "diverging"),
}

#: 任务类目 → 主表达亲和（审定静态表；id 必须可解析为 native 模型，
#: referential integrity 由测试锁定）。表外的类目无亲和 —— 所有可行
#: 模型平权（不猜）。
_CATEGORY_MODEL_AFFINITY: Dict[str, Tuple[str, ...]] = {
    "spatial_distribution": (
        "point_overlay", "simple_point_map", "categorized_point",
        "graduated_point", "proportional_symbol", "point_cluster",
        "dot_density_map", "categorical_thematic",
        "administrative_choropleth", "aggregate_grid"),
    "density": (
        "visual_heatmap", "dot_density_map", "aggregate_grid",
        "kernel_density_surface", "isoline_contour"),
    "administrative_aggregation": (
        "administrative_choropleth", "normalized_choropleth",
        "aggregate_grid"),
    "thematic_cartography": (
        "administrative_choropleth", "normalized_choropleth",
        "diverging_choropleth", "bivariate_choropleth",
        "graduated_point", "proportional_symbol", "aggregate_grid"),
    "interpolation": (
        "interpolation_result_map", "kernel_density_surface",
        "uncertainty_surface", "isoline_contour"),
    "hotspot": (
        "hotspot_overlay", "point_cluster"),
    "clustering": (
        "point_cluster", "hotspot_overlay", "dbscan_cluster_map"),
    "terrain": (
        "elevation_tint_hillshade", "terrain_analytical_surface",
        "classified_raster"),
    "hydrology": (
        "classified_raster", "terrain_analytical_surface"),
    "change_detection": (
        "change_comparison_map", "classified_raster"),
    "comparison": (
        "diverging_choropleth", "bivariate_choropleth",
        "change_comparison_map"),
    "multi_criteria": (
        "bivariate_choropleth", "administrative_choropleth"),
    "uncertainty": (
        "uncertainty_surface",),
    "remote_sensing_extraction": (
        "classified_raster",),
    "overlay": (
        "administrative_choropleth", "proportional_symbol"),
    "suitability": (
        "bivariate_choropleth", "administrative_choropleth"),
    "accessibility_network": (
        "isoline_contour",),
    "proximity": (
        "isoline_contour",),
    "spatiotemporal_pattern": (
        "change_comparison_map", "point_cluster"),
    "atlas_reporting": (
        "administrative_choropleth", "aggregate_grid"),
}


class TaskCartographyContext(BaseModel):
    """任务制图上下文（Harness 从 intent/profile/artifact 事实投影）。"""

    task_categories: Tuple[str, ...] = ()   # 任务类目（ranked；首类目为主）
    geometry_kind: str = "polygon"          # point/line/polygon/raster
    variable_kind: VariableKind = "continuous"
    statistic: str = ""                     # count/sum/rate/index/density/…
    scale_hint: str = ""                    # block/district/city/region/…
    class_count: int = 5                    # 期望分级数（0 = 未表达）
    output_target: str = "interactive"
    artifact_types: Tuple[str, ...] = ()    # 在场产物语义类型
    available_components: Tuple[str, ...] = ()  # 类型级在场组件
    uncertainty_required: bool = False
    comparison_required: bool = False

    @property
    def primary_category(self) -> str:
        return self.task_categories[0] if self.task_categories else ""


class CompositionCandidate(BaseModel):
    """一个候选组合：主表达 × 组合模板 × 规划产物 + 确定性得分。"""

    map_model_id: str
    composition_template_id: str
    spec_id: str
    category_id: str = ""
    score: float
    reasons: List[str] = Field(default_factory=list)
    slot_components: List[str] = Field(default_factory=list)  # 组件类型清单
    disclosures: List[str] = Field(default_factory=list)

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "mapModel": self.map_model_id[:48],
            "composition": self.composition_template_id[:64],
            "spec": self.spec_id[:48],
            "category": self.category_id[:40],
            "score": round(self.score, 2),
            "reasons": [r[:96] for r in self.reasons[:_MAX_REASONS]],
            "slots": [s[:32] for s in self.slot_components[:12]],
            "disclosures": [d[:120] for d in self.disclosures[:4]],
        }


def _candidate_map_models(
    ctx: TaskCartographyContext,
    model_reg: Any,
) -> List[Any]:
    """几何 ∩ 产物硬过滤后的可行 native 主表达（确定性序）。"""
    out: List[Any] = []
    artifact_set = set(ctx.artifact_types)
    for model_id in model_reg.native_ids():
        model = model_reg.get(model_id)
        if model is None:
            continue
        if ctx.geometry_kind not in (model.geometry_kinds or []):
            continue
        if artifact_set and model.accepted_artifact_types:
            if not (artifact_set & set(model.accepted_artifact_types)):
                continue  # 产物不被该模型接受 → 不可行
        out.append(model)
    out.sort(key=lambda m: m.id)
    return out


def _variable_fit(model: Any, ctx: TaskCartographyContext) -> Tuple[float, List[str]]:
    """变量语义契合（classification + color_scheme 双通道）。"""
    reasons: List[str] = []
    score = 1.0
    cls_fit = ctx.variable_kind in _VARIABLE_CLASSIFICATION_FIT and \
        model.classification in _VARIABLE_CLASSIFICATION_FIT[ctx.variable_kind]
    scheme_fit = ctx.variable_kind in _VARIABLE_SCHEME_FIT and \
        model.color_scheme_kind in _VARIABLE_SCHEME_FIT[ctx.variable_kind]
    if ctx.variable_kind != "none":
        if cls_fit:
            score += 1.0
            reasons.append(f"分级语义契合 {ctx.variable_kind}")
        if scheme_fit:
            score += 0.5
            reasons.append(f"色带语义契合 {model.color_scheme_kind}")
        if not cls_fit and model.classification != "none":
            score -= 0.5  # 硬表达了一个不匹配的测量层级
    return max(0.0, score), reasons


def _component_availability(plan: Any, ctx: TaskCartographyContext) -> Tuple[float, List[str]]:
    """规划槽位组件的类型级可用性（registry 可解析 + 在场清单）。"""
    from app.lib.cartography.component_registry import get_component_registry
    comp_reg = get_component_registry()
    available = set(ctx.available_components)
    missing: List[str] = []
    total = 0
    for slot in plan.slot_fills[:14]:
        ctype = slot.component_type
        if not ctype:
            continue
        total += 1
        desc = comp_reg.get(ctype) or comp_reg.get_by_type(ctype)
        if desc is not None and ctype not in available:
            if slot.cardinality == "required":
                missing.append(ctype)
    if total == 0:
        return 0.5, ["规划无组件槽位（回退基底）"]
    penalty = 0.3 * len(missing[:3])
    return max(0.0, 1.0 - penalty), (
        [f"必选组件缺场 {','.join(missing[:2])}"] if missing else [])


def _affinity_score(model: Any, categories: List[str]) -> Tuple[float, str]:
    """类目亲和：命中 = 1.0；类目无亲和表 = 中性 0.5；表未命中 = 0.2。

    类目间的意图排序不进亲和分 —— 由候选合并的类目轮转（round-robin）
    表达（首类目的最优候选排最前，但次类目的最优仍可进 top-N，保证
    「同一数据 → 不同合理组合」的多样性）。
    """
    for category_id in categories:
        affinity = _CATEGORY_MODEL_AFFINITY.get(category_id)
        if affinity and model.id in affinity:
            return 1.0, f"类目亲和 {category_id}"
    if not any(c in _CATEGORY_MODEL_AFFINITY for c in categories):
        return 0.5, ""
    return 0.2, ""


def select_composition_alternatives(
    ctx: TaskCartographyContext,
    *,
    max_alternatives: int = MAX_ALTERNATIVES,
    spec_registry: Any = None,
    taxonomy: Any = None,
) -> List[CompositionCandidate]:
    """任务上下文 → 排序的候选组合（确定性；无 query 输入）。

    每个候选 = 类目 × 规划（plan_composition）× 候选主表达；评分权重：
      数据契合 0.30 / 类目亲和 0.25 / 变量契合 0.15 / 输出契合 0.10 /
      组件可用性 0.10 / 统计-比例尺契合 0.10。
    tie-break：(-score, map_model_id, composition_template_id) 稳定全序。
    """
    from app.lib.cartography.model_library import get_map_model_registry

    if spec_registry is None:
        from app.lib.cartography.template_intelligence import (
            get_template_spec_registry,
        )
        spec_registry = get_template_spec_registry()
    if taxonomy is None:
        from app.lib.gis.methodology.taxonomy import get_task_taxonomy
        taxonomy = get_task_taxonomy()
    model_reg = get_map_model_registry()

    categories = [c for c in ctx.task_categories if c][:3]
    if not categories:
        categories = ["thematic_cartography"]  # 结构化兜底（无 query）

    models = _candidate_map_models(ctx, model_reg)

    # 每类目独立产出候选清单（按分内排序）；随后类目轮转合并 ——
    # 首类目最优排第一，次类目最优紧随，保证 top-N 跨类目多样性。
    per_category: List[List[CompositionCandidate]] = []
    seen: set = set()

    for category_id in categories:
        plan = _plan_for_category(category_id, ctx, spec_registry=spec_registry,
                                  taxonomy=taxonomy)
        if plan is None:
            continue
        comp_score, comp_reasons = _component_availability(plan, ctx)
        out_score = 1.0 if plan.output_target == ctx.output_target else 0.4
        stat_score, stat_reasons = _statistic_scale_fit(ctx)
        category_candidates: List[CompositionCandidate] = []
        ranked: List[Tuple[float, Any, List[str], float]] = []
        for model in models:
            aff, aff_reason = _affinity_score(model, [category_id])
            accepted = list(model.accepted_artifact_types)
            artifact_hits = len(set(ctx.artifact_types) & set(accepted)) \
                if ctx.artifact_types else 0
            # 数据契合：命中数 + 产物专精度（hits/accepted —— 模型越专注
            # 于在场产物类型，语义越贴合；isoline 兼收 point 只是能力，
            # heatmap 专收 point 才是本命表达）
            if artifact_hits and accepted:
                data_score = 0.6 + 0.2 * min(artifact_hits, 2) / 2 \
                    + 0.2 * (artifact_hits / len(accepted))
            else:
                data_score = 0.6
            ranked.append((aff, model, ([aff_reason] if aff_reason else []),
                           data_score))
        if not ranked:
            continue
        ranked.sort(key=lambda t: (-t[0], -t[3], t[1].id))
        for aff, model, aff_reasons, data_score in ranked[:_MODELS_PER_CATEGORY]:
            key = (model.id, plan.base_composition_template_id)
            if key in seen:
                continue
            seen.add(key)
            var_score, var_reasons = _variable_fit(model, ctx)
            total = (0.30 * data_score + 0.25 * aff + 0.15 * var_score
                     + 0.10 * out_score + 0.10 * comp_score + 0.10 * stat_score)
            slot_components = sorted({
                s.component_type for s in plan.slot_fills[:14] if s.component_type})
            reasons = [
                *(f"产物契合 {a}" for a in sorted(
                    set(ctx.artifact_types) & set(model.accepted_artifact_types))[:2]),
                *aff_reasons,
                *var_reasons,
                *(["输出契合 " + ctx.output_target] if out_score == 1.0 else []),
                *comp_reasons,
                *stat_reasons,
            ]
            category_candidates.append(CompositionCandidate(
                map_model_id=model.id,
                composition_template_id=plan.base_composition_template_id,
                spec_id=plan.spec_id,
                category_id=category_id,
                score=total,
                reasons=reasons[:_MAX_REASONS],
                slot_components=slot_components,
                disclosures=[d[:120] for d in plan.disclosures[:4]],
            ))
        category_candidates.sort(key=lambda c: (-c.score, c.map_model_id,
                                                c.composition_template_id))
        per_category.append(category_candidates)

    # 类目轮转合并（rank 0 全部类目按意图序 → rank 1 …）。合并序即最终序：
    # 它同时编码了「类目内按分排序」与「跨类目多样性 + 主意图优先」；
    # 再按分全局重排会同分 tie 挤掉后续类目候选（破坏多样性语义）。
    candidates: List[CompositionCandidate] = []
    max_len = max((len(lst) for lst in per_category), default=0)
    for rank in range(max_len):
        for lst in per_category:
            if rank < len(lst):
                candidates.append(lst[rank])
    return candidates[: max(1, max_alternatives)]


def _plan_for_category(
    category_id: str,
    ctx: TaskCartographyContext,
    *,
    spec_registry: Any,
    taxonomy: Any,
) -> Optional[Any]:
    from app.lib.cartography.template_intelligence import plan_composition
    try:
        return plan_composition(
            category_id,
            artifact_types=list(ctx.artifact_types)[:12],
            output_target=ctx.output_target,
            uncertainty_required=ctx.uncertainty_required,
            comparison_required=ctx.comparison_required,
            taxonomy=taxonomy,
            spec_registry=spec_registry,
        )
    except Exception:
        return None


def _statistic_scale_fit(ctx: TaskCartographyContext) -> Tuple[float, List[str]]:
    reasons: List[str] = []
    score = 0.6
    artifact_set = set(ctx.artifact_types)
    if ctx.statistic in ("rate", "index", "density"):
        score += 0.2
        reasons.append(f"统计语义 {ctx.statistic} 需归一面（normalized 通道）")
    if "admin_aggregate_table" in artifact_set:
        score += 0.2
        reasons.append("聚合表在场：伴生统计图表槽位成立")
    return score, reasons


def validate_affinity_table() -> List[str]:
    """亲和表 referential integrity（id 必须可解析；测试锁定）。"""
    from app.lib.cartography.model_library import get_map_model_registry
    model_reg = get_map_model_registry()
    issues: List[str] = []
    for category_id, model_ids in sorted(_CATEGORY_MODEL_AFFINITY.items()):
        for mid in model_ids:
            model = model_reg.resolve(mid)
            if model is None:
                issues.append(f"affinity[{category_id}]: {mid} 未注册")
            elif model.runtime_status != "native":
                issues.append(f"affinity[{category_id}]: {mid} 非 native")
    return issues


__all__ = [
    "TaskCartographyContext",
    "CompositionCandidate",
    "select_composition_alternatives",
    "validate_affinity_table",
    "MAX_ALTERNATIVES",
]
