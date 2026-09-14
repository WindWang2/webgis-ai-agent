"""Procedure Compiler —— 数据流提取与契约封装（ADR-0191 D2/D3）。

三步法的第二、三步：

1. **数据流提取**：把轨迹步骤的线性依赖拓扑提炼为 ADR-0182
   ``SkillProcedure`` 声明式 IR（steps/requirements/evidence_nodes/
   fallbacks）——引用不复制（capability id 引用注册表；词表全部
   复用既有事实源），不新造执行图（尊重 ADR-0184 D1）。
2. **契约封装**：合成 ``SkillContract``（SkillCard 选择面 + 质量义务
   + 完成证据 + 溯源），并渲染为版本化 YAML 资产文本。合成契约
   必须过 ``validate_contract``（注入谓词）——编译器**强制**生成
   三触发器 fallback 与交付义务，不依赖轨迹碰巧包含。

红线：编译产物 pack=induced（草案资产）；induced.cap.* 能力经动态
挂钩以 planned 状态登记（诚实语义）；guidance 恒为空（防 Skill
Prompt 化，ADR-0182 S26）。全部确定性：零 LLM、零 I/O。
"""
from __future__ import annotations

import hashlib
from typing import Any, Callable, Dict, List, Mapping, Optional

import yaml
from pydantic import BaseModel, Field

from app.lib.gis.capability_registry import (
    CapabilityDescriptor,
    CapabilityRegistry,
)
from app.services.gis_harness.skills.contract import (
    SKILL_DOMAINS,
    CapabilityRequirement,
    QualityObligation,
    SkillContract,
    SkillExample,
    SkillFallbackPolicy,
)
from app.services.gis_harness.skills.procedure_ir import (
    EvidenceNode,
    FallbackNode,
    ProcedureStep,
    RequirementNode,
    SkillProcedure,
    StepEvidenceRequirement,
)
from app.services.gis_harness.skills.semantics import StatisticalSemantics

from app.services.gis_skills.induction.parameter_generalizer import (
    InducedParameter,
    build_model_from_parameters,
)

#: 产物命名空间与草案版本语义。
INDUCED_SKILL_PREFIX = "induced"
INDUCED_PACK = "induced"
INDUCED_VERSION = "0.1.0"

#: 强制降级声明（ADR-0182 §2.5：三触发器缺一即 fatal；禁止 silent fallback）。
_FORCED_FALLBACKS = (
    ("fb_missing_input", "missing_input", "blocked",
     "缺输入数据：不虚构、不静默降级，声明缺口后阻塞该步骤。"),
    ("fb_unsupported_geometry", "unsupported_geometry", "blocked",
     "几何类别不受支持：拒绝以当前方法执行并披露原因。"),
    ("fb_insufficient_data", "insufficient_data", "reduced_output",
     "数据不足：降级产出并披露覆盖度与口径，禁止以稀薄数据下强结论。"),
)


def _slug(text: str, length: int = 10) -> str:
    digest = hashlib.sha256((text or "").encode("utf-8"),
                            usedforsecurity=False).hexdigest()
    return digest[:length]


def _register_projected_capability(registry: CapabilityRegistry,
                                   capability_id: str, tool_name: str) -> str:
    """未注册能力经动态挂钩登记为 planned（幂等；已存在即跳过）。

    返回空串 = 登记成功；返回文本 = 登记失败原因（预算耗尽/纪律冲突），
    由调用方并入编译违规（fail-closed，不崩溃、不静默）。
    """
    if registry.has(capability_id):
        return ""
    try:
        registry.register_dynamic(CapabilityDescriptor(
            id=capability_id,
            name=tool_name[:60] or capability_id,
            description=f"自轨迹工具 {tool_name} 投影的声明型能力"
                        f"（{capability_id}；planned，非 native）",
            category="analysis",
            status="planned",
            deterministic=True,
        ))
    except ValueError as exc:
        # 预算耗尽 / 前缀冲突等：转显式违规（审查 P1-b——原实现异常
        # 逃逸出 engine.induce，击穿批处理）。
        return f"capability_register:{capability_id}: {exc}"
    return ""


def _capability_hard_gate(registry: CapabilityRegistry,
                          capability_id: str) -> bool:
    desc = registry.get(capability_id)
    return bool(desc and desc.status == "native")


class CompiledSkill(BaseModel):
    """编译产物：契约 + YAML 资产文本 + 参数槽位 + 溯源 + 编译期违规。"""

    contract: SkillContract
    yaml_text: str = ""
    parameters: List[InducedParameter] = Field(default_factory=list)
    provenance: Dict[str, Any] = Field(default_factory=dict)
    violations: List[str] = Field(default_factory=list)

    def build_parameter_model(self):
        """参数槽位 → 动态 Pydantic 模型（沙盒变体重放的校验层）。"""
        return build_model_from_parameters(self.parameters)


