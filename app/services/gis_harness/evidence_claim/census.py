"""Projectors that READ existing authoritative stores into EvidenceNode stubs.

Adapters only — never copy payloads or invent a second registry.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from .contracts import (
    EvidenceFreshness,
    EvidenceKind,
    EvidenceNode,
    RelationEdge,
    RelationType,
    Scope,
)
from .store import ClaimStore


def _status_to_freshness(status: str) -> EvidenceFreshness:
    s = (status or "").lower()
    if s in ("valid", "alive", "present"):
        return EvidenceFreshness.FRESH
    if s == "stale":
        return EvidenceFreshness.STALE
    if s == "superseded":
        return EvidenceFreshness.SUPERSEDED
    if s in ("expired", "failed", "dead"):
        return EvidenceFreshness.EXPIRED
    if s in ("missing", "absent", "not_found"):
        return EvidenceFreshness.MISSING
    return EvidenceFreshness.UNKNOWN


def project_artifact_record(
    record: Any,
    *,
    tenant_id: str = "",
    session_id: str = "",
) -> EvidenceNode:
    """ArtifactRecord → EvidenceNode stub (ref = artifact_id)."""
    artifact_id = str(getattr(record, "artifact_id", "") or "")
    status = str(getattr(record, "status", "") or "")
    revision = str(getattr(record, "revision", "") or "")
    producer = str(
        getattr(record, "producer_capability", "")
        or getattr(record, "producer_tool", "")
        or ""
    )
    meta = getattr(record, "metadata", None) or {}
    kind = EvidenceKind.ARTIFACT
    atype = str(getattr(record, "artifact_type", "") or "")
    if atype in ("stats_table", "admin_aggregate_table", "grid_aggregate", "od_matrix"):
        kind = EvidenceKind.STATISTIC
    elif atype in ("chart_spec",):
        kind = EvidenceKind.CHART
    elif atype in ("density_surface", "raster_surface", "feature_collection"):
        kind = EvidenceKind.ANALYSIS
    return EvidenceNode(
        evidence_id=f"art:{artifact_id}"[:64],
        kind=kind,
        ref=artifact_id[:128],
        producer=producer[:64],
        version=str(meta.get("dataset_version") or meta.get("version") or "")[:64],
        revision=revision[:64],
        method=str(meta.get("method") or "")[:64],
        freshness=_status_to_freshness(status),
        tenant_id=tenant_id[:64],
        session_id=(session_id or str(getattr(record, "session_id", "") or ""))[:64],
        metadata={
            "artifact_type": atype[:64],
            "status": status[:32],
        },
    )


def project_artifact_registry(
    registry: Any,
    store: ClaimStore,
    *,
    tenant_id: str = "",
    session_id: str = "",
) -> List[EvidenceNode]:
    """Read ArtifactRegistry records into store; also wire input lineage edges."""
    records = []
    if hasattr(registry, "all_records"):
        records = list(registry.all_records())
    elif hasattr(registry, "records"):
        raw = registry.records
        records = list(raw.values()) if isinstance(raw, dict) else list(raw)
    elif isinstance(registry, dict):
        records = list(registry.values())

    nodes: List[EvidenceNode] = []
    for rec in records[:4096]:
        node = project_artifact_record(rec, tenant_id=tenant_id, session_id=session_id)
        store.upsert_evidence(node)
        nodes.append(node)
        inputs = list(getattr(rec, "inputs", None) or [])[:16]
        for i, upstream in enumerate(inputs):
            edge = RelationEdge(
                edge_id=f"lin:{node.evidence_id}:{i}"[:64],
                relation=RelationType.DERIVED_FROM,
                src=node.evidence_id,
                dst=f"art:{upstream}"[:64],
            )
            store.add_edge(edge)
            replaces = getattr(rec, "replaces", None)
            if replaces:
                store.add_edge(RelationEdge(
                    edge_id=f"sup:{node.evidence_id}"[:64],
                    relation=RelationType.SUPERSEDES,
                    src=node.evidence_id,
                    dst=f"art:{replaces}"[:64],
                ))
    return nodes


def project_dataset_version(
    *,
    dataset_key: str,
    version_token: str,
    content_fingerprint: str = "",
    schema_fingerprint: str = "",
    pin: str = "",
    tenant_id: str = "",
    session_id: str = "",
    store: Optional[ClaimStore] = None,
) -> EvidenceNode:
    """Data-fabric SnapshotRecord / version pin → EvidenceNode."""
    eid = f"dsv:{dataset_key}:{version_token or pin}"[:64]
    node = EvidenceNode(
        evidence_id=eid,
        kind=EvidenceKind.DATASET_VERSION,
        ref=f"dataset:{dataset_key}"[:128],
        version=(version_token or pin)[:64],
        revision=(content_fingerprint or "")[:64],
        freshness=EvidenceFreshness.FRESH if version_token or pin else EvidenceFreshness.UNKNOWN,
        tenant_id=tenant_id[:64],
        session_id=session_id[:64],
        metadata={
            "schema_fingerprint": (schema_fingerprint or "")[:64],
            "pin": (pin or "")[:64],
        },
    )
    if store is not None:
        store.upsert_evidence(node)
    return node


def project_product_facet(
    facet: Any,
    *,
    lineage_refs: Optional[Iterable[Any]] = None,
    tenant_id: str = "",
    session_id: str = "",
    store: Optional[ClaimStore] = None,
) -> EvidenceNode:
    """ProductNode / FacetLineageEntry → EvidenceNode + depends_on edges."""
    if isinstance(facet, dict):
        node_id = str(facet.get("node_id") or facet.get("facet_id") or "")
        kind_raw = str(facet.get("kind") or "product_facet")
        label = str(facet.get("label") or "")
        artifact_ref = str(facet.get("artifact_ref") or "")
    else:
        node_id = str(getattr(facet, "node_id", "") or getattr(facet, "facet_id", "") or "")
        kind_raw = str(getattr(facet, "kind", "") or "product_facet")
        label = str(getattr(facet, "label", "") or "")
        artifact_ref = str(getattr(facet, "artifact_ref", "") or "")

    kind_map = {
        "map_layer": EvidenceKind.MAP_LAYER,
        "analysis": EvidenceKind.ANALYSIS,
        "statistics": EvidenceKind.STATISTIC,
        "chart": EvidenceKind.CHART,
        "narrative": EvidenceKind.OBSERVATION,
    }
    node = EvidenceNode(
        evidence_id=f"facet:{node_id}"[:64],
        kind=kind_map.get(kind_raw, EvidenceKind.PRODUCT_FACET),
        ref=(artifact_ref or node_id)[:128],
        producer=label[:64],
        freshness=EvidenceFreshness.UNKNOWN,
        tenant_id=tenant_id[:64],
        session_id=session_id[:64],
        metadata={"facet_kind": kind_raw[:32]},
    )
    if store is not None:
        store.upsert_evidence(node)
        for i, lr in enumerate(list(lineage_refs or [])[:8]):
            ref = str(getattr(lr, "ref", None) or (lr.get("ref") if isinstance(lr, dict) else "") or "")
            if not ref:
                continue
            store.add_edge(RelationEdge(
                edge_id=f"pf:{node.evidence_id}:{i}"[:64],
                relation=RelationType.DEPENDS_ON,
                src=node.evidence_id,
                dst=f"art:{ref}"[:64],
            ))
    return node


def project_goal_evidence(
    goal_evidence: Any,
    *,
    tenant_id: str = "",
    session_id: str = "",
    store: Optional[ClaimStore] = None,
) -> EvidenceNode:
    """GoalEvidence → stub (align vocabulary; do not replace goal_satisfaction)."""
    if isinstance(goal_evidence, dict):
        eid = str(goal_evidence.get("id") or "")
        kind = str(goal_evidence.get("kind") or "observation")
        status = str(goal_evidence.get("status") or "")
        revision = str(goal_evidence.get("revision") or "")
        source = str(goal_evidence.get("source") or "")
        detail = str(goal_evidence.get("detail") or "")
    else:
        eid = str(getattr(goal_evidence, "id", "") or "")
        kind = str(getattr(getattr(goal_evidence, "kind", None), "value", None) or getattr(goal_evidence, "kind", "") or "")
        status = str(getattr(getattr(goal_evidence, "status", None), "value", None) or getattr(goal_evidence, "status", "") or "")
        revision = str(getattr(goal_evidence, "revision", "") or "")
        source = str(getattr(goal_evidence, "source", "") or "")
        detail = str(getattr(goal_evidence, "detail", "") or "")

    freshness = {
        "present": EvidenceFreshness.FRESH,
        "absent": EvidenceFreshness.MISSING,
        "stale": EvidenceFreshness.STALE,
        "failed": EvidenceFreshness.EXPIRED,
    }.get(status.lower(), EvidenceFreshness.UNKNOWN)

    node = EvidenceNode(
        evidence_id=f"ge:{eid}"[:64],
        kind=EvidenceKind.OBSERVATION,
        ref=revision[:128] if revision else source[:128],
        producer=source[:64],
        revision=revision[:64],
        method=detail[:64],
        freshness=freshness,
        tenant_id=tenant_id[:64],
        session_id=session_id[:64],
        metadata={"goal_evidence_kind": kind[:48], "status": status[:32]},
    )
    if store is not None:
        store.upsert_evidence(node)
    return node


def project_mapspec_layer(
    *,
    layer_id: str,
    source_ref: str = "",
    style_digest: str = "",
    mapspec_revision: str = "",
    metric_field: str = "",
    tenant_id: str = "",
    session_id: str = "",
    store: Optional[ClaimStore] = None,
) -> EvidenceNode:
    node = EvidenceNode(
        evidence_id=f"layer:{layer_id}"[:64],
        kind=EvidenceKind.MAP_LAYER,
        ref=(source_ref or layer_id)[:128],
        version=mapspec_revision[:64],
        revision=style_digest[:64],
        method=metric_field[:64],
        freshness=EvidenceFreshness.FRESH if mapspec_revision else EvidenceFreshness.UNKNOWN,
        tenant_id=tenant_id[:64],
        session_id=session_id[:64],
        metadata={"metric_field": metric_field[:64]},
    )
    if store is not None:
        store.upsert_evidence(node)
        if source_ref:
            store.add_edge(RelationEdge(
                edge_id=f"viz:{node.evidence_id}"[:64],
                relation=RelationType.VISUALIZED_AS,
                src=f"art:{source_ref}"[:64],
                dst=node.evidence_id,
            ))
    return node


def resolve_live_ref(
    ref: str,
    *,
    records: Optional[Dict[str, Any]] = None,
    descriptors: Optional[Dict[str, Any]] = None,
    tenant_id: str = "",
    expected_tenant_id: str = "",
) -> Dict[str, Any]:
    """Bounded liveness check — mirrors product_lineage._liveness_of + tenant guard."""
    if expected_tenant_id and tenant_id and expected_tenant_id != tenant_id:
        return {"ref": ref, "liveness": "rejected_cross_tenant", "freshness": EvidenceFreshness.MISSING.value}

    record = None
    if records is not None:
        record = records.get(ref)
    if record is not None:
        status = str(getattr(record, "status", None) or (record.get("status") if isinstance(record, dict) else "") or "")
        rec_tenant = str(
            getattr(record, "tenant_id", None)
            or (record.get("tenant_id") if isinstance(record, dict) else "")
            or ""
        )
        if expected_tenant_id and rec_tenant and rec_tenant != expected_tenant_id:
            return {"ref": ref, "liveness": "rejected_cross_tenant", "freshness": EvidenceFreshness.MISSING.value}
        return {
            "ref": ref,
            "liveness": status or "unknown",
            "freshness": _status_to_freshness(status).value,
        }
    if descriptors is not None and ref in descriptors:
        alive = descriptors.get(ref) is not None
        return {
            "ref": ref,
            "liveness": "alive" if alive else "expired",
            "freshness": EvidenceFreshness.FRESH.value if alive else EvidenceFreshness.EXPIRED.value,
        }
    return {"ref": ref, "liveness": "missing", "freshness": EvidenceFreshness.MISSING.value}
