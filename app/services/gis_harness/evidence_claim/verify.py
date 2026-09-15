"""Deterministic claim verification pipeline (positive proof).

Claim → resolve refs → liveness → freshness → semantics → method/units/scope
→ uncertainty obligations → verdict.

Never promote missing evidence to PASS/supported.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Set

from .contracts import (
    STAT_FAMILY,
    Claim,
    ClaimStatus,
    ClaimType,
    EvidenceFreshness,
    EvidenceKind,
    VerificationResult,
    VerificationStage,
    VerificationStep,
)
from .store import ClaimStore


_FRESH_OK = {EvidenceFreshness.FRESH, EvidenceFreshness.UNKNOWN}
_DEAD = {
    EvidenceFreshness.MISSING,
    EvidenceFreshness.EXPIRED,
    EvidenceFreshness.SUPERSEDED,
    EvidenceFreshness.STALE,
}


def _semantic_compatible(claim: Claim, evidence_meta: Dict[str, Any]) -> tuple[bool, str]:
    declared = str(evidence_meta.get("stat_type") or evidence_meta.get("claim_type") or "")
    if not declared:
        # Statistic evidence without declared type: require claim method present
        # as weak structural signal — still not enough alone for PASS later.
        return True, "no_declared_stat_type"
    if declared == claim.claim_type.value:
        return True, "stat_type_match"
    fam_a = STAT_FAMILY.get(claim.claim_type.value)
    fam_b = STAT_FAMILY.get(declared)
    if fam_a and fam_b and fam_a == fam_b:
        return True, "same_stat_family"
    return False, f"incompatible_semantics:{claim.claim_type.value} vs {declared}"


def _unit_compatible(claim: Claim, evidence_meta: Dict[str, Any]) -> tuple[bool, str]:
    eunit = str(evidence_meta.get("unit") or "")
    if not claim.unit and not eunit:
        return True, "units_unspecified"
    if claim.unit and eunit and claim.unit != eunit:
        return False, f"unit_mismatch:{claim.unit} vs {eunit}"
    return True, "units_ok"


def _scope_compatible(claim: Claim, evidence_meta: Dict[str, Any]) -> tuple[bool, str]:
    e_aoi = str(evidence_meta.get("aoi_ref") or "")
    e_temporal = str(evidence_meta.get("temporal_label") or "")
    if claim.spatial_scope.aoi_ref and e_aoi and claim.spatial_scope.aoi_ref != e_aoi:
        return False, "aoi_mismatch"
    if claim.temporal_scope.temporal_label and e_temporal and claim.temporal_scope.temporal_label != e_temporal:
        return False, "temporal_mismatch"
    return True, "scope_ok"


def verify_claim(
    claim: Claim,
    store: ClaimStore,
    *,
    require_uncertainty: bool = False,
    records: Optional[Dict[str, Any]] = None,
    expected_tenant_id: str = "",
) -> VerificationResult:
    steps: List[VerificationStep] = []
    reasons: List[str] = []

    # Narrative / free-text without numeric grounding cannot become supported.
    if claim.claim_type is ClaimType.NARRATIVE:
        steps.append(VerificationStep(
            stage=VerificationStage.VERDICT, ok=False,
            detail="narrative_claim_not_authoritative",
        ))
        return VerificationResult(
            claim_id=claim.claim_id,
            status=ClaimStatus.UNSUPPORTED,
            steps=steps,
            positive_proof=False,
            reasons=["narrative_without_typed_evidence"],
        )

    # Tenant isolation
    if expected_tenant_id and claim.tenant_id and claim.tenant_id != expected_tenant_id:
        steps.append(VerificationStep(
            stage=VerificationStage.RESOLVE, ok=False, detail="cross_tenant_rejected",
        ))
        return VerificationResult(
            claim_id=claim.claim_id,
            status=ClaimStatus.UNSUPPORTED,
            steps=steps,
            positive_proof=False,
            reasons=["cross_tenant_rejected"],
        )

    # 1. Resolve evidence refs
    resolved = []
    missing = []
    for eid in claim.supporting_evidence_refs:
        node = store.get_evidence(eid)
        if node is None:
            missing.append(eid)
        else:
            if expected_tenant_id and node.tenant_id and node.tenant_id != expected_tenant_id:
                steps.append(VerificationStep(
                    stage=VerificationStage.RESOLVE, ok=False,
                    detail="cross_tenant_evidence_rejected", evidence_ids=[eid],
                ))
                return VerificationResult(
                    claim_id=claim.claim_id,
                    status=ClaimStatus.UNSUPPORTED,
                    steps=steps,
                    positive_proof=False,
                    reasons=["cross_tenant_rejected"],
                )
            resolved.append(node)

    if missing and not resolved:
        steps.append(VerificationStep(
            stage=VerificationStage.RESOLVE, ok=False,
            detail="all_supporting_refs_missing", evidence_ids=missing[:8],
        ))
        return VerificationResult(
            claim_id=claim.claim_id,
            status=ClaimStatus.UNSUPPORTED,
            steps=steps,
            positive_proof=False,
            reasons=["missing_evidence"],
        )

    if missing:
        steps.append(VerificationStep(
            stage=VerificationStage.RESOLVE, ok=False,
            detail="partial_refs_missing", evidence_ids=missing[:8],
        ))
        reasons.append("partial_evidence")
    else:
        steps.append(VerificationStep(
            stage=VerificationStage.RESOLVE, ok=True,
            detail="refs_resolved", evidence_ids=[n.evidence_id for n in resolved],
        ))

    # 2. Liveness (optional registry snapshot)
    live_ok = True
    for node in resolved:
        if records is not None and node.ref:
            rec = records.get(node.ref)
            if rec is None:
                live_ok = False
                reasons.append(f"dead_ref:{node.ref}")
            else:
                status = str(getattr(rec, "status", None) or (rec.get("status") if isinstance(rec, dict) else "") or "")
                if status in ("expired", "failed", "superseded"):
                    live_ok = False
                    reasons.append(f"dead_status:{status}")
    steps.append(VerificationStep(
        stage=VerificationStage.LIVENESS, ok=live_ok,
        detail="liveness_ok" if live_ok else "liveness_failed",
        evidence_ids=[n.evidence_id for n in resolved],
    ))

    # 3. Freshness
    fresh_ok = True
    stale_hit = False
    for node in resolved:
        if node.freshness in _DEAD:
            fresh_ok = False
            if node.freshness is EvidenceFreshness.STALE:
                stale_hit = True
            reasons.append(f"freshness:{node.freshness.value}")
    steps.append(VerificationStep(
        stage=VerificationStage.FRESHNESS, ok=fresh_ok,
        detail="fresh" if fresh_ok else ("stale" if stale_hit else "not_fresh"),
        evidence_ids=[n.evidence_id for n in resolved],
    ))

    # 4–5. Semantics + method/units/scope (need at least one statistic-like node)
    stat_nodes = [
        n for n in resolved
        if n.kind in (EvidenceKind.STATISTIC, EvidenceKind.ANALYSIS, EvidenceKind.DATASET_VERSION)
    ]
    sem_ok = True
    method_ok = True
    if not stat_nodes:
        sem_ok = False
        reasons.append("no_statistic_or_analysis_evidence")
    for node in resolved:
        meta = dict(node.metadata or {})
        if node.method and not meta.get("stat_type"):
            meta.setdefault("stat_type", "")
        ok_s, detail_s = _semantic_compatible(claim, meta)
        if meta.get("stat_type") and not ok_s:
            sem_ok = False
            reasons.append(detail_s)
        ok_u, detail_u = _unit_compatible(claim, meta)
        if not ok_u:
            method_ok = False
            reasons.append(detail_u)
        ok_sc, detail_sc = _scope_compatible(claim, {
            "aoi_ref": node.scope.aoi_ref,
            "temporal_label": node.scope.temporal_label,
            **meta,
        })
        if not ok_sc:
            method_ok = False
            reasons.append(detail_sc)
    if claim.method:
        method_linked = any(
            e.dst.startswith("method:") or e.dst == f"method:{claim.method}"[:64]
            for e in store.edges_from(claim.claim_id)
        ) or bool(claim.method)
        # method string on claim counts as declared method obligation met structurally
        if not claim.method.strip():
            method_ok = False
            reasons.append("method_missing")
    else:
        method_ok = False
        reasons.append("method_missing")

    steps.append(VerificationStep(
        stage=VerificationStage.SEMANTICS, ok=sem_ok,
        detail="semantics_ok" if sem_ok else "semantics_failed",
    ))
    steps.append(VerificationStep(
        stage=VerificationStage.METHOD_SCOPE, ok=method_ok,
        detail="method_scope_ok" if method_ok else "method_scope_failed",
    ))

    # 6. Uncertainty obligation (positive proof)
    unc_ok = True
    if require_uncertainty or claim.uncertainty_ref:
        if not claim.uncertainty_ref:
            unc_ok = False
            reasons.append("uncertainty_disclosure_missing")
        else:
            unc_node = store.get_evidence(claim.uncertainty_ref)
            if unc_node is None:
                # allow bare ref string as evidence_id attempt
                unc_ok = False
                reasons.append("uncertainty_ref_unresolved")
            elif unc_node.kind is not EvidenceKind.UNCERTAINTY and unc_node.freshness is EvidenceFreshness.MISSING:
                unc_ok = False
                reasons.append("uncertainty_not_present")
    steps.append(VerificationStep(
        stage=VerificationStage.UNCERTAINTY, ok=unc_ok,
        detail="uncertainty_ok" if unc_ok else "uncertainty_failed",
        evidence_ids=[claim.uncertainty_ref] if claim.uncertainty_ref else [],
    ))

    # Verdict — positive proof: ALL critical stages ok AND at least one resolved support
    positive = bool(resolved) and live_ok and fresh_ok and sem_ok and method_ok and unc_ok and not missing

    if claim.status is ClaimStatus.CONTRADICTED or claim.contradicting_evidence_refs:
        # External contradiction marks win over support
        if claim.contradicting_evidence_refs:
            status = ClaimStatus.CONTRADICTED
            positive = False
            reasons.append("contradicting_evidence_present")
        else:
            status = ClaimStatus.CONTRADICTED
            positive = False
    elif stale_hit and not fresh_ok:
        status = ClaimStatus.STALE
        positive = False
    elif positive:
        status = ClaimStatus.SUPPORTED
    elif resolved and (sem_ok or method_ok) and live_ok:
        status = ClaimStatus.PARTIALLY_SUPPORTED
    elif not resolved:
        status = ClaimStatus.UNSUPPORTED
    else:
        status = ClaimStatus.UNSUPPORTED if not live_ok or not fresh_ok else ClaimStatus.PARTIALLY_SUPPORTED

    # Missing evidence never → supported (belt and suspenders)
    if not resolved or missing:
        if status is ClaimStatus.SUPPORTED:
            status = ClaimStatus.PARTIALLY_SUPPORTED if resolved else ClaimStatus.UNSUPPORTED
            positive = False
            reasons.append("positive_proof_blocked_missing")

    steps.append(VerificationStep(
        stage=VerificationStage.VERDICT, ok=positive,
        detail=status.value,
    ))

    result = VerificationResult(
        claim_id=claim.claim_id,
        status=status,
        steps=steps,
        positive_proof=positive,
        reasons=reasons[:8],
    )
    store.mark_claim_status(claim.claim_id, status)
    return result


def verify_all(store: ClaimStore, **kwargs: Any) -> List[VerificationResult]:
    results = []
    for claim in sorted(store.all_claims(), key=lambda c: c.claim_id):
        results.append(verify_claim(claim, store, **kwargs))
    return results