def _build_procedure(analysis, capability_by_seq: Dict[int, str],
                     field_params: List[InducedParameter]) -> SkillProcedure:
    steps: List[ProcedureStep] = []
    for step in analysis.steps:
        steps.append(ProcedureStep(
            step_id=f"s{step.seq}",
            title=step.tool_name[:80],
            kind=step.kind,
            description=f"步骤 {step.seq}：由轨迹调用 {step.tool_name} 归纳"
                        f"（参数槽位 {sorted(step.arguments)[:6]}）。"[:240],
            capability_refs=[capability_by_seq[step.seq]],
            depends_on=[f"s{step.seq - 1}"] if step.seq > 1 else [],
            evidence_requirements=[
                StepEvidenceRequirement(evidence_kind=ek,
                                        description=f"{step.tool_name} 的 {ek} 证据")
                for ek in step.evidence_kinds
            ],
            required=True,
            skip_policy="never",
            guidance="",  # 防 Skill Prompt 化：合成技能不携带模型提示
        ))

    # 前置守卫：field 语义参数 → field 资格节点（degrade_with_disclosure）
    requirements: List[RequirementNode] = []
    for p in field_params:
        requirements.append(RequirementNode(
            node_id=f"req_field_{p.name}",
            requirement_kind="field",
            value=str(p.example)[:64],
            reason_code=f"FIELD_MISSING_{p.name.upper()[:32]}",
            on_fail="degrade_with_disclosure",
        ))

    evidence_kinds = sorted({ek for s in steps
                             for ev in s.evidence_requirements
                             for ek in [ev.evidence_kind]})
    evidence_nodes = [
        EvidenceNode(node_id=f"ev_{i}", evidence_kind=ek,
                     description=f"归纳证据要求：{ek}")
        for i, ek in enumerate(evidence_kinds, start=1)
    ]
    fallbacks = [
        FallbackNode(node_id=nid, trigger=trigger, action=action,
                     disclosure=disclosure, reason_code=nid.upper())
        for nid, trigger, action, disclosure in _FORCED_FALLBACKS
    ]
    return SkillProcedure(steps=steps, decisions=[], requirements=requirements,
                          evidence_nodes=evidence_nodes, fallbacks=fallbacks)


def _statistical_semantics(analysis) -> StatisticalSemantics:
    measures = ["count"]
    names = " ".join(s.tool_name.lower() for s in analysis.steps)
    if any(k in names for k in ("index", "exposure", "compute", "score")):
        measures.append("index")
    if "density" in names:
        measures.append("density")
    return StatisticalSemantics(
        measure_semantics=measures,
        denominator_required=False,
        normalization="",
    )


