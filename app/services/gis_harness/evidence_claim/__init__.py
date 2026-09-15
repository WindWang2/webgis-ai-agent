"""Spatial Evidence / Claim / Provenance Graph (Direction 03).

existing authoritative refs → Evidence projection/index → Claim graph

Not a second Artifact Registry / Provenance DB / Replay / Product Lineage.
"""
from .carto_binding import (
    bind_layer_to_statistic,
    explain_map_feature,
    invalidate_binding_on_break_change,
)
from .census import (
    project_artifact_record,
    project_artifact_registry,
    project_dataset_version,
    project_goal_evidence,
    project_mapspec_layer,
    project_product_facet,
    resolve_live_ref,
)
from .claims import claim_from_rank_table, claim_from_statistic, narrative_claim_unverified
from .contracts import (
    SCHEMA_VERSION,
    STAT_FAMILY,
    Claim,
    ClaimStatus,
    ClaimType,
    ContradictionResult,
    CartoBinding,
    EvidenceFreshness,
    EvidenceKind,
    EvidenceNode,
    RelationEdge,
    RelationType,
    Scope,
    VerificationResult,
    VerificationStage,
)
from .contradiction import detect_contradictions
from .freshness import (
    affected_descendants,
    invalidate_affected_claims,
    mark_evidence_stale,
    mark_evidence_superseded,
)
from .graph import RelationGraph, TraversalResult
from .grounding import answer_why_highest_density, grounding_projection
from .narrative import ClaimNarrativeProjection
from .query import (
    affected_claims,
    claims_for_artifact,
    contradictions,
    evidence_for,
    why_claim,
    why_map_feature,
)
from .store import ClaimStore
from .verify import verify_all, verify_claim

__all__ = [
    "SCHEMA_VERSION",
    "STAT_FAMILY",
    "Claim",
    "ClaimStatus",
    "ClaimStore",
    "ClaimType",
    "ClaimNarrativeProjection",
    "ContradictionResult",
    "CartoBinding",
    "EvidenceFreshness",
    "EvidenceKind",
    "EvidenceNode",
    "RelationEdge",
    "RelationGraph",
    "RelationType",
    "Scope",
    "TraversalResult",
    "VerificationResult",
    "VerificationStage",
    "affected_claims",
    "affected_descendants",
    "answer_why_highest_density",
    "bind_layer_to_statistic",
    "claim_from_rank_table",
    "claim_from_statistic",
    "claims_for_artifact",
    "contradictions",
    "detect_contradictions",
    "evidence_for",
    "explain_map_feature",
    "grounding_projection",
    "invalidate_affected_claims",
    "invalidate_binding_on_break_change",
    "mark_evidence_stale",
    "mark_evidence_superseded",
    "narrative_claim_unverified",
    "project_artifact_record",
    "project_artifact_registry",
    "project_dataset_version",
    "project_goal_evidence",
    "project_mapspec_layer",
    "project_product_facet",
    "resolve_live_ref",
    "verify_all",
    "verify_claim",
    "why_claim",
    "why_map_feature",
]
