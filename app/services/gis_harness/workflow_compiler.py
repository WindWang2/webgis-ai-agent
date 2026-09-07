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

管线（规格 §10；V3 扩展后 15 阶段）：

    1 normalize_intent          8  compile_capability_dag
    2 map_task_ontology (V3)    9  evaluate_obligations
    3 resolve_task_family      10  resolve_algorithms
    4 resolve_scope            11  compute_transformations
    5 resolve_recipe_candidates 12 resolve_cartography
    6 resolve_data_roles       13  produce_map_product_plan
    7 qualify_data (V3)        14  produce_completion_contract
    7b plan_candidates (V3)
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from app.services.gis_harness.intent import MapRequestIntent, resolve_map_request_intent

#: 编译器管线的固定阶段序（测试锁定顺序与数量）。
COMPILER_STAGES = (
    "normalize_intent",
    "map_task_ontology",
    "resolve_task_family",
    "resolve_scope",
    "resolve_recipe_candidates",
    "resolve_data_roles",
    "qualify_data",
    "plan_candidates",
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
    # V3：intent → 本体任务的有序匹配（GIS task ontology 映射证据）。
    ontology_matches: List[Dict[str, Any]] = Field(default_factory=list)
    # V3：数据角色资格裁决（typed data qualification，per-role 四态+修复）。
    data_qualifications: List[Dict[str, Any]] = Field(default_factory=list)
    # V3：多候选规划候选集（selected + rejected + 拒绝理由，可解释 trace）。
    plan_candidates: Dict[str, Any] = Field(default_factory=dict)
    # V3：四层回退裁决（preferred/degraded/minimal/blocked + 披露）。
    fallback_resolution: Dict[str, Any] = Field(default_factory=dict)
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
            "ontology_matches": self.ontology_matches[:6],
            "data_qualifications": self.data_qualifications[:16],
            "plan_candidates": self.plan_candidates,
            "fallback_resolution": self.fallback_resolution,
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
    """把 query/intent 确定性编译为 WorkflowCompilation（15 阶段）。

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

    # ── 2 map_task_ontology（V3：intent → GIS 任务本体匹配）──────────
    from app.services.gis_harness.gis_ontology import match_task_ontology
    onto_matches = match_task_ontology(merged, limit=5)
    compilation.ontology_matches = [m.to_bounded_dict() for m in onto_matches]
    stages.append(_stage_record(
        "map_task_ontology",
        reason_codes=(
            [f"planned_task:{m.task_id}" for m in onto_matches
             if m.semantic_status == "planned"][:_STAGE_REASON_BUDGET]
        ),
        evidence={
            "primary_task": onto_matches[0].task_id if onto_matches else "",
            "matches": [
                {"task": m.task_id, "score": round(m.score, 2)}
                for m in onto_matches[:4]
            ],
        },
    ))

    # ── 3 resolve_task_family ────────────────────────────────────────
    task = merged.task
    wf_hint = registry.keyword_hits(merged.query)
    stages.append(_stage_record(
        "resolve_task_family",
        evidence={"task": task,
                  "keyword_signals": [r.id for r in wf_hint[:4]]},
    ))

    # ── 4 resolve_scope ──────────────────────────────────────────────
    stages.append(_stage_record(
        "resolve_scope",
        evidence={"scope": merged.scope.name or "unresolved",
                  "level": merged.scope.level,
                  "subject": merged.subject.type,
                  "geometry_expectation": merged.geometry_expectation},
    ))

    # ── 5 resolve_recipe_candidates ──────────────────────────────────
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

    # ── 6 resolve_data_roles（finalize 期随 profile 复评）────────────
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

    # ── 7 qualify_data（V3：per-role 数据资格四态裁决 + 修复声明）────
    def _run_qualify(wf: Any, resolutions: List[Any]) -> tuple:
        """资格裁决 + 阶段记录（reroute 改写 recipe 后必须重跑，保证
        remediation 物化与 fallback 裁决消费的是**当前** recipe 的证据）。"""
        quals: List[Any] = []
        if wf is not None and wf.data_roles:
            from app.services.gis_harness.data_qualification import (
                qualify_workflow_data_roles,
            )
            # 投影/度量类 precondition 在场 → 资格阶段联动
            # projected_crs_required 检查（委托算法层，单一事实源）。
            crs_obligation = any(
                "projected_crs" in (getattr(o, "precondition_id", "") or "")
                or "local_metric_crs" in (getattr(o, "precondition_id", "") or "")
                for o in wf.obligations
            )
            quals = qualify_workflow_data_roles(
                wf.data_roles, resolutions,
                resolver_profile=profile,
                crs_projection_obligation=crs_obligation,
            )
        role_states = {q.role: q.state for q in quals}
        record = _stage_record(
            "qualify_data",
            status="skipped" if not quals else (
                "blocked"
                if any(q.state == "blocked" for q in quals)
                else "ok"),
            reason_codes=[
                q.reason_code for q in quals
                if q.state in ("blocked", "degraded")
            ][:_STAGE_REASON_BUDGET],
            evidence={
                "states": dict(list(role_states.items())[:8]),
                "remediations": sum(len(q.remediation) for q in quals),
            },
        )
        return quals, role_states, record

    data_qualifications, states, qualify_stage = _run_qualify(
        wf_profile, role_resolutions)
    compilation.data_qualifications = [
        q.to_bounded_dict() for q in data_qualifications
    ]
    stages.append(qualify_stage)

    # ── 7b plan_candidates（V3：多候选生成/评分/可解释选择）──────────
    from app.services.gis_harness.plan_candidates import generate_plan_candidates
    candidate_set = generate_plan_candidates(
        merged, profile=profile, available_tools=available_tools)
    compilation.plan_candidates = candidate_set.to_bounded_dict()
    selected_candidate = candidate_set.selected
    # 零漂移改写：仅当语义 top-1 被科学阻断而最优候选可行时，改写计划
    # 承载 recipe（候选集本身完整保留，供 trace/replay/evaluation）。
    reroute_note = ""
    if (candidate_set.rerouted and selected_candidate is not None
            and not recipe_id):
        reroute_note = candidate_set.reroute_reason
        selected = registry.get(selected_candidate.recipe_id) or selected
        compilation.recipe_id = selected.id
        wf_profile = getattr(selected, "workflow", None)
        role_resolutions = resolve_data_roles(
            selected.id, wf_profile, resolver_profile=profile)
        compilation.data_roles = [r.to_bounded_dict() for r in role_resolutions]
        # 陈旧证据守卫：改写后重跑资格裁决（stage 7 记录替换为新 recipe 的
        # 证据）—— 否则 remediation 物化与 fallback 裁决消费旧 recipe 事实。
        data_qualifications, states, qualify_stage = _run_qualify(
            wf_profile, role_resolutions)
        compilation.data_qualifications = [
            q.to_bounded_dict() for q in data_qualifications
        ]
        stages[:] = [
            s if s.stage != "qualify_data" else qualify_stage for s in stages
        ]
    if selected_candidate is not None and selected_candidate.disclosures:
        stages.append(_stage_record(
            "plan_candidates",
            reason_codes=[],
            evidence={"selected": selected_candidate.candidate_id,
                      "scenario_disclosures": list(
                          selected_candidate.disclosures[:2])},
        ))
    else:
        stages.append(_stage_record(
            "plan_candidates",
            reason_codes=([reroute_note] if reroute_note else []),
            evidence={"selected": candidate_set.selected_id,
                      "candidate_count": len(candidate_set.candidates),
                      "rejected": sum(
                          1 for c in candidate_set.candidates
                          if c.status == "rejected")},
        ))

    # ── 7a plan production（先编译 plan，供 8/9/12 消费确定性产物）──
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

    # ── 8 compile_capability_dag ─────────────────────────────────────
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

    # ── 9 evaluate_obligations（复用 planner/ finalize 期评估）────────
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

    # ── 10 resolve_algorithms ─────────────────────────────────────────
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

    # ── 11 compute_transformations ────────────────────────────────────
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
    # V3：数据资格的 remediation 物化为显式 transform step（仅收
    # auto_applicable=True 的操作 —— 有确定性实现；否则留在资格证据中）。
    for q in data_qualifications:
        for r in q.remediation:
            if r.auto_applicable:
                transformations.append({
                    "source": "data_qualification",
                    "role": q.role,
                    "operation": r.operation,
                    "target": r.target,
                    "params": r.params,
                    "reason_code": r.reason_code,
                    "disclosure": r.disclosure,
                })
    compilation.transformations = transformations[:8]
    stages.append(_stage_record(
        "compute_transformations",
        evidence={"count": len(transformations)},
    ))

    # ── 12 resolve_cartography ───────────────────────────────────────
    stages.append(_stage_record(
        "resolve_cartography",
        evidence={
            "primary_cartography": selected.primary_cartography,
            "secondary": list(selected.secondary_cartography)[:4],
            "template_id": plan_dump.get("template_id", ""),
            "layers": [
                {"role": layer.get("role"), "cartography": layer.get("cartography"),
                 "layer_type": layer.get("layer_type")}
                for layer in (plan_dump.get("map_layers") or [])[:6]
            ],
        },
    ))

    # ── 13 produce_map_product_plan ──────────────────────────────────
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

    # ── 14 produce_completion_contract ───────────────────────────────
    from app.services.gis_harness.workflow_schema import COMPLETION_DIMENSIONS
    wc = plan_dump.get("workflow_contract") or {}

    # V3：四层回退裁决（preferred/degraded/minimal/blocked）。事实来源：
    # 资格状态 + 义务阻断 + planned 能力 + 本体主任务状态 + 场景 minimal。
    from app.services.gis_harness.fallback_v3 import resolve_fallback_tier
    scenario_minimal = ""
    if selected_candidate is not None and selected_candidate.scenario_id:
        from app.services.gis_harness.workflow_families import (
            get_workflow_family_registry,
        )
        _scn = get_workflow_family_registry().scenario(
            selected_candidate.scenario_id)
        if _scn is not None:
            scenario_minimal = _scn.minimal_disclosure
    fallback_res = resolve_fallback_tier(
        ontology_task_id=(onto_matches[0].task_id if onto_matches else ""),
        data_states=tuple(states.values()),
        method_blockers=tuple(contract_report.method_blockers),
        data_blockers=tuple(contract_report.data_blockers),
        uses_planned_capability=bool(
            selected_candidate is not None
            and selected_candidate.cost.uses_planned_capability),
        scenario_minimal_disclosure=scenario_minimal,
    )
    compilation.fallback_resolution = fallback_res.to_bounded_dict()

    dim_states: Dict[str, Optional[bool]] = {}
    for dim in COMPLETION_DIMENSIONS:
        if dim == "data":
            dim_states[dim] = not (wc.get("data_blockers") or [])
        elif dim == "science":
            dim_states[dim] = not (wc.get("method_blockers") or [])
        else:
            # R1-A12：规划期只声明契约与义务；执行期维度证据未到，诚实置
            # None（unknown）—— True 会诱导下游当作已满足。
            dim_states[dim] = None
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
        # V3：回退裁决进入完成契约（finalize/verdict 消费同一份证据）
        "fallback_tier": fallback_res.tier,
        "fallback_downgrade_class": fallback_res.downgrade_class,
        "fallback_disclosures": fallback_res.disclosures[:6],
    }
    blocked_codes = sorted(
        set(contract_report.method_blockers + contract_report.data_blockers))
    stages.append(_stage_record(
        "produce_completion_contract",
        status="blocked" if blocked_codes else "ok",
        reason_codes=blocked_codes[:_STAGE_REASON_BUDGET],
        evidence={"dimensions_declared": len(COMPLETION_DIMENSIONS),
                  "fallback_tier": fallback_res.tier},
    ))

    compilation.reason_codes = [
        rc for s in stages for rc in s.reason_codes
    ][:_STAGE_REASON_BUDGET * 2]
    return compilation
