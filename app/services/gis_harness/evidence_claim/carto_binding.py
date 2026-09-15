"""Cartographic claim binding: layer → style → breaks → metric → statistic → dataset."""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from .contracts import CartoBinding, RelationEdge, RelationType
from .census import project_mapspec_layer
from .store import ClaimStore


def bind_layer_to_statistic(
    *,
    layer_id: str,
    style_class: str,
    classification_breaks: Sequence[float],
    metric_field: str,
    metric_value: Optional[float],
    statistic_evidence_id: str,
    claim_id: str = "",
    dataset_version_ref: str = "",
    mapspec_revision: str = "",
    source_ref: str = "",
    tenant_id: str = "",
    session_id: str = "",
    store: Optional[ClaimStore] = None,
) -> CartoBinding:
    binding = CartoBinding(
        layer_id=layer_id[:64],
        style_class=style_class[:64],
        classification_breaks=[float(x) for x in list(classification_breaks)[:16]],
        metric_field=metric_field[:64],
        metric_value=metric_value,
        statistic_evidence_id=statistic_evidence_id[:64],
        claim_id=claim_id[:64],
        dataset_version_ref=dataset_version_ref[:128],
        mapspec_revision=mapspec_revision[:64],
        stale=False,
    )
    if store is not None:
        project_mapspec_layer(
            layer_id=layer_id,
            source_ref=source_ref,
            style_digest=f"{style_class}:{metric_field}:{mapspec_revision}"[:64],
            mapspec_revision=mapspec_revision,
            metric_field=metric_field,
            tenant_id=tenant_id,
            session_id=session_id,
            store=store,
        )
        store.add_edge(RelationEdge(
            edge_id=f"carto:{layer_id}:stat"[:64],
            relation=RelationType.DEPENDS_ON,
            src=f"layer:{layer_id}"[:64],
            dst=statistic_evidence_id[:64],
            metadata={"style_class": style_class[:32], "metric": metric_field[:32]},
        ))
        if claim_id:
            store.add_edge(RelationEdge(
                edge_id=f"carto:{layer_id}:claim"[:64],
                relation=RelationType.VISUALIZED_AS,
                src=claim_id[:64],
                dst=f"layer:{layer_id}"[:64],
            ))
        if dataset_version_ref:
            store.add_edge(RelationEdge(
                edge_id=f"carto:{layer_id}:ds"[:64],
                relation=RelationType.DERIVED_FROM,
                src=statistic_evidence_id[:64],
                dst=dataset_version_ref[:64],
            ))
    return binding


def explain_map_feature(
    binding: CartoBinding,
    *,
    current_mapspec_revision: str = "",
    current_breaks: Optional[Sequence[float]] = None,
) -> Dict[str, Any]:
    """Bounded explainability for 'why is this region red?' — technical evidence only."""
    stale = False
    reasons: List[str] = []
    if current_mapspec_revision and binding.mapspec_revision:
        if str(current_mapspec_revision) != str(binding.mapspec_revision):
            stale = True
            reasons.append("mapspec_revision_changed")
    if current_breaks is not None:
        cur = [float(x) for x in list(current_breaks)[:16]]
        if cur != list(binding.classification_breaks):
            stale = True
            reasons.append("classification_breaks_changed")

    chain = [
        {"step": "rendered_layer", "ref": binding.layer_id},
        {"step": "style_class", "ref": binding.style_class},
        {"step": "classification_breaks", "ref": list(binding.classification_breaks)},
        {"step": "metric_field", "ref": binding.metric_field, "value": binding.metric_value},
        {"step": "statistic", "ref": binding.statistic_evidence_id},
        {"step": "claim", "ref": binding.claim_id},
        {"step": "dataset_version", "ref": binding.dataset_version_ref},
    ]
    return {
        "binding": binding.to_bounded_dict(),
        "chain": chain,
        "stale": stale or binding.stale,
        "reasons": reasons[:8],
    }


def invalidate_binding_on_break_change(
    binding: CartoBinding,
    new_breaks: Sequence[float],
) -> CartoBinding:
    cur = [float(x) for x in list(new_breaks)[:16]]
    if cur != list(binding.classification_breaks):
        return binding.model_copy(update={"stale": True, "classification_breaks": cur})
    return binding