def compile_skill(
    analysis,
    generalization,
    *,
    registry: CapabilityRegistry,
    domain: str = "general",
    capability_map: Optional[Mapping[str, str]] = None,
    recipe_exists: Optional[Callable[[str], bool]] = None,
    ontology_task_exists: Optional[Callable[[str], bool]] = None,
    artifact_type_exists: Optional[Callable[[str], bool]] = None,
    precondition_exists: Optional[Callable[[str], bool]] = None,
) -> CompiledSkill:
    """泛化产物 → ADR-0182 SkillContract + YAML（纯函数 + 受控注册）。

    ``registry`` 必显式传入（审查 P2：库函数缺省写进程级能力单例是
    隐式全局副作用——现在调用方决定登记面，引擎/CLI 走私有实例）。
    能力投影以分析产物的 ``capability_id`` 为准（分析期一次性投影，
    编译器不二次投影，保证拓扑指纹与源轨迹可对账）。
    """
    if domain not in SKILL_DOMAINS:
        domain = "general"

    # 未知能力经动态挂钩登记（诚实 planned 语义；幂等；失败 → 编译违规）
    registration_errors: List[str] = []
    seen_caps: set = set()
    for step in analysis.steps:
        if step.capability_id in seen_caps:
            continue
        seen_caps.add(step.capability_id)
        error = _register_projected_capability(registry, step.capability_id,
                                               step.tool_name)
        if error:
            registration_errors.append(error)

    capability_by_seq: Dict[int, str] = {
        step.seq: step.capability_id for step in analysis.steps}
    goal_text = (analysis.goal_text or analysis.selected_workflow
                 or "induced-procedure").strip()
    skill_id = f"{INDUCED_SKILL_PREFIX}.{domain}.{_slug(goal_text)}"
    field_params = [p for p in generalization.parameters
                    if p.semantic_role == "field"]
    procedure = _build_procedure(analysis, capability_by_seq, field_params)

    completion = sorted({ek for s in procedure.steps
                         if s.kind == "deliver"
                         for ev in s.evidence_requirements
                         for ek in [ev.evidence_kind]}) \
        or [procedure.steps[-1].evidence_requirements[0].evidence_kind]

    obligations = [QualityObligation(
        obligation_id="obl_product_completeness",
        obligation_kind="procedure",
        description="交付步骤必须留下产品完整性证据（标题/图例/来源）。",
        evidence_kind="product_completeness"
        if any(ev.evidence_kind == "product_completeness"
               for s in procedure.steps
               for ev in s.evidence_requirements)
        else completion[0],
    )]
    if analysis.mutations_count > 0:
        obligations.append(QualityObligation(
            obligation_id="obl_mutation_receipt",
            obligation_kind="procedure",
            description="变异事件必须留下 receipts（可追溯、可回滚）。",
            evidence_kind="map_mutation_receipt",
        ))

    contract = SkillContract(
        id=skill_id,
        name=f"自合成·{goal_text[:24]}",
        description=f"由轨迹 {analysis.session_id}/{analysis.turn_id} 归纳的 "
                    f"{len(procedure.steps)} 步过程"
                    f"（源满意度 {analysis.satisfaction if analysis.satisfaction is not None else 0:.2f}）。"[:240],
        domain=domain,
        pack=INDUCED_PACK,
        intent_patterns=[goal_text[:32]] if goal_text else [],
        ontology_tasks=[],
        task_types=[],
        when_to_use=f"目标与「{goal_text[:64]}」同类的作业流程；"
                    f"由高满意度成功轨迹自合成。"[:240],
        when_not_to_use="数据前提或参数语义不符时；存在审定 core 技能时优先审定资产。",
        capability_requirements=[
            CapabilityRequirement(
                capability_id=cap_id,
                purpose=f"轨迹工具 {tool} 的能力投影",
                criticality="required",
                hard_gate=_capability_hard_gate(registry, cap_id),
            )
            for cap_id, tool in _dedup_capabilities(analysis, capability_by_seq)
        ],
        input_roles=[],
        output_roles=[],
        output_artifacts=[],
        recipe_refs=[],
        procedure=procedure,
        statistical_semantics=_statistical_semantics(analysis),
        quality_obligations=obligations,
        fallback_policy=[
            SkillFallbackPolicy(trigger="missing_input",
                                downgrade_class="not_allowed",
                                disclosure="缺输入不执行。",
                                blocks_completion=True),
        ],
        completion_evidence=completion,
        product_requirements=[],
        version=INDUCED_VERSION,
        examples=[SkillExample(
            title=f"源轨迹 {analysis.session_id}/{analysis.turn_id}"[:80],
            query=(analysis.user_input or "")[:200],
        )] if analysis.user_input else [],
        guidance="",
        source_doc=f"replay-recordings/{analysis.session_id}/"
                   f"{analysis.turn_id}.json",
    )

    violations = contract.validate_contract(
        capability_exists=registry.has,
        recipe_exists=recipe_exists or (lambda rid: True),
        ontology_task_exists=ontology_task_exists or (lambda t: True),
        artifact_type_exists=artifact_type_exists or (lambda a: True),
        precondition_exists=precondition_exists or (lambda p: True),
    )
    # 动态登记失败（预算/纪律）并入编译违规：带 IND_COMPILE_VIOLATIONS
    # 走 fail-closed 拒绝，绝不带病入库。
    violations.extend(registration_errors)
    yaml_text = yaml.safe_dump(
        contract.model_dump(), allow_unicode=True, sort_keys=False,
        default_flow_style=False)

    return CompiledSkill(
        contract=contract,
        yaml_text=yaml_text,
        parameters=list(generalization.parameters),
        provenance={
            "session_id": analysis.session_id,
            "turn_id": analysis.turn_id,
            "behavior_digest": analysis.behavior_digest,
            "source_satisfaction": analysis.satisfaction,
            "mutations_count": analysis.mutations_count,
            "artifacts_count": analysis.artifacts_count,
            "engine": "gis-skills.induction-v1",
        },
        violations=violations,
    )


def _dedup_capabilities(analysis,
                        capability_by_seq: Dict[int, str]) -> List[tuple]:
    seen: set = set()
    pairs: List[tuple] = []
    for step in analysis.steps:
        cap_id = capability_by_seq[step.seq]
        if cap_id in seen:
            continue
        seen.add(cap_id)
        pairs.append((cap_id, step.tool_name))
    return pairs


__all__ = [
    "INDUCED_SKILL_PREFIX",
    "INDUCED_PACK",
    "INDUCED_VERSION",
    "CompiledSkill",
    "compile_skill",
]
