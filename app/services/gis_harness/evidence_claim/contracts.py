"""Spatial Evidence / Claim / Provenance Graph contracts (Direction 03).

Projection layer over authoritative stores (ArtifactRegistry, ProductGraph,
data fabric pins, MapSpec revisions). Persist only durable claim identity and
lightweight evidence stubs — never GIS payloads.

Positive-proof invariant: missing evidence is NEVER promoted to PASS/supported.
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

SCHEMA_VERSION = "evidence_claim.v1"

# ── Bounds (GOAL §22) ────────────────────────────────────────────────────
MAX_EVIDENCE_NODES = 4096
MAX_CLAIMS = 2048
MAX_EDGES = 10000
MAX_SUPPORTING_REFS = 16
MAX_TEXT = 200
MAX_REF = 128
MAX_TRAVERSAL_DEPTH = 12
MAX_TRAVERSAL_NODES = 256
MAX_GROUNDING_EVIDENCE = 12


class EvidenceKind(str, Enum):
    DATASET = "dataset"
    DATASET_VERSION = "dataset_version"
    SPATIAL_SUBSET = "spatial_subset"
    TRANSFORMATION = "transformation"
    ANALYSIS = "analysis"
    STATISTIC = "statistic"
    SPATIAL_RELATION = "spatial_relation"
    ARTIFACT = "artifact"
    MAP_LAYER = "map_layer"
    MAP_VIEW = "map_view"
    CHART = "chart"
    SIMULATION = "simulation"
    OBSERVATION = "observation"
    USER_ASSERTION = "user_assertion"
    EXTERNAL_SOURCE = "external_source"
    METHOD = "method"
    UNCERTAINTY = "uncertainty"
    PRODUCT_FACET = "product_facet"


class EvidenceFreshness(str, Enum):
    FRESH = "fresh"
    STALE = "stale"
    SUPERSEDED = "superseded"
    EXPIRED = "expired"
    UNKNOWN = "unknown"
    MISSING = "missing"


class ClaimType(str, Enum):
    COUNT = "count"
    SUM = "sum"
    MEAN = "mean"
    MEDIAN = "median"
    DENSITY = "density"
    RATE = "rate"
    RATIO = "ratio"
    SHARE = "share"
    PERCENTAGE = "percentage"
    RANK = "rank"
    MAXIMUM = "maximum"
    MINIMUM = "minimum"
    CHANGE = "change"
    TREND = "trend"
    DIFFERENCE = "difference"
    SPATIAL_RELATION = "spatial_relation"
    COVERAGE = "coverage"
    RISK = "risk"
    SCORE = "score"
    NARRATIVE = "narrative"  # free-text; never auto-verified as numeric


class ClaimStatus(str, Enum):
    SUPPORTED = "supported"
    PARTIALLY_SUPPORTED = "partially_supported"
    UNSUPPORTED = "unsupported"
    CONTRADICTED = "contradicted"
    STALE = "stale"
    UNKNOWN = "unknown"


class RelationType(str, Enum):
    DERIVED_FROM = "derived_from"
    COMPUTED_BY = "computed_by"
    AGGREGATED_FROM = "aggregated_from"
    FILTERED_FROM = "filtered_from"
    SUPPORTS = "supports"
    CONTRADICTS = "contradicts"
    VISUALIZED_AS = "visualized_as"
    SUMMARIZED_BY = "summarized_by"
    SUPERSEDES = "supersedes"
    INVALIDATES = "invalidates"
    DEPENDS_ON = "depends_on"


class VerificationStage(str, Enum):
    RESOLVE = "resolve"
    LIVENESS = "liveness"
    FRESHNESS = "freshness"
    SEMANTICS = "semantics"
    METHOD_SCOPE = "method_scope"
    UNCERTAINTY = "uncertainty"
    VERDICT = "verdict"


# Semantic families — incompatible across families without extra evidence.
STAT_FAMILY: Dict[str, str] = {
    ClaimType.COUNT.value: "cardinality",
    ClaimType.SUM.value: "cardinality",
    ClaimType.MEAN.value: "central",
    ClaimType.MEDIAN.value: "central",
    ClaimType.DENSITY.value: "intensity",
    ClaimType.RATE.value: "intensity",
    ClaimType.RATIO.value: "relative",
    ClaimType.SHARE.value: "relative",
    ClaimType.PERCENTAGE.value: "relative",
    ClaimType.RANK.value: "order",
    ClaimType.MAXIMUM.value: "order",
    ClaimType.MINIMUM.value: "order",
    ClaimType.CHANGE.value: "delta",
    ClaimType.TREND.value: "delta",
    ClaimType.DIFFERENCE.value: "delta",
    ClaimType.SPATIAL_RELATION.value: "spatial",
    ClaimType.COVERAGE.value: "spatial",
    ClaimType.RISK.value: "score",
    ClaimType.SCORE.value: "score",
    ClaimType.NARRATIVE.value: "prose",
}


class Scope(BaseModel):
    """Spatial / temporal / filter scope (bounded, no geometry payloads)."""

    spatial_name: str = ""
    spatial_level: str = ""
    aoi_ref: str = ""
    temporal_start: str = ""
    temporal_end: str = ""
    temporal_label: str = ""
    filter_digest: str = ""
    group_by: str = ""

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "spatial_name": self.spatial_name[:64],
            "spatial_level": self.spatial_level[:32],
            "aoi_ref": self.aoi_ref[:MAX_REF],
            "temporal_start": self.temporal_start[:32],
            "temporal_end": self.temporal_end[:32],
            "temporal_label": self.temporal_label[:64],
            "filter_digest": self.filter_digest[:64],
            "group_by": self.group_by[:48],
        }

    def overlaps(self, other: "Scope") -> bool:
        """Conservative overlap: empty fields are wildcards."""
        if self.spatial_name and other.spatial_name and self.spatial_name != other.spatial_name:
            if self.aoi_ref and other.aoi_ref and self.aoi_ref != other.aoi_ref:
                return False
            if not self.aoi_ref and not other.aoi_ref:
                return False
        if self.aoi_ref and other.aoi_ref and self.aoi_ref != other.aoi_ref:
            return False
        if self.temporal_label and other.temporal_label and self.temporal_label != other.temporal_label:
            return False
        if self.filter_digest and other.filter_digest and self.filter_digest != other.filter_digest:
            return False
        if self.group_by and other.group_by and self.group_by != other.group_by:
            return False
        return True


class EvidenceNode(BaseModel):
    """Lightweight evidence stub — refs authoritative objects, no heavy payload."""

    evidence_id: str
    kind: EvidenceKind
    ref: str = ""
    producer: str = ""
    version: str = ""
    revision: str = ""
    scope: Scope = Field(default_factory=Scope)
    method: str = ""
    uncertainty_ref: str = ""
    freshness: EvidenceFreshness = EvidenceFreshness.UNKNOWN
    tenant_id: str = ""
    session_id: str = ""
    metadata: Dict[str, Any] = Field(default_factory=dict)

    def to_bounded_dict(self) -> Dict[str, Any]:
        meta = {str(k)[:48]: str(v)[:MAX_TEXT] for k, v in list(self.metadata.items())[:12]}
        return {
            "evidence_id": self.evidence_id[:64],
            "kind": self.kind.value,
            "ref": self.ref[:MAX_REF],
            "producer": self.producer[:64],
            "version": self.version[:64],
            "revision": self.revision[:64],
            "scope": self.scope.to_bounded_dict(),
            "method": self.method[:64],
            "uncertainty_ref": self.uncertainty_ref[:MAX_REF],
            "freshness": self.freshness.value,
            "tenant_id": self.tenant_id[:64],
            "session_id": self.session_id[:64],
            "metadata": meta,
        }


class Claim(BaseModel):
    """Typed spatial/analytical claim — numbers resolve to statistic evidence."""

    claim_id: str
    claim_type: ClaimType
    subject: str = ""
    predicate: str = ""
    value: Optional[float] = None
    value_text: str = ""
    unit: str = ""
    comparator: str = ""  # e.g. highest / eq / gt
    reference_scope: Scope = Field(default_factory=Scope)
    temporal_scope: Scope = Field(default_factory=Scope)
    spatial_scope: Scope = Field(default_factory=Scope)
    method: str = ""
    supporting_evidence_refs: List[str] = Field(default_factory=list, max_length=MAX_SUPPORTING_REFS)
    contradicting_evidence_refs: List[str] = Field(default_factory=list, max_length=MAX_SUPPORTING_REFS)
    confidence: Optional[float] = None
    uncertainty_ref: str = ""
    status: ClaimStatus = ClaimStatus.UNKNOWN
    tenant_id: str = ""
    session_id: str = ""
    narrative: str = ""  # descriptive only; never authoritative for numbers
    product_facet_id: str = ""
    map_layer_id: str = ""

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "claim_id": self.claim_id[:64],
            "claim_type": self.claim_type.value,
            "subject": self.subject[:MAX_TEXT],
            "predicate": self.predicate[:64],
            "value": self.value,
            "value_text": self.value_text[:MAX_TEXT],
            "unit": self.unit[:32],
            "comparator": self.comparator[:32],
            "reference_scope": self.reference_scope.to_bounded_dict(),
            "temporal_scope": self.temporal_scope.to_bounded_dict(),
            "spatial_scope": self.spatial_scope.to_bounded_dict(),
            "method": self.method[:64],
            "supporting_evidence_refs": [r[:MAX_REF] for r in self.supporting_evidence_refs[:MAX_SUPPORTING_REFS]],
            "contradicting_evidence_refs": [
                r[:MAX_REF] for r in self.contradicting_evidence_refs[:MAX_SUPPORTING_REFS]
            ],
            "confidence": self.confidence,
            "uncertainty_ref": self.uncertainty_ref[:MAX_REF],
            "status": self.status.value,
            "tenant_id": self.tenant_id[:64],
            "session_id": self.session_id[:64],
            "narrative": self.narrative[:MAX_TEXT],
            "product_facet_id": self.product_facet_id[:64],
            "map_layer_id": self.map_layer_id[:64],
        }


class RelationEdge(BaseModel):
    edge_id: str
    relation: RelationType
    src: str
    dst: str
    metadata: Dict[str, str] = Field(default_factory=dict)

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "edge_id": self.edge_id[:64],
            "relation": self.relation.value,
            "src": self.src[:64],
            "dst": self.dst[:64],
            "metadata": {str(k)[:32]: str(v)[:64] for k, v in list(self.metadata.items())[:8]},
        }


class VerificationStep(BaseModel):
    stage: VerificationStage
    ok: bool
    detail: str = ""
    evidence_ids: List[str] = Field(default_factory=list)

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "stage": self.stage.value,
            "ok": self.ok,
            "detail": self.detail[:MAX_TEXT],
            "evidence_ids": [e[:64] for e in self.evidence_ids[:8]],
        }


class VerificationResult(BaseModel):
    claim_id: str
    status: ClaimStatus
    steps: List[VerificationStep] = Field(default_factory=list)
    positive_proof: bool = False
    reasons: List[str] = Field(default_factory=list)

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "claim_id": self.claim_id[:64],
            "status": self.status.value,
            "steps": [s.to_bounded_dict() for s in self.steps[:16]],
            "positive_proof": self.positive_proof,
            "reasons": [r[:MAX_TEXT] for r in self.reasons[:8]],
        }


class ContradictionResult(BaseModel):
    contradiction_id: str
    claim_ids: List[str]
    kind: str  # hard | scoped_divergence
    detail: str = ""
    inspected: Dict[str, str] = Field(default_factory=dict)

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "contradiction_id": self.contradiction_id[:64],
            "claim_ids": [c[:64] for c in self.claim_ids[:8]],
            "kind": self.kind[:32],
            "detail": self.detail[:MAX_TEXT],
            "inspected": {str(k)[:32]: str(v)[:64] for k, v in list(self.inspected.items())[:12]},
        }


class CartoBinding(BaseModel):
    """layer → style/class → break → metric → statistic → dataset version."""

    layer_id: str
    style_class: str = ""
    classification_breaks: List[float] = Field(default_factory=list)
    metric_field: str = ""
    metric_value: Optional[float] = None
    statistic_evidence_id: str = ""
    claim_id: str = ""
    dataset_version_ref: str = ""
    mapspec_revision: str = ""
    stale: bool = False

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "layer_id": self.layer_id[:64],
            "style_class": self.style_class[:64],
            "classification_breaks": self.classification_breaks[:16],
            "metric_field": self.metric_field[:64],
            "metric_value": self.metric_value,
            "statistic_evidence_id": self.statistic_evidence_id[:64],
            "claim_id": self.claim_id[:64],
            "dataset_version_ref": self.dataset_version_ref[:MAX_REF],
            "mapspec_revision": self.mapspec_revision[:64],
            "stale": self.stale,
        }
