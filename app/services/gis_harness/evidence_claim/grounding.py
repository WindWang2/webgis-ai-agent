"""Pi-facing bounded grounding projection — no CoT / hidden reasoning."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .contracts import MAX_GROUNDING_EVIDENCE, ClaimStatus
from .query import why_claim
from .store import ClaimStore
from .verify import verify_claim


def grounding_projection(
    store: ClaimStore,
    claim_id: str,
    *,
    records: Optional[Dict[str, Any]] = None,
    expected_tenant_id: str = "",
    require_uncertainty: bool = False,
) -> Dict[str, Any]:
    """Bounded technical grounding block suitable for final-answer disclosure."""
    claim = store.get_claim(claim_id)
    if claim is None:
        return {
            "claim": None,
            "status": ClaimStatus.UNKNOWN.value,
            "evidence": [],
            "uncertainty": None,
            "freshness": "missing",
            "error": "claim_not_found",
        }

    verdict = verify_claim(
        claim, store,
        records=records,
        expected_tenant_id=expected_tenant_id,
        require_uncertainty=require_uncertainty,
    )
    evidence_blocks: List[Dict[str, Any]] = []
    freshness_bits: List[str] = []
    for eid in claim.supporting_evidence_refs[:MAX_GROUNDING_EVIDENCE]:
        node = store.get_evidence(eid)
        if node is None:
            evidence_blocks.append({"ref": eid, "kind": "missing", "freshness": "missing"})
            freshness_bits.append("missing")
            continue
        evidence_blocks.append({
            "evidence_id": node.evidence_id[:64],
            "kind": node.kind.value,
            "ref": node.ref[:128],
            "version": node.version[:64],
            "method": node.method[:64],
            "freshness": node.freshness.value,
            "producer": node.producer[:64],
        })
        freshness_bits.append(node.freshness.value)

    unc = None
    if claim.uncertainty_ref:
        unc_node = store.get_evidence(claim.uncertainty_ref)
        unc = (
            unc_node.to_bounded_dict() if unc_node
            else {"ref": claim.uncertainty_ref, "freshness": "missing"}
        )

    # Aggregate freshness label
    if any(f in ("missing", "expired", "superseded") for f in freshness_bits):
        freshness = "not_fresh"
    elif any(f == "stale" for f in freshness_bits):
        freshness = "stale"
    elif freshness_bits and all(f == "fresh" for f in freshness_bits):
        freshness = "fresh"
    else:
        freshness = "unknown"

    return {
        "claim": {
            "claim_id": claim.claim_id,
            "claim_type": claim.claim_type.value,
            "subject": claim.subject,
            "predicate": claim.predicate,
            "value": claim.value,
            "unit": claim.unit,
            "comparator": claim.comparator,
            "spatial_scope": claim.spatial_scope.to_bounded_dict(),
            "temporal_scope": claim.temporal_scope.to_bounded_dict(),
            "method": claim.method,
        },
        "status": verdict.status.value,
        "positive_proof": verdict.positive_proof,
        "evidence": evidence_blocks,
        "uncertainty": unc,
        "freshness": freshness,
        "verification_reasons": verdict.reasons[:8],
        "map_layer_id": claim.map_layer_id,
        "product_facet_id": claim.product_facet_id,
    }


def answer_why_highest_density(
    store: ClaimStore,
    *,
    subject_hint: str = "",
) -> Dict[str, Any]:
    """Success-criteria helper: explain a density/highest claim chain."""
    candidates = [
        c for c in store.all_claims()
        if c.comparator == "highest" and c.claim_type.value in ("density", "rate", "count")
    ]
    if subject_hint:
        hinted = [c for c in candidates if subject_hint in c.subject]
        if hinted:
            candidates = hinted
    if not candidates:
        return {"error": "no_matching_claim", "subject_hint": subject_hint}
    claim = sorted(candidates, key=lambda c: c.claim_id)[0]
    detail = why_claim(store, claim.claim_id)
    ground = grounding_projection(store, claim.claim_id)
    return {
        "question": f"为什么{claim.subject}被判断为{claim.claim_type.value}最高？",
        "grounding": ground,
        "chain": detail,
    }
