"""Method Candidate Ranker V2 —— 混合排序 + Abstention（Epic 11 §5.E）。

在 V4 资格裁决之上的**排序适配层**：V4 已回答「这个方法在数据事实上
成立吗」（资格/拒绝），本模块回答「成立的候选里哪个最合适、
证据不足时是否弃权」。

确定性混合分（架构 §3.5 冻结权重）：

    score = 0.30*taxonomy + 0.25*qualification + 0.15*graph_compat
          + 0.10*lexical + 0.10*prior + 0.05*cost_fit + 0.05*constraint_fit

- taxonomy：类目匹配证据（``taxonomy.match_query`` 投影，非新词表；
  V4 族路由优先——ranker 不推翻 V4 的族裁决，只能在其族内排序）；
- qualification：``qualification.qualify_method`` 报告级状态映射
  （viable=1.0 / unknown=0.5 / degraded=0.6 / rejected=0.0）×
  事实置信度折算；
- graph_compat：知识图兼容（族归属 + 产物 ∈ 类别期望产物投影）；
- lexical：方法标签/族关键词命中（V4 词表投影，长短语加权）；
- prior：V4 priority 归一（专业首选先验）；
- cost_fit：computational_class → 确定性成本分（closed_form 最低成本）；
- constraint_fit：用户输出约束（不确定性要求 / 避免近似）满足度。

Abstention（返回空选择 + 稳定理由码，**不是**失败）：
  ``ABSTAIN_ALL_REJECTED``（该族在本事实上无成立方法）/
  ``ABSTAIN_AMBIGUOUS_TIE``（类目歧义且 top 分差 < ε）/
  ``ABSTAIN_NO_EVIDENCE``（零词汇/类目/族证据——乱码/无关 query）。

基准（反循环）：method_corpus 先于本模块调参冻结；invalid gold 可由
既有 oracle 复算；报告披露 lexical-only 失败占比。
零 LLM、零 I/O、同输入同输出。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

from pydantic import BaseModel, Field

from app.lib.gis.methodology.descriptors import (
    get_method_descriptor_registry,
)
from app.lib.gis.methodology.qualification import (
    MethodQualificationReport,
    QualificationFacts,
    qualify_method,
)

#: 冻结权重（架构 §3.5；总和 = 1.0，测试锁定）。
WEIGHT_TAXONOMY = 0.30
WEIGHT_QUALIFICATION = 0.25
WEIGHT_GRAPH = 0.15
WEIGHT_LEXICAL = 0.10
WEIGHT_PRIOR = 0.10
WEIGHT_COST = 0.05
WEIGHT_CONSTRAINT = 0.05

#: 平局弃权阈值（top1-top2 同类别歧义）。
_TIE_EPSILON = 0.02

#: computational_class → 确定性成本分（低成本低分耗）。
_COST_SCORE = {
    "closed_form": 1.0,
    "local_neighborhood": 0.9,
    "iterative": 0.75,
    "combinatorial": 0.65,
    "simulation": 0.5,
}

_STATUS_SCORE = {
    "viable": 1.0,
    "unknown": 0.5,
    "degraded": 0.6,
    "rejected": 0.0,
}

#: transform 级 degraded 的软惩罚豁免集（可自动修复：重投影/空值过滤/
#: 前置条件 transform）——修复链显式化后近似 viable，不与真实数据降级
#: 同罚（坡度@4326 需重投影 ≠ 方法不合适）。
_SOFT_TRANSFORM_DIMS = frozenset({
    "crs_scale", "nodata_quality", "scientific_precondition"})

#: V4 族路由一致性加分（ranker 不推翻 V4 路由；池化排序内以加分表达）。
_ROUTING_BONUS = 0.15

#: 不确定性查询词 → uncertainty_support 的语义满足分（查询显式要求
#: 不确定性产物时：field=完全满足；none=完全不满足——词面到不了的
#: 产出语义差异，只有克里金族产预测方差）。
_UNCERTAINTY_QUERY_TERMS = ("不确定性", "方差", "误差面", "置信", "可靠")
_UNCERTAINTY_SUPPORT_SCORE = {"field": 1.0, "ensemble": 0.8, "analytic": 0.5}

#: 最小层兜底族（ontology minimal tier 的物化：全部候选被拒/降级时，
#: 安全、真实、不误导的描述性输出）。
_FALLBACK_FAMILY = "descriptive_mapping"
_FALLBACK_METHODS = ("descriptive.simple_display", "density.visual_heatmap")

ABSTAIN_ALL_REJECTED = "ABSTAIN_ALL_REJECTED"
ABSTAIN_AMBIGUOUS_TIE = "ABSTAIN_AMBIGUOUS_TIE"
ABSTAIN_NO_EVIDENCE = "ABSTAIN_NO_EVIDENCE"


class MethodCandidateScore(BaseModel):
    """一个候选方法的评分与解释（有界投影）。"""

    method_id: str
    family_id: str = ""
    score: float = 0.0
    components: Dict[str, float] = Field(default_factory=dict)
    qualification_status: str = "unknown"
    reason_codes: List[str] = Field(default_factory=list)
    disclosures: List[str] = Field(default_factory=list)

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "method_id": self.method_id[:64],
            "family_id": self.family_id[:40],
            "score": round(self.score, 4),
            "components": {k[:20]: round(v, 3)
                           for k, v in list(self.components.items())[:7]},
            "status": self.qualification_status[:12],
            "reason_codes": [c[:72] for c in self.reason_codes[:6]],
            "disclosures": [d[:160] for d in self.disclosures[:3]],
        }


class RankResult(BaseModel):
    """一次排序结果（selected + 全序 + abstention 语义）。"""

    selected: Optional[MethodCandidateScore] = None
    abstained: bool = False
    abstain_reason: str = ""
    ranked: List[MethodCandidateScore] = Field(default_factory=list)
    #: 裁决依据（类别/族/证据命中；解释面）
    evidence: Dict[str, Any] = Field(default_factory=dict)

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "selected": self.selected.to_bounded_dict()
            if self.selected else None,
            "abstained": self.abstained,
            "abstain_reason": self.abstain_reason[:32],
            "ranked": [r.to_bounded_dict() for r in self.ranked[:8]],
            "evidence": {
                str(k)[:24]: v for k, v in
                list(self.evidence.items())[:6]
            },
        }


def _taxonomy_component(
    query: str,
    category_id: str,
    taxonomy: Any,
) -> float:
    """类目匹配证据（0-1；查询命中该类目的归一分）。"""
    if not query:
        return 0.5  # 无查询证据：中性（资格与图分量接管）
    matches = taxonomy.match_query(query, limit=20)
    for cid, score in matches:
        if cid == category_id:
            return max(score, 0.05)
    return 0.0


def _graph_component(
    method_id: str,
    family_id: str,
    category_families: Sequence[str],
    category_artifacts: Sequence[str],
    candidate: Any,
    graph: Any,
    *,
    routing_bonus: float = 0.0,
) -> float:
    """图兼容：族归属为主，产物对齐加分（graph 只作只读投影查询）。"""
    score = 0.0
    if family_id in category_families:
        score = 0.7
    produced = set(candidate.output_artifacts or ())
    if produced and category_artifacts:
        overlap = produced & set(category_artifacts)
        if overlap:
            score = min(1.0, score + 0.3)
    if graph is not None and score == 0.0:
        # 族不在类别覆盖内但图上有 alternative 边 → 弱兼容
        alts = graph.neighbors(f"method:{method_id}",
                               relation="alternative_method")
        if alts:
            score = 0.2
    return min(1.0, score + routing_bonus)


def _lexical_component(
    query: str, candidate: Any, family: Any, descriptor: Any = None,
) -> float:
    lowered = (query or "").lower()
    if not lowered:
        return 0.0
    hits = 0.0
    for label in (candidate.label_zh, candidate.label_en):
        t = str(label or "").strip()
        if t and (t in lowered or (t.isascii() and t.lower() in lowered)):
            hits += len(t)
    for kw in list(getattr(family, "keywords_zh", ()) or ()):
        if kw and kw in lowered:
            hits += len(kw)
    for kw in list(getattr(family, "keywords_en", ()) or ()):
        k = str(kw or "").lower()
        if k and k in lowered:
            hits += len(k)
    for kw in list(getattr(descriptor, "keywords_zh", ()) or ()) if descriptor else ():
        if kw and kw in lowered:
            hits += len(kw)
    for kw in list(getattr(descriptor, "keywords_en", ()) or ()) if descriptor else ():
        k = str(kw or "").lower()
        if k and k in lowered:
            hits += len(k)
    return min(1.0, hits / 20.0)


def qualification_score(report: MethodQualificationReport) -> float:
    """报告 → [0,1] 分量（transform 级降级软豁免，见 _SOFT_TRANSFORM_DIMS）。"""
    base = _STATUS_SCORE.get(report.status, 0.5)
    if (report.status == "degraded"
            and all(d.dimension in _SOFT_TRANSFORM_DIMS
                    for d in report.dimension_states
                    if d.state == "transform")):
        base = 0.85
    return base * (0.5 + 0.5 * report.confidence)


def _cost_component(method_id: str) -> float:
    desc = get_method_descriptor_registry().get(method_id)
    if desc is None:
        return 0.75
    return _COST_SCORE.get(desc.computational_class, 0.75)


def _constraint_component(
    method_id: str, constraints: Dict[str, Any], query: str = "",
) -> float:
    desc = get_method_descriptor_registry().get(method_id)
    score = 1.0
    if not desc:
        return score
    merged = dict(constraints or {})
    # 查询显式要求不确定性产物（方差/误差面…）= 输出约束
    if any(t in (query or "") for t in _UNCERTAINTY_QUERY_TERMS):
        merged.setdefault("require_uncertainty", True)
    if not merged:
        return score
    if merged.get("require_uncertainty"):
        if desc.uncertainty_support in ("none", "disclosure_only"):
            score -= 0.8
        elif desc.uncertainty_support == "analytic":
            score -= 0.2
    if merged.get("avoid_approximate") and desc.problem_class in (
            "display",) and merged.get("quantitative"):
        score -= 0.3
    return max(0.0, score)


def rank_family_methods(
    query: str,
    family_id: str,
    facts: QualificationFacts,
    *,
    category_id: str = "",
    constraints: Optional[Dict[str, Any]] = None,
    taxonomy: Any = None,
    graph: Any = None,
    methodology_registry: Any = None,
    exclude: Sequence[str] = (),
) -> RankResult:
    """一个族内的候选排序（单族入口；跨族用 rank_methods 池化排序）。"""
    if taxonomy is None:
        from app.lib.gis.methodology.taxonomy import get_task_taxonomy
        taxonomy = get_task_taxonomy()
    if graph is None:
        from app.lib.gis.methodology.graph import get_knowledge_graph
        graph = get_knowledge_graph()
    if methodology_registry is None:
        from app.services.gis_harness.workflow_v4.methodology import (
            get_methodology_registry,
        )
        methodology_registry = get_methodology_registry()

    family = methodology_registry.family(family_id)
    if family is None:
        return RankResult(abstained=True, abstain_reason=ABSTAIN_NO_EVIDENCE,
                          evidence={"family": family_id})
    cat = taxonomy.get(category_id) if category_id else None
    category_families = cat.methodology_family_ids if cat else ()
    category_artifacts = cat.output_artifact_types if cat else ()

    scored: List[MethodCandidateScore] = []
    for m in family.candidate_methods:
        if m.method_id in exclude:
            continue
        report = qualify_method(m.method_id, facts,
                                methodology_registry=methodology_registry)
        qual = qualification_score(report)
        comps = {
            "taxonomy": _taxonomy_component(query, category_id, taxonomy)
            if category_id else 0.5,
            "qualification": qual,
            "graph": _graph_component(
                m.method_id, family_id, category_families,
                category_artifacts, m, graph),
            "lexical": _lexical_component(query, m, family),
            "prior": max(0.0, min(1.0, (100 - m.priority) / 100.0)),
            "cost": _cost_component(m.method_id),
            "constraint": _constraint_component(
                m.method_id, constraints or {}, query),
        }
        total = _weighted_total(comps)
        scored.append(MethodCandidateScore(
            method_id=m.method_id, family_id=family_id,
            score=round(total, 6), components=comps,
            qualification_status=report.status,
            reason_codes=list(report.reason_codes),
            disclosures=list(report.disclosures),
        ))

    return RankResult(
        selected=scored[0] if scored else None,
        abstained=bool(scored)
        and all(s.qualification_status == "rejected" for s in scored),
        abstain_reason=ABSTAIN_ALL_REJECTED
        if scored and all(s.qualification_status == "rejected"
                          for s in scored) else "",
        ranked=_partition_sort(scored, methodology_registry),
        evidence={"family": family_id, "category": category_id,
                  "candidates": len(scored)},
    )


def _weighted_total(comps: Dict[str, float]) -> float:
    return round(
        WEIGHT_TAXONOMY * comps["taxonomy"]
        + WEIGHT_QUALIFICATION * comps["qualification"]
        + WEIGHT_GRAPH * comps["graph"]
        + WEIGHT_LEXICAL * comps["lexical"]
        + WEIGHT_PRIOR * comps["prior"]
        + WEIGHT_COST * comps["cost"]
        + WEIGHT_CONSTRAINT * comps["constraint"], 6)


def _partition_sort(
    scored: List[MethodCandidateScore], methodology_registry: Any,
) -> List[MethodCandidateScore]:
    """rejected 恒排末段（结构化信号救回语义：被拒者不应因 prior/lexical
    高分压过可行候选/最小层兜底）；段内按 (-score, priority, id)。"""
    def _key(s: MethodCandidateScore) -> Tuple[int, float, int, str]:
        cand = methodology_registry.method(s.method_id)
        prio = cand.priority if cand else 50
        return (1 if s.qualification_status == "rejected" else 0,
                -s.score, prio, s.method_id)
    return sorted(scored, key=_key)


def rank_methods(
    query: str,
    facts: QualificationFacts,
    *,
    category_id: str = "",
    constraints: Optional[Dict[str, Any]] = None,
    taxonomy: Any = None,
    graph: Any = None,
    methodology_registry: Any = None,
) -> RankResult:
    """类目池化方法排序：候选池 = 类目覆盖族的全部方法。

    V4 路由一致性（R1-F6）：V4 ``resolve_methodology_family_for_query``
    的族裁决不推翻——路由命中的族拿 ``_ROUTING_BONUS`` 加分；ranker 的
    排序自由度限于类目的族集合内。全部候选 rejected/无 viable 时走
    ontology minimal-tier 兜底（安全描述性输出 + 显式披露）。
    """
    if taxonomy is None:
        from app.lib.gis.methodology.taxonomy import get_task_taxonomy
        taxonomy = get_task_taxonomy()
    if graph is None:
        from app.lib.gis.methodology.graph import get_knowledge_graph
        graph = get_knowledge_graph()
    if methodology_registry is None:
        from app.services.gis_harness.workflow_v4.methodology import (
            get_methodology_registry,
        )
        methodology_registry = get_methodology_registry()

    cat_matches = taxonomy.match_query(query, limit=2) if query else []
    if not category_id and not cat_matches:
        return RankResult(abstained=True, abstain_reason=ABSTAIN_NO_EVIDENCE,
                          evidence={"query_terms": bool(query)})
    target_category = category_id or cat_matches[0][0]
    cat = taxonomy.get(target_category)
    if cat is None:
        return RankResult(abstained=True, abstain_reason=ABSTAIN_NO_EVIDENCE,
                          evidence={"category": target_category})

    from app.services.gis_harness.workflow_v4.methodology import (
        resolve_methodology_family_for_query,
    )

    routed_family = ""
    if query:
        fam = resolve_methodology_family_for_query(
            query, cat.ontology_task_ids[0] if cat.ontology_task_ids else "")
        if fam is not None and fam.family_id in cat.methodology_family_ids:
            routed_family = fam.family_id

    category_families = cat.methodology_family_ids
    category_artifacts = cat.output_artifact_types
    desc_reg = get_method_descriptor_registry()

    scored: List[MethodCandidateScore] = []
    for fid in category_families:
        family = methodology_registry.family(fid)
        if family is None:
            continue
        for m in family.candidate_methods:
            report = qualify_method(m.method_id, facts,
                                    methodology_registry=methodology_registry)
            comps = {
                "taxonomy": _taxonomy_component(query, target_category,
                                                taxonomy),
                "qualification": qualification_score(report),
                "graph": _graph_component(
                    m.method_id, fid, category_families, category_artifacts,
                    m, graph,
                    routing_bonus=(_ROUTING_BONUS
                                   if fid == routed_family else 0.0)),
                "lexical": _lexical_component(
                    query, m, family, desc_reg.get(m.method_id)),
                "prior": max(0.0, min(1.0, (100 - m.priority) / 100.0)),
                "cost": _cost_component(m.method_id),
                "constraint": _constraint_component(
                    m.method_id, constraints or {}, query),
            }
            total = _weighted_total(comps)
            scored.append(MethodCandidateScore(
                method_id=m.method_id, family_id=fid,
                score=round(total, 6), components=comps,
                qualification_status=report.status,
                reason_codes=list(report.reason_codes),
                disclosures=list(report.disclosures),
            ))

    scored = _partition_sort(scored, methodology_registry)

    viable = [s for s in scored
              if s.qualification_status in ("viable", "unknown")]
    if not viable:
        # ontology minimal tier 兜底：安全描述性输出（显式披露降级语义）
        scored, fallback_used = _append_minimal_fallback(
            scored, facts, methodology_registry)
    else:
        fallback_used = False

    if not scored or all(s.qualification_status == "rejected"
                         for s in scored):
        return RankResult(
            abstained=True, abstain_reason=ABSTAIN_ALL_REJECTED,
            ranked=scored,
            evidence={"category": target_category,
                      "candidates": len(scored)},
        )

    return RankResult(
        selected=scored[0],
        abstained=False,
        ranked=scored,
        evidence={
            "category": target_category,
            "routed_family": routed_family,
            "candidates": len(scored),
            "minimal_fallback": fallback_used,
        },
    )


def _append_minimal_fallback(
    scored: List[MethodCandidateScore],
    facts: QualificationFacts,
    methodology_registry: Any,
) -> Tuple[List[MethodCandidateScore], bool]:
    """全部不可行时追加 minimal-tier 描述性兜底（不虚构、不静默）。"""
    family = methodology_registry.family(_FALLBACK_FAMILY)
    if family is None:
        return scored, False
    fallback: List[MethodCandidateScore] = list(scored)
    used = False
    for m in family.candidate_methods:
        if m.method_id not in _FALLBACK_METHODS:
            continue
        report = qualify_method(m.method_id, facts,
                                methodology_registry=methodology_registry)
        if report.status == "rejected":
            continue
        desc = get_method_descriptor_registry().get(m.method_id)
        fam_kw = family
        comps = {
            "taxonomy": 0.0, "qualification": qualification_score(report),
            "graph": 0.0, "lexical": _lexical_component("", m, fam_kw, desc),
            "prior": 0.2, "cost": 1.0, "constraint": 1.0,
        }
        fallback.append(MethodCandidateScore(
            method_id=m.method_id, family_id=_FALLBACK_FAMILY,
            score=round(_weighted_total(comps), 6), components=comps,
            qualification_status=report.status,
            disclosures=["全部定量方法在当前数据事实上不成立："
                         "降级为描述性展示（minimal tier，不虚构结论）。"],
        ))
        used = True
    fallback = _partition_sort(fallback, methodology_registry)
    return fallback, used


# ── 基准指标（method_corpus 消费；反循环披露）────────────────────────────

def evaluate_corpus(
    corpus: Sequence[Any],
    *,
    k_values: Sequence[int] = (1, 3, 5),
    constraints: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """语料级指标：Recall@k / MRR / invalid(trap) rate / abstention。

    - expected 命中按 ``expected_methods``（有序 gold）；
    - trap 命中 = top-k 撞 ``trap_methods``（词面陷阱 = invalid selection）；
    - ambiguous 案例 top-1 ∈ valid 即计对（不苛求顺序）；
    - ``lexical_baseline_misses``：仅 lexical（prior+lexical）会排错、
      结构化信号救回的案例数（披露 MRR 非空转）。
    """
    recalls = {k: [] for k in k_values}
    mrr: List[float] = []
    trap_hits: List[str] = []
    abstain_ok = 0
    abstain_total = 0
    lexical_rescued = 0
    case_count = 0
    for case in corpus:
        facts = QualificationFacts.from_profile(
            dict(case.profile[0]) if case.profile else {},
            role_states=dict(case.role_states),
            measure_kind=case.measure_kind,
        )
        result = rank_methods(case.query_zh, facts,
                              category_id=case.expected_category,
                              constraints=constraints)
        case_count += 1
        gold = list(case.expected_methods)
        valid = list(case.valid_methods) or gold
        if case.kind == "hard_negative" and case.trap_methods:
            # 语义正确性 > 词面：trap 被资格拒绝则不算 trap 命中
            top_ids = [s.method_id for s in result.ranked[:5]]
            surviving_traps = [
                t for t in case.trap_methods
                if statuses_of(result).get(t, "viable") not in
                ("rejected",)]
            if any(t in top_ids for t in surviving_traps):
                trap_hits.append(case.case_id)
        elif case.trap_methods:
            top_ids = [s.method_id for s in result.ranked[:5]]
            if any(t in top_ids for t in case.trap_methods):
                trap_hits.append(case.case_id)
        if case.kind == "ambiguous":
            abstain_total += 1
            top1 = result.ranked[0].method_id if result.ranked else ""
            if top1 in valid:
                abstain_ok += 1
                mrr.append(1.0)
            for k in k_values:
                topk = [s.method_id for s in result.ranked[:k]]
                recalls[k].append(1.0 if any(g in topk for g in valid)
                                  else 0.0)
            continue
        rank_of_gold: Optional[int] = None
        for i, s in enumerate(result.ranked, start=1):
            if s.method_id in gold:
                rank_of_gold = i
                break
        for k in k_values:
            topk = [s.method_id for s in result.ranked[:k]]
            recalls[k].append(1.0 if any(g in topk for g in gold) else 0.0)
        mrr.append(1.0 / rank_of_gold if rank_of_gold else 0.0)
        # lexical-only 基线（prior+lexical 主导）失败但结构化信号救回
        if rank_of_gold and rank_of_gold > 1:
            lexical_first = _lexical_baseline_pick(case, result)
            if lexical_first and lexical_first not in gold:
                lexical_rescued += 1
    metrics: Dict[str, Any] = {
        "cases": case_count,
        "invalid_selection_rate": (len(trap_hits) / case_count
                                   if case_count else 0.0),
        "trap_hit_cases": trap_hits[:8],
        "ambiguous_valid_top1": (abstain_ok / abstain_total
                                 if abstain_total else None),
        "lexical_baseline_rescued": lexical_rescued,
    }
    for k in k_values:
        values = recalls[k]
        metrics[f"recall@{k}"] = (sum(values) / len(values)
                                   if values else 0.0)
    metrics["mrr"] = (sum(mrr) / len(mrr)) if mrr else 0.0
    return metrics


def statuses_of(result: RankResult) -> Dict[str, str]:
    return {s.method_id: s.qualification_status for s in result.ranked}


def _lexical_baseline_pick(case: Any, result: RankResult) -> str:
    """lexical-only 基线的 top-1（按 lexical+prior 分量重排）。"""
    if not result.ranked:
        return ""
    pick = max(result.ranked, key=lambda s: (
        0.7 * s.components.get("lexical", 0.0)
        + 0.3 * s.components.get("prior", 0.0),
        s.method_id))
    return pick.method_id


__all__ = [
    "WEIGHT_TAXONOMY",
    "WEIGHT_QUALIFICATION",
    "WEIGHT_GRAPH",
    "WEIGHT_LEXICAL",
    "WEIGHT_PRIOR",
    "WEIGHT_COST",
    "WEIGHT_CONSTRAINT",
    "ABSTAIN_ALL_REJECTED",
    "ABSTAIN_AMBIGUOUS_TIE",
    "ABSTAIN_NO_EVIDENCE",
    "MethodCandidateScore",
    "RankResult",
    "rank_family_methods",
    "rank_methods",
    "evaluate_corpus",
    "statuses_of",
]
