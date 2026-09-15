"""Deterministic typed claim generation from analysis/statistic outputs.

LLM prose MUST NOT be the authoritative numeric source.
"""
from __future__ import annotations

import hashlib
from typing import Any, Dict, Optional, Sequence

from .contracts import (
    Claim,
    ClaimStatus,
    ClaimType,
    EvidenceKind,
    EvidenceNode,
    RelationEdge,
    RelationType,
    Scope,
)
from .store import ClaimStore


def _claim_id(*parts: str) -> str:
    raw = "|".join(str(p) for p in parts)
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    return f"claim:{digest}"


def claim_from_statistic(
    *,
    subject: str,
    claim_type: ClaimType | str,
    value: float,
    unit: str = "",
    comparator: str = "",
    method: str = "",
    statistic_evidence_id: str,
    dataset_version_evidence_id: str = "",
    spatial_scope: Optional[Scope] = None,
    temporal_scope: Optional[Scope] = None,
    reference_scope: Optional[Scope] = None,
    uncertainty_ref: str = "",
    tenant_id: str = "",
    session_id: str = "",
    narrative: str = "",
    product_facet_id: str = "",
    map_layer_id: str = "",
    store: Optional[ClaimStore] = None,
) -> Claim:
    """Build a typed numeric claim grounded on statistic (+ optional dataset) evidence."""
    ct = claim_type if isinstance(claim_type, ClaimType) else ClaimType(str(claim_type))
    if ct is ClaimType.NARRATIVE:
        raise ValueError("use narrative_claim_unverified for free-text claims")

    spatial = spatial_scope or Scope()
    temporal = temporal_scope or Scope()
    reference = reference_scope or Scope()
    supporting = [statistic_evidence_id]
    if dataset_version_evidence_id:
        supporting.append(dataset_version_evidence_id)

    claim = Claim(
        claim_id=_claim_id(
            ct.value, subject, str(value), unit, comparator,
            spatial.spatial_name, temporal.temporal_label, method, tenant_id, session_id,
        ),
        claim_type=ct,
        subject=subject[:200],
        predicate=ct.value,
        value=float(value),
        unit=unit[:32],
        comparator=comparator[:32],
        reference_scope=reference,
        temporal_scope=temporal,
        spatial_scope=spatial,
        method=method[:64],
        supporting_evidence_refs=supporting[:16],
        uncertainty_ref=uncertainty_ref[:128],
        status=ClaimStatus.UNKNOWN,
        tenant_id=tenant_id[:64],
        session_id=session_id[:64],
        narrative=narrative[:200],
        product_facet_id=product_facet_id[:64],
        map_layer_id=map_layer_id[:64],
    )
    if store is not None:
        store.upsert_claim(claim)
        store.add_edge(RelationEdge(
            edge_id=f"sup:{claim.claim_id}:0"[:64],
            relation=RelationType.SUPPORTS,
            src=statistic_evidence_id[:64],
            dst=claim.claim_id,
        ))
        if dataset_version_evidence_id:
            store.add_edge(RelationEdge(
                edge_id=f"dep:{claim.claim_id}:ds"[:64],
                relation=RelationType.DEPENDS_ON,
                src=claim.claim_id,
                dst=dataset_version_evidence_id[:64],
            ))
            store.add_edge(RelationEdge(
                edge_id=f"der:{statistic_evidence_id}:ds"[:64],
                relation=RelationType.DERIVED_FROM,
                src=statistic_evidence_id[:64],
                dst=dataset_version_evidence_id[:64],
            ))
        if method:
            method_id = f"method:{method}"[:64]
            store.upsert_evidence(EvidenceNode(
                evidence_id=method_id,
                kind=EvidenceKind.METHOD,
                ref=method_id,
                method=method[:64],
                tenant_id=tenant_id[:64],
                session_id=session_id[:64],
            ))
            store.add_edge(RelationEdge(
                edge_id=f"cmp:{claim.claim_id}:m"[:64],
                relation=RelationType.COMPUTED_BY,
                src=claim.claim_id,
                dst=method_id,
            ))
        if map_layer_id:
            store.add_edge(RelationEdge(
                edge_id=f"viz:{claim.claim_id}"[:64],
                relation=RelationType.VISUALIZED_AS,
                src=claim.claim_id,
                dst=f"layer:{map_layer_id}"[:64],
            ))
    return claim


def claim_from_rank_table(
    rows: Sequence[Dict[str, Any]],
    *,
    subject_key: str = "name",
    value_key: str = "value",
    claim_type: ClaimType = ClaimType.DENSITY,
    unit: str = "",
    method: str = "",
    statistic_evidence_id: str,
    dataset_version_evidence_id: str = "",
    spatial_level: str = "district",
    group_by: str = "district",
    temporal_label: str = "",
    tenant_id: str = "",
    session_id: str = "",
    store: Optional[ClaimStore] = None,
) -> Optional[Claim]:
    """Deterministic 'highest' claim from a ranked aggregate table."""
    ranked = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = str(row.get(subject_key) or "").strip()
        if not name:
            continue
        try:
            val = float(row[value_key])
        except (KeyError, TypeError, ValueError):
            continue
        ranked.append((name, val))
    if not ranked:
        return None
    ranked.sort(key=lambda x: (-x[1], x[0]))
    top_name, top_val = ranked[0]
    return claim_from_statistic(
        subject=top_name,
        claim_type=claim_type,
        value=top_val,
        unit=unit,
        comparator="highest",
        method=method,
        statistic_evidence_id=statistic_evidence_id,
        dataset_version_evidence_id=dataset_version_evidence_id,
        spatial_scope=Scope(spatial_name=top_name, spatial_level=spatial_level, group_by=group_by),
        reference_scope=Scope(group_by=group_by, spatial_level=spatial_level),
        temporal_scope=Scope(temporal_label=temporal_label),
        tenant_id=tenant_id,
        session_id=session_id,
        narrative=f"{top_name} {claim_type.value} highest",
        store=store,
    )


def narrative_claim_unverified(
    text: str,
    *,
    tenant_id: str = "",
    session_id: str = "",
    store: Optional[ClaimStore] = None,
) -> Claim:
    """Free-text claim without evidence — remains unsupported forever until grounded."""
    claim = Claim(
        claim_id=_claim_id("narrative", text, tenant_id, session_id),
        claim_type=ClaimType.NARRATIVE,
        subject="",
        predicate="narrative",
        value=None,
        value_text=text[:200],
        narrative=text[:200],
        status=ClaimStatus.UNSUPPORTED,
        tenant_id=tenant_id[:64],
        session_id=session_id[:64],
    )
    if store is not None:
        store.upsert_claim(claim)
    return claim
