"""ProcedureBinding —— Skill procedure → SessionPlan/PlanGraph 结构化绑定（ADR-0219）。

ADR-0182 的 SkillProcedure 是声明式方法知识；本模块把它**派生**成对既有
计划的可核对约束投影（**绑定不执行**）：

    SkillPolicyDecision + SkillContract + MapProductPlan
      → ProcedureBinding
          ├── capabilities（skill 级：契约 capability_requirements ×
          │   计划行 —— 对全部技能可用；required 能力未进计划 =
          │   plan-time 违例）
          ├── steps（step 级：capability_refs 精确映射到 PlanGraph node /
          │   kernel PlanStep；未声明 refs 的步骤 = obligation-only 绑定，
          │   按 evidence kind / 决策结构对账）
          ├── obligations（质量义务声明投影）
          └── decisions（决策点结构 + 结构化裁决）

红线（ADR-0219 §2.6）：

- **派生不执行**：不是 SkillExecutor、不建第二 DAG；PlanGraph/SessionPlan
  仍是运行计划唯一真相；
- **稳定映射规则**：PlanGraph ``node_id == capability``、kernel step id
  规则 ``step-{cap[:48]}``（runtime._materialize_steps 同规）—— 本模块
  不发明第二套节点 id；多 step → 同 capability 允许（many-to-one）；
- **分支只能由结构化事实裁决**：绑定期以计划内容做结构裁决（选项步骤
  的 capability 在/不在计划）；LLM 文本无权选分支；
- **确定性**：零 IO、零 LLM、零时间戳 —— 同输入同指纹；
- **fail-closed for authority**：绑定只披露不授权；技能不可绑定（mode ∈
  {none, fallback, blocked} / 无技能 / 无计划）→ None，调用方干净回落
  既有 planner。

``enforce_procedure_binding``（C3）在绑定之上做 plan-time 校验 →
deterministic violations + replan hints；披露不阻断（技能对 planner 无
veto 权，user/planner-wins）。
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.services.gis_harness.skills.contract import SkillContract
from app.services.gis_harness.skills.policy import SkillPolicyDecision

#: 绑定结构版本（结构变化必须提升）。
PROCEDURE_BINDING_SCHEMA = "procedure_binding.v1"

#: skill 级 capability 绑定状态词表。
CAPABILITY_BINDING_STATUSES = (
    "bound",            # 契约能力进入计划（有行）
    "unplanned",        # 未进入计划
)

#: 步骤绑定状态词表。
STEP_BINDING_STATUSES = (
    "bound",                  # ≥1 capability（capability_refs）已映射到计划节点
    "bound_obligation_only",  # 未声明 capability_refs；按 evidence kind/决策对账
    "capability_absent",      # required 步骤声明的 capability 不在计划
    "optional_unplanned",     # optional 步骤声明的 capability 不在计划
)

#: 绑定整体状态词表。
BINDING_STATUSES = ("bound", "partial", "unbound")

#: 计划校验违例码词表（C3；closed）。
PROCEDURE_VIOLATION_CODES = (
    "required_capability_unplanned",  # skill 级 required+hard 能力未进计划
    "required_step_unplanned",        # required 步骤无计划踪迹
    "order_conflict",                 # 显式步骤依赖与计划依赖边矛盾
    "bound_capability_unresolved",    # 绑定能力的计划行未解析 tool/algorithm
)

#: 决策裁决来源词表（closed）。
DECISION_RESOLVED_BY = ("plan_content", "default", "")

_MAX_STEPS = 32
_MAX_REASONS = 8


def kernel_step_ref(capability: str) -> str:
    """capability → kernel PlanStep id（与 runtime._materialize_steps 同规）。"""
    return f"step-{capability[:48]}"


class CapabilityBinding(BaseModel):
    """skill 级能力绑定：契约 capability_requirement × 计划行。

    对全部技能可用（不依赖 step 级 capability_refs）—— skill 声明的
    required 能力是否进入计划、映射到哪个 PlanGraph node / kernel step。
    """

    model_config = ConfigDict(extra="forbid")

    capability_id: str
    criticality: str = "required"    # required | optional（契约声明）
    hard_gate: bool = True
    purpose: str = ""
    status: str = "unplanned"        # ⊆ CAPABILITY_BINDING_STATUSES
    plan_node_id: str = ""           # == capability（PlanGraph node_id 同规）
    session_step_ref: str = ""       # kernel step id（确定性规则）
    reason_codes: List[str] = Field(default_factory=list)


class ProcedureStepBinding(BaseModel):
    """一个 procedure step 对既有计划的绑定投影。"""

    model_config = ConfigDict(extra="forbid")

    step_id: str
    title: str = ""
    kind: str = ""
    required: bool = True
    skip_policy: str = "never"
    depends_on: List[str] = Field(default_factory=list)
    #: 步骤声明的 capability id（⊆ 契约 capability_requirements；库内
    #: 未声明 = 空 → obligation-only）
    capability_ids: List[str] = Field(default_factory=list)
    #: capability_ids ∩ 计划 capability 集（按计划声明序）
    plan_node_ids: List[str] = Field(default_factory=list)
    #: kernel PlanStep id（runtime 物化规则 step-{cap[:48]}，确定性）
    session_step_refs: List[str] = Field(default_factory=list)
    evidence_kinds: List[str] = Field(default_factory=list)
    #: 该步骤出现在哪些决策点的非缺省分支（conditional 上下文）
    conditional_on: List[str] = Field(default_factory=list)
    status: str = "bound"           # ⊆ STEP_BINDING_STATUSES
    reason_codes: List[str] = Field(default_factory=list)


class ProcedureObligationBinding(BaseModel):
    """质量义务绑定（声明投影；满足与否由 coverage 对照运行期事实）。"""

    model_config = ConfigDict(extra="forbid")

    obligation_id: str
    obligation_kind: str = "procedure"
    evidence_kind: str = ""
    precondition_id: str = ""
    description: str = ""


class ProcedureDecisionBinding(BaseModel):
    """决策点绑定：选项结构 + 结构化裁决结果。

    ``selected_option`` 只能由结构化事实产生：某选项的 then_step_ids 中
    存在 capability 已进计划的步骤 → 该选项被计划内容选中
    （resolved_by=plan_content）；无选项命中 → 显式走 default
    （resolved_by=default）；多选项同时命中 → 不裁决（resolved_by=""，
    honest ambiguous）。LLM 文本不得直接选分支。
    """

    model_config = ConfigDict(extra="forbid")

    node_id: str
    question: str = ""
    default_option_id: str = ""
    options: List[Dict[str, Any]] = Field(default_factory=list)
    selected_option: str = ""
    resolved_by: str = ""           # ⊆ DECISION_RESOLVED_BY
    disclosure: str = ""


class ProcedureBinding(BaseModel):
    """一次 plan seam 的完整绑定（纯派生、确定性、无时间戳）。"""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = PROCEDURE_BINDING_SCHEMA
    skill_id: str
    skill_version: str = ""
    skill_fingerprint: str = ""
    mode: str = ""                  # 绑定发生时的 SkillPolicy mode
    trust_tier: str = ""
    plan_id: str = ""
    plan_fingerprint: str = ""      # MapProductPlan.manifest_fingerprint
    #: skill 级能力绑定（对全部技能可用）
    capabilities: List[CapabilityBinding] = Field(default_factory=list)
    #: step 级绑定（capability_refs 精确映射 / obligation-only）
    steps: List[ProcedureStepBinding] = Field(default_factory=list)
    obligations: List[ProcedureObligationBinding] = Field(default_factory=list)
    decisions: List[ProcedureDecisionBinding] = Field(default_factory=list)
    completion_evidence: List[str] = Field(default_factory=list)
    #: 统计语义快照（S10 分母义务：coverage 据此做分母专项对账，无需
    #: 运行期再取技能资产）
    denominator_required: bool = False
    #: 技能级回退声明（trigger:action 稳定码）
    fallback_policy: List[str] = Field(default_factory=list)
    binding_status: str = "bound"   # ⊆ BINDING_STATUSES
    reason_codes: List[str] = Field(default_factory=list)

    def fingerprint(self) -> str:
        """绑定内容指纹（canonical JSON SHA256[:16]；确定性）。"""
        payload = self.model_dump(mode="json", exclude={"schema_version"})
        canonical = json.dumps(
            payload, ensure_ascii=False, sort_keys=True,
            separators=(",", ":"), default=str,
        )
        return hashlib.sha256(
            canonical.encode("utf-8"), usedforsecurity=False,
        ).hexdigest()[:16]

    def step(self, step_id: str) -> Optional[ProcedureStepBinding]:
        for s in self.steps:
            if s.step_id == step_id:
                return s
        return None

    def to_bounded_dict(self) -> Dict[str, Any]:
        """持久化/传输投影（id 是 join key 不截断语义；自由文本有界）。"""
        return {
            "schema_version": self.schema_version,
            "skill_id": self.skill_id[:64],
            "skill_version": self.skill_version[:16],
            "skill_fingerprint": self.skill_fingerprint[:16],
            "mode": self.mode[:24],
            "trust_tier": self.trust_tier[:16],
            "plan_id": self.plan_id[:64],
            "plan_fingerprint": self.plan_fingerprint[:64],
            "capabilities": [
                {
                    "capability_id": c.capability_id[:64],
                    "criticality": c.criticality[:16],
                    "hard_gate": c.hard_gate,
                    "purpose": c.purpose[:120],
                    "status": c.status[:16],
                    "plan_node_id": c.plan_node_id[:64],
                    "session_step_ref": c.session_step_ref[:64],
                    "reason_codes": [r[:48] for r in c.reason_codes[:4]],
                }
                for c in self.capabilities[:16]
            ],
            "steps": [
                {
                    "step_id": s.step_id[:64],
                    "title": s.title[:120],
                    "kind": s.kind[:16],
                    "required": s.required,
                    "skip_policy": s.skip_policy[:24],
                    "depends_on": s.depends_on[:8],
                    "capability_ids": s.capability_ids[:8],
                    "plan_node_ids": s.plan_node_ids[:8],
                    "session_step_refs": s.session_step_refs[:8],
                    "evidence_kinds": s.evidence_kinds[:8],
                    "conditional_on": s.conditional_on[:4],
                    "status": s.status[:32],
                    "reason_codes": [r[:48] for r in s.reason_codes[:4]],
                }
                for s in self.steps[:_MAX_STEPS]
            ],
            "obligations": [
                {
                    "obligation_id": o.obligation_id[:64],
                    "obligation_kind": o.obligation_kind[:16],
                    "evidence_kind": o.evidence_kind[:48],
                    "precondition_id": o.precondition_id[:64],
                    "description": o.description[:160],
                }
                for o in self.obligations[:16]
            ],
            "decisions": [
                {
                    "node_id": d.node_id[:64],
                    "question": d.question[:160],
                    "default_option_id": d.default_option_id[:64],
                    "options": [
                        {
                            "option_id": str(o.get("option_id", ""))[:64],
                            "then_step_ids": [
                                str(x)[:64]
                                for x in (o.get("then_step_ids") or [])[:8]
                            ],
                        }
                        for o in d.options[:8]
                    ],
                    "selected_option": d.selected_option[:64],
                    "resolved_by": d.resolved_by[:16],
                    "disclosure": d.disclosure[:160],
                }
                for d in self.decisions[:16]
            ],
            "completion_evidence": self.completion_evidence[:16],
            "denominator_required": self.denominator_required,
            "fallback_policy": [f[:80] for f in self.fallback_policy[:8]],
            "binding_status": self.binding_status[:16],
            "reason_codes": [r[:48] for r in self.reason_codes[:_MAX_REASONS]],
        }


def _plan_rows(plan: Any) -> tuple[List[str], Dict[str, Dict[str, Any]]]:
    """(计划 capability 声明序, capability → 行 dict)。"""
    rows: List[Any] = []
    if isinstance(plan, dict):
        rows = list(plan.get("data_requirements") or []) + list(
            plan.get("analysis_steps") or [])
    else:
        for attr in ("data_requirements", "analysis_steps"):
            for row in list(getattr(plan, attr, None) or []):
                rows.append(
                    row if isinstance(row, dict) else
                    (row.model_dump() if hasattr(row, "model_dump") else {}))
    ordered: List[str] = []
    by_cap: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        cap = str(row.get("capability") or "")
        if not cap:
            continue
        if cap not in by_cap:
            ordered.append(cap)
            by_cap[cap] = row
    return ordered, by_cap


def bind_procedure_to_plan(
    skill: Optional[SkillContract],
    decision: SkillPolicyDecision,
    plan: Any,
    *,
    capability_exists=None,
) -> Optional[ProcedureBinding]:
    """技能契约 × 策略裁决 × MapProductPlan → ProcedureBinding（纯函数）。

    返回 None = 不可绑定（mode ∈ {none, fallback, blocked, shadow} 或无
    技能/无计划）—— 调用方**必须**干净回落既有 planner 并披露原因
    （decision.fallback_reason）。``guide``/``execute_guided`` 均产出绑定
    （enforcement 的 advisory 门由 mode 区分）。
    """
    if skill is None or decision.mode not in ("guide", "execute_guided"):
        return None
    if plan is None:
        return None

    plan_caps, plan_rows = _plan_rows(plan)
    plan_cap_index = {cap: i for i, cap in enumerate(plan_caps)}

    # 硬能力缺口（registry 谓词）：绑定仍可产出（披露 partial），但不
    # 虚报已映射 —— guides_planning 已在 planning_projection 层裁决。
    missing_hard: List[str] = []
    try:
        if capability_exists is not None:
            from app.services.gis_harness.skills.bridges import (
                missing_capabilities,
            )
            missing_hard = missing_capabilities(
                skill, capability_exists=capability_exists)
    except Exception:  # noqa: BLE001 — 缺口面缺席按无信号处理
        missing_hard = []

    # ── skill 级能力绑定（对全部技能可用）────────────────────────────
    capabilities: List[CapabilityBinding] = []
    for req in skill.capability_requirements[:16]:
        in_plan = req.capability_id in plan_cap_index
        reasons: List[str] = []
        if req.capability_id in missing_hard:
            reasons.append("hard_capability_missing")
        if not in_plan and req.criticality == "required":
            reasons.append("required_not_in_plan")
        capabilities.append(CapabilityBinding(
            capability_id=req.capability_id,
            criticality=req.criticality,
            hard_gate=req.hard_gate,
            purpose=req.purpose,
            status="bound" if in_plan else "unplanned",
            plan_node_id=req.capability_id if in_plan else "",
            session_step_ref=kernel_step_ref(req.capability_id) if in_plan else "",
            reason_codes=reasons[:4],
        ))

    # ── step 级绑定（capability_refs 精确映射 / obligation-only）──────
    steps: List[ProcedureStepBinding] = []
    for step in skill.procedure.steps[:_MAX_STEPS]:
        caps = list(dict.fromkeys(step.capability_refs))
        plan_nodes = sorted(
            (c for c in caps if c in plan_cap_index),
            key=lambda c: plan_cap_index[c],
        )
        reasons: List[str] = []
        if caps and len(plan_nodes) < len(caps):
            reasons.append("partial_capability_mapping")
        if any(c in missing_hard for c in caps):
            reasons.append("hard_capability_missing")
        if caps:
            if plan_nodes:
                status = "bound"
            elif step.required:
                status = "capability_absent"
                reasons.append("capability_not_in_plan")
            else:
                status = "optional_unplanned"
        else:
            status = "bound_obligation_only"
        steps.append(ProcedureStepBinding(
            step_id=step.step_id,
            title=step.title,
            kind=step.kind,
            required=step.required,
            skip_policy=step.skip_policy,
            depends_on=list(step.depends_on)[:8],
            capability_ids=caps[:8],
            plan_node_ids=plan_nodes[:8],
            session_step_refs=[
                kernel_step_ref(c) for c in plan_nodes[:8]],
            evidence_kinds=[
                ev.evidence_kind for ev in step.evidence_requirements
                if ev.evidence_kind][:8],
            conditional_on=[],  # 决策上下文在下方统一回填
            status=status,
            reason_codes=reasons[:4],
        ))

    # ── 决策结构绑定 + 结构化裁决（计划内容 = 结构化 Situation 事实）──
    by_step = {s.step_id: s for s in steps}
    decisions: List[ProcedureDecisionBinding] = []
    for dec in skill.procedure.decisions[:16]:
        options = [
            {
                "option_id": opt.option_id,
                "then_step_ids": list(opt.then_step_ids)[:8],
            }
            for opt in dec.options[:8]
        ]
        default_id = dec.default_option_id
        hits: List[str] = []
        for opt in dec.options:
            if not opt.then_step_ids:
                continue
            planned = any(
                (by_step.get(sid) is not None
                 and by_step[sid].plan_node_ids)
                for sid in opt.then_step_ids
            )
            if planned and opt.option_id not in hits:
                hits.append(opt.option_id)
        selected, resolved_by, disclosure = "", "", ""
        if len(hits) == 1:
            selected, resolved_by = hits[0], "plan_content"
        elif not hits:
            selected, resolved_by = default_id, "default"
            disclosure = (
                f"decision={dec.node_id}: no option structurally hit; "
                f"default={default_id or 'none'}"
            )[:160]
        # len(hits) > 1 → ambiguous：不裁决（honest unknown）
        decisions.append(ProcedureDecisionBinding(
            node_id=dec.node_id,
            question=dec.question,
            default_option_id=default_id,
            options=options,
            selected_option=selected,
            resolved_by=resolved_by,
            disclosure=disclosure,
        ))

    # 条件上下文回填：步骤只出现在非缺省分支 → conditional_on 该决策点。
    for dec in skill.procedure.decisions[:16]:
        default_id = dec.default_option_id
        for opt in dec.options:
            if opt.option_id == default_id:
                continue
            for sid in opt.then_step_ids:
                sb = by_step.get(sid)
                if sb is not None and dec.node_id not in sb.conditional_on:
                    sb.conditional_on.append(dec.node_id)
                    sb.conditional_on = sb.conditional_on[:4]

    required_absent = [
        s.step_id for s in steps
        if s.required and s.status == "capability_absent"]
    required_caps_unplanned = [
        c.capability_id for c in capabilities
        if c.criticality == "required" and c.status == "unplanned"]
    binding_status = (
        "partial" if (required_absent or required_caps_unplanned) else "bound")
    reason_codes: List[str] = [f"mode:{decision.mode}"]
    if missing_hard:
        reason_codes.append(
            "missing_hard_capabilities:" + ",".join(missing_hard[:4])[:64])
    if required_absent:
        reason_codes.append(
            "required_absent:" + ",".join(required_absent[:4])[:64])
    if required_caps_unplanned:
        reason_codes.append(
            "required_capabilities_unplanned:"
            + ",".join(required_caps_unplanned[:4])[:64])

    fallback_policy: List[str] = []
    for fb in skill.procedure.fallbacks[:8]:
        fallback_policy.append(
            f"{fb.trigger}:{fb.action}"
            + (f":{fb.fallback_skill_id}" if fb.fallback_skill_id else ""))

    return ProcedureBinding(
        skill_id=skill.id,
        skill_version=skill.version,
        skill_fingerprint=skill.fingerprint()[:16],
        mode=decision.mode,
        trust_tier=decision.trust_tier,
        plan_id=str(plan.get("plan_id") or "") if isinstance(plan, dict)
        else str(getattr(plan, "plan_id", "") or ""),
        plan_fingerprint=(
            str(plan.get("manifest_fingerprint") or "")
            if isinstance(plan, dict)
            else str(getattr(plan, "manifest_fingerprint", "") or "")),
        capabilities=capabilities,
        steps=steps,
        obligations=[
            ProcedureObligationBinding(
                obligation_id=obl.obligation_id,
                obligation_kind=obl.obligation_kind,
                evidence_kind=obl.evidence_kind,
                precondition_id=obl.precondition_id,
                description=obl.description,
            )
            for obl in skill.quality_obligations[:16]
        ],
        decisions=decisions,
        completion_evidence=list(skill.completion_evidence)[:16],
        denominator_required=bool(
            skill.statistical_semantics
            and skill.statistical_semantics.denominator_required),
        fallback_policy=fallback_policy[:8],
        binding_status=binding_status,
        reason_codes=reason_codes[:_MAX_REASONS],
    )


# ── C3: plan-time enforcement ────────────────────────────────────────────────


class ProcedureViolation(BaseModel):
    """一条确定性计划违例（含可执行的重规划提示）。"""

    code: str                        # ⊆ PROCEDURE_VIOLATION_CODES
    subject: str = ""                # step_id 或 capability_id
    detail: str = ""
    hint: str = ""

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code[:48],
            "subject": self.subject[:64],
            "detail": self.detail[:160],
            "hint": self.hint[:160],
        }


class ProcedurePlanCheck(BaseModel):
    """绑定 × 计划 的确定性校验结果（披露不阻断）。"""

    skill_id: str
    binding_fingerprint: str = ""
    mode: str = ""
    advisory: bool = False           # guide 模式：提示不作为执行门
    violations: List[ProcedureViolation] = Field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.violations

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "skill_id": self.skill_id[:64],
            "binding_fingerprint": self.binding_fingerprint[:16],
            "mode": self.mode[:24],
            "advisory": self.advisory,
            "ok": self.ok,
            "violations": [v.to_bounded_dict() for v in self.violations[:8]],
        }


def _plan_dependency_edges(plan: Any) -> Dict[str, List[str]]:
    """计划 capability 依赖邻接表（复用 plan_graph 推断；失败 → 空）。"""
    try:
        from app.services.gis_harness.plan_graph import build_plan_graph

        graph = build_plan_graph(plan, evaluate=False)
        return {n.capability: list(n.depends_on) for n in graph.nodes}
    except Exception:  # noqa: BLE001 — 校验面不因图推断失败而误报
        return {}


def _reachable(adj: Dict[str, List[str]], src: str, dst: str) -> bool:
    stack, seen = [src], set()
    while stack:
        cur = stack.pop()
        if cur == dst:
            return True
        if cur in seen:
            continue
        seen.add(cur)
        stack.extend(adj.get(cur, []))
    return False


def enforce_procedure_binding(
    binding: ProcedureBinding,
    plan: Any,
) -> ProcedurePlanCheck:
    """C3：绑定 × 计划 → deterministic violations + replan hints。

    只读披露：违例不阻断规划（技能对 planner 无 veto 权）；execute_guided
    下调用方应把违例透出为 ⚠ guidance / procedure_check 面，使计划缺口不再
    "静默看起来完整"。顺序冲突检测复用 PlanGraph 依赖边（不建第二图）。
    """
    check = ProcedurePlanCheck(
        skill_id=binding.skill_id,
        binding_fingerprint=binding.fingerprint(),
        mode=binding.mode,
        advisory=binding.mode != "execute_guided",
    )
    _, plan_rows = _plan_rows(plan)
    adj = _plan_dependency_edges(plan)

    # 1) skill 级：required 能力未进计划（对全部技能可用）。以**当前**
    #    计划行为准（绑定快照与当前计划可能已分叉 —— enforcement 是
    #    binding × plan 的确定性重算，不信任绑定时的行在场快照）。
    for cap in binding.capabilities:
        if cap.criticality == "required" and (
                cap.plan_node_id not in plan_rows):
            check.violations.append(ProcedureViolation(
                code="required_capability_unplanned",
                subject=cap.capability_id,
                detail=(
                    f"技能 required 能力 {cap.capability_id}"
                    f"（{cap.purpose[:80]}）未进入计划"),
                hint=(
                    f"将 {cap.capability_id} 纳入计划（或按技能 fallback "
                    "声明披露降级路径）"),
            ))

    for step in binding.steps:
        # 2) required 步骤无计划踪迹（capability_refs 已声明但 capability
        #    不在计划；条件步骤走缺省分支不算违例 —— skipped_declared
        #    语义，coverage 对账）。
        if step.required and step.status == "capability_absent":
            default_covered = any(
                d.node_id in step.conditional_on
                and d.resolved_by == "default"
                for d in binding.decisions)
            if not default_covered:
                check.violations.append(ProcedureViolation(
                    code="required_step_unplanned",
                    subject=step.step_id,
                    detail=(
                        f"required step（skip_policy={step.skip_policy}）的"
                        f" capability {','.join(step.capability_ids[:3])}"
                        " 未进入计划"),
                    hint=(
                        "补入 capability "
                        + ",".join(step.capability_ids[:3])
                        + "，或按技能声明 fallback 披露跳过原因"),
                ))
        # 3) 显式步骤依赖 vs 计划依赖边矛盾（s1 depends_on s2 = procedure
        #    序 s2 → s1，即 cap(s2) 应先于 cap(s1) 执行）。冲突 = 计划里
        #    cap(s2)（应先执行者）反而（传递）依赖 cap(s1)。
        for dep_id in step.depends_on:
            dep = binding.step(dep_id)
            if dep is None:
                continue
            for cap_later in step.plan_node_ids:
                for cap_earlier in dep.plan_node_ids:
                    if cap_earlier != cap_later and _reachable(
                            adj, cap_earlier, cap_later):
                        check.violations.append(ProcedureViolation(
                            code="order_conflict",
                            subject=step.step_id,
                            detail=(
                                f"step {step.step_id} depends_on {dep_id}"
                                f"（procedure 序 {dep_id} → {step.step_id}），"
                                f"但计划依赖边 {cap_earlier} → {cap_later}"
                                " 使应先执行者反居下游"),
                            hint=(
                                f"按方法序先执行 {cap_earlier} 再 {cap_later}"
                                "（或修订步骤依赖声明）"),
                        ))
        # 4) required 步骤绑定的能力行未解析出 tool/algorithm（计划内有
        #    行但无执行面 —— 方法步骤会落空）。
        if step.required and step.plan_node_ids:
            unresolved = [
                cap for cap in step.plan_node_ids
                if not str(plan_rows.get(cap, {}).get("resolved_tool") or "")
                and not str(
                    plan_rows.get(cap, {}).get("resolved_algorithm") or "")
            ]
            if unresolved:
                check.violations.append(ProcedureViolation(
                    code="bound_capability_unresolved",
                    subject=step.step_id,
                    detail="绑定能力的计划行无 resolved_tool/algorithm: "
                           + ",".join(unresolved[:3]),
                    hint="为 " + ",".join(unresolved[:3])
                         + " 解析工具/算法，或按 fallback 换路径",
                ))
    return check


__all__ = [
    "PROCEDURE_BINDING_SCHEMA",
    "PROCEDURE_VIOLATION_CODES",
    "CAPABILITY_BINDING_STATUSES",
    "STEP_BINDING_STATUSES",
    "BINDING_STATUSES",
    "DECISION_RESOLVED_BY",
    "CapabilityBinding",
    "ProcedureStepBinding",
    "ProcedureObligationBinding",
    "ProcedureDecisionBinding",
    "ProcedureBinding",
    "ProcedureViolation",
    "ProcedurePlanCheck",
    "bind_procedure_to_plan",
    "enforce_procedure_binding",
    "kernel_step_ref",
]
