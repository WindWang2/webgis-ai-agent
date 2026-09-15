"""Bounded Pi context card: skill decision + claim grounding (no CoT)."""
from __future__ import annotations

from typing import Any, Dict, List


def build_hotpath_pi_context(
    *,
    skill_bundle: Any = None,
    claim_store: Any = None,
    primary_claim_id: str = "",
    max_claims: int = 3,
) -> Dict[str, Any]:
    """Assemble a bounded disclosure card for Pi / SessionPlan consumers."""
    card: Dict[str, Any] = {
        "schema": "hotpath_pi_context.v1",
        "skill": None,
        "claims": [],
        "grounding": None,
    }

    if skill_bundle is not None:
        try:
            decision = getattr(skill_bundle, "decision", None)
            if decision is not None and hasattr(decision, "pi_context_card"):
                card["skill"] = decision.pi_context_card()
            elif hasattr(skill_bundle, "to_bounded_dict"):
                bounded = skill_bundle.to_bounded_dict()
                card["skill"] = bounded.get("pi_context") or {
                    "mode": (bounded.get("decision") or {}).get("mode"),
                    "guides_planning": bounded.get("guides_planning"),
                }
        except Exception:  # noqa: BLE001 — card is additive
            card["skill"] = {"error": "skill_card_failed"}

    if claim_store is not None:
        try:
            from app.services.gis_harness.evidence_claim.grounding import (
                grounding_projection,
            )

            claim_ids: List[str] = []
            if primary_claim_id:
                claim_ids.append(str(primary_claim_id)[:64])
            else:
                all_claims = list(claim_store.all_claims())[:max_claims]
                claim_ids = [c.claim_id for c in all_claims]

            summaries = []
            primary_ground = None
            for cid in claim_ids[:max_claims]:
                ground = grounding_projection(claim_store, cid)
                summaries.append({
                    "claim_id": cid[:64],
                    "status": ground.get("status"),
                    "positive_proof": ground.get("positive_proof"),
                    "freshness": ground.get("freshness"),
                    "subject": ((ground.get("claim") or {}) or {}).get("subject", "")[:80],
                })
                if primary_ground is None:
                    primary_ground = ground
            card["claims"] = summaries
            card["grounding"] = primary_ground
        except Exception:  # noqa: BLE001
            card["claims"] = []
            card["grounding"] = {"error": "grounding_failed"}

    # Hard bound: strip any accidental reasoning dumps.
    for banned in ("chain_of_thought", "cot", "raw_llm", "messages", "thinking"):
        card.pop(banned, None)
    return card


__all__ = ["build_hotpath_pi_context"]
