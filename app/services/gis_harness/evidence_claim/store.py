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
        self._by_ref: Dict[str, Dict[str, None]] = {}
        self._claims_by_ref: Dict[str, Dict[str, None]] = {}
        self._by_src: Dict[str, Dict[str, RelationEdge]] = {}
        self._by_dst: Dict[str, Dict[str, RelationEdge]] = {}

    @staticmethod
    def _discard(index: Any, key: str, ident: str) -> None:
        entries = index.get(key)
        if entries is not None:
            entries.pop(ident, None)
            if not entries:
                index.pop(key, None)

    # ── Evidence ─────────────────────────────────────────────────────────
    def upsert_evidence(self, node: EvidenceNode) -> EvidenceNode:
        if node.evidence_id not in self._evidence and len(self._evidence) >= self._max_evidence:
            raise OverflowError("evidence node budget exceeded")
        old = self._evidence.get(node.evidence_id)
        if old:
            self._discard(self._by_ref, old.ref, node.evidence_id)
        self._by_ref.setdefault(node.ref, {})[node.evidence_id] = None
        self._evidence[node.evidence_id] = node
        return node

    def get_evidence(self, evidence_id: str) -> Optional[EvidenceNode]:
        return self._evidence.get(evidence_id)

    def evidence_by_ref(self, ref: str) -> List[EvidenceNode]:
        return [self._evidence[eid] for eid in self._by_ref.get(ref, {})]

    def all_evidence(self) -> List[EvidenceNode]:
        return list(self._evidence.values())

    # ── Claims ───────────────────────────────────────────────────────────
    def upsert_claim(self, claim: Claim) -> Claim:
        if claim.claim_id not in self._claims and len(self._claims) >= self._max_claims:
            raise OverflowError("claim budget exceeded")
        old = self._claims.get(claim.claim_id)
        if old:
            for ref in old.supporting_evidence_refs + old.contradicting_evidence_refs:
                self._discard(self._claims_by_ref, ref, claim.claim_id)
        for ref in claim.supporting_evidence_refs + claim.contradicting_evidence_refs:
            self._claims_by_ref.setdefault(ref, {})[claim.claim_id] = None
        self._claims[claim.claim_id] = claim
        return claim

    def get_claim(self, claim_id: str) -> Optional[Claim]:
        return self._claims.get(claim_id)

    def all_claims(self) -> List[Claim]:
        return list(self._claims.values())

    def claims_for_artifact(self, artifact_ref: str) -> List[Claim]:
        ids = dict(self._claims_by_ref.get(artifact_ref, {}))
        for eid in self._by_ref.get(artifact_ref, {}):
            ids.update(self._claims_by_ref.get(eid, {}))
        return [self._claims[cid] for cid in ids]

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
        old = self._edges.get(edge.edge_id)
        if old:
            self._discard(self._by_src, old.src, edge.edge_id)
            self._discard(self._by_dst, old.dst, edge.edge_id)
        self._by_src.setdefault(edge.src, {})[edge.edge_id] = edge
        self._by_dst.setdefault(edge.dst, {})[edge.edge_id] = edge
        self._edges[edge.edge_id] = edge
        return edge

    def edges_from(self, src: str) -> List[RelationEdge]:
        return list(self._by_src.get(src, {}).values())

    def edges_to(self, dst: str) -> List[RelationEdge]:
        return list(self._by_dst.get(dst, {}).values())

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
        self._by_ref.clear()
        self._claims_by_ref.clear()
        self._by_src.clear()
        self._by_dst.clear()

    def stats(self) -> Dict[str, int]:
        return {
            "evidence": len(self._evidence),
            "claims": len(self._claims),
            "edges": len(self._edges),
        }
