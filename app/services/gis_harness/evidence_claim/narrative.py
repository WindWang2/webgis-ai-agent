"""ClaimNarrativeProjection — adapter for StoryMap / report grounding.

Does not rewrite StoryMap compiler. Exposes claim refs narrative can cite.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .contracts import Claim, ClaimStatus, ClaimType, MAX_TEXT
from .store import ClaimStore


class ClaimNarrativeProjection:
    """Project verified claims into narrative-consumable bounded dicts."""

    def __init__(self, store: ClaimStore) -> None:
        self._store = store

    def project_claim(self, claim_id: str) -> Optional[Dict[str, Any]]:
        claim = self._store.get_claim(claim_id)
        if claim is None:
            return None
        return self._one(claim)

    def project_supported(self, *, limit: int = 16) -> List[Dict[str, Any]]:
        out = []
        for claim in sorted(self._store.all_claims(), key=lambda c: c.claim_id):
            if claim.status is ClaimStatus.SUPPORTED:
                out.append(self._one(claim))
            if len(out) >= limit:
                break
        return out

    def sentence_binding(self, claim_id: str, sentence: str) -> Dict[str, Any]:
        """Bind a narrative sentence to a claim_id — numbers stay on the claim."""
        claim = self._store.get_claim(claim_id)
        return {
            "sentence": (sentence or "")[:MAX_TEXT],
            "claim_id": claim_id[:64],
            "claim_status": claim.status.value if claim else ClaimStatus.UNKNOWN.value,
            "value": claim.value if claim else None,
            "unit": claim.unit if claim else "",
            "authoritative": bool(claim and claim.claim_type is not ClaimType.NARRATIVE
                                  and claim.status is ClaimStatus.SUPPORTED),
        }

    def storymap_trace_input(self, claim_ids: Optional[List[str]] = None) -> Dict[str, Any]:
        """Shape consumable alongside GisTraceChain / ReplayTrace for StoryMap."""
        claims = []
        if claim_ids:
            for cid in claim_ids[:24]:
                p = self.project_claim(cid)
                if p:
                    claims.append(p)
        else:
            claims = self.project_supported(limit=24)
        return {
            "schema": "claim_narrative_projection.v1",
            "claims": claims,
            "note": "Narrative may paraphrase; numeric authority remains on claims.",
        }

    @staticmethod
    def _one(claim: Claim) -> Dict[str, Any]:
        return {
            "claim_id": claim.claim_id[:64],
            "claim_type": claim.claim_type.value,
            "subject": claim.subject[:MAX_TEXT],
            "predicate": claim.predicate[:64],
            "value": claim.value,
            "unit": claim.unit[:32],
            "comparator": claim.comparator[:32],
            "status": claim.status.value,
            "spatial": claim.spatial_scope.spatial_name[:64],
            "temporal": claim.temporal_scope.temporal_label[:64],
            "method": claim.method[:64],
            "supporting_evidence_refs": claim.supporting_evidence_refs[:8],
            "narrative": claim.narrative[:MAX_TEXT],
        }
