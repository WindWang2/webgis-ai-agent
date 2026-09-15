"""Production hot-path seam —— Situation/Intent → SkillPolicy → planning input.

唯一权威集成点：调用方（tools / planner 适配）只应经过 ``resolve_skill_guidance``，
禁止在代码库各处散落 SkillResolver 生产裁决。
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from pydantic import BaseModel, Field

from app.services.gis_harness.skills.planning_projection import (
    SkillPlanningProjection,
    project_skill_for_planning,
)
from app.services.gis_harness.skills.policy import (
    SkillPolicy,
    SkillPolicyDecision,
    resolve_for_planning,
)
from app.services.gis_harness.skills.resolver import SkillResolver
from app.services.gis_harness.skills.shadow import (
    ShadowEvaluationReport,
    run_shadow_if_present,
)
from app.services.gis_harness.skills.situation import SelectionFacts


class SkillGuidanceBundle(BaseModel):
    """热路径一次解析的完整有界包。"""

    decision: SkillPolicyDecision
    projection: SkillPlanningProjection
    shadow: Optional[ShadowEvaluationReport] = None

    @property
    def guides_planning(self) -> bool:
        return self.projection.guides_planning

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "decision": self.decision.to_bounded_dict(),
            "projection": self.projection.to_bounded_dict(),
            "shadow": self.shadow.to_bounded_dict() if self.shadow else None,
            "guides_planning": self.guides_planning,
            "pi_context": self.decision.pi_context_card(),
        }


def resolve_skill_guidance(
    facts: SelectionFacts,
    *,
    library=None,
    shadow_resolver: Optional[SkillResolver] = None,
    prefer_execute: bool = True,
    allow_shadow: bool = True,
    record_evidence: bool = True,
) -> SkillGuidanceBundle:
    """权威缝：事实 → 策略裁决 → 规划投影 →（可选）影子评估。

    无合适技能时 ``guides_planning=False``，调用方必须走既有 harness planning。
    """
    if library is None:
        from app.services.gis_harness.skills.loader import get_skill_library
        library = get_skill_library()

    decision = resolve_for_planning(
        facts,
        library=library,
        shadow_resolver=shadow_resolver,
        prefer_execute=prefer_execute,
        allow_shadow=allow_shadow,
    )

    skill = None
    if decision.selected_skill:
        skill = library.get(decision.selected_skill)

    # 硬能力缺口：用 library resolver 的谓词（若有）
    capability_exists = getattr(library.resolver, "_capability_exists", None)
    projection = project_skill_for_planning(
        skill, decision, capability_exists=capability_exists,
    )

    shadow = run_shadow_if_present(
        facts=facts,
        decision=decision,
        production_projection=projection if projection.guides_planning else None,
        shadow_resolver=shadow_resolver,
    )

    if record_evidence:
        _record(decision, shadow)

    return SkillGuidanceBundle(decision=decision, projection=projection, shadow=shadow)


def _record(
    decision: SkillPolicyDecision,
    shadow: Optional[ShadowEvaluationReport],
) -> None:
    try:
        from app.tools.skill_library_tools import get_skill_evidence_recorder

        rec = get_skill_evidence_recorder()
        rec.record_policy(
            skill_id=decision.selected_skill or "",
            skill_version=decision.skill_version,
            mode=decision.mode,
            trust_tier=decision.trust_tier,
            confidence=decision.confidence,
            reasons=decision.reasons,
        )
        if decision.selected_skill and decision.mode in (
            "guide", "execute_guided",
        ):
            rec.record_selection(
                decision.selected_skill,
                decision.skill_version,
                decision.reasons + decision.matched_signals,
                decision.confidence,
            )
        if decision.mode in ("fallback", "blocked"):
            rec.record_fallback(
                decision.selected_skill or "",
                decision.mode,
                "existing_planner",
                decision.fallback_reason[:200],
            )
        if shadow is not None:
            rec.record_shadow(
                skill_id=shadow.induced_skill_id,
                skill_version=shadow.induced_skill_version,
                notes=f"overlap_cap={shadow.capability_overlap};div={','.join(shadow.divergences[:3])}",
            )
    except Exception:  # noqa: BLE001 - 证据失败不得阻断热路径
        pass


def attach_skill_guidance_to_plan_inputs(
    plan_inputs: Dict[str, Any],
    bundle: SkillGuidanceBundle,
) -> Dict[str, Any]:
    """把技能投影**附加**到既有规划输入字典（不替换权威字段）。

    技能不能绕过 security/tool/resource：只添加 ``skill_guidance`` 键。
    """
    out = dict(plan_inputs)
    out["skill_guidance"] = bundle.to_bounded_dict()
    if bundle.guides_planning:
        proj = bundle.projection
        # 提示性合并：仅当调用方尚未声明 required_capabilities 时填充建议
        if not out.get("required_capabilities") and proj.required_capabilities:
            out["suggested_capabilities"] = list(proj.required_capabilities)
        out["skill_step_ordering"] = list(proj.step_ordering)
        out["skill_evidence_requirements"] = list(proj.evidence_requirements)
    return out


__all__ = [
    "SkillGuidanceBundle",
    "attach_skill_guidance_to_plan_inputs",
    "resolve_skill_guidance",
]
