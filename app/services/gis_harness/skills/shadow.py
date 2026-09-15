"""Induced skill shadow evaluation —— 旁路对照，零写生产状态。

生产规划照常；候选 induced 技能只产出 ShadowEvaluationReport，
不得 mutate SessionPlan / WorldState / 技能库。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from app.services.gis_harness.skills.contract import SkillContract
from app.services.gis_harness.skills.planning_projection import (
    SkillPlanningProjection,
    project_skill_for_planning,
)
from app.services.gis_harness.skills.policy import (
    SkillPolicyDecision,
    trust_tier_for,
)
from app.services.gis_harness.skills.resolver import SkillResolver
from app.services.gis_harness.skills.situation import SelectionFacts


class ShadowEvaluationReport(BaseModel):
    """影子评估报告（只读对照）。"""

    induced_skill_id: str = ""
    induced_skill_version: str = ""
    trust_tier: str = "experimental"
    eligible: bool = False
    eligibility_codes: List[str] = Field(default_factory=list)
    procedure_topology: List[str] = Field(default_factory=list)
    capability_choice: List[str] = Field(default_factory=list)
    expected_cost_tier: int = 1
    goal_requirements: List[str] = Field(default_factory=list)
    methodology_obligations: List[str] = Field(default_factory=list)
    production_skill_id: str = ""
    production_mode: str = "none"
    topology_overlap: float = 0.0
    capability_overlap: float = 0.0
    divergences: List[str] = Field(default_factory=list)
    mutates_production: bool = False  # 恒 False：契约钉死

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "induced_skill_id": self.induced_skill_id[:64],
            "induced_skill_version": self.induced_skill_version[:16],
            "trust_tier": self.trust_tier,
            "eligible": self.eligible,
            "eligibility_codes": [c[:48] for c in self.eligibility_codes[:8]],
            "procedure_topology": self.procedure_topology[:24],
            "capability_choice": self.capability_choice[:16],
            "expected_cost_tier": self.expected_cost_tier,
            "goal_requirements": [g[:48] for g in self.goal_requirements[:8]],
            "methodology_obligations": [
                m[:64] for m in self.methodology_obligations[:8]
            ],
            "production_skill_id": self.production_skill_id[:64],
            "production_mode": self.production_mode,
            "topology_overlap": round(self.topology_overlap, 3),
            "capability_overlap": round(self.capability_overlap, 3),
            "divergences": [d[:80] for d in self.divergences[:8]],
            "mutates_production": False,
        }


def _overlap(a: List[str], b: List[str]) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    sa, sb = set(a), set(b)
    return round(len(sa & sb) / len(sa | sb), 4)


def _cost_tier(step_count: int, required_caps: int) -> int:
    score = step_count + required_caps
    if score <= 4:
        return 1
    if score <= 8:
        return 2
    return 3


def evaluate_shadow(
    *,
    facts: SelectionFacts,
    production_decision: SkillPolicyDecision,
    production_projection: Optional[SkillPlanningProjection],
    induced_skill: SkillContract,
    shadow_resolver: Optional[SkillResolver] = None,
) -> ShadowEvaluationReport:
    """对照生产决策与 induced 投影；永不修改传入对象之外的全局状态。"""
    if induced_skill.pack != "induced":
        return ShadowEvaluationReport(
            induced_skill_id=induced_skill.id,
            induced_skill_version=induced_skill.version,
            trust_tier=trust_tier_for(induced_skill),
            eligible=False,
            eligibility_codes=["NOT_INDUCED_PACK"],
            production_skill_id=production_decision.selected_skill or "",
            production_mode=production_decision.mode,
            divergences=["shadow_only_for_induced"],
            mutates_production=False,
        )

    eligibility_codes: List[str] = []
    eligible = True
    if shadow_resolver is not None:
        rej = shadow_resolver.check_eligibility(induced_skill, facts)
        if rej is not None:
            eligible = False
            eligibility_codes = list(rej.reason_codes)

    # 用 shadow 决策壳驱动投影（mode=shadow → guides_planning 仍 False）
    SkillPolicyDecision(
        mode="shadow",
        selected_skill=induced_skill.id,
        skill_version=induced_skill.version,
        trust_tier="experimental",
        confidence=0.0,
        eligibility_ok=eligible,
        situation_signature=production_decision.situation_signature,
        reasons=["shadow_evaluation"],
    )
    # project_skill_for_planning 对 shadow 模式：我们临时用 guide 形态提取结构，
    # 但不把 guides_planning 打开——下面强制 False。
    structural = SkillPolicyDecision(
        mode="guide",
        selected_skill=induced_skill.id,
        skill_version=induced_skill.version,
        trust_tier="experimental",
        confidence=1.0,
        eligibility_ok=eligible,
        situation_signature=production_decision.situation_signature,
        reasons=["shadow_structure_only"],
    )
    proj = project_skill_for_planning(induced_skill, structural)
    proj.guides_planning = False
    proj.mode = "shadow"

    prod_steps = list(production_projection.step_ordering) if production_projection else []
    prod_caps = list(production_projection.required_capabilities) if production_projection else []
    topo = list(proj.step_ordering)
    caps = list(proj.required_capabilities)

    divergences: List[str] = []
    if not eligible:
        divergences.append("induced_ineligible")
    if production_decision.selected_skill and production_decision.selected_skill != induced_skill.id:
        divergences.append("different_from_production_skill")
    if prod_caps and set(caps) != set(prod_caps):
        divergences.append("capability_set_differs")
    if prod_steps and topo != prod_steps:
        divergences.append("procedure_topology_differs")

    return ShadowEvaluationReport(
        induced_skill_id=induced_skill.id,
        induced_skill_version=induced_skill.version,
        trust_tier="experimental",
        eligible=eligible,
        eligibility_codes=eligibility_codes,
        procedure_topology=topo[:24],
        capability_choice=caps[:16],
        expected_cost_tier=_cost_tier(len(topo), len(caps)),
        goal_requirements=list(proj.evidence_requirements)[:8],
        methodology_obligations=list(proj.methodology_obligations)[:8],
        production_skill_id=production_decision.selected_skill or "",
        production_mode=production_decision.mode,
        topology_overlap=_overlap(topo, prod_steps),
        capability_overlap=_overlap(caps, prod_caps),
        divergences=divergences,
        mutates_production=False,
    )


def run_shadow_if_present(
    *,
    facts: SelectionFacts,
    decision: SkillPolicyDecision,
    production_projection: Optional[SkillPlanningProjection],
    shadow_resolver: Optional[SkillResolver],
) -> Optional[ShadowEvaluationReport]:
    """若决策携带 shadow_candidate 则评估；否则 None。"""
    if not decision.shadow_candidate or shadow_resolver is None:
        return None
    skill = shadow_resolver.get(decision.shadow_candidate)
    if skill is None:
        return None
    return evaluate_shadow(
        facts=facts,
        production_decision=decision,
        production_projection=production_projection,
        induced_skill=skill,
        shadow_resolver=shadow_resolver,
    )


__all__ = [
    "ShadowEvaluationReport",
    "evaluate_shadow",
    "run_shadow_if_present",
]
