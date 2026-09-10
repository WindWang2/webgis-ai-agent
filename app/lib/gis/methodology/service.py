"""Knowledge Service —— 方法知识统一门面（Epic 11 §5.J/§5.28）。

Harness / Workflow / Agent Tools 消费方法知识的**唯一入口**：

    classify          自然语言 → 任务类目 + 本体任务 + 方法族（证据面）
    retrieve_methods  query + 数据事实 → 方法候选排序（含弃权语义）
    qualify_method    单方法资格统一报告
    alternatives      方法的合法替代集（descriptor/graph 投影）
    explain_method    方法解释（assumptions/parameters/invalid_when/出处）
    explain_plan      全链解释：类目→资格→排序→模板组合的证据链
    plan_template     方法/类目 → TemplateSpecV2 + CompositionPlan
    compose_components组合组件清单（slot fills 投影）
    query_components  组件目录查询（语义角色过滤）
    workflow_skeleton 方法 → workflow 骨架投影（typed DAG 消费面）
    render_intent     方法+产物 → render intent（MapModel/组件/义务声明）
    knowledge_stats   图/词表规模与指纹（诊断面）

红线（架构 §0/§2）：

- **不暴露 graph/descriptor 内部结构给 LLM**：全部输出 bounded 投影
  （to_bounded_dict 家族）；图结构只作内部查询（neighbors/edges_of）；
- 零 LLM 裁决、零 I/O（反馈 writer 例外且默认禁用）；同输入同输出；
- 职责边界：本门面只做编排与投影——科学语义在算法层/preconditions，
  制图语义在 renderer/model library，组合语义在 template intelligence。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from app.lib.gis.methodology.descriptors import (
    get_method_descriptor_registry,
)
from app.lib.gis.methodology.graph import get_knowledge_graph
from app.lib.gis.methodology.qualification import (
    MethodQualificationReport,
    QualificationFacts,
    qualify_method,
)
from app.lib.gis.methodology.ranking import (
    rank_methods,
)
from app.lib.gis.methodology.taxonomy import get_task_taxonomy


class KnowledgeService:
    """方法知识门面（无状态；registry 单例直查，O(1)/有界扫描）。"""

    # ── classify ─────────────────────────────────────────────────────
    def classify(self, query: str) -> Dict[str, Any]:
        """query → 类目/本体任务/方法族证据（V4 路由优先，tie-break 用）。"""
        query = (query or "")[:2000]  # 防御性截断（Round2 #6：非工具入口）
        taxonomy = get_task_taxonomy()
        ontology = _ontology()
        category_matches = taxonomy.match_query(query, limit=3)
        primary_category = category_matches[0][0] if category_matches else ""
        cat = taxonomy.get(primary_category) if primary_category else None
        ontology_tasks: List[Dict[str, str]] = []
        if cat is not None:
            for tid in cat.ontology_task_ids[:4]:
                task = ontology.get(tid)
                if task is not None:
                    ontology_tasks.append({
                        "task_id": tid[:64],
                        "label_zh": task.label_zh[:32],
                        "semantic_status": task.semantic_status[:12],
                    })
        routed_family = ""
        if cat is not None and cat.ontology_task_ids:
            from app.services.gis_harness.workflow_v4.methodology import (
                resolve_methodology_family_for_query,
            )
            fam = resolve_methodology_family_for_query(
                query, cat.ontology_task_ids[0])
            if fam is not None and fam.family_id in cat.methodology_family_ids:
                routed_family = fam.family_id
        return {
            "query": query[:120],
            "categories": [{"category_id": c[:40], "score": round(s, 3)}
                           for c, s in category_matches],
            "primary_category": primary_category[:40],
            "ontology_tasks": ontology_tasks,
            "routed_family": routed_family[:40],
            "data_role_demands": list(cat.data_role_demands[:6])
            if cat else [],
            "abstain": not category_matches,
        }

    # ── retrieve / rank ──────────────────────────────────────────────
    def retrieve_methods(
        self,
        query: str,
        facts: QualificationFacts,
        *,
        category_id: str = "",
        constraints: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """方法检索 + 排序（RankResult bounded 投影；弃权显式）。"""
        result = rank_methods(query, facts, category_id=category_id,
                              constraints=constraints)
        return result.to_bounded_dict()

    # ── qualify ──────────────────────────────────────────────────────
    def qualify_method(
        self,
        method_id: str,
        facts: QualificationFacts,
    ) -> MethodQualificationReport:
        return qualify_method(method_id, facts)

    # ── alternatives ─────────────────────────────────────────────────
    def alternatives(self, method_id: str) -> Dict[str, Any]:
        """方法的合法替代集（descriptor + graph alternative 边投影）。"""
        desc_reg = get_method_descriptor_registry()
        desc = desc_reg.get(method_id)
        graph = get_knowledge_graph()
        graph_alts = graph.neighbors(f"method:{method_id}",
                                     relation="alternative_method")
        descriptor_alts = [f"method:{a}" for a in
                           (desc.alternatives if desc else ())]
        merged = list(dict.fromkeys(descriptor_alts + graph_alts))
        methods_reg = _methodology()
        alternatives = []
        for key in merged[:6]:
            mid = key.split(":", 1)[-1]
            cand = methods_reg.method(mid)
            if cand is not None:
                alternatives.append({
                    "method_id": mid[:64],
                    "label_zh": cand.label_zh[:40],
                })
        return {
            "method_id": method_id[:64],
            "alternatives": alternatives,
            "abstain_guidance": bool(desc and desc.invalid_when),
        }

    # ── explain ──────────────────────────────────────────────────────
    def explain_method(self, method_id: str) -> Dict[str, Any]:
        """方法解释面（assumptions/parameters/invalid_when/出处）。

        输出为 LLM 可见的有界投影——不含 graph/descriptor 内部结构。
        """
        desc_reg = get_method_descriptor_registry()
        methods_reg = _methodology()
        candidate = methods_reg.method(method_id)
        if candidate is None:
            return {"method_id": method_id[:64], "known": False}
        desc = desc_reg.get(method_id)
        provenance = None
        if desc is not None and desc.provenance_id:
            from app.lib.gis.methodology.provenance import get_provenance
            provenance = get_provenance(desc.provenance_id)
        return {
            "method_id": method_id[:64],
            "known": True,
            "family": candidate.family_id[:40],
            "label_zh": candidate.label_zh[:60],
            "approximate": candidate.approximate,
            "problem_class": desc.problem_class if desc else "",
            "assumptions": list(desc.assumptions[:4]) if desc else [],
            "parameters": [{"name": n[:40], "constraint": d[:80]}
                           for n, d in (desc.parameters[:6] if desc else [])],
            "invalid_when": [
                {"dimension": c.dimension[:24],
                 "condition": c.condition[:120],
                 "severity": c.severity[:8]}
                for c in (desc.invalid_when[:4] if desc else [])],
            "uncertainty_support": desc.uncertainty_support if desc else "none",
            "disclosures": list(candidate.disclosures[:3]),
            "provenance": provenance.to_bounded_dict() if provenance else None,
        }

    def explain_plan(
        self,
        query: str,
        facts: QualificationFacts,
        *,
        category_id: str = "",
    ) -> Dict[str, Any]:
        """全链解释：classify → 资格 → 排序 → 模板 的证据链（有界）。"""
        classification = self.classify(query)
        rank = rank_methods(query, facts,
                            category_id=category_id
                            or classification["primary_category"])
        chain: List[Dict[str, Any]] = [
            {"stage": "classify", "outcome": classification["primary_category"]},
            {"stage": "family_routing",
             "outcome": classification["routed_family"]},
            {"stage": "qualification",
             "outcome": (rank.selected.qualification_status
                         if rank.selected else "abstained"),
             "top": rank.ranked[0].method_id if rank.ranked else ""},
            {"stage": "ranking",
             "outcome": rank.selected.method_id if rank.selected
             else rank.abstain_reason},
        ]
        template: Dict[str, Any] = {}
        if rank.selected is not None:
            from app.lib.cartography.template_intelligence import (
                plan_composition_for_method,
            )
            plan = plan_composition_for_method(
                rank.selected.method_id, classification["primary_category"],
                facts=facts)
            template = plan.to_bounded_dict()
            chain.append({"stage": "template",
                          "outcome": plan.base_composition_template_id})
        return {
            "query": query[:120],
            "chain": chain,
            "rank": rank.to_bounded_dict(),
            "template": template,
        }

    # ── template / compose / components ──────────────────────────────
    def plan_template(
        self,
        method_id: str,
        category_id: str,
        *,
        facts: Any = None,
        output_target: str = "interactive",
    ) -> Dict[str, Any]:
        from app.lib.cartography.template_intelligence import (
            plan_composition_for_method,
        )
        plan = plan_composition_for_method(
            method_id, category_id, facts=facts,
            output_target=output_target)
        return plan.to_bounded_dict()

    def query_components(
        self,
        *,
        semantic_role: str = "",
        compatible_artifact: str = "",
    ) -> Dict[str, Any]:
        """组件目录查询（语义角色/产物兼容过滤；bounded 投影）。"""
        from app.lib.cartography.component_registry import (
            components_for_role,
            get_component_registry,
        )
        comp_reg = get_component_registry()
        if semantic_role:
            ids = components_for_role(semantic_role)
        elif compatible_artifact:
            ids = [d.id for d in
                   (comp_reg.get(i) for i in comp_reg.all_ids)
                   if d is not None
                   and compatible_artifact in d.compatible_artifact_types]
        else:
            ids = comp_reg.all_ids
        out = []
        for cid in ids[:12]:
            d = comp_reg.get(cid)
            if d is None:
                continue
            out.append({
                "id": d.id[:32],
                "category": d.category[:40],
                "outputs": list(d.supported_outputs[:4]),
                "placement": d.placement_domain[:12],
            })
        return {"components": out, "count": len(out)}

    # ── workflow skeleton / render intent ────────────────────────────
    def workflow_skeleton(self, method_id: str) -> Dict[str, Any]:
        """方法 → workflow 骨架投影（typed DAG/plan 消费面，非执行）。

        节点 = 数据获取（角色）→ 算法执行（算法引用）→ 产物（artifact）；
        声明与证据，执行归 Harness runtime（架构 §2 红线）。
        """
        methods_reg = _methodology()
        candidate = methods_reg.method(method_id)
        if candidate is None:
            return {"method_id": method_id[:64], "known": False}
        algorithms = _algorithms()
        steps: List[Dict[str, Any]] = []
        for role in candidate.requires_roles[:4]:
            steps.append({
                "kind": "acquire",
                "ref": role[:32],
                "detail": f"数据角色获取：{role}",
            })
        for aid in candidate.algorithm_ids[:3]:
            algo = algorithms.get(aid)
            steps.append({
                "kind": "execute",
                "ref": aid[:64],
                "detail": algo.name[:48] if algo else "",
                "deterministic": algo.deterministic if algo else True,
            })
        for at in candidate.output_artifacts[:3]:
            steps.append({"kind": "produce", "ref": at[:40],
                          "detail": "产物物化"})
        return {
            "method_id": method_id[:64],
            "family": candidate.family_id[:40],
            "steps": steps[:10],
            "obligations": {
                "approximate_disclosure": candidate.approximate,
                "preconditions": list(candidate.preconditions[:4]),
            },
        }

    def render_intent(
        self,
        method_id: str,
        category_id: str,
        *,
        facts: Any = None,
        output_target: str = "interactive",
    ) -> Dict[str, Any]:
        """方法 + 产物 → render intent（结构化制图意图声明，不渲染）。

        渲染语义归 Cartography renderer；此处只声明「用什么 MapModel
        家族、哪些组件槽位、什么图例语义、哪些义务披露」。
        """
        methods_reg = _methodology()
        candidate = methods_reg.method(method_id)
        if candidate is None:
            return {"method_id": method_id[:64], "known": False}
        from app.lib.gis.methodology.viz_bridge import bridge_plan
        vplan = bridge_plan(list(candidate.output_artifacts))
        composition = self.plan_template(
            method_id, category_id, facts=facts,
            output_target=output_target)
        return {
            "method_id": method_id[:64],
            "primary_viz_family": vplan["primary_family"][:40],
            "legend_semantics": (vplan["legend_semantics"][:2]),
            "map_model_candidates": _map_models_for(
                list(candidate.output_artifacts)[:3]),
            "component_slots": composition.get("slots", [])[:12],
            "disclosures": vplan["disclosures"][:4],
        }

    # ── stats ────────────────────────────────────────────────────────
    def knowledge_stats(self) -> Dict[str, Any]:
        """知识面规模与指纹（诊断；不暴露图结构）。"""
        graph = get_knowledge_graph()
        taxonomy = get_task_taxonomy()
        return {
            "graph": graph.to_bounded_dict(),
            "categories": len(taxonomy.all_ids),
            "methods": _methodology().method_count(),
            "descriptors": get_method_descriptor_registry().count,
            "taxonomy_fingerprint": taxonomy.fingerprint()[:16],
        }


# ── registry 延迟对接（lib→services 无顶层依赖，R1-F8）──────────────────

def _ontology() -> Any:
    from app.services.gis_harness.gis_ontology import get_task_ontology
    return get_task_ontology()


def _methodology() -> Any:
    from app.services.gis_harness.workflow_v4.methodology import (
        get_methodology_registry,
    )
    return get_methodology_registry()


def _algorithms() -> Any:
    from app.lib.gis.algorithm_registry import get_algorithm_registry
    return get_algorithm_registry()


def _map_models_for(artifact_types: Sequence[str]) -> List[str]:
    """产物 → 候选 MapModel（artifact typical 投影；有界去重）。"""
    from app.lib.gis.artifacts import get_artifact_type_registry
    arts = get_artifact_type_registry()
    models: List[str] = []
    for at in artifact_types:
        desc = arts.get(at)
        if desc is None:
            continue
        for mm in desc.typical_map_models:
            if mm not in models:
                models.append(mm)
    return models[:6]


_singleton: Optional[KnowledgeService] = None


def get_knowledge_service() -> KnowledgeService:
    global _singleton
    if _singleton is None:
        _singleton = KnowledgeService()
    return _singleton


__all__ = [
    "KnowledgeService",
    "get_knowledge_service",
]
