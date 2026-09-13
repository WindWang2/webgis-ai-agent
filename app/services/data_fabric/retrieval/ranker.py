"""Hybrid ranking with explainable contributions (DS2, ADR-0172).

score = w_rel·relevance + w_cov·coverage + w_fresh·freshness + w_cost·cost
        + w_trust·trust

All factors are normalised to [0, 1]; weights are **provisional** (task book
§0.5) — DS8 calibrates them from measured retrieval quality and usage.
Every hit carries its factor breakdown so the reason is a fact, not prose.

Confidence: a calibrated-ish mapping of the top factors and evidence overlap;
below ``LOW_CONFIDENCE_THRESHOLD`` the caller must ask the user instead of
silently taking the first candidate.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from app.services.data_fabric.retrieval.cards import DatasetCard

#: Ranking weights — **calibrated** in DS8 (scripts/ads_calibrate.py sweep over
#: the 278-sample eval: relevance 0.65 beat the original 0.55 by +0.0009 MRR;
#: cost keeps a 0.05 floor to preserve the local-first tie-break semantics —
#: the raw sweep optimum (cost 0) was rejected for that reason).
WEIGHTS: Dict[str, float] = {
    "relevance": 0.65,
    "coverage": 0.10,
    "freshness": 0.10,
    "cost": 0.05,
    "trust": 0.10,
}

LOW_CONFIDENCE_THRESHOLD = 0.5

#: freshness decay per staleness bucket (declared frequency → score).
_FRESHNESS_SCORE = {
    "continuous": 1.0,
    "daily": 0.95,
    "monthly": 0.8,
    "annual": 0.65,
    "irregular": 0.5,
    "manual_ingest": 0.5,
}


def freshness_score(card: DatasetCard) -> float:
    return _FRESHNESS_SCORE.get(card.update_frequency or "", 0.6)


def cost_score(card: DatasetCard) -> float:
    """Cheaper acquisition → higher score (bounded by quota)."""
    if card.local:
        return 1.0  # local assets ≈ zero network cost (local-first)
    rpm = card.requests_per_minute
    if rpm is None:
        return 0.5  # unknown quota — neutral, not optimistic
    if rpm <= 0:
        return 0.1
    return min(1.0, 0.5 + rpm / 200.0)  # 60rpm→0.8, ≥100rpm→1.0


def trust_score(card: DatasetCard) -> float:
    return 1.0 if card.verified else 0.4


def coverage_score(
    card: DatasetCard,
    bbox: Optional[List[float]] = None,
    temporal: Optional[Tuple[str, str]] = None,
) -> Tuple[float, List[str]]:
    """Structured coverage match: spatial overlap + temporal containment.
    Returns (score, matched_facets) — matched facets feed the reason."""
    matched: List[str] = []
    score = 0.5  # unknown coverage → neutral
    if card.bbox and bbox:
        minx, miny, maxx, maxy = bbox
        cb = card.bbox
        overlaps = not (cb[2] < minx or cb[0] > maxx or cb[3] < miny or cb[1] > maxy)
        score = 1.0 if overlaps else 0.0
        if overlaps:
            matched.append("bbox")
    elif bbox is None:
        score = 0.5
    if temporal and card.temporal_start:
        q_start, q_end = temporal
        start_ok = card.temporal_start <= (q_end or "9999")
        end_ok = (card.temporal_end is None) or (card.temporal_end >= (q_start or "0000"))
        if start_ok and end_ok:
            score = min(1.0, score + 0.5)
            matched.append("temporal")
    elif temporal is None and card.temporal_start:
        matched.append("temporal_declared")
        score = min(1.0, score + 0.25)
    return min(1.0, score), matched


def rank_card(
    card: DatasetCard,
    relevance: float,
    *,
    bbox: Optional[List[float]] = None,
    temporal: Optional[Tuple[str, str]] = None,
) -> Dict[str, Any]:
    """Score one card; returns the factor breakdown for the reason."""
    cov, cov_matched = coverage_score(card, bbox=bbox, temporal=temporal)
    factors = {
        "relevance": max(0.0, min(1.0, relevance)),
        "coverage": cov,
        "freshness": freshness_score(card),
        "cost": cost_score(card),
        "trust": trust_score(card),
    }
    score = sum(WEIGHTS[k] * v for k, v in factors.items())
    top_factor = max(factors, key=lambda k: factors[k] * WEIGHTS[k]) if factors else "relevance"
    return {
        "card_id": card.card_id,
        "score": round(score, 4),
        "factors": {k: round(v, 4) for k, v in factors.items()},
        "top_factor": top_factor,
        "coverage_matched": cov_matched,
        "confidence": round(_confidence(factors, relevance), 4),
    }


def _confidence(factors: Dict[str, float], relevance: float) -> float:
    """Confidence in [0,1]: relevance dominates; trust and coverage dampen.

    - keyword-only relevance saturates lower than an embedding-backed match,
      which naturally pushes weak keyword hits below the clarification
      threshold instead of silently winning.
    """
    conf = 0.35 * factors["relevance"] + 0.25 * factors["trust"] + 0.25 * factors["coverage"] + 0.15 * factors["freshness"]
    if relevance >= 0.99:
        conf = min(1.0, conf + 0.25)
    return conf


def reason_text(card: DatasetCard, breakdown: Dict[str, Any], degraded: bool) -> str:
    """Human-readable, fact-based reason (双语可用模板)。"""
    why = breakdown["factors"]
    parts = [f"关键词相关性 {why['relevance']:.2f}"]
    if breakdown["coverage_matched"]:
        parts.append("覆盖匹配 " + "/".join(breakdown["coverage_matched"]))
    parts.append(f"新鲜度 {why['freshness']:.2f}")
    parts.append(f"可信度 {why['trust']:.2f}（{'verified' if card.verified else 'unverified 源'}）")
    if card.local:
        parts.append("本地资产零网络代价")
    if degraded:
        parts.append("degraded: 无 embedding，纯关键词检索")
    return "；".join(parts)


__all__ = [
    "WEIGHTS",
    "LOW_CONFIDENCE_THRESHOLD",
    "rank_card",
    "reason_text",
    "coverage_score",
    "cost_score",
    "trust_score",
    "freshness_score",
]
