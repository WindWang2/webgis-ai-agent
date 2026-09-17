"""Analysis/product/map settle → EvidenceNode + typed Claim projection.

Fail-closed: never invent SUPPORTED; missing evidence → no PASS.
Uses existing evidence_claim projectors only.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence

from app.services.gis_harness.hotpath_convergence.flags import claim_ingest_enabled


@dataclass
class ClaimIngestReport:
    enabled: bool = True
    evidence_ids: List[str] = field(default_factory=list)
    claim_ids: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    skipped_reason: str = ""
    # Ingest never auto-verifies to SUPPORTED; callers may verify separately.
    statuses: List[str] = field(default_factory=list)

    @property
    def any_supported(self) -> bool:
        return any(s == "supported" for s in self.statuses)

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "enabled": self.enabled,
            "evidence_ids": [e[:64] for e in self.evidence_ids[:16]],
            "claim_ids": [c[:64] for c in self.claim_ids[:16]],
            "errors": [e[:120] for e in self.errors[:8]],
            "skipped_reason": self.skipped_reason[:120],
            "statuses": [s[:32] for s in self.statuses[:16]],
            "any_supported": self.any_supported,
        }


def ingest_on_settle(
    store: Any,
    *,
    artifacts: Optional[Iterable[Any]] = None,
    rank_rows: Optional[Sequence[Dict[str, Any]]] = None,
    rank_meta: Optional[Dict[str, Any]] = None,
    product_facets: Optional[Iterable[Any]] = None,
    mapspec_layers: Optional[Iterable[Dict[str, Any]]] = None,
    goal_evidence_items: Optional[Iterable[Any]] = None,
    tenant_id: str = "",
    session_id: str = "",
) -> ClaimIngestReport:
    """Project settle outputs into ClaimStore. Fail-closed; no SUPPORTED invent."""
    report = ClaimIngestReport()
    if not claim_ingest_enabled():
        report.enabled = False
        report.skipped_reason = "GIS_CLAIM_INGEST disabled"
        return report
    if store is None:
        report.enabled = False
        report.skipped_reason = "no_claim_store"
        report.errors.append("no_claim_store")
        return report

    from app.services.gis_harness.evidence_claim.census import (
        project_artifact_record,
        project_goal_evidence,
        project_mapspec_layer,
        project_product_facet,
    )
    from app.services.gis_harness.evidence_claim.claims import (
        claim_from_rank_table,
        claim_from_statistic,
    )
    from app.services.gis_harness.evidence_claim.contracts import ClaimStatus

    tid = str(tenant_id or "")[:64]
    sid = str(session_id or "")[:64]
    statistic_evidence_id = ""

    try:
        for rec in list(artifacts or [])[:64]:
            try:
                node = project_artifact_record(rec, tenant_id=tid, session_id=sid)
                store.upsert_evidence(node)
                report.evidence_ids.append(node.evidence_id)
                atype = ""
                meta = getattr(rec, "metadata", None) or {}
                if isinstance(meta, dict):
                    atype = str(meta.get("artifact_type") or "")
                atype = atype or str(getattr(rec, "artifact_type", "") or "")
                if atype in (
                    "stats_table", "admin_aggregate_table", "grid_aggregate", "od_matrix",
                ) and not statistic_evidence_id:
                    statistic_evidence_id = node.evidence_id
                    # Optional typed claim when numeric value present in metadata.
                    value = meta.get("value") if isinstance(meta, dict) else None
                    if value is not None:
                        try:
                            claim = claim_from_statistic(
                                subject=str(meta.get("subject") or "unknown")[:200],
                                claim_type=str(meta.get("stat_type") or "count"),
                                value=float(value),
                                unit=str(meta.get("unit") or "")[:32],
                                method=str(meta.get("method") or "")[:64],
                                statistic_evidence_id=node.evidence_id,
                                tenant_id=tid,
                                session_id=sid,
                                store=store,
                            )
                            report.claim_ids.append(claim.claim_id)
                            report.statuses.append(
                                getattr(claim.status, "value", str(claim.status))
                            )
                            if claim.status == ClaimStatus.SUPPORTED:
                                # Fail-closed: projector must not invent SUPPORTED.
                                report.errors.append("unexpected_supported_at_ingest")
                                claim.status = ClaimStatus.UNKNOWN
                                store.upsert_claim(claim)
                                report.statuses[-1] = ClaimStatus.UNKNOWN.value
                        except Exception as exc:  # noqa: BLE001
                            report.errors.append(
                                f"statistic_claim:{type(exc).__name__}"[:120]
                            )
            except Exception as exc:  # noqa: BLE001
                report.errors.append(f"artifact:{type(exc).__name__}"[:120])

        for facet in list(product_facets or [])[:32]:
            try:
                node = project_product_facet(
                    facet, tenant_id=tid, session_id=sid, store=store,
                )
                report.evidence_ids.append(node.evidence_id)
            except Exception as exc:  # noqa: BLE001
                report.errors.append(f"facet:{type(exc).__name__}"[:120])

        for layer in list(mapspec_layers or [])[:32]:
            try:
                if not isinstance(layer, dict):
                    continue
                lid = str(layer.get("layer_id") or layer.get("id") or "")
                if not lid:
                    continue
                node = project_mapspec_layer(
                    layer_id=lid,
                    source_ref=str(layer.get("source_ref") or "")[:128],
                    style_digest=str(layer.get("style_digest") or "")[:64],
                    mapspec_revision=str(layer.get("mapspec_revision") or "")[:64],
                    metric_field=str(layer.get("metric_field") or "")[:64],
                    tenant_id=tid,
                    session_id=sid,
                    store=store,
                )
                report.evidence_ids.append(node.evidence_id)
            except Exception as exc:  # noqa: BLE001
                report.errors.append(f"layer:{type(exc).__name__}"[:120])

        for ge in list(goal_evidence_items or [])[:32]:
            try:
                node = project_goal_evidence(
                    ge, tenant_id=tid, session_id=sid, store=store,
                )
                report.evidence_ids.append(node.evidence_id)
            except Exception as exc:  # noqa: BLE001
                report.errors.append(f"goal_evidence:{type(exc).__name__}"[:120])

        if rank_rows:
            meta = dict(rank_meta or {})
            # Positive-proof: require an explicit statistic evidence id.
            seid = str(meta.get("statistic_evidence_id") or statistic_evidence_id or "")
            if not seid:
                report.errors.append("rank_claim_missing_statistic_evidence")
            else:
                try:
                    from app.services.gis_harness.evidence_claim.contracts import ClaimType
                    ct_raw = meta.get("claim_type")
                    # Fail-closed: never invent ClaimType.DENSITY for missing claim_type.
                    if ct_raw is None or ct_raw == "":
                        report.errors.append("rank_claim_missing_claim_type")
                    else:
                        if isinstance(ct_raw, ClaimType):
                            ct = ct_raw
                        else:
                            ct = ClaimType(str(ct_raw))
                        claim = claim_from_rank_table(
                            list(rank_rows)[:64],
                            subject_key=str(meta.get("subject_key") or "name"),
                            value_key=str(meta.get("value_key") or "value"),
                            claim_type=ct,
                            unit=str(meta.get("unit") or ""),
                            method=str(meta.get("method") or ""),
                            statistic_evidence_id=seid,
                            dataset_version_evidence_id=str(
                                meta.get("dataset_version_evidence_id") or ""
                            ),
                            spatial_level=str(meta.get("spatial_level") or "district"),
                            group_by=str(meta.get("group_by") or "district"),
                            temporal_label=str(meta.get("temporal_label") or ""),
                            tenant_id=tid,
                            session_id=sid,
                            store=store,
                        )
                        if claim is not None:
                            report.claim_ids.append(claim.claim_id)
                            status = getattr(claim.status, "value", str(claim.status))
                            report.statuses.append(status)
                            if status == "supported":
                                report.errors.append("unexpected_supported_at_ingest")
                                from app.services.gis_harness.evidence_claim.contracts import (
                                    ClaimStatus as CS,
                                )
                                claim.status = CS.UNKNOWN
                                store.upsert_claim(claim)
                                report.statuses[-1] = CS.UNKNOWN.value
                except Exception as exc:  # noqa: BLE001
                    report.errors.append(f"rank_claim:{type(exc).__name__}"[:120])
    except Exception as exc:  # noqa: BLE001 — top-level fail-closed
        report.errors.append(f"ingest_failed:{type(exc).__name__}"[:120])

    return report


def ingest_map_product_settle(
    map_product: Dict[str, Any],
    *,
    store: Any = None,
    chapter: Optional[Dict[str, Any]] = None,
    tenant_id: str = "",
    session_id: str = "",
) -> ClaimIngestReport:
    """Convenience: project map_product / chapter settle surfaces into ClaimStore."""
    if not claim_ingest_enabled():
        return ClaimIngestReport(
            enabled=False, skipped_reason="GIS_CLAIM_INGEST disabled",
        )
    if store is None:
        from app.services.gis_harness.hotpath_convergence.session_ctx import (
            get_or_create_claim_store,
        )
        store = get_or_create_claim_store(session_id, tenant_id=tenant_id)

    facets = []
    layers = []
    goal_items = []
    if isinstance(map_product, dict):
        facets = list(map_product.get("product_facets") or [])[:16]
        layers = list(map_product.get("layers") or map_product.get("map_layers") or [])[:16]
        gs = map_product.get("goal_satisfaction")
        if isinstance(gs, dict):
            for ev in list(gs.get("evidence") or [])[:16]:
                goal_items.append(ev)
    if isinstance(chapter, dict) and not facets:
        facets = list((chapter.get("product_facets") or []))[:16]

    return ingest_on_settle(
        store,
        product_facets=facets,
        mapspec_layers=layers,
        goal_evidence_items=goal_items,
        tenant_id=tenant_id,
        session_id=session_id,
    )


__all__ = [
    "ClaimIngestReport",
    "ingest_map_product_settle",
    "ingest_on_settle",
]
