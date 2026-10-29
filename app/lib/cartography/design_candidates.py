"""MapDesignCandidate —— 表达模型层的可比较制图设计方案候选（/goal cartography-design）。

职责切分（与既有模块零重叠）：
- ``composition_selection``（V7）回答「同一数据有哪些合理**组合**」——组合模板
  层候选与评分；本模块**调用**它，不复制其评分逻辑；
- ``model_library`` 是表达模型知识单一事实源（data requirements / pitfalls /
  fallback 链）；本模块只读消费；
- ``choose_classification``（visualization_plan，C3/ADR-0073）是分布→分类法
  唯一裁决；本模块把其结论装配进候选（R6），不另行裁决；
- 本模块新增的是**表达模型层**的候选与门控：每个候选 = 主表达模型 × 组合
  × 数据资格条件（typed codes）× 科学风险 × 降级路径 ×「为什么合适 / 对照
  落选者为什么不合适」。2–4 个，确定性纯函数，无 query 字符串输入。

门控规则（每条独立可测，全部引用既有权威阈值）：
- R1 分母门控：率/密度语义模型（normalized_choropleth 等）无真实分母 →
  BLOCKED（不可计算），降级链指向原始计数 choropleth，并携带面积偏误披露；
  原始计数模型在用户语义为密度/率时 → CONDITIONAL（DENOMINATOR_MISSING，
  「显示原始计数，不得标注为每万人密度」）。
- R2 过密点图：point_count ≥ POINT_OVERPLOT_THRESHOLD（本模块新定的点
  可读性启发阈值——非既有权威，只降级推荐不做硬拦截）
  → point 族候选 CONDITIONAL（POINT_OVERPLOT_RISK），聚合/密度族获得对照
  理由。点数证据缺失（basis=unknown）不裁决（诚实缺席）。
- R3 热力诚实：密度面族恒带 KDE 披露（KDE_NOT_POPULATION_DENSITY）；点数
  证据在场且 < HEATMAP_MIN_POINTS（app.lib.gis.cost_model 单一事实源，
  heatmap_data 工具硬门槛）→ BLOCKED（INSUFFICIENT_POINTS_FOR_DENSITY）。
  用户显式要求热力也被 typed block（不静默换方案、不假成功）。
- R4 时相对比：temporal_periods ≥ 2 → 任务类目补充 change_detection（结构化
  事实派生，非 query），change/temporal 族候选自然进入池。
- R5 聚合质心谬误：admin_aggregate_table 在场 + 比例符号（symbol_scalar）
  族候选 → AGGREGATE_CENTROID_PLACEMENT（聚合值定位在质心，位置不具要素
  精度；point 族逐要素定位不受此谬误影响，不触发）。
- R6 分类装配：graduated 模型 + 分布统计在场 → choose_classification 结论
  装配；统计缺席 → CLASSIFICATION_DEFERRED（不编造）。
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field

from app.lib.cartography.model_library import get_map_model_registry
from app.lib.cartography.visualization_plan import (
    DistributionStats,
    choose_classification,
)
#: HEATMAP_MIN_POINTS 单一事实源在 app.lib.gis.cost_model（heatmap_data 工具
#: 硬门槛）；本模块 import 复用，不复制数值。
from app.lib.gis.cost_model import HEATMAP_MIN_POINTS

DESIGN_CANDIDATES_VERSION = 1
#: 候选上限（契约：2–4；数据只支撑 1 个时如实 1，不注水）。
MAX_DESIGN_CANDIDATES = 4
#: 点图过密启发阈值（仅降级为 CONDITIONAL 推荐，不做硬拦截）：视口内数百
#: 以上独立点符号线索互相侵蚀，聚合/网格可读性更佳。有界、保守、可调。
POINT_OVERPLOT_THRESHOLD = 250

#: 率/密度语义模型（表达值 = 分子/分母）：无分母不可计算 → BLOCKED。
#: 审定静态表（同 _CATEGORY_MODEL_AFFINITY 性质），id 引用完整性由测试锁定。
_RATE_SEMANTIC_MODELS: Tuple[str, ...] = (
    "normalized_choropleth", "dasymetric_map", "cartogram_map",
)
#: 用户统计语义为率/密度时，原始计数表达的披露触发词。
_DENSITY_CLAIM_STATISTICS: Tuple[str, ...] = ("rate", "density", "per_capita")

#: 表达模型 → 表达族（多样性去重维度；审定静态表 + layer_type 兜底）。
_EXPRESSION_FAMILIES: Dict[str, str] = {
    "simple_point_map": "point", "point_overlay": "point",
    "categorized_point": "point", "graduated_point": "point",
    "categorical_thematic": "point", "proximity_overlay": "point",
    "hotspot_overlay": "point", "dbscan_cluster_map": "point",
    "proportional_symbol": "symbol_scalar", "graduated_symbol": "symbol_scalar",
    "point_cluster": "aggregation", "aggregate_grid": "aggregation",
    "administrative_aggregation": "aggregation",
    "dot_density_map": "density_surface", "visual_heatmap": "density_surface",
    "kernel_density_surface": "density_surface",
    "administrative_choropleth": "area_stat",
    "normalized_choropleth": "area_stat", "diverging_choropleth": "area_stat",
    "bivariate_choropleth": "area_stat", "uncertainty_choropleth": "area_stat",
    "change_comparison_map": "change", "temporal_comparison_map": "change",
    "before_after_swipe": "change", "small_multiple_map": "change",
    "interpolation_result_map": "surface_continuous",
    "isoline_contour": "isoline", "uncertainty_surface": "uncertainty",
}
#: layer_type 兜底（模型不在上表时的确定性族归属）。
_LAYER_TYPE_FAMILY_FALLBACK: Dict[str, str] = {
    "heatmap": "density_surface", "fill": "area_stat", "circle": "point",
}


class DenominatorEvidence(BaseModel):
    """分母证据（结构化；来自 profile/dataset_semantics 投影，不信前端）。"""

    field: str = ""
    available: bool = False
    unit: str = ""            # 人口/面积等单位语义
    source: str = "profile"   # profile|overlay|intent|dataset_semantics
    #（overlay = 主数据无分母、overlay 数据词表命中的跨源投影，M6）


class DesignTaskContext(BaseModel):
    """设计候选任务上下文（结构化事实投影；禁止 query 字符串）。"""

    task_categories: Tuple[str, ...] = ()
    geometry_kind: str = "polygon"
    #: 在场次数据几何（overlay 面/线数据）：主表达几何之外可用的表达面。
    #: 成都学校（点）+ 行政区（面）→ 面族表达（区级统计）可行。
    secondary_geometry_kinds: Tuple[str, ...] = ()
    variable_kind: str = "continuous"
    statistic: str = ""
    artifact_types: Tuple[str, ...] = ()
    point_count: int = 0
    point_count_basis: str = "unknown"   # profile|dataset|sampled|unknown
    denominator: DenominatorEvidence = Field(default_factory=DenominatorEvidence)
    temporal_periods: int = 0
    user_requested_models: Tuple[str, ...] = ()
    scale_hint: str = ""
    output_target: str = "interactive"
    distribution: Optional[DistributionStats] = None


class DenominatorCondition(str, Enum):
    NOT_APPLICABLE = "not_applicable"
    REQUIRED_SATISFIED = "required_satisfied"
    REQUIRED_MISSING = "required_missing"


class CandidateSuitability(str, Enum):
    SUITABLE = "suitable"
    CONDITIONAL = "conditional"
    BLOCKED = "blocked"


_MAX_REASONS = 6
_MAX_RISKS = 4
_MAX_COMPONENTS = 12


class MapDesignCandidate(BaseModel):
    """一个表达模型层候选：主表达 × 组合 × 资格条件 × 风险 × 降级路径。"""

    candidate_id: str = ""
    map_model_id: str
    map_model_name_zh: str = ""
    expression_family: str = ""
    composition_template_id: str = ""
    score: float = 0.0
    suitability: CandidateSuitability = CandidateSuitability.SUITABLE
    condition_codes: List[str] = Field(default_factory=list)
    condition_reasons_zh: List[str] = Field(default_factory=list)
    data_requirements: List[str] = Field(default_factory=list)
    denominator_condition: DenominatorCondition = DenominatorCondition.NOT_APPLICABLE
    classification: Optional[Dict[str, Any]] = None
    palette_semantics: str = ""
    components: List[str] = Field(default_factory=list)
    scientific_risks_zh: List[str] = Field(default_factory=list)
    degradation_path: List[str] = Field(default_factory=list)
    reasons_zh: List[str] = Field(default_factory=list)
    against_zh: List[str] = Field(default_factory=list)
    user_requested: bool = False

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "candidateId": self.candidate_id[:64],
            "mapModel": self.map_model_id[:48],
            "name": self.map_model_name_zh[:48],
            "family": self.expression_family[:24],
            "composition": self.composition_template_id[:64],
            "score": round(self.score, 2),
            "suitability": self.suitability.value,
            "conditionCodes": [c[:48] for c in self.condition_codes[:6]],
            "conditionReasons": [r[:120] for r in self.condition_reasons_zh[:6]],
            "dataRequirements": [d[:40] for d in self.data_requirements[:6]],
            "denominator": self.denominator_condition.value,
            "classification": self.classification,
            "palette": self.palette_semantics[:24],
            "components": [c[:32] for c in self.components[:_MAX_COMPONENTS]],
            "risks": [r[:160] for r in self.scientific_risks_zh[:_MAX_RISKS]],
            "degradation": [d[:48] for d in self.degradation_path[:3]],
            "reasons": [r[:120] for r in self.reasons_zh[:_MAX_REASONS]],
            "against": [a[:120] for a in self.against_zh[:3]],
            "userRequested": self.user_requested,
        }


class DesignCandidateSet(BaseModel):
    """候选集 + 选择对齐说明（planner 实际产物 vs 候选池）。"""

    version: int = DESIGN_CANDIDATES_VERSION
    candidates: List[MapDesignCandidate] = Field(default_factory=list)
    selected_candidate_id: str = ""
    selection_basis: str = ""
    user_request_handling: Dict[str, List[str]] = Field(
        default_factory=lambda: {"requested": [], "honored": [], "typed_blocked": []})

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "version": self.version,
            "count": len(self.candidates),
            "candidates": [c.to_bounded_dict() for c in self.candidates],
            "selected": self.selected_candidate_id[:64],
            "selectionBasis": self.selection_basis[:160],
            "userRequestHandling": {
                k: [m[:48] for m in v[:6]]
                for k, v in self.user_request_handling.items()
            },
        }


def _expression_family(model: Any) -> str:
    family = _EXPRESSION_FAMILIES.get(model.id)
    if family:
        return family
    return _LAYER_TYPE_FAMILY_FALLBACK.get(model.maplibre_layer_type, "other")


def _fallback_chain(model_id: str, model_reg: Any, _depth: int = 0) -> List[str]:
    """fallback_model_id 链（≤3，防环由 registry 校验保证，双保险截断）。"""
    chain: List[str] = []
    current = model_id
    while _depth < 3:
        model = model_reg.get(current)
        nxt = getattr(model, "fallback_model_id", "") if model else ""
        if not nxt or nxt in chain:
            break
        chain.append(nxt)
        current = nxt
        _depth += 1
    return chain


def _task_ctx_from_design(ctx: DesignTaskContext) -> Any:
    from app.lib.cartography.composition_selection import TaskCartographyContext

    categories = list(ctx.task_categories[:3])
    # R4：双期在场 → 结构化补充 change 类目（让组合/亲和机制自然给出
    # change 族候选；不是 query 字符串，是数据事实派生）。
    if ctx.temporal_periods >= 2 and "change_detection" not in categories \
            and len(categories) < 3:
        categories.append("change_detection")
    statistic = ctx.statistic
    if not statistic:
        statistic = "rate" if ctx.denominator.available else "count"
    return TaskCartographyContext(
        task_categories=tuple(categories),
        geometry_kind=ctx.geometry_kind,
        variable_kind=ctx.variable_kind if ctx.variable_kind != "rate"
        else ("rate" if ctx.denominator.available else "continuous"),
        statistic=statistic,
        artifact_types=ctx.artifact_types[:12],
        output_target=ctx.output_target,
    )


def _composition_lookup() -> Any:
    from app.lib.cartography.composition_selection import (
        select_composition_alternatives,
    )
    return select_composition_alternatives


def _pool_from_composition(
    ctx: DesignTaskContext,
    *,
    extra_categories: Tuple[str, ...] = (),
) -> Dict[str, Dict[str, Any]]:
    """组合层候选 → {map_model_id: {composition, score, slots, reasons}}。

    借道 select_composition_alternatives（组合层单一事实源）；extra_categories
    用于用户显式要求模型的定向检索（该模型亲和类目 → 结构化补类目再选）。
    组合层每类目只取 top-N 模型（数据契合分偏向多产物模型），会把点族等
    单产物表达挤出池 → 由 ``_augment_with_affinity_families`` 按亲和表补齐
    缺失表达族（复用其硬过滤与亲和表，不复制评分逻辑）。
    """
    select = _composition_lookup()
    task_ctx = _task_ctx_from_design(ctx)
    if extra_categories:
        merged = list(task_ctx.task_categories[:2]) + [
            c for c in extra_categories if c not in task_ctx.task_categories]
        task_ctx = task_ctx.model_copy(
            update={"task_categories": tuple(merged[:3])})
    pool: Dict[str, Dict[str, Any]] = {}
    try:
        for cand in select(task_ctx, max_alternatives=8):
            pool[cand.map_model_id] = {
                "composition": cand.composition_template_id,
                "score": cand.score,
                "slots": cand.slot_components,
                "reasons": cand.reasons,
            }
    except Exception as exc:  # noqa: BLE001 — 降级可观测（P3-11）
        import logging
        logging.getLogger(__name__).warning(
            "[design_candidates] composition pool degraded: %s", exc)
        return {}
    return pool


def _augment_with_affinity_families(
    ctx: DesignTaskContext,
    pool: Dict[str, Dict[str, Any]],
) -> None:
    """按类目亲和表补齐组合层挤出的表达族（确定性；不复制评分）。

    只补「可行且亲和」的模型：可行性 = 组合层同款硬过滤（几何 ∩ 产物，
    ``_candidate_map_models`` 复用）；亲和 = 该类目审定表命中。每个缺失族
    补亲和表序首个可行模型，score 置 0（组合层未覆盖，如实标注）。
    """
    from app.lib.cartography.composition_selection import (
        _CATEGORY_MODEL_AFFINITY,
        _candidate_map_models,
    )
    model_reg = get_map_model_registry()
    task_ctx = _task_ctx_from_design(ctx)
    try:
        feasible = {m.id: m for m in _candidate_map_models(task_ctx, model_reg)}
    except Exception:
        return
    covered = set()
    for model_id in pool:
        model = model_reg.get(model_id)
        if model is not None:
            covered.add(_expression_family(model))
    for category in task_ctx.task_categories:
        for model_id in _CATEGORY_MODEL_AFFINITY.get(category, ()):
            if model_id in pool:
                continue
            model = model_reg.get(model_id)
            if model is None:
                continue
            # 扩展可行域：主几何（组合层同款）∪ 次几何（overlay 面数据）。
            if model_id not in feasible and not _model_feasible_extended(
                    model, ctx):
                continue
            via_secondary = model_id not in feasible
            family = _expression_family(model)
            if model_id in _RATE_SEMANTIC_MODELS:
                # 率/密度语义模型与原始计数是不同语义层级（分母门控对），
                # 无条件入池——REQUIRED_SATISFIED/MISSING 的对照解释是
                # 候选引擎的核心价值，不因组合层字母序截断而缺席。
                pool[model_id] = {
                    "composition": "",
                    "score": 0.0,
                    "slots": [],
                    "reasons": [
                        f"类目亲和 {category}（率语义候选，分母门控对）"],
                }
                continue
            if family in covered:
                continue
            pool[model_id] = {
                "composition": "",
                "score": 0.0,
                "slots": [],
                "reasons": [(
                    f"类目亲和 {category}（overlay 次几何表达）" if via_secondary
                    else f"类目亲和 {category}（组合层未覆盖，按亲和表补齐）")],
            }
            covered.add(family)


#: 用户显式要求模型的定向亲和类目（审定静态表，反向查 _CATEGORY_MODEL_AFFINITY）。
def _affinity_categories_for(model_id: str) -> Tuple[str, ...]:
    from app.lib.cartography.composition_selection import (
        _CATEGORY_MODEL_AFFINITY,
    )
    return tuple(
        cat for cat, models in sorted(_CATEGORY_MODEL_AFFINITY.items())
        if model_id in models
    )[:2]


def _model_feasible(model: Any, task_ctx: Any) -> bool:
    """单模型可行域判定（与组合层 ``_candidate_map_models`` 同款硬过滤：
    几何 ∩ 产物；不复制评分）。"""
    if task_ctx.geometry_kind not in (model.geometry_kinds or []):
        return False
    artifact_set = set(task_ctx.artifact_types)
    if artifact_set and model.accepted_artifact_types:
        if not (artifact_set & set(model.accepted_artifact_types)):
            return False
    return True


def _model_feasible_extended(model: Any, ctx: DesignTaskContext) -> bool:
    """扩展可行域：主几何 ∪ 次几何（overlay 面数据在场面族表达可行）。

    组合层候选池只认主几何（其词表语义）；表达层候选在 overlay 面数据
    在场时，面族（区级统计/归一化）对同一制图任务是合法表达——按
    DesignTaskContext 的结构化事实判定，不猜。planned 模型不可行
    （F1：与组合层 native_ids() 同款过滤，未实现的表达不推荐）。
    """
    if getattr(model, "runtime_status", "native") != "native":
        return False
    task_ctx = _task_ctx_from_design(ctx)
    if _model_feasible(model, task_ctx):
        return True
    if model.geometry_kinds and not (
            {ctx.geometry_kind, *ctx.secondary_geometry_kinds}
            & set(model.geometry_kinds)):
        return False
    artifact_set = set(task_ctx.artifact_types)
    if artifact_set and model.accepted_artifact_types:
        if not (artifact_set & set(model.accepted_artifact_types)):
            return False
    return True


def _apply_rules(
    ctx: DesignTaskContext,
    model: Any,
    base: Dict[str, Any],
) -> MapDesignCandidate:
    """R1–R6 门控与装配（纯函数；blocked 只来自科学不可计算）。"""
    codes: List[str] = []
    reasons_zh: List[str] = []
    against: List[str] = []
    risks: List[str] = list(
        getattr(model, "pitfalls_zh", []) or [])[:_MAX_RISKS]
    suitability = CandidateSuitability.SUITABLE

    # ── R1 分母门控 ─────────────────────────────────────────────
    if model.id in _RATE_SEMANTIC_MODELS:
        if ctx.denominator.available:
            denom_cond = DenominatorCondition.REQUIRED_SATISFIED
            reasons_zh.append(
                f"分母在场（{ctx.denominator.field or '字段未名'}，"
                f"{ctx.denominator.unit or '单位未名'}）——率/密度语义可计算")
        else:
            denom_cond = DenominatorCondition.REQUIRED_MISSING
            suitability = CandidateSuitability.BLOCKED
            codes.append("DENOMINATOR_MISSING")
            reasons_zh.append(
                "无真实分母（人口/面积），率/密度不可计算——本方案被阻断，"
                "改用原始计数表达并披露面积偏误")
    else:
        denom_cond = DenominatorCondition.NOT_APPLICABLE
        if ctx.statistic in _DENSITY_CLAIM_STATISTICS and \
                model.classification == "graduated":
            codes.append("DENOMINATOR_MISSING")
            reasons_zh.append(
                "用户语义为密度/率，本方案显示原始计数——图面不得标注为"
                "「每万人/单位面积」口径")
            suitability = CandidateSuitability.CONDITIONAL

    # ── R2 过密点图 ─────────────────────────────────────────────
    family = _expression_family(model)
    point_evidence = ctx.point_count_basis != "unknown" and ctx.point_count > 0
    overplot = point_evidence and ctx.point_count >= POINT_OVERPLOT_THRESHOLD
    if family == "point":
        if overplot:
            codes.append("POINT_OVERPLOT_RISK")
            reasons_zh.append(
                f"点数 {ctx.point_count} ≥ {POINT_OVERPLOT_THRESHOLD}——过密点图"
                "线索互蚀，建议聚合/网格替代")
            suitability = CandidateSuitability.CONDITIONAL
    elif family in ("aggregation", "density_surface") and overplot:
        against.append(f"点图过密（{ctx.point_count} 点），本方案可读性更佳")

    # ── R3 热力诚实 ─────────────────────────────────────────────
    if family == "density_surface":
        codes.append("KDE_NOT_POPULATION_DENSITY")
        risks.append("密度面为核密度估计（KDE/点密度），不是人口密度——"
                     "图例与来源注记必须如实表述")
        if point_evidence and ctx.point_count < HEATMAP_MIN_POINTS:
            suitability = CandidateSuitability.BLOCKED
            codes.append("INSUFFICIENT_POINTS_FOR_DENSITY")
            reasons_zh.append(
                f"点数 {ctx.point_count} < HEATMAP_MIN_POINTS={HEATMAP_MIN_POINTS}"
                "（heatmap_data 工具硬门槛，样本过少热力核无统计意义）")

    # ── R5 聚合质心谬误（比例符号：聚合值定位在质心）────────────
    # 仅 symbol_scalar 族：比例符号把聚合表数值画在行政区质心，位置不具
    # 要素精度；point 族逐要素定位（每个点即真实位置），不受此谬误影响。
    if "admin_aggregate_table" in ctx.artifact_types and \
            family == "symbol_scalar" and \
            ctx.scale_hint in ("district", "city", "region"):
        codes.append("AGGREGATE_CENTROID_PLACEMENT")
        reasons_zh.append("聚合表以比例符号定位在行政区质心——位置不具要素"
                          "精度，图面需明示聚合口径")
        if suitability == CandidateSuitability.SUITABLE:
            suitability = CandidateSuitability.CONDITIONAL

    # ── R6 分类装配 ─────────────────────────────────────────────
    classification: Optional[Dict[str, Any]] = None
    if model.classification == "graduated":
        if ctx.distribution is not None:
            choice = choose_classification(
                ctx.distribution,
                recommended=list(model.recommended_classifiers),
                default_k=model.default_class_count,
            )
            classification = {
                "method": choice.method, "k": choice.k,
                "authority": choice.authority, "source": choice.source,
            }
            reasons_zh.append(
                f"分类裁决：{choice.method}/k={choice.k}（{choice.source}）")
        else:
            codes.append("CLASSIFICATION_DEFERRED")
            reasons_zh.append("分布统计缺席——分类法待数据到位后由分布裁决")

    degradation = _fallback_chain(model.id, get_map_model_registry())
    if suitability == CandidateSuitability.BLOCKED and not degradation:
        # 模型库未声明降级链时给族内最近替代（诚实：明确是启发替代）。
        degradation = ["administrative_choropleth"] if family != "area_stat" else []

    comp_reasons = base.get("reasons") or []
    return MapDesignCandidate(
        candidate_id=f"{model.id}@{family}",
        map_model_id=model.id,
        map_model_name_zh=model.name_zh,
        expression_family=family,
        composition_template_id=base.get("composition") or "",
        score=float(base.get("score") or 0.0),
        suitability=suitability,
        condition_codes=codes,
        condition_reasons_zh=reasons_zh[:_MAX_REASONS],
        data_requirements=list(getattr(model, "accepted_artifact_types", []) or [])[:6],
        denominator_condition=denom_cond,
        classification=classification,
        palette_semantics=model.color_scheme_kind,
        components=[c for c in (base.get("slots") or
                                list(model.recommended_components or []))
                    ][:_MAX_COMPONENTS],
        scientific_risks_zh=risks[:_MAX_RISKS],
        degradation_path=degradation,
        reasons_zh=reasons_zh[:_MAX_REASONS] or comp_reasons[:_MAX_REASONS],
        against_zh=against[:3],
        user_requested=model.id in ctx.user_requested_models,
    )


def generate_design_candidates(
    ctx: DesignTaskContext,
    *,
    primary_model_id: str = "",
) -> DesignCandidateSet:
    """结构化任务事实 → 2–4 个表达模型层候选（确定性，无 IO/LLM）。

    primary_model_id = planner 实际产出的主表达（若提供）：对齐进候选集
    （在场则标记 selected；不在场则作为「planner 实况」候选补入并说明）。
    """
    model_reg = get_map_model_registry()

    pool = _pool_from_composition(ctx)
    _augment_with_affinity_families(ctx, pool)
    # P1-3（评审）：密度/分布类目在亲和表中无率语义模型——用户语义为
    # 密度/率，或分母在场，且存在面表达面（主/次 polygon）时，率语义
    # 模型按可行域直接入池（分母门控对不依赖类目路由运气）。
    _pair_fallback_ids: set = set()
    _polygon_surface = (
        ctx.geometry_kind == "polygon" or "polygon" in ctx.secondary_geometry_kinds)
    _density_semantics = ctx.statistic in _DENSITY_CLAIM_STATISTICS
    if (_density_semantics or ctx.denominator.available) and _polygon_surface:
        for _mid in _RATE_SEMANTIC_MODELS:
            _model = model_reg.get(_mid)
            # F1（评审 R2）：只注入 native 模型（组合层单一事实源同款
            # 过滤——planned 模型不得作为可实施方案推荐，也不可被 pin）。
            if (_model is None
                    or getattr(_model, "runtime_status", "native") != "native"
                    or not _model_feasible_extended(_model, ctx)):
                continue
            if _mid not in pool:
                pool[_mid] = {
                    "composition": "", "score": 0.0, "slots": [],
                    "reasons": [(
                        "分母在场 + 面表达面（率语义候选，分母门控对）"
                        if ctx.denominator.available
                        else "用户密度语义 + 面表达面（率语义候选，分母"
                        "门控对——无分母时 blocked 披露）")],
                }
            # 门控对的原始计数半边：率模型的降级链目标（如
            # normalized→administrative_choropleth）同批入池——blocked 与
            # 可用替代必须成对可见。
            _fb = getattr(_model, "fallback_model_id", "") or ""
            _fb_model = model_reg.get(_fb) if _fb else None
            if _fb_model is not None and _fb not in pool and \
                    _model_feasible_extended(_fb_model, ctx):
                pool[_fb] = {
                    "composition": "", "score": 0.0, "slots": [],
                    "reasons": ["分母门控对的原始计数对照（降级链目标）"],
                }
                _pair_fallback_ids.add(_fb)
            # F7（评审 R2）：只注入一对（审定序首个可行 native 率模型 +
            # 其降级目标）——全部率模型注入会使候选面退化为单一 area_stat
            # 族，破坏「2–4 个可比较表达方案」的族多样性契约。
            break

    # 用户显式要求但组合层未给出的模型：①定向类目再检索一次（仍走同一
    # 组合选择器）；②仍被每类目 top-N 截断挤出 → 可行域直判（复用组合层
    # 同款几何∩产物硬过滤），可行即入池（组合面留空，诚实标注）；
    # ③不可行 → 以 GEOMETRY_INCOMPATIBLE typed block 呈现（用户要求可见，
    # 不静默丢弃、不假成功）。
    requested = [m for m in ctx.user_requested_models if model_reg.resolve(m)]
    incompatible_requested: Dict[str, MapDesignCandidate] = {}
    task_ctx_for_feasibility = None
    for model_id in requested:
        if model_id not in pool:
            extra = _pool_from_composition(
                ctx, extra_categories=_affinity_categories_for(model_id))
            if model_id in extra:
                pool[model_id] = extra[model_id]
                continue
            model = model_reg.get(model_id)
            if model is None:
                continue
            if task_ctx_for_feasibility is None:
                task_ctx_for_feasibility = _task_ctx_from_design(ctx)
            if _model_feasible_extended(model, ctx):
                pool[model_id] = {
                    "composition": "", "score": 0.0, "slots": [],
                    "reasons": ["用户显式要求（可行域直判，组合层未覆盖）"],
                }
            else:
                blocked = _apply_rules(ctx, model, {
                    "composition": "", "score": 0.0, "slots": [],
                    "reasons": ["用户显式要求（与数据形态不匹配）"],
                })
                blocked.suitability = CandidateSuitability.BLOCKED
                _tctx = task_ctx_for_feasibility
                if _tctx.geometry_kind not in (model.geometry_kinds or []):
                    blocked.condition_codes.append("GEOMETRY_INCOMPATIBLE")
                    blocked.condition_reasons_zh.append(
                        f"表达需 {','.join(model.geometry_kinds or ['?'])} 几何，"
                        f"数据为 {_tctx.geometry_kind}——不可行")
                else:
                    blocked.condition_codes.append("DATA_REQUIREMENT_UNMET")
                    blocked.condition_reasons_zh.append(
                        "在场产物不满足表达数据要求（需 "
                        + ", ".join(model.accepted_artifact_types[:3])
                        + "）——先执行对应分析产出该数据，再切换表达")
                incompatible_requested[model_id] = blocked

    if not pool:
        return DesignCandidateSet()

    # 表达族去重：同族保组合层分最高者。例外——率/密度语义模型与其原始
    # 计数同族但语义层级不同（typed block + 降级目标必须成对可见），
    # 不参与去重；用户显式要求亦不去重（blocked 也要可见）。
    by_family: Dict[str, MapDesignCandidate] = {}
    requested_overrides: Dict[str, MapDesignCandidate] = {}
    rate_semantic_survivors: List[MapDesignCandidate] = []
    for model_id in sorted(pool):
        model = model_reg.get(model_id)
        if model is None:
            continue
        cand = _apply_rules(ctx, model, pool[model_id])
        if model_id in requested:
            requested_overrides[model_id] = cand
            continue
        if model_id in _RATE_SEMANTIC_MODELS or model_id in _pair_fallback_ids:
            rate_semantic_survivors.append(cand)
            continue
        fam = cand.expression_family
        if fam not in by_family or cand.score > by_family[fam].score:
            by_family[fam] = cand

    # planner 实况候选（primary 不在池内时补入，诚实对齐）。
    primary_in_pool = bool(primary_model_id) and primary_model_id in pool
    primary_cand: Optional[MapDesignCandidate] = None
    if primary_model_id and not primary_in_pool:
        model = model_reg.get(primary_model_id)
        if model is not None:
            base = pool.get(primary_model_id) or {
                "composition": "", "score": 0.0, "slots": [],
                "reasons": ["planner 实际产物（组合层未覆盖，如实补入）"],
            }
            primary_cand = _apply_rules(ctx, model, base)
            if primary_model_id in requested:
                requested_overrides[primary_model_id] = primary_cand

    def _rank(c: MapDesignCandidate) -> Tuple[int, float, str]:
        """planner 实况优先；其次按「组合分 − 适宜性罚分」混合排序。

        罚分而非字典序：CONDITIONAL 只降 0.25 分——最高分的条件候选
        （如 1.08 的分级点图）仍应压过 0.62 分的普通候选；BLOCKED 罚 4.0
        排到队尾但不剔除（用户显式要求时由保留席位保证可见）。
        """
        penalty = {
            CandidateSuitability.SUITABLE: 0.0,
            CandidateSuitability.CONDITIONAL: 0.25,
            CandidateSuitability.BLOCKED: 4.0,
        }[c.suitability]
        is_primary = c.map_model_id == primary_model_id
        return (0 if is_primary else 1, -(c.score - penalty), c.map_model_id)

    requested_overrides.update(incompatible_requested)
    merged = (list(by_family.values()) + rate_semantic_survivors
              + list(requested_overrides.values()))
    if primary_cand is not None and primary_model_id not in requested:
        merged.append(primary_cand)
    merged.sort(key=_rank)
    # 用户显式要求与 planner 实况候选保留席位（blocked 也必须可见——
    # typed block 是可解释决策，不是静默丢弃），其余按序补足上限。
    # 分母门控对在密度语义/分母证据在场时占保留席位——cap-4 罚分排序
    # 不得把门控对挤出（入池 ≠ 入集）。只保留**首个**率语义模型 + 其直接
    # 降级目标（全部率模型占席会反把原始计数对照挤出，评审 R1 实证）。
    _pair_reserved = (
        _density_semantics or ctx.denominator.available) and _polygon_surface
    _rate_reserved_ids: set = set()      # 率成员（硬席位，溢出不弃）
    _fallback_reserved_ids: set = set()  # 对照成员（软席位，溢出可弃——
    # 其信息保留在率候选的 degradation_path，成对语义不丢失）
    if _pair_reserved:
        _merged_ids = {c.map_model_id for c in merged}
        for _mid in _RATE_SEMANTIC_MODELS:  # 审定优先序（normalized 优先）
            if _mid in _merged_ids:
                _rate_reserved_ids.add(_mid)
                for _fb in _fallback_chain(_mid, model_reg)[:1]:
                    _fallback_reserved_ids.add(_fb)
                break
    _pair_all = _rate_reserved_ids | _fallback_reserved_ids
    reserved = [
        c for c in merged
        if c.user_requested or c.map_model_id == primary_model_id
        or c.map_model_id in _pair_all]
    rest = [c for c in merged
            if not (c.user_requested or c.map_model_id == primary_model_id
                    or c.map_model_id in _pair_all)]
    candidates = (reserved + rest)[:MAX_DESIGN_CANDIDATES]
    # F3（评审 R2）：席位溢出角落（pin + bare_density + primary + 对 = 5 > 4）
    # 此前按排序切，blocked 率半边可能殿后被挤出——「门控对成对可见」是
    # 铁律：缺席时从尾部剔除非对内、非用户/非 primary 候选换入。
    _final_ids = {c.map_model_id for c in candidates}
    for _pair_id in sorted(_rate_reserved_ids):
        if _pair_id in _final_ids:
            continue
        _pair_cand = next(
            (c for c in merged if c.map_model_id == _pair_id), None)
        if _pair_cand is None:
            continue
        for _i in range(len(candidates) - 1, -1, -1):
            _c = candidates[_i]
            # 可换出：非硬席位者。对照成员（软席位）亦可被率成员换出——
            # 其表达保留在率候选 degradation_path，成对语义不丢失。
            if (_c.map_model_id not in _rate_reserved_ids
                    and not _c.user_requested
                    and _c.map_model_id != primary_model_id):
                candidates[_i] = _pair_cand
                _final_ids = {c.map_model_id for c in candidates}
                break

    # selected 对齐：primary 优先；缺席则取首个非 blocked；全 blocked 如实空。
    selected_id = ""
    basis = ""
    if primary_model_id:
        match = next((c for c in candidates
                      if c.map_model_id == primary_model_id), None)
        if match is not None:
            selected_id = match.candidate_id
            basis = "planner_primary" + (
                "" if primary_in_pool else "（组合层未覆盖，已如实补入）")
    if not selected_id:
        match = next((c for c in candidates
                      if c.suitability != CandidateSuitability.BLOCKED), None)
        if match is not None:
            selected_id = match.candidate_id
            basis = "top_eligible"

    handling: Dict[str, List[str]] = {
        "requested": list(requested),
        "honored": [
            m for m in requested
            if any(c.map_model_id == m and c.suitability !=
                   CandidateSuitability.BLOCKED for c in candidates)
        ],
        "typed_blocked": [
            m for m in requested
            if any(c.map_model_id == m and c.suitability ==
                   CandidateSuitability.BLOCKED for c in candidates)
        ],
    }
    return DesignCandidateSet(
        candidates=candidates,
        selected_candidate_id=selected_id,
        selection_basis=basis,
        user_request_handling=handling,
    )


__all__ = [
    "DESIGN_CANDIDATES_VERSION",
    "MAX_DESIGN_CANDIDATES",
    "POINT_OVERPLOT_THRESHOLD",
    "DenominatorEvidence",
    "DesignTaskContext",
    "DenominatorCondition",
    "CandidateSuitability",
    "MapDesignCandidate",
    "DesignCandidateSet",
    "generate_design_candidates",
]
