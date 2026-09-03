"""
Evidence Hardening, Temporal Validity, and Rule Conflict Detection Engine for Spatial Decision Intelligence V3.
Detects contradictory regulations, checks temporal expiration, evaluates evidence credibility,
and prevents silent averaging of conflicting empirical observations.
"""
import logging
from typing import Any, Dict, List, Optional, Tuple
from datetime import datetime

from app.services.spatial_decision.models import DomainRule, EvidenceItem

logger = logging.getLogger(__name__)

# Credibility weights by source classification
SOURCE_TYPE_CREDIBILITY = {
    "observed_fact": 1.0,
    "computed_fact": 0.92,
    "retrieved_rule": 0.88,
    "expert_model": 0.80,
    "inference": 0.65,
    "assumption": 0.40,
}


def detect_rule_conflicts(
    rules: List[DomainRule],
    reference_date: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Detects semantic, parameter, and temporal conflicts among domain rules.
    Does NOT silently resolve conflicts; returns detailed audit evidence.
    """
    conflicts: List[Dict[str, Any]] = []
    now_str = reference_date or datetime.now().strftime("%Y-%m-%d")

    # 1. Temporal Validity Check
    for r in rules:
        p = r.parameters or {}
        valid_until = p.get("valid_until")
        if valid_until and str(valid_until) < now_str:
            conflicts.append({
                "type": "temporal_expired",
                "rule_id": r.id,
                "rule_name": r.name,
                "valid_until": valid_until,
                "reference_date": now_str,
                "message": f"Rule [{r.name}] expired on {valid_until}; cannot be applied as current policy without explicit review.",
            })

    # 2. Parameter Incompatibility Check across same domain & parameter keys
    for i, r1 in enumerate(rules):
        for r2 in rules[i + 1:]:
            if r1.domain != r2.domain:
                continue

            p1 = r1.parameters or {}
            p2 = r2.parameters or {}

            common_keys = set(p1.keys()).intersection(set(p2.keys()))
            for k in common_keys:
                # Ignore non-numeric or metadata keys
                v1, v2 = p1[k], p2[k]
                if isinstance(v1, (int, float)) and isinstance(v2, (int, float)):
                    # If same parameter key differs by more than 20%
                    ratio = abs(v1 - v2) / max(abs(v1), abs(v2), 1e-6)
                    if ratio > 0.20:
                        conflicts.append({
                            "type": "parameter_conflict",
                            "rule_a_id": r1.id,
                            "rule_b_id": r2.id,
                            "parameter_key": k,
                            "value_a": v1,
                            "value_b": v2,
                            "discrepancy_pct": round(ratio * 100, 1),
                            "message": (
                                f"Rule conflict detected for parameter '{k}' in domain '{r1.domain}': "
                                f"[{r1.name}] prescribes {v1}, whereas [{r2.name}] prescribes {v2} (discrepancy {ratio * 100:.1f}%)."
                            ),
                        })

    return conflicts


def evaluate_evidence_quality_and_conflicts(
    evidence_chain: List[EvidenceItem],
) -> Tuple[float, List[Dict[str, Any]], List[str]]:
    """
    Evaluates evidence quality and identifies conflicting evidence items.

    Returns:
        Tuple of (overall_quality_score [0.0, 1.0], conflicts_list, warnings_list)
    """
    if not evidence_chain:
        return 0.5, [], ["No evidence items supplied in audit chain."]

    conflicts: List[Dict[str, Any]] = []
    warnings: List[str] = []

    # 1. Quality scoring by source strength and confidence
    scores: List[float] = []
    for ev in evidence_chain:
        base_cred = SOURCE_TYPE_CREDIBILITY.get(ev.type, 0.7)
        score = base_cred * ev.confidence
        scores.append(score)

    avg_score = sum(scores) / len(scores)

    # 2. Conflicting evidence detection (e.g. contradictory facts in parameters)
    param_records: Dict[str, List[Tuple[str, Any, float]]] = {}
    for ev in evidence_chain:
        for k, v in ev.parameters.items():
            if isinstance(v, (int, float)):
                param_records.setdefault(k, []).append((ev.id, v, ev.confidence))

    for param_k, observations in param_records.items():
        if len(observations) > 1:
            vals = [obs[1] for obs in observations]
            min_v, max_v = min(vals), max(vals)
            diff_ratio = (max_v - min_v) / max(abs(min_v), 1e-6)
            if diff_ratio > 0.30:
                # Contradictory evidence detected
                conflicts.append({
                    "type": "evidence_contradiction",
                    "parameter": param_k,
                    "sources": [obs[0] for obs in observations],
                    "values": vals,
                    "discrepancy_ratio": round(diff_ratio, 2),
                    "message": (
                        f"Conflicting evidence for parameter '{param_k}': "
                        f"Sources disagree between {min_v} and {max_v} (discrepancy {diff_ratio * 100:.0f}%). "
                        f"Confidence downgraded; silent averaging prohibited."
                    ),
                })
                # Downgrade score due to conflict
                avg_score *= 0.85

    # Diversity bonus (having multiple types of evidence)
    types_present = {ev.type for ev in evidence_chain}
    diversity_multiplier = min(1.15, 1.0 + 0.05 * len(types_present))
    final_quality = min(1.0, round(avg_score * diversity_multiplier, 4))

    return final_quality, conflicts, warnings
