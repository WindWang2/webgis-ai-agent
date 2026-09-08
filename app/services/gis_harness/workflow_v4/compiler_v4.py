"""Workflow Compiler V4 —— 15 阶段管线的 additive V4 扩展（Epic workflow-v4）。

``compile_workflow_v4`` = 既有 15 阶段确定性编译（``compile_workflow``，
零改动、零漂移）+ V4 语义阶段：

    16 resolve_methodology    本体任务 → 方法论族（12 族透镜）
    17 select_method          方法候选资格裁决与确定性排序
    18 compile_typed_dag      类型化工作流图（端口/CRS 类/artifact 类型）

后续 wave 追加阶段（义务继承/包版本/参数/重算/制图义务/获取声明）时，
WORKFLOW_V4_STAGES 按序追加 —— 每个阶段的功能落地才算数，不留 placeholder。

红线：

- V4 阶段是**编排**：复用 methodology / typed_dag / data_qualification /
  workflow_schema 的既有评估器重放确定性事实（同输入同输出），不复制
  任何事实、不建第二评估路径；
- 编译器不执行工具：产物是声明与证据，执行归 Harness runtime；
- 产物可序列化、有界、同输入同输出；COMPILER_STAGES 契约（15 阶段，
  测试锁定）不变。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from app.services.gis_harness.workflow_compiler import (
    compile_workflow,
    WorkflowCompilation,
    WorkflowStageRecord,
)

#: V4 编译器版本（semver；major = V4 契约，minor = 阶段增补，patch = 文案）。
WORKFLOW_COMPILER_VERSION = "4.0.0"

#: V4 扩展阶段序（追加式；与 COMPILER_STAGES 拼接为完整 23 阶段）。
WORKFLOW_V4_STAGES = (
    "resolve_methodology",
    "select_method",
    "compile_typed_dag",
    "inherit_obligations",
    "resolve_parameters",
    "evaluate_cartographic_obligations",
    "plan_acquisition",
    "emit_workflow_package",
)

_STAGE_REASON_BUDGET = 8


class WorkflowCompilationV4(BaseModel):
    """V4 编译产物：base（15 阶段）+ V4 语义层（方法/方法族/typed DAG/
    义务链/参数/制图义务/获取声明/包）。"""
    base: WorkflowCompilation
    v4_stages: List[WorkflowStageRecord] = Field(default_factory=list)
    methodology_family: str = ""
    methodology_family_zh: str = ""
    method_qualification: Dict[str, Any] = Field(default_factory=dict)
    typed_dag: Dict[str, Any] = Field(default_factory=dict)
    obligation_chain: Dict[str, Any] = Field(default_factory=dict)
    parameters: List[Dict[str, Any]] = Field(default_factory=list)
    cartographic_obligations: List[Dict[str, Any]] = Field(default_factory=list)
    acquisition_plan: Dict[str, Any] = Field(default_factory=dict)
    package_fingerprint: str = ""
    compiler_version: str = WORKFLOW_COMPILER_VERSION
    methodology_fingerprint: str = ""
    reason_codes: List[str] = Field(default_factory=list)

    @property
    def full_stage_sequence(self) -> List[str]:
        return ([s.stage for s in self.base.stages]
                + [s.stage for s in self.v4_stages])

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "compiler_version": self.compiler_version,
            "base": self.base.to_bounded_dict(),
            "v4_stages": [s.to_bounded_dict() for s in self.v4_stages],
            "methodology_family": self.methodology_family[:40],
            "methodology_family_zh": self.methodology_family_zh[:60],
            "method_qualification": self.method_qualification,
            "typed_dag": self.typed_dag,
            "obligation_chain": self.obligation_chain,
            "parameters": self.parameters[:16],
            "cartographic_obligations": self.cartographic_obligations[:8],
            "acquisition_plan": self.acquisition_plan,
            "package_fingerprint": self.package_fingerprint[:64],
            "methodology_fingerprint": self.methodology_fingerprint[:64],
            "reason_codes": [str(c)[:64] for c in self.reason_codes[:12]],
        }


def compile_workflow_v4(
    query: str,
    *,
    intent: Optional[Any] = None,
    hint: Optional[Dict[str, Any]] = None,
    profile: Optional[Dict[str, Any]] = None,
    available_tools: Optional[List[str]] = None,
    project_verified: Optional[set] = None,
    recipe_id: str = "",
    template_id: str = "",
    min_points_default: int = 10,
) -> WorkflowCompilationV4:
    """确定性编译：15 阶段 base + V4 方法论/方法/typed-DAG 阶段。"""
    from app.services.gis_harness.recipes import get_recipe_registry
    from app.services.gis_harness.workflow_schema import (
        resolve_data_roles,
    )
    from app.services.gis_harness.workflow_v4.methodology import (
        get_methodology_registry,
        qualify_method_candidates,
    )
    from app.services.gis_harness.workflow_v4.typed_dag import (
        build_typed_dag,
    )

    base = compile_workflow(
        query, intent=intent, hint=hint, profile=profile,
        available_tools=available_tools,
        project_verified=project_verified, recipe_id=recipe_id,
        template_id=template_id, min_points_default=min_points_default,
    )
    result = WorkflowCompilationV4(base=base)
    v4_stages = result.v4_stages  # 直接持有模型列表（pydantic 校验会拷贝入参列表）

    registry = get_methodology_registry()
    result.methodology_fingerprint = registry.fingerprint

    # ── 16 resolve_methodology：本体任务 → 方法族 ─────────────────────
    primary_task = (
        base.ontology_matches[0].get("task_id", "")
        if base.ontology_matches else ""
    )
    family = registry.family_for_task(primary_task)
    family = family[0] if family else None
    if family is None:
        v4_stages.append(WorkflowStageRecord(
            stage="resolve_methodology", status="skipped",
            reason_codes=["METHODOLOGY_FAMILY_UNMAPPED"],
            evidence={"ontology_task": primary_task[:64]},
        ))
        result.reason_codes = [rc for s in v4_stages for rc in s.reason_codes]
        return result
    result.methodology_family = family.family_id
    result.methodology_family_zh = family.label_zh
    v4_stages.append(WorkflowStageRecord(
        stage="resolve_methodology",
        reason_codes=[f"planned_family:{family.family_id}"],
        evidence={
            "ontology_task": primary_task[:64],
            "all_families": [
                f.family_id for f in registry.family_for_task(primary_task)
            ][:6],
            "label_zh": family.label_zh[:60],
        },
    ))

    # ── 17 select_method：候选资格裁决（重放既有评估器，无新事实源）──
    recipe_reg = get_recipe_registry()
    recipe = recipe_reg.get(base.recipe_id)
    wf_profile = getattr(recipe, "workflow", None)
    role_resolutions = resolve_data_roles(
        base.recipe_id, wf_profile, resolver_profile=profile)
    role_states = {r.role: r.status for r in role_resolutions}
    qual_set = qualify_method_candidates(
        family.family_id, role_states=role_states, profile=profile,
        registry=registry,
    )
    result.method_qualification = qual_set.to_bounded_dict()
    selected_method = (
        registry.method(qual_set.selected_id) if qual_set.selected_id else None
    )
    v4_stages.append(WorkflowStageRecord(
        stage="select_method",
        status="blocked" if qual_set.all_rejected else "ok",
        reason_codes=(
            ["METHOD_FAMILY_ALL_REJECTED"]
            if qual_set.all_rejected
            else []
        ) + [
            rc for q in qual_set.qualifications[:6] if q.status == "rejected"
            for rc in q.reason_codes[:2]
        ][:_STAGE_REASON_BUDGET],
        evidence={
            "selected": qual_set.selected_id[:64],
            "candidate_count": len(qual_set.qualifications),
            "rejected": sum(
                1 for q in qual_set.qualifications if q.status == "rejected"),
        },
    ))

    # ── 18 compile_typed_dag：plan steps + 方法契约 → 类型化图 ────────
    analysis_steps = (base.plan.get("analysis_steps") or [])
    transforms = [
        t for t in (base.transformations or [])
        if t.get("source") == "data_qualification" and t.get("role")
    ]
    graph = build_typed_dag(
        analysis_steps,
        data_roles=role_resolutions,
        selected_method=selected_method,
        extra_transforms=transforms,
    )
    result.typed_dag = graph.to_bounded_dict()
    v4_stages.append(WorkflowStageRecord(
        stage="compile_typed_dag",
        status="blocked" if graph.validation_violations else "ok",
        reason_codes=list(graph.validation_violations[:_STAGE_REASON_BUDGET]),
        evidence={
            "nodes": len(graph.nodes),
            "edges": len(graph.edges),
            "primary_output": graph.primary_output[:64],
            "parallel_safe": sum(1 for n in graph.nodes if n.parallel_safe),
        },
    ))

    # 后续阶段共享的中间事实（全部为既有评估器的确定性重放）
    secondary_cartography = tuple(
        getattr(recipe, "secondary_cartography", ()) or ())

    # ── 19 inherit_obligations：recipe(+家族上下文) → 义务链 ─────────
    from app.services.gis_harness.workflow_v4.obligations import (
        inherit_obligations,
        profile_to_source,
    )
    chain = inherit_obligations([profile_to_source(
        base.recipe_id, wf_profile, family=family.family_id)])
    result.obligation_chain = chain.to_bounded_dict()
    v4_stages.append(WorkflowStageRecord(
        stage="inherit_obligations",
        evidence={
            "sources": chain.sources_count,
            "obligations": len(chain.obligations),
            "conflicts": sum(1 for o in chain.obligations if o.conflict),
            "fingerprint": chain.fingerprint[:16],
        },
    ))

    # ── 20 resolve_parameters：契约默认 → hint → user（不阻塞）────────
    from app.services.gis_harness.workflow_v4.parameters import (
        extract_workflow_parameters,
        resolve_workflow_parameters,
    )
    params = extract_workflow_parameters(
        selected_method,
        node_id=(f"cap:{selected_method.capabilities[0]}"
                 if getattr(selected_method, "capabilities", ()) else ""),
    )
    resolved_params = resolve_workflow_parameters(
        params, hint_values=hint)
    result.parameters = [p.to_bounded_dict() for p in resolved_params]
    v4_stages.append(WorkflowStageRecord(
        stage="resolve_parameters",
        reason_codes=[
            f"PARAM_FALLBACK_DEFAULT:{p.name}"
            for p in resolved_params if p.disclosure][:_STAGE_REASON_BUDGET],
        evidence={
            "count": len(resolved_params),
            "provenances": sorted({p.provenance for p in resolved_params}),
        },
    ))

    # ── 21 evaluate_cartographic_obligations：表达-数据资格义务 ───────
    from app.services.gis_harness.workflow_v4.cartography import (
        evaluate_cartographic_obligations,
    )
    carto_obls = evaluate_cartographic_obligations(
        selected_method, role_states=role_states, profile=profile,
        secondary_cartography=secondary_cartography)
    result.cartographic_obligations = [
        o.to_bounded_dict() for o in carto_obls]
    v4_stages.append(WorkflowStageRecord(
        stage="evaluate_cartographic_obligations",
        status="blocked" if any(
            o.on_violation == "block_method" for o in carto_obls) else "ok",
        reason_codes=[
            o.rule_code for o in carto_obls
            if o.on_violation in ("block_method", "degrade_with_disclosure")
        ][:_STAGE_REASON_BUDGET],
        evidence={"count": len(carto_obls)},
    ))

    # ── 22 plan_acquisition：获取备选声明（不抓取）────────────────────
    from app.services.gis_harness.workflow_v4.acquisition import (
        plan_acquisition,
    )
    acq_plan = plan_acquisition(role_resolutions)
    result.acquisition_plan = acq_plan.to_bounded_dict()
    v4_stages.append(WorkflowStageRecord(
        stage="plan_acquisition",
        evidence={
            "roles": len(acq_plan.roles),
            "feasible_local": sum(
                1 for r in acq_plan.roles if "local" in r.feasible_channels),
            "synthetic_demo": acq_plan.synthetic_demo_allowed,
        },
    ))

    # ── 23 emit_workflow_package：不可变包指纹 ─────────────────────────
    from app.services.gis_harness.workflow_v4.package import (
        emit_workflow_package,
    )
    package = emit_workflow_package(result)
    result.package_fingerprint = package.fingerprint
    v4_stages.append(WorkflowStageRecord(
        stage="emit_workflow_package",
        evidence={
            "package_id": package.package_id[:64],
            "version": package.version,
            "fingerprint": package.fingerprint[:16],
        },
    ))

    result.reason_codes = [
        rc for s in v4_stages for rc in s.reason_codes
    ][:_STAGE_REASON_BUDGET * 2]
    return result
