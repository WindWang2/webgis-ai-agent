"""Freshness / invalidation projection — affected descendants, no eager recompute."""
from __future__ import annotations

from typing import Any, Dict, List, Set

from .contracts import (
    ClaimStatus,
    EvidenceFreshness,
    MAX_TRAVERSAL_DEPTH,
    MAX_TRAVERSAL_NODES,
    RelationType,
)
from .graph import RelationGraph
from .store import ClaimStore


def mark_evidence_stale(store: ClaimStore, evidence_id: str, *, reason: str = "upstream_change") -> None:
    node = store.get_evidence(evidence_id)
    if node is None:
        return
    store.upsert_evidence(node.model_copy(update={
        "freshness": EvidenceFreshness.STALE,
        "metadata": {**(node.metadata or {}), "stale_reason": reason[:64]},
    }))


def mark_evidence_superseded(
    store: ClaimStore,
    old_evidence_id: str,
    new_evidence_id: str,
) -> None:
    old = store.get_evidence(old_evidence_id)
    if old is not None:
        store.upsert_evidence(old.model_copy(update={"freshness": EvidenceFreshness.SUPERSEDED}))
    from .contracts import RelationEdge
    store.add_edge(RelationEdge(
        edge_id=f"sup:{new_evidence_id}:{old_evidence_id}"[:64],
        relation=RelationType.SUPERSEDES,
        src=new_evidence_id,
        dst=old_evidence_id,
    ))
    store.add_edge(RelationEdge(
        edge_id=f"inv:{new_evidence_id}:{old_evidence_id}"[:64],
        relation=RelationType.INVALIDATES,
        src=new_evidence_id,
        dst=old_evidence_id,
    ))


def affected_descendants(
    store: ClaimStore,
    ref_or_evidence_id: str,
    *,
    max_depth: int = MAX_TRAVERSAL_DEPTH,
    max_nodes: int = MAX_TRAVERSAL_NODES,
) -> Dict[str, Any]:
    """When upstream evidence changes, list affected analysis/stats/claims/layers.

    Does NOT recompute — returns bounded ids for replanning/execution to consume.
    """
    # Resolve ref → evidence ids
    seeds: List[str] = []
    if store.get_evidence(ref_or_evidence_id):
        seeds.append(ref_or_evidence_id)
    for node in store.evidence_by_ref(ref_or_evidence_id):
        seeds.append(node.evidence_id)
    # also accept art: prefix stripped
    if ref_or_evidence_id.startswith("art:"):
        bare = ref_or_evidence_id[4:]
        for node in store.evidence_by_ref(bare):
            seeds.append(node.evidence_id)
    seeds = sorted(set(seeds))

    graph = RelationGraph(store)
    affected_evidence: Set[str] = set()
    affected_claims: Set[str] = set()
    truncated = False
    cycle = False

    for seed in seeds:
        trav = graph.descendants(seed, max_depth=max_depth, max_nodes=max_nodes)
        truncated = truncated or trav.truncated
        cycle = cycle or trav.cycle_detected
        for nid in trav.nodes:
            if nid.startswith("claim:") or store.get_claim(nid):
                affected_claims.add(nid)
            else:
                affected_evidence.add(nid)
    # Indexed reverse lookup; retain only support dependencies, not contradictions.
    for ref in set(seeds + [ref_or_evidence_id]):
        for claim in store.claims_for_artifact(ref):
            for eid in claim.supporting_evidence_refs:
                node = store.get_evidence(eid)
                if eid in seeds or eid == ref_or_evidence_id or (node and node.ref == ref_or_evidence_id):
                    affected_claims.add(claim.claim_id)
                    break

    analysis = sorted(e for e in affected_evidence if e.startswith("art:") or e.startswith("facet:"))
    layers = sorted(e for e in affected_evidence if e.startswith("layer:"))
    stats = sorted(e for e in affected_evidence if store.get_evidence(e) and store.get_evidence(e).kind.value == "statistic")

    return {
        "seed": ref_or_evidence_id[:128],
        "seed_evidence_ids": seeds[:32],
        "affected_evidence": sorted(affected_evidence)[:max_nodes],
        "affected_claims": sorted(affected_claims)[:max_nodes],
        "analysis": analysis[:64],
        "statistics": stats[:64],
        "map_layers": layers[:64],
        "truncated": truncated,
        "cycle_detected": cycle,
    }


def invalidate_affected_claims(
    store: ClaimStore,
    ref_or_evidence_id: str,
    *,
    mark_evidence: bool = True,
) -> Dict[str, Any]:
    """Mark affected claims stale after upstream version change."""
    report = affected_descendants(store, ref_or_evidence_id)
    if mark_evidence:
        for eid in report["seed_evidence_ids"]:
            mark_evidence_stale(store, eid, reason="version_update")
        for eid in report["affected_evidence"]:
            mark_evidence_stale(store, eid, reason="upstream_stale")
    stale_claims = []
    for cid in report["affected_claims"]:
        claim = store.mark_claim_status(cid, ClaimStatus.STALE)
        if claim:
            stale_claims.append(cid)
    report["stale_claims"] = stale_claims[:256]
    return report
