"""Durable-light Claim / Evidence / Edge store (in-memory + dict round-trip).

Mirrors ArtifactRegistry / SkillEvidenceRecorder patterns: session-scoped,
bounded, serializable. Not a second ArtifactRegistry — stores claim identity
and evidence *stubs* only.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .contracts import (
    MAX_CLAIMS,
    MAX_EDGES,
    MAX_EVIDENCE_NODES,
    Claim,
    ClaimStatus,
    EvidenceNode,
    RelationEdge,
)


class ClaimStore:
    """Bounded in-memory store for evidence stubs, claims, and typed edges."""

    def __init__(
        self,
        *,
        max_evidence: int = MAX_EVIDENCE_NODES,
        max_claims: int = MAX_CLAIMS,
        max_edges: int = MAX_EDGES,
    ) -> None:
        self._max_evidence = max_evidence
        self._max_claims = max_claims
        self._max_edges = max_edges
        self._evidence: Dict[str, EvidenceNode] = {}
        self._claims: Dict[str, Claim] = {}
        self._edges: Dict[str, RelationEdge] = {}

    # ── Evidence ─────────────────────────────────────────────────────────
    def upsert_evidence(self, node: EvidenceNode) -> EvidenceNode:
        if node.evidence_id not in self._evidence and len(self._evidence) >= self._max_evidence:
            raise OverflowError("evidence node budget exceeded")
        self._evidence[node.evidence_id] = node
        return node

    def get_evidence(self, evidence_id: str) -> Optional[EvidenceNode]:
        return self._evidence.get(evidence_id)

    def evidence_by_ref(self, ref: str) -> List[EvidenceNode]:
        return [n for n in self._evidence.values() if n.ref == ref]

    def all_evidence(self) -> List[EvidenceNode]:
        return list(self._evidence.values())

    # ── Claims ───────────────────────────────────────────────────────────
    def upsert_claim(self, claim: Claim) -> Claim:
        if claim.claim_id not in self._claims and len(self._claims) >= self._max_claims:
            raise OverflowError("claim budget exceeded")
        self._claims[claim.claim_id] = claim
        return claim

    def get_claim(self, claim_id: str) -> Optional[Claim]:
        return self._claims.get(claim_id)

    def all_claims(self) -> List[Claim]:
        return list(self._claims.values())

    def claims_for_artifact(self, artifact_ref: str) -> List[Claim]:
        out: List[Claim] = []
        for c in self._claims.values():
            if artifact_ref in c.supporting_evidence_refs or artifact_ref in c.contradicting_evidence_refs:
                out.append(c)
                continue
            for eid in c.supporting_evidence_refs:
                node = self._evidence.get(eid)
                if node and node.ref == artifact_ref:
                    out.append(c)
                    break
        return out

    def mark_claim_status(self, claim_id: str, status: ClaimStatus) -> Optional[Claim]:
        claim = self._claims.get(claim_id)
        if claim is None:
            return None
        updated = claim.model_copy(update={"status": status})
        self._claims[claim_id] = updated
        return updated

    # ── Edges ────────────────────────────────────────────────────────────
    def add_edge(self, edge: RelationEdge) -> RelationEdge:
        if edge.edge_id not in self._edges and len(self._edges) >= self._max_edges:
            raise OverflowError("edge budget exceeded")
        self._edges[edge.edge_id] = edge
        return edge

    def edges_from(self, src: str) -> List[RelationEdge]:
        return [e for e in self._edges.values() if e.src == src]

    def edges_to(self, dst: str) -> List[RelationEdge]:
        return [e for e in self._edges.values() if e.dst == dst]

    def all_edges(self) -> List[RelationEdge]:
        return list(self._edges.values())

    # ── Serialization ────────────────────────────────────────────────────
    def to_dict(self) -> Dict[str, Any]:
        return {
            "evidence": [n.to_bounded_dict() for n in self._evidence.values()],
            "claims": [c.to_bounded_dict() for c in self._claims.values()],
            "edges": [e.to_bounded_dict() for e in self._edges.values()],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ClaimStore":
        store = cls()
        for raw in data.get("evidence") or []:
            if isinstance(raw, dict):
                store.upsert_evidence(EvidenceNode.model_validate(raw))
        for raw in data.get("claims") or []:
            if isinstance(raw, dict):
                store.upsert_claim(Claim.model_validate(raw))
        for raw in data.get("edges") or []:
            if isinstance(raw, dict):
                store.add_edge(RelationEdge.model_validate(raw))
        return store

    def clear(self) -> None:
        self._evidence.clear()
        self._claims.clear()
        self._edges.clear()

    def stats(self) -> Dict[str, int]:
        return {
            "evidence": len(self._evidence),
            "claims": len(self._claims),
            "edges": len(self._edges),
        }
