"""Deterministic Workflow Compiler（Goal C / C5）—— 12 阶段确定性编译管线。

把「自然语言 GIS 请求 → 产品计划 + 完成契约」组织为显式、可审计、可单测
的编译阶段；每个阶段有确定性输入/输出、机器可读 reason codes 与有界
evidence。设计约束：

- 编译器是既有确定性组件（intent resolver / RecipeRegistry / planner /
  AlgorithmResolver / scientific preconditions / plan_graph）的**编排层**，
  不复制任何事实、不建第二套真相；
- 阶段产物全部可序列化、有界、同输入同输出（零 LLM / 零 I/O；
  available_tools 视图由调用方显式注入）；
- durable execution 仍由 SessionPlan / Pi runtime 负责 —— 本模块只编译，
  不执行。

管线（规格 §10）：

    1 normalize_intent          7 evaluate_obligations
    2 resolve_task_family       8 resolve_algorithms
    3 resolve_scope             9 compute_transformations
    4 resolve_recipe_candidates 10 resolve_cartography
    5 resolve_data_roles        11 produce_map_product_plan
    6 compile_capability_dag    12 produce_completion_contract
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from app.services.gis_harness.intent import MapRequestIntent, resolve_map_request_intent

#: 编译器管线的固定阶段序（测试锁定顺序与数量）。
COMPILER_STAGES = (
    "normalize_intent",
    "resolve_task_family",
    "resolve_scope",
    "resolve_recipe_candidates",
    "resolve_data_roles",
    "compile_capability_dag",
    "evaluate_obligations",
    "resolve_algorithms",
    "compute_transformations",
    "resolve_cartography",
    "produce_map_product_plan",
    "produce_completion_contract",
)

_STAGE_REASON_BUDGET = 8
_STAGE_EVIDENCE_KEYS = 8


class WorkflowStageRecord(BaseModel):
    """单个编译阶段的确定性产出记录（bounded）。"""
    stage: str
    status: str = "ok"                 # ok | skipped | blocked
    reason_codes: List[str] = Field(default_factory=list)
    evidence: Dict[str, Any] = Field(default_factory=dict)

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "stage": self.stage,
            "status": self.status,
            "reason_codes": [str(c)[:64] for c in self.reason_codes[:_STAGE_REASON_BUDGET]],
            "evidence": {
                str(k)[:32]: v for k, v in
                list(self.evidence.items())[:_STAGE_EVIDENCE_KEYS]
            },
        }


class WorkflowCompilation(BaseModel):
    """一次工作流编译的完整产物（serializable / bounded / deterministic）。"""
    query: str
    recipe_id: str = ""
    stages: List[WorkflowStageRecord] = Field(default_factory=list)
    intent: Optional[MapRequestIntent] = None
    # plan 为 map_product_plan 阶段的有界 dump（同 SessionPlan chapter 形态）；
    # 保持 dict 以免引入 planner 模型对编译器产物的硬依赖。
    plan: Dict[str, Any] = Field(default_factory=dict)
    data_roles: List[Dict[str, Any]] = Field(default_factory=list)
    obligations: List[Dict[str, Any]] = Field(default_factory=list)
    capability_dag: Dict[str, Any] = Field(default_factory=dict)
    transformations: List[Dict[str, Any]] = Field(default_factory=list)
    completion_contract: Dict[str, Any] = Field(default_factory=dict)
    reason_codes: List[str] = Field(default_factory=list)

    def stage(self, name: str) -> Optional[WorkflowStageRecord]:
        return next((s for s in self.stages if s.stage == name), None)

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "query": self.query[:200],
            "recipe_id": self.recipe_id,
            "stages": [s.to_bounded_dict() for s in self.stages],
            "data_roles": self.data_roles[:16],
            "obligations": self.obligations[:16],
            "capability_dag": self.capability_dag,
            "transformations": self.transformations[:8],
            "completion_contract": self.completion_contract,
            "reason_codes": [str(c)[:64] for c in self.reason_codes[:12]],
        }


def _stage_record(stage: str, *, status: str = "ok",
                  reason_codes: Optional[List[str]] = None,
                  evidence: Optional[Dict[str, Any]] = None) -> WorkflowStageRecord:
    return WorkflowStageRecord(
        stage=stage, status=status,
        reason_codes=reason_codes or [], evidence=evidence or {},
    )


def compile_workflow(
    query: str,
    *,
    intent: Optional[MapRequestIntent] = None,
    hint: Optional[Dict[str, Any]] = None,
    profile: Optional[Dict[str, Any]] = None,
    available_tools: Optional[List[str]] = None,
    project_verified: Optional[set] = None,
    recipe_id: str = "",
    template_id: str = "",
    min_points_default: int = 10,
) -> WorkflowCompilation:
    """把 query/intent 确定性编译为 WorkflowCompilation（12 阶段）。

    ``profile``（Spatial Meta Profile / resolver camelCase 形态）在数据到手
    后传入，用于 finalize 与义务评估；规划期可省略（义务按 unknown ≠
    unsatisfied 处理，不虚构资格）。
    """
    from app.services.gis_harness.intent import merge_intent_hints
    from app.services.gis_harness.planner import MapProductPlanner
    from app.services.gis_harness.planner_runtime import get_planner_runtime
    from app.services.gis_harness.plan_graph import build_plan_graph
    from app.services.gis_harness.recipes import get_recipe_registry
    from app.services.gis_harness.workflow_schema import (
        evaluate_workflow_obligations,
        resolve_data_roles,
    )

    compilation = WorkflowCompilation(query=query)
    stages = compilation.stages
    planner: MapProductPlanner = get_planner_runtime()
    registry = get_recipe_registry()

    # ── 1 normalize_intent ───────────────────────────────────────────
    base = intent or resolve_map_request_intent(query)
    merged = merge_intent_hints(base, hint) if hint else base
    compilation.intent = merged
    stages.append(_stage_record(
        "normalize_intent",
        reason_codes=list(merged.matched_rules[:6]),
        evidence={"confidence": round(merged.confidence, 2),
                  "hint_applied": list(merged.hint_applied[:4])},
    ))

    # ── 2 resolve_task_family ────────────────────────────────────────
    task = merged.task
    wf_hint = registry.keyword_hits(merged.query)
    stages.append(_stage_record(
        "resolve_task_family",
        evidence={"task": task,
                  "keyword_signals": [r.id for r in wf_hint[:4]]},
    ))

    # ── 3 resolve_scope ──────────────────────────────────────────────
    stages.append(_stage_record(
        "resolve_scope",
        evidence={"scope": merged.scope.name or "unresolved",
                  "level": merged.scope.level,
                  "subject": merged.subject.type,
                  "geometry_expectation": merged.geometry_expectation},
    ))

    # ── 4 resolve_recipe_candidates ──────────────────────────────────
    candidates = registry.select_candidates(
        merged, project_verified=project_verified)
    selected = registry.get(recipe_id) if recipe_id else (
        candidates[0] if candidates else registry.default_recipe()
    )
    compilation.recipe_id = selected.id
    candidate_evidence = [
        {"recipe_id": c.id, "priority": c.priority,
         "has_workflow": c.workflow is not None}
        for c in candidates[:4]
    ]
    explicit_choice = bool(recipe_id and recipe_id != (candidates[0].id if candidates else ""))
    stages.append(_stage_record(
        "resolve_recipe_candidates",
        reason_codes=(
            ["explicit_recipe_override"] if explicit_choice else []
        ) + ([] if candidates else ["no_candidates_default_fallback"]),
        evidence={"selected": selected.id,
                  "candidates": candidate_evidence},
    ))

    # ── 5 resolve_data_roles（阶段 5；finalize 期随 profile 复评）────
    wf_profile = getattr(selected, "workflow", None)
    role_resolutions = resolve_data_roles(
        selected.id, wf_profile, resolver_profile=profile)
    compilation.data_roles = [r.to_bounded_dict() for r in role_resolutions]
    stages.append(_stage_record(
        "resolve_data_roles",
        status="skipped" if wf_profile is None else "ok",
        reason_codes=[
            r.reason_code for r in role_resolutions
            if r.required and r.status in ("unresolved", "degraded")
        ][:_STAGE_REASON_BUDGET],
        evidence={"roles": {r.role: r.status for r in role_resolutions[:8]},
                  "has_workflow_profile": wf_profile is not None},
    ))

    # ── 11a plan production（先编译 plan，供 6/8/10 消费确定性产物）──
    plan = planner.plan_from_intent(
        merged, template_id=template_id, recipe_id=selected.id,
        available_tools=available_tools, project_verified=project_verified,
    )
    if profile is not None:
        plan = planner.finalize_with_profile(
            plan, profile, min_points_default=min_points_default,
            available_tools=available_tools,
        )
    plan_dump = plan.model_dump(mode="json")

    # ── 6 compile_capability_dag ─────────────────────────────────────
    try:
        graph = build_plan_graph(plan)
        dag_evidence = {
            "nodes": len(graph.nodes),
            "ready": graph.ready_nodes()[:6],
            "unresolved_dependency_refs": graph.unresolved_dependency_refs[:4],
        }
        stages.append(_stage_record("compile_capability_dag", evidence=dag_evidence))
        compilation.capability_dag = {
            "nodes": [
                {"capability": n.capability, "kind": n.kind, "status": str(n.status.value),
                 "optional": n.optional, "depends_on": n.depends_on[:6]}
                for n in graph.nodes[:48]
            ],
        }
    except Exception as exc:  # noqa: BLE001 - DAG 构建失败诚实留痕
        stages.append(_stage_record(
            "compile_capability_dag", status="blocked",
            reason_codes=["PLAN_GRAPH_BUILD_FAILED"],
            evidence={"error": str(exc)[:160]},
        ))
        compilation.capability_dag = {"nodes": [], "error": str(exc)[:160]}

    # ── 7 evaluate_obligations（复用 planner/ finalize 期评估）────────
    contract_report = evaluate_workflow_obligations(
        selected.id, wf_profile,
        resolver_profile=profile, role_resolutions=role_resolutions,
    )
    compilation.obligations = [o.to_bounded_dict() for o in contract_report.obligations]
    obligation_evidence = {
        "method_blockers": contract_report.method_blockers[:6],
        "data_blockers": contract_report.data_blockers[:6],
        "warning_codes": [w.get("code") for w in contract_report.warnings[:6]],
    }
    stages.append(_stage_record(
        "evaluate_obligations",
        status="skipped" if wf_profile is None else (
            "blocked" if contract_report.method_blockers else "ok"),
        reason_codes=(
            contract_report.method_blockers
            + contract_report.data_blockers
            + [w.get("code") for w in contract_report.warnings if w.get("code")]
        )[:_STAGE_REASON_BUDGET],
        evidence=obligation_evidence,
    ))

    # ── 8 resolve_algorithms ─────────────────────────────────────────
    selections = plan_dump.get("algorithm_selections") or []
    unresolved = [
        s.get("capability") for s in selections if s.get("status") == "unavailable"
    ]
    stages.append(_stage_record(
        "resolve_algorithms",
        reason_codes=[
            f"unavailable:{cap}" for cap in unresolved[:_STAGE_REASON_BUDGET]
        ],
        evidence={
            "resolved": sum(1 for s in selections if s.get("status") == "resolved"),
            "unavailable": len(unresolved),
        },
    ))

    # ── 9 compute_transformations ────────────────────────────────────
    transformations: List[Dict[str, Any]] = []
    for s in selections:
        for t in (s.get("required_transformations") or [])[:2]:
            transformations.append({
                "capability": s.get("capability"),
                "transformation": str(t)[:120],
            })
    for obl_ev in compilation.obligations:
        hint_text = (obl_ev.get("evidence") or {}).get("transform_hint")
        if hint_text:
            transformations.append({
                "obligation": obl_ev.get("obligation_id"),
                "transformation": str(hint_text)[:120],
            })
    compilation.transformations = transformations[:8]
    stages.append(_stage_record(
        "compute_transformations",
        evidence={"count": len(transformations)},
    ))

    # ── 10 resolve_cartography ───────────────────────────────────────
    stages.append(_stage_record(
        "resolve_cartography",
        evidence={
            "primary_cartography": selected.primary_cartography,
            "secondary": list(selected.secondary_cartography)[:4],
            "template_id": plan_dump.get("template_id", ""),
            "layers": [
                {"role": l.get("role"), "cartography": l.get("cartography"),
                 "layer_type": l.get("layer_type")}
                for l in (plan_dump.get("map_layers") or [])[:6]
            ],
        },
    ))

    # ── 11 produce_map_product_plan ──────────────────────────────────
    compilation.plan = {
        k: plan_dump.get(k)
        for k in ("plan_id", "query", "recipe_id", "template_id", "status",
                  "data_requirements", "analysis_steps", "map_layers",
                  "statistics", "charts", "fallbacks", "methodology_warnings",
                  "workflow_contract", "manifest_fingerprint", "completeness")
    }
    stages.append(_stage_record(
        "produce_map_product_plan",
        evidence={"plan_id": plan_dump.get("plan_id", ""),
                  "status": plan_dump.get("status", ""),
                  "fallback_count": len(plan_dump.get("fallbacks") or [])},
    ))

    # ── 12 produce_completion_contract ───────────────────────────────
    from app.services.gis_harness.workflow_schema import COMPLETION_DIMENSIONS
    wc = plan_dump.get("workflow_contract") or {}
    dim_states: Dict[str, bool] = {}
    for dim in COMPLETION_DIMENSIONS:
        if dim == "data":
            dim_states[dim] = not (wc.get("data_blockers") or [])
        elif dim == "science":
            dim_states[dim] = not (wc.get("method_blockers") or [])
        else:
            # 规划期只声明契约与义务；执行期维度由 completion 管线在
            # 证据到手后核验（planning 期不虚构满足）。
            dim_states[dim] = True
    compilation.completion_contract = {
        "dimensions_declared": list(COMPLETION_DIMENSIONS),
        "planning_time_states": dim_states,
        "required_disclosures": list(getattr(wf_profile, "required_disclosures", []) or [])[:8],
        "evidence_requirements": list(getattr(wf_profile, "evidence_requirements", []) or [])[:8],
        "fallback_policies": [
            {"reason_code": p.reason_code,
             "downgrade_class": p.downgrade_class,
             "blocks_completion": p.blocks_completion}
            for p in (getattr(wf_profile, "fallback_policies", []) or [])[:8]
        ],
    }
    blocked_codes = sorted(
        set(contract_report.method_blockers + contract_report.data_blockers))
    stages.append(_stage_record(
        "produce_completion_contract",
        status="blocked" if blocked_codes else "ok",
        reason_codes=blocked_codes[:_STAGE_REASON_BUDGET],
        evidence={"dimensions_declared": len(COMPLETION_DIMENSIONS)},
    ))

    compilation.reason_codes = [
        rc for s in stages for rc in s.reason_codes
    ][:_STAGE_REASON_BUDGET * 2]
    return compilation
