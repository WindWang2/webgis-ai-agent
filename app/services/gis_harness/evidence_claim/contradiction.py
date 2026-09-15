"""Contradiction detection across typed claims."""
from __future__ import annotations

import hashlib
from typing import List, Optional

from .contracts import (
    Claim,
    ClaimStatus,
    ClaimType,
    ContradictionResult,
    RelationEdge,
    RelationType,
)
from .store import ClaimStore


def _pair_key(a: str, b: str) -> str:
    x, y = sorted([a, b])
    digest = hashlib.sha256(f"{x}|{y}".encode()).hexdigest()[:12]
    return f"contra:{digest}"


def _same_metric_axis(a: Claim, b: Claim) -> bool:
    if a.claim_type is ClaimType.NARRATIVE or b.claim_type is ClaimType.NARRATIVE:
        return False
    if a.predicate != b.predicate and a.claim_type != b.claim_type:
        return False
    if a.comparator and b.comparator and a.comparator != b.comparator:
        # both claiming "highest" on same axis is the interesting case
        pass
    return a.claim_type == b.claim_type


def _values_conflict(a: Claim, b: Claim) -> bool:
    if a.comparator == "highest" and b.comparator == "highest":
        return a.subject != b.subject
    if a.value is not None and b.value is not None and a.subject == b.subject:
        return float(a.value) != float(b.value)
    if a.comparator == b.comparator == "eq" and a.subject == b.subject:
        return a.value != b.value
    return False


def detect_contradictions(
    store: ClaimStore,
    *,
    claim_ids: Optional[List[str]] = None,
) -> List[ContradictionResult]:
    claims = store.all_claims()
    if claim_ids is not None:
        idset = set(claim_ids)
        claims = [c for c in claims if c.claim_id in idset]
    claims = sorted(claims, key=lambda c: c.claim_id)
    results: List[ContradictionResult] = []

    for i, a in enumerate(claims):
        for b in claims[i + 1 :]:
            if not _same_metric_axis(a, b):
                continue
            if a.tenant_id and b.tenant_id and a.tenant_id != b.tenant_id:
                continue
            if not _values_conflict(a, b):
                continue

            scopes_overlap = a.spatial_scope.overlaps(b.spatial_scope) and a.temporal_scope.overlaps(b.temporal_scope)
            # Also check reference_scope filters
            scopes_overlap = scopes_overlap and a.reference_scope.overlaps(b.reference_scope)

            inspected = {
                "dataset_a": ",".join(a.supporting_evidence_refs[:2]),
                "dataset_b": ",".join(b.supporting_evidence_refs[:2]),
                "time_a": a.temporal_scope.temporal_label,
                "time_b": b.temporal_scope.temporal_label,
                "aoi_a": a.spatial_scope.aoi_ref or a.spatial_scope.spatial_name,
                "aoi_b": b.spatial_scope.aoi_ref or b.spatial_scope.spatial_name,
                "method_a": a.method,
                "method_b": b.method,
                "filter_a": a.reference_scope.filter_digest,
                "filter_b": b.reference_scope.filter_digest,
                "metric": a.claim_type.value,
            }

            if scopes_overlap and a.method == b.method:
                kind = "hard"
                detail = f"conflicting {a.claim_type.value}: {a.subject} vs {b.subject}"
                store.mark_claim_status(a.claim_id, ClaimStatus.CONTRADICTED)
                store.mark_claim_status(b.claim_id, ClaimStatus.CONTRADICTED)
                # wire contradicts edges
                cid = _pair_key(a.claim_id, b.claim_id)
                store.add_edge(RelationEdge(
                    edge_id=f"cx:{cid}:ab"[:64],
                    relation=RelationType.CONTRADICTS,
                    src=a.claim_id,
                    dst=b.claim_id,
                ))
                store.add_edge(RelationEdge(
                    edge_id=f"cx:{cid}:ba"[:64],
                    relation=RelationType.CONTRADICTS,
                    src=b.claim_id,
                    dst=a.claim_id,
                ))
                a_upd = store.get_claim(a.claim_id)
                b_upd = store.get_claim(b.claim_id)
                if a_upd:
                    refs = list(a_upd.contradicting_evidence_refs)
                    if b.claim_id not in refs:
                        refs.append(b.claim_id)
                    store.upsert_claim(a_upd.model_copy(update={
                        "contradicting_evidence_refs": refs[:16],
                        "status": ClaimStatus.CONTRADICTED,
                    }))
                if b_upd:
                    refs = list(b_upd.contradicting_evidence_refs)
                    if a.claim_id not in refs:
                        refs.append(a.claim_id)
                    store.upsert_claim(b_upd.model_copy(update={
                        "contradicting_evidence_refs": refs[:16],
                        "status": ClaimStatus.CONTRADICTED,
                    }))
            else:
                kind = "scoped_divergence"
                detail = (
                    f"same metric under different scopes: "
                    f"{a.subject}@{a.temporal_scope.temporal_label or a.spatial_scope.spatial_name}"
                    f" vs {b.subject}@{b.temporal_scope.temporal_label or b.spatial_scope.spatial_name}"
                )

            results.append(ContradictionResult(
                contradiction_id=_pair_key(a.claim_id, b.claim_id),
                claim_ids=[a.claim_id, b.claim_id],
                kind=kind,
                detail=detail[:200],
                inspected=inspected,
            ))
    return results
