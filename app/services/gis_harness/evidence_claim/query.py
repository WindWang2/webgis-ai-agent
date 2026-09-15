"""Bounded technical query API — no unrestricted graph dumps."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .carto_binding import CartoBinding, explain_map_feature
from .contradiction import detect_contradictions
from .freshness import affected_descendants
from .graph import RelationGraph
from .store import ClaimStore
from .verify import verify_claim


def why_claim(store: ClaimStore, claim_id: str, **verify_kwargs: Any) -> Dict[str, Any]:
    claim = store.get_claim(claim_id)
    if claim is None:
        return {"claim_id": claim_id, "error": "claim_not_found"}
    verdict = verify_claim(claim, store, **verify_kwargs)
    graph = RelationGraph(store)
    upstream = graph.ancestors(claim_id, max_depth=8, max_nodes=64)
    evidence = []
    for eid in claim.supporting_evidence_refs[:16]:
        node = store.get_evidence(eid)
        if node:
            evidence.append(node.to_bounded_dict())
        else:
            evidence.append({"evidence_id": eid, "freshness": "missing"})
    return {
        "claim": claim.to_bounded_dict(),
        "verification": verdict.to_bounded_dict(),
        "evidence": evidence,
        "upstream": upstream.to_bounded_dict(),
    }


def why_map_feature(
    store: ClaimStore,
    binding: CartoBinding,
    *,
    current_mapspec_revision: str = "",
    current_breaks: Optional[List[float]] = None,
) -> Dict[str, Any]:
    explained = explain_map_feature(
        binding,
        current_mapspec_revision=current_mapspec_revision,
        current_breaks=current_breaks,
    )
    claim_part = None
    if binding.claim_id:
        claim_part = why_claim(store, binding.claim_id)
    return {
        "map_explanation": explained,
        "claim_explanation": claim_part,
    }


def evidence_for(store: ClaimStore, claim_id: str) -> Dict[str, Any]:
    claim = store.get_claim(claim_id)
    if claim is None:
        return {"claim_id": claim_id, "evidence": [], "error": "claim_not_found"}
    items = []
    for eid in claim.supporting_evidence_refs:
        node = store.get_evidence(eid)
        items.append(node.to_bounded_dict() if node else {"evidence_id": eid, "freshness": "missing"})
    return {"claim_id": claim_id, "evidence": items[:16]}


def claims_for_artifact(store: ClaimStore, artifact_ref: str) -> Dict[str, Any]:
    claims = store.claims_for_artifact(artifact_ref)
    # also match evidence nodes with this ref
    for node in store.evidence_by_ref(artifact_ref):
        for c in store.all_claims():
            if node.evidence_id in c.supporting_evidence_refs and c not in claims:
                claims.append(c)
    return {
        "artifact_ref": artifact_ref[:128],
        "claims": [c.to_bounded_dict() for c in sorted(claims, key=lambda x: x.claim_id)[:32]],
    }


def affected_claims(store: ClaimStore, ref: str) -> Dict[str, Any]:
    return affected_descendants(store, ref)


def contradictions(store: ClaimStore, claim_ids: Optional[List[str]] = None) -> Dict[str, Any]:
    results = detect_contradictions(store, claim_ids=claim_ids)
    return {
        "contradictions": [r.to_bounded_dict() for r in results[:32]],
        "count": len(results),
    }
