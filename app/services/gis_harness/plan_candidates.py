"""Multi-Candidate GIS Planning —— 多候选工作流规划与可解释选择（Goal V3）。

把「keyword 路由取 top-1」升级为「候选生成 → 科学合法过滤 → 数据资格
评分 → 成本/质量估计 → 工具与制图可用性检查 → 确定性选择」，并保留
完整候选集（selected + rejected + rejection reasons）供 trace / replay /
evaluation 消费。

候选来源（全部既有事实，不新造真相）：

- RecipeRegistry.select_candidates（语义路由 top-N）；
- CompositeRecipe（本体任务 / 显式形态信号触发；supporting 层带资格条件）；
- ScenarioTemplate（主体词 + 本体任务双信号激活 → 场景候选变体）。

红线：

- 全部确定性：同输入同候选集同选择（零 LLM / 零 I/O）；
- 科学合法性过滤复用 recipes.check_eligibility + workflow_schema 义务
  评估（不重复科学语义）；
- 工具可用性委托 AlgorithmResolver（capability → 算法 → 工具链）；
- 候选集是**解释与证据**：compiler 只在「现选择被科学阻断而候选可行」
  时改写 recipe 选择（零漂移），其余场景下计划仍由既有管线产出。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field

#: 评分维度（§12 evaluation metrics 对齐；全部 0-1，缺证据记 0.5 中性）。
SCORE_DIMENSIONS = (
    "semantic_fit",         # 本体/任务/关键词语义贴合
    "data_fit",             # 数据资格状态（eligible=1 / unknown=0.5 / blocked=0）
    "scientific_validity",  # 无科学阻断（block_method / 数据 block）
    "cost",                 # 成本反相（工具调用/计算估计越低越好）
    "latency",              # 时延反相（tier 估计）
    "determinism",          # 确定性（planned 能力/外部依赖降分）
    "user_intent",          # 显式形态信号（aggregate_grid/proportional_symbol）命中
    "output_quality",       # 期望产出质量（制图丰富度/组件/图表）
    "fallback_quality",     # 声明式回退完善度
)

#: 数据资格状态 → data_fit 得分（确定性映射；unknown 中性 0.5）。
_DATA_FIT_SCORE = {
    "eligible": 1.0,
    "transform_required": 0.75,
    "degraded": 0.4,
    "blocked": 0.0,
    "unknown": 0.5,
}

#: 公共别名（防副本漂移，同 workflow_schema.DENOMINATOR_FIELD_HINTS 前例）：
#: 方法资格引擎（workflow_v4.methodology）消费同一映射，不复制词表。
DATA_FIT_SCORE = _DATA_FIT_SCORE

#: 维度权重（确定性；科学合法与数据贴合主导，意图与质量次之）。
_DIMENSION_WEIGHTS = {
    "semantic_fit": 0.20,
    "data_fit": 0.22,
    "scientific_validity": 0.20,
    "cost": 0.06,
    "latency": 0.04,
    "determinism": 0.06,
    "user_intent": 0.08,
    "output_quality": 0.09,
    "fallback_quality": 0.05,
}


class CostEstimate(BaseModel):
    """确定性成本估计（规划期静态，不含运行期数据量修正）。"""
    tool_call_estimate: int = 0        # 基础工具调用数估计
    latency_tier: int = 1              # 1(轻) .. 3(重)
    uses_planned_capability: bool = False

    @property
    def cost_score(self) -> float:
        """0-1：调用少、层级轻 → 高分（确定性归一）。"""
        call_part = max(0.0, 1.0 - self.tool_call_estimate / 12.0)
        tier_part = 1.0 - (self.latency_tier - 1) / 2.0
        return round(0.6 * call_part + 0.4 * tier_part, 3)


class PlanCandidate(BaseModel):
    """一个可行工作流候选（含评分与拒绝理由的完整证据）。"""
    candidate_id: str                  # recipe:<id> / composite:<id> / scenario:<sid>:recipe:<id>
    kind: str                          # recipe | composite | scenario_variant
    recipe_id: str                     # 承载计划的产品族
    composite_id: str = ""
    scenario_id: str = ""
    ontology_tasks: Tuple[str, ...] = ()
    scores: Dict[str, float] = Field(default_factory=dict)
    score: float = 0.0
    # 资格/可用性证据
    data_fit_state: str = "unknown"    # 候选角色集的最差资格状态
    method_blockers: Tuple[str, ...] = ()
    data_blockers: Tuple[str, ...] = ()
    tools_available: Optional[bool] = None   # None = 未提供 available_tools（不阻断）
    unavailable_capabilities: Tuple[str, ...] = ()
    map_models: Tuple[str, ...] = ()
    disclosures: Tuple[str, ...] = ()
    cost: CostEstimate = Field(default_factory=CostEstimate)
    # selected / feasible / rejected
    status: str = "feasible"
    rejection_reasons: Tuple[str, ...] = ()

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "candidate_id": self.candidate_id[:80],
            "kind": self.kind,
            "recipe_id": self.recipe_id[:64],
            "composite_id": self.composite_id[:64],
            "scenario_id": self.scenario_id[:64],
            "ontology_tasks": [t[:64] for t in self.ontology_tasks[:4]],
            "scores": {k: round(v, 3) for k, v in
                       list(self.scores.items())[:len(SCORE_DIMENSIONS)]},
            "score": round(self.score, 3),
            "data_fit_state": self.data_fit_state,
            "method_blockers": list(self.method_blockers[:4]),
            "data_blockers": list(self.data_blockers[:4]),
            "tools_available": self.tools_available,
            "unavailable_capabilities": [
                c[:48] for c in self.unavailable_capabilities[:4]],
            "map_models": [m[:48] for m in self.map_models[:4]],
            "disclosures": [d[:120] for d in self.disclosures[:4]],
            "cost": {
                "tool_call_estimate": self.cost.tool_call_estimate,
                "latency_tier": self.cost.latency_tier,
                "uses_planned_capability": self.cost.uses_planned_capability,
            },
            "status": self.status,
            "rejection_reasons": [r[:80] for r in self.rejection_reasons[:4]],
        }


class PlanCandidateSet(BaseModel):
    """候选集：selected + rejected（完整 trace/replay 证据）。"""
    query: str = ""
    candidates: List[PlanCandidate] = Field(default_factory=list)
    selected_id: str = ""
    rerouted: bool = False             # True = 相对语义 top-1 发生了改选
    reroute_reason: str = ""

    @property
    def selected(self) -> Optional[PlanCandidate]:
        return next((c for c in self.candidates if c.candidate_id == self.selected_id),
                    None)

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "query": self.query[:200],
            "selected_id": self.selected_id[:80],
            "rerouted": self.rerouted,
            "reroute_reason": self.reroute_reason[:160],
            "candidates": [c.to_bounded_dict() for c in self.candidates[:8]],
        }


# ── 候选生成与评分（确定性纯函数）────────────────────────────────────────


def _worst_state(states: List[str]) -> str:
    order = ["blocked", "degraded", "unknown", "transform_required", "eligible"]
    for s in order:
        if s in states:
            return s
    return "unknown"


def _semantic_fit(
    intent: Any,
    ontology_task_ids: List[str],
    recipe: Any,
    routing_rank: Optional[int] = None,
) -> float:
    """语义贴合 = 任务/本体语义 + 语义路由名次（确定性）。

    语义路由（select_candidates 十一层序，含 seed 资历）是产品族稳定性的
    既有语义权威 —— 多候选评分在其**内部**精化，不得越过它：routing
    rank 以 0.5 权重并入（rank0=1.0、rank1=0.5、rank2≈0.33 …），未入池
    的组合层候选不获得名次加成。
    """
    score = 0.4
    task = getattr(intent, "task", "")
    if task and task in (recipe.intent_tasks or []):
        score += 0.4
    declared = set(getattr(recipe, "ontology_tasks", None) or [])
    if declared and set(ontology_task_ids) & declared:
        score += 0.2
    if routing_rank is not None:
        rank_component = 1.0 / (1.0 + routing_rank)
        score = 0.5 * score + 0.5 * rank_component
    return min(1.0, score)


def _user_intent_fit(intent: Any, recipe: Any) -> float:
    cartography = set(getattr(intent, "cartography_intents", []) or [])
    cart_set = set(recipe.intent_cartography or [])
    if cartography and cart_set:
        return min(1.0, 0.5 + 0.5 * len(cartography & cart_set) / len(cartography))
    return 0.5


def _output_quality(recipe: Any, ontology_scores: Dict[str, float]) -> float:
    """确定性启发式：制图/组件/图表丰富度 + 本体制图期望对齐。"""
    score = 0.4
    if recipe.primary_cartography:
        score += 0.15
    score += min(0.2, 0.04 * len(recipe.secondary_cartography or []))
    score += min(0.15, 0.03 * len(recipe.default_components or []))
    # 本体制图期望交集（期望的制图形态越对齐，期望产出质量越高）
    from app.services.gis_harness.gis_ontology import get_task_ontology
    expected: set = set()
    for tid in getattr(recipe, "ontology_tasks", None) or []:
        desc = get_task_ontology().get(tid)
        if desc is not None:
            expected.update(desc.cartographic_expectations)
    if expected and recipe.primary_cartography in expected:
        score += 0.1
    return min(1.0, score)


def _fallback_quality(recipe: Any) -> float:
    fb = len(recipe.fallbacks or [])
    wf = recipe.workflow
    wf_fb = len(wf.fallback_policies) if wf is not None else 0
    return min(1.0, 0.3 + 0.15 * (fb + wf_fb))


def _estimate_cost(
    recipe: Any,
    unavailable_caps: Tuple[str, ...] = (),
) -> CostEstimate:
    calls = 1 + len(recipe.preferred_analysis or [])
    heavy_caps = {"spatial_interpolation", "regression_kriging", "gwr",
                  "terrain_hydrology_advanced", "mcda_evaluation"}
    tier = 2 if heavy_caps & set(recipe.preferred_analysis or []) else 1
    # planned 事实来自 _check_tools 的 resolver 裁决（capability:reason
    # 形态）——空 dict 恒 None 的死代码路径已移除（review A2）。
    planned = bool(unavailable_caps)
    return CostEstimate(tool_call_estimate=min(calls, 12), latency_tier=tier,
                        uses_planned_capability=planned)


def _check_tools(
    recipe: Any,
    *,
    profile: Optional[Dict[str, Any]],
    available_tools: Optional[List[str]],
) -> Tuple[Optional[bool], Tuple[str, ...]]:
    """工具可用性检查（委托 AlgorithmResolver；零 available_tools 时 None）。

    unavailable 形如 ``capability:reason``—— 同时是 planned 事实的单一
    来源（resolver 裁决该能力链当前不可用 = 只能近似/代理）。
    """
    if not recipe.preferred_analysis:
        return None, ()
    from app.lib.gis.algorithm_resolver import get_algorithm_resolver

    resolver = get_algorithm_resolver()
    unavailable: List[str] = []
    for cap in recipe.preferred_analysis[:8]:
        resolution = resolver.resolve(
            cap, profile=profile, available_tools=available_tools)
        if resolution.status != "resolved":
            unavailable.append(f"{cap}:{resolution.reason[:40]}")
    if not unavailable:
        return True, ()
    if len(unavailable) < max(1, len(recipe.preferred_analysis[:8])):
        return None, tuple(unavailable)   # 部分可用：不阻断，评分体现
    return False, tuple(unavailable)


def _evaluate_candidate(
    candidate_id: str,
    kind: str,
    recipe_id: str,
    *,
    composite_id: str = "",
    scenario_id: str = "",
    intent: Any,
    ontology_task_ids: List[str],
    ontology_scores: Dict[str, float],
    profile: Optional[Dict[str, Any]],
    available_tools: Optional[List[str]],
    disclosures: Tuple[str, ...] = (),
    routing_rank: Optional[int] = None,
) -> PlanCandidate:
    from app.services.gis_harness.recipes import (
        check_eligibility,
        get_recipe_registry,
    )
    from app.services.gis_harness.workflow_schema import (
        evaluate_workflow_obligations,
        resolve_data_roles,
    )

    registry = get_recipe_registry()
    recipe = registry.get(recipe_id)
    if recipe is None:
        return PlanCandidate(
            candidate_id=candidate_id, kind=kind, recipe_id=recipe_id,
            composite_id=composite_id, scenario_id=scenario_id,
            status="rejected",
            rejection_reasons=("recipe_not_registered",),
        )

    # ── 科学合法过滤（复用既有资格/义务评估，不重复科学语义）─────────
    method_blockers: List[str] = []
    data_blockers: List[str] = []
    data_states: List[str] = []
    if recipe.workflow is not None:
        role_resolutions = resolve_data_roles(recipe_id, recipe.workflow,
                                              resolver_profile=profile)
        contract = evaluate_workflow_obligations(
            recipe_id, recipe.workflow, resolver_profile=profile,
            role_resolutions=role_resolutions)
        method_blockers = list(contract.method_blockers)
        data_blockers = list(contract.data_blockers)
        # 数据资格（V3 typed data qualification）
        if profile is not None and recipe.workflow.data_roles:
            from app.services.gis_harness.data_qualification import (
                qualify_workflow_data_roles,
            )
            quals = qualify_workflow_data_roles(
                recipe.workflow.data_roles, role_resolutions,
                resolver_profile=profile)
            data_states = [q.state for q in quals]
    eligibility = check_eligibility(
        recipe, profile=profile) if profile is not None else None
    if eligibility is not None and not eligibility.eligible:
        data_blockers = list(data_blockers) + [
            d.reason_code for d in eligibility.disabled[:2]]

    tools_available, unavailable_caps = _check_tools(
        recipe, profile=profile, available_tools=available_tools)

    # ── 评分（§12 维度；确定性）─────────────────────────────────────
    data_fit_state = _worst_state(data_states) if data_states else (
        "blocked" if data_blockers else "unknown")
    semantic = _semantic_fit(intent, ontology_task_ids, recipe,
                             routing_rank=routing_rank)
    # 本体匹配得分加成（命中本体主任务 +）
    if ontology_task_ids and set(recipe.ontology_tasks or []) & set(
            ontology_task_ids[:1]):
        semantic = min(1.0, semantic + 0.15)
    validity = 1.0 if not method_blockers else 0.0
    cost = _estimate_cost(recipe, unavailable_caps)
    determinism = 0.6 if cost.uses_planned_capability else 1.0
    scores = {
        "semantic_fit": round(semantic, 3),
        "data_fit": _DATA_FIT_SCORE.get(data_fit_state, 0.5),
        "scientific_validity": validity,
        "cost": cost.cost_score,
        "latency": round(1.0 - (cost.latency_tier - 1) / 2.0, 3),
        "determinism": determinism,
        "user_intent": _user_intent_fit(intent, recipe),
        "output_quality": _output_quality(recipe, ontology_scores),
        "fallback_quality": _fallback_quality(recipe),
    }
    score = round(sum(scores[d] * _DIMENSION_WEIGHTS[d] for d in SCORE_DIMENSIONS), 4)

    rejection: List[str] = []
    if method_blockers:
        rejection.append(f"science_blocked:{method_blockers[0]}")
    if data_blockers:
        rejection.append(f"data_blocked:{data_blockers[0]}")
    if tools_available is False:
        rejection.append("tools_unavailable")
    status = "rejected" if rejection else "feasible"

    return PlanCandidate(
        candidate_id=candidate_id, kind=kind, recipe_id=recipe_id,
        composite_id=composite_id, scenario_id=scenario_id,
        ontology_tasks=tuple(ontology_task_ids[:4]),
        scores=scores, score=score,
        data_fit_state=data_fit_state,
        method_blockers=tuple(method_blockers[:4]),
        data_blockers=tuple(data_blockers[:4]),
        tools_available=tools_available,
        unavailable_capabilities=unavailable_caps[:4],
        map_models=tuple([
            m for m in ([recipe.primary_cartography]
                        + list(recipe.secondary_cartography or [])) if m][:4]),
        disclosures=disclosures,
        cost=cost,
        status=status,
        rejection_reasons=tuple(rejection),
    )


def generate_plan_candidates(
    intent: Any,
    *,
    profile: Optional[Dict[str, Any]] = None,
    available_tools: Optional[List[str]] = None,
    limit: int = 6,
) -> PlanCandidateSet:
    """生成 + 评估 + 选择候选（确定性；同输入同输出）。

    候选池 = 语义路由 top-N recipe + 触发的 composite + 激活 scenario 的
    场景变体；评分排序后选择最优 feasible 候选。科学合法过滤不通过的
    候选保留在集合中并携带拒绝理由（可解释性红线）。
    """
    from app.services.gis_harness.gis_ontology import match_task_ontology
    from app.services.gis_harness.workflow_families import (
        get_workflow_family_registry,
    )
    from app.services.gis_harness.recipes import get_recipe_registry

    registry = get_recipe_registry()
    layer = get_workflow_family_registry()
    onto_matches = match_task_ontology(intent, limit=5)
    ontology_ids = [m.task_id for m in onto_matches]
    ontology_scores = {m.task_id: m.score for m in onto_matches}
    query = str(getattr(intent, "query", "") or "")

    candidates: List[PlanCandidate] = []
    seen: set = set()

    def _add(candidate: PlanCandidate) -> None:
        if candidate.candidate_id in seen:
            return
        seen.add(candidate.candidate_id)
        candidates.append(candidate)

    # 1) 语义路由 recipe 候选（名次是语义权威：并入 semantic_fit）
    recipe_candidates = registry.select_candidates(intent, limit=limit)
    routing_rank: Dict[str, int] = {
        r.id: i for i, r in enumerate(recipe_candidates)}
    for idx, recipe in enumerate(recipe_candidates):
        _add(_evaluate_candidate(
            f"recipe:{recipe.id}", "recipe", recipe.id,
            intent=intent, ontology_task_ids=ontology_ids,
            ontology_scores=ontology_scores, profile=profile,
            available_tools=available_tools,
            routing_rank=routing_rank.get(recipe.id),
        ))

    # 2) composite 候选（本体任务 / 显式形态信号触发；base 已在池中则合并证据）
    cartography_intents = set(getattr(intent, "cartography_intents", []) or [])
    for composite in layer.composites:
        task_hit = bool(set(composite.trigger_ontology_tasks) & set(ontology_ids))
        cart_hit = bool(set(composite.trigger_cartography) & cartography_intents)
        if not (task_hit or cart_hit):
            continue
        _add(_evaluate_candidate(
            f"composite:{composite.composite_id}", "composite",
            composite.base_recipe_id,
            composite_id=composite.composite_id,
            intent=intent, ontology_task_ids=ontology_ids,
            ontology_scores=ontology_scores, profile=profile,
            available_tools=available_tools,
            disclosures=tuple(composite.disclosures),
            routing_rank=routing_rank.get(composite.base_recipe_id),
        ))

    # 3) scenario 候选变体（主体词 + 本体任务双信号）
    for scenario in layer.match_scenarios(intent, ontology_ids):
        for rid in scenario.candidate_recipes[:4]:
            _add(_evaluate_candidate(
                f"scenario:{scenario.scenario_id}:recipe:{rid}",
                "scenario_variant", rid,
                scenario_id=scenario.scenario_id,
                intent=intent, ontology_task_ids=ontology_ids,
                ontology_scores=ontology_scores, profile=profile,
                available_tools=available_tools,
                disclosures=(scenario.minimal_disclosure,) if scenario.minimal_disclosure else (),
                routing_rank=routing_rank.get(rid),
            ))

    # ── base 语义相关性过滤（路由权威红线）────────────────────────────
    # composite / scenario 变体是 base 产品族的**增强层**，不是路由覆盖：
    # base recipe 与 intent task 无关且不是语义 top-1 时，不得成为选择
    # （保留在候选集中并携带拒绝理由，供 trace/evaluation）。
    top1_recipe_id = next(
        (c.recipe_id for c in candidates if c.kind == "recipe"), None)
    task = str(getattr(intent, "task", "") or "")
    for c in candidates:
        if c.kind == "recipe" or c.status == "rejected":
            continue
        base = registry.get(c.recipe_id)
        if base is None:
            continue
        base_task_hit = bool(task and task in (base.intent_tasks or []))
        if not base_task_hit and c.recipe_id != top1_recipe_id:
            c.status = "rejected"
            c.rejection_reasons = c.rejection_reasons + (
                "base_not_task_relevant",)

    # ── 确定性排序：feasible 先于 rejected；score 降序；池内序；id 兜底 ──
    pool_order = {c.candidate_id: i for i, c in enumerate(candidates)}
    candidates.sort(key=lambda c: (
        0 if c.status == "feasible" else 1,
        -c.score,
        pool_order[c.candidate_id],
        c.candidate_id,
    ))

    candidate_set = PlanCandidateSet(query=query, candidates=candidates)
    feasible = next((c for c in candidates if c.status == "feasible"), None)
    if feasible is not None:
        candidate_set.selected_id = feasible.candidate_id
        # 零漂移改写规则：仅当语义 top-1 recipe 被科学阻断而最优候选可行
        # 时，选择才改写 recipe（其余场景计划仍由既有管线产出）。
        top1 = next((c for c in candidates if c.kind == "recipe"), None)
        if top1 is not None and top1.status == "rejected" \
                and feasible.recipe_id != top1.recipe_id:
            candidate_set.rerouted = True
            candidate_set.reroute_reason = (
                f"top1_blocked:{'|'.join(top1.rejection_reasons[:2])}")
    else:
        # 全部候选被拒：保留 top-1（其拒绝理由即最终 verdict 证据）
        first = candidates[0] if candidates else None
        if first is not None:
            candidate_set.selected_id = first.candidate_id
    for c in candidate_set.candidates:
        if c.candidate_id != candidate_set.selected_id and c.status == "feasible":
            c.status = "rejected"
            c.rejection_reasons = c.rejection_reasons + ("score_ranked_lower",)
        elif (c.candidate_id == candidate_set.selected_id
                and c.status == "feasible"):
            # 仅 feasible 候选可成为 selected —— 全拒场景的 selected_id
            # 只是「最优拒绝证据」的指向，不得把拒绝候选翻转为 selected。
            c.status = "selected"
    return candidate_set


__all__ = [
    "SCORE_DIMENSIONS",
    "CostEstimate",
    "PlanCandidate",
    "PlanCandidateSet",
    "generate_plan_candidates",
]
