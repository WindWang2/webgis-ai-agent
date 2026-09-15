"""SkillPlanningProjection —— SkillProcedure → 既有规划输入的有界投影。

不新建 DAG：投影供 SessionPlan / planner / ExecutionGraph 消费。
大正文禁止进入 Pi context（渐进披露）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from app.services.gis_harness.skills.bridges import (
    missing_capabilities,
    project_capability_plan_inputs,
    project_product_requirements,
)
from app.services.gis_harness.skills.contract import SkillContract
from app.services.gis_harness.skills.policy import SkillPolicyDecision


class SkillPlanningProjection(BaseModel):
    """有界规划投影（技能描述 HOW；不授予 authority）。"""

    skill_id: str = ""
    skill_version: str = ""
    mode: str = "none"
    trust_tier: str = "core"
    required_capabilities: List[str] = Field(default_factory=list)
    optional_capabilities: List[str] = Field(default_factory=list)
    step_ordering: List[str] = Field(default_factory=list)
    preconditions: List[str] = Field(default_factory=list)
    quality_obligations: List[str] = Field(default_factory=list)
    methodology_obligations: List[str] = Field(default_factory=list)
    evidence_requirements: List[str] = Field(default_factory=list)
    fallback_hints: List[str] = Field(default_factory=list)
    input_roles: List[str] = Field(default_factory=list)
    output_roles: List[str] = Field(default_factory=list)
    product_requirements: List[str] = Field(default_factory=list)
    missing_hard_capabilities: List[str] = Field(default_factory=list)
    guides_planning: bool = False

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "skill_id": self.skill_id[:64],
            "skill_version": self.skill_version[:16],
            "mode": self.mode,
            "trust_tier": self.trust_tier,
            "required_capabilities": self.required_capabilities[:16],
            "optional_capabilities": self.optional_capabilities[:12],
            "step_ordering": self.step_ordering[:24],
            "preconditions": [p[:64] for p in self.preconditions[:12]],
            "quality_obligations": [q[:64] for q in self.quality_obligations[:12]],
            "methodology_obligations": [
                m[:64] for m in self.methodology_obligations[:12]
            ],
            "evidence_requirements": self.evidence_requirements[:12],
            "fallback_hints": [h[:80] for h in self.fallback_hints[:8]],
            "input_roles": self.input_roles[:8],
            "output_roles": self.output_roles[:8],
            "product_requirements": self.product_requirements[:8],
            "missing_hard_capabilities": self.missing_hard_capabilities[:8],
            "guides_planning": self.guides_planning,
        }


def project_skill_for_planning(
    skill: Optional[SkillContract],
    decision: SkillPolicyDecision,
    *,
    capability_exists=None,
) -> SkillPlanningProjection:
    """决策 + 契约 → 规划投影。

    ``mode in {none, fallback, blocked}`` 或无 skill → 空投影（``guides_planning=False``），
    保证调用方干净回落既有 harness planning。
    """
    if skill is None or decision.mode in ("none", "fallback", "blocked"):
        return SkillPlanningProjection(
            mode=decision.mode,
            trust_tier=decision.trust_tier,
            guides_planning=False,
            fallback_hints=[decision.fallback_reason] if decision.fallback_reason else [],
        )

    cap = project_capability_plan_inputs(skill)
    product = project_product_requirements(skill)
    missing = missing_capabilities(skill, capability_exists=capability_exists)

    quality: List[str] = []
    methodology: List[str] = []
    for obl in skill.quality_obligations:
        tag = f"{obl.obligation_id}:{obl.obligation_kind}"
        if obl.obligation_kind == "precondition" or obl.precondition_id:
            methodology.append(
                f"{obl.precondition_id or obl.obligation_id}:{obl.evidence_kind}"
            )
        else:
            quality.append(tag)

    preconditions = [
        s.precondition for s in skill.procedure.steps
        if getattr(s, "precondition", "")
    ]
    # procedure-level named requirements if present
    for req in getattr(skill.procedure, "requirements", []) or []:
        rid = getattr(req, "requirement_id", None) or str(req)
        preconditions.append(str(rid)[:64])

    fallback_hints: List[str] = []
    for fb in skill.fallback_policy:
        fallback_hints.append(f"{fb.trigger}:{fb.downgrade_class}")
    for fb in skill.procedure.fallbacks:
        action = getattr(fb, "action", "")
        trigger = getattr(fb, "trigger", "")
        alt = getattr(fb, "fallback_skill_id", "") or ""
        fallback_hints.append(f"{trigger}:{action}:{alt}"[:80])

    guides = decision.mode in ("guide", "execute_guided") and not missing
    # 硬能力缺口 → 不引导规划（回落），但不授予绕过权
    if missing:
        guides = False

    return SkillPlanningProjection(
        skill_id=skill.id,
        skill_version=skill.version,
        mode=decision.mode if guides or decision.mode == "shadow" else "fallback",
        trust_tier=decision.trust_tier,
        required_capabilities=list(cap["required_capabilities"]),
        optional_capabilities=list(cap["optional_capabilities"]),
        step_ordering=list(cap["procedure_step_ids"]),
        preconditions=preconditions[:16],
        quality_obligations=quality[:16],
        methodology_obligations=methodology[:16],
        evidence_requirements=list(skill.completion_evidence)[:16],
        fallback_hints=fallback_hints[:8],
        input_roles=list(skill.input_roles),
        output_roles=list(skill.output_roles),
        product_requirements=list(product["component_expectations"])[:8],
        missing_hard_capabilities=list(missing),
        guides_planning=guides,
    )


def enrich_plan_candidate_scores(
    base_scores: Dict[str, float],
    projection: SkillPlanningProjection,
) -> Dict[str, float]:
    """可选：当技能引导规划时，对既有候选分数做**有界**加性提示。

    不改变可行集；仅轻微抬升 semantic_fit / output_quality（≤0.05），
    且不引入非确定性。技能不授予工具可用性。
    """
    out = dict(base_scores)
    if not projection.guides_planning:
        return out
    if "semantic_fit" in out:
        out["semantic_fit"] = min(1.0, round(out["semantic_fit"] + 0.05, 3))
    if "output_quality" in out and projection.product_requirements:
        out["output_quality"] = min(1.0, round(out["output_quality"] + 0.03, 3))
    return out


__all__ = [
    "SkillPlanningProjection",
    "project_skill_for_planning",
    "enrich_plan_candidate_scores",
]
