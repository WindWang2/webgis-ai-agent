"""Situation→plan SkillPolicy attach (once per plan seam).

When ``GIS_SKILL_POLICY=0`` → return inputs unchanged and ``None`` bundle
(byte-identical planner path; no skill side effects).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from app.services.gis_harness.hotpath_convergence.flags import skill_policy_enabled


def _facts_from_intent(
    intent: Any,
    *,
    situation: Any = None,
    ontology_matches: Optional[List[str]] = None,
):
    from app.services.gis_harness.skills.situation import SelectionFacts

    if situation is not None:
        try:
            facts = SelectionFacts.from_situation(situation)
        except Exception:  # noqa: BLE001 — honest empty on non-contract shape
            facts = SelectionFacts()
        # Overlay intent fields when situation left them blank.
        intent_facts = SelectionFacts.from_intent(intent)
        if not facts.goal_text and intent_facts.goal_text:
            facts.goal_text = intent_facts.goal_text
        if not facts.task_type and intent_facts.task_type:
            facts.task_type = intent_facts.task_type
        if not facts.scope_name and intent_facts.scope_name:
            facts.scope_name = intent_facts.scope_name
        if not facts.geometry_kinds and intent_facts.geometry_kinds:
            facts.geometry_kinds = list(intent_facts.geometry_kinds)
    else:
        facts = SelectionFacts.from_intent(intent)

    if ontology_matches:
        facts.ontology_matches = [str(x)[:64] for x in ontology_matches[:8]]
    return facts


def bind_skill_guidance_at_plan_seam(
    intent: Any,
    *,
    situation: Any = None,
    plan_inputs: Optional[Dict[str, Any]] = None,
    ontology_matches: Optional[List[str]] = None,
    record_evidence: bool = True,
    library=None,
) -> Tuple[Dict[str, Any], Optional[Any]]:
    """Resolve SkillPolicy once and attach guidance to plan inputs.

    Returns ``(plan_inputs, bundle)``. When policy disabled, returns a shallow
    copy of ``plan_inputs`` (or ``{}``) with **no** ``skill_guidance`` key and
    ``bundle=None`` — callers must treat this as the unchanged planner path.
    """
    base = dict(plan_inputs or {})
    if not skill_policy_enabled():
        return base, None

    from app.services.gis_harness.skills.hotpath import (
        attach_skill_guidance_to_plan_inputs,
        resolve_skill_guidance,
    )

    facts = _facts_from_intent(
        intent, situation=situation, ontology_matches=ontology_matches,
    )
    bundle = resolve_skill_guidance(
        facts,
        library=library,
        record_evidence=record_evidence,
    )
    out = attach_skill_guidance_to_plan_inputs(base, bundle)
    return out, bundle


__all__ = ["bind_skill_guidance_at_plan_seam"]
