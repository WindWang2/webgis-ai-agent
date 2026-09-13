"""Retrieval service: hybrid search over the registry cards (DS2, ADR-0172).

``retrieve(query)`` → top-k hits, each with score / factor breakdown (reason)
/ confidence; low-confidence top hit flags ``clarification_needed`` instead of
silently returning a first pick (task book §0.5). Keyword BM25 is the base;
an embedding provider (when available) merges a semantic relevance signal;
without one the response is ``degraded=True``.

This is the acquisition-side entry point ("找什么数据") — the cartography
line's ``intent_semantic`` is a separate domain, integrated via the intent
detector's candidate seam, not by sharing internals.
"""
from __future__ import annotations

import threading
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field

from app.services.data_fabric.retrieval.cards import DatasetCard, build_cards
from app.services.data_fabric.retrieval.embeddings import get_provider
from app.services.data_fabric.retrieval.keyword import BM25Index
from app.services.data_fabric.retrieval.ranker import (
    LOW_CONFIDENCE_THRESHOLD,
    reason_text,
    rank_card,
)


class RetrievalFilters(BaseModel):
    """Structured filters (task book DS2.2: bbox / 时间覆盖 / 粒度 / 许可)."""

    model_config = ConfigDict(extra="allow")

    bbox: Optional[List[float]] = None          # [minx, miny, maxx, maxy]
    temporal: Optional[List[str]] = None        # [start, end] ISO
    granularity: Optional[str] = None           # 省/市/县/乡镇/...
    licenses_allow: Optional[List[str]] = None  # e.g. ["cc-by-4.0", "public-domain"]
    data_types_allow: Optional[List[str]] = None
    local_only: bool = False
    verified_only: bool = False


class RetrievalHit(BaseModel):
    model_config = ConfigDict(extra="allow")

    card_id: str
    source_id: str
    dataset_id: str
    title: str
    score: float
    confidence: float
    reason: str
    factors: Dict[str, float]
    top_factor: str
    card: Dict[str, Any]  # the DatasetCard payload (D1-convertible)


class RetrievalResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    query: str
    hits: List[RetrievalHit] = Field(default_factory=list)
    degraded: bool = False   # True = no embedding provider → keyword-only
    clarification_needed: bool = False
    clarification_reason: str = ""
    total_candidates: int = 0


class RetrievalService:
    """Rebuilds its index when the registry hot-reloads (mtime driven)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._index: Optional[BM25Index] = None
        self._cards: List[DatasetCard] = []
        self._snapshot_mtimes: Optional[Dict[str, float]] = None

    # -- index lifecycle ------------------------------------------------------
    def _registry_signature(self) -> Optional[Dict[str, float]]:
        try:
            from app.services.data_fabric.source_registry import source_registry_service

            service = source_registry_service
            service.reload_if_changed()
            return {e.path.name: e.mtime for e in service._loaded.values()}  # noqa: SLF001 — same-package snapshot
        except Exception:  # noqa: BLE001 — registry-less operation still works
            return None

    def _ensure_index(self) -> Tuple[List[DatasetCard], BM25Index]:
        with self._lock:
            sig = self._registry_signature()
            if self._index is None or sig != self._snapshot_mtimes:
                self._cards = build_cards()
                triples = []
                for c in self._cards:
                    triples.append((
                        c.card_id,
                        c.searchable_text(),
                        list(c.keywords) + [c.source_name],
                    ))
                self._index = BM25Index(triples)
                self._snapshot_mtimes = sig
            return self._cards, self._index

    # -- retrieval --------------------------------------------------------------
    def retrieve(
        self,
        query: str,
        *,
        top_k: int = 5,
        filters: Optional[RetrievalFilters] = None,
        candidate_pool: int = 30,
    ) -> RetrievalResponse:
        cards, index = self._ensure_index()
        filters = filters or RetrievalFilters()
        by_id = {c.card_id: c for c in cards}

        # 1. BM25 base
        bm25 = index.search(query, top_k=max(candidate_pool, top_k * 4))

        # 2. optional semantic signal (provider must self-report available)
        provider = get_provider()
        usable = provider is not None and bool(getattr(provider, "available", lambda: True)())
        degraded = not usable
        emb_query = provider.embed([query])[0] if usable else None
        card_vecs = provider.embed([c.searchable_text() for c in cards]) if usable else None
        vec_by_id = {c.card_id: v for c, v in zip(cards, card_vecs)} if usable else {}

        hits: List[RetrievalHit] = []
        for card_id, bm25_score in bm25:
            card = by_id.get(card_id)
            if card is None or self._excluded(card, filters):
                continue
            # normalise BM25 into [0,1]: best hit of THIS query = 1.0
            rel = bm25_score / bm25[0][1] if bm25 and bm25[0][1] > 0 else 0.0
            if emb_query is not None:
                sim = _cos(vec_by_id.get(card_id), emb_query)
                rel = max(rel, 0.5 * rel + 0.5 * sim) if sim > 0 else rel
            temporal = tuple(filters.temporal) if filters.temporal else None
            breakdown = rank_card(card, rel, bbox=filters.bbox, temporal=temporal)
            hits.append(RetrievalHit(
                card_id=card.card_id,
                source_id=card.source_id,
                dataset_id=card.dataset_id,
                title=card.title,
                score=breakdown["score"],
                confidence=breakdown["confidence"],
                reason=reason_text(card, breakdown, degraded),
                factors=breakdown["factors"],
                top_factor=breakdown["top_factor"],
                card=card.model_dump(),
            ))
        hits.sort(key=lambda h: (-h.score, h.card_id))
        hits = hits[:top_k]

        clarification_needed = False
        clarification_reason = ""
        if hits and hits[0].confidence < LOW_CONFIDENCE_THRESHOLD:
            clarification_needed = True
            clarification_reason = (
                f"最高置信度 {hits[0].confidence:.2f} < {LOW_CONFIDENCE_THRESHOLD}："
                "请确认目标数据集/范围/时间，避免误取"
            )
        return RetrievalResponse(
            query=query,
            hits=hits,
            degraded=degraded,
            clarification_needed=clarification_needed,
            clarification_reason=clarification_reason,
            total_candidates=len(bm25),
        )

    @staticmethod
    def _excluded(card: DatasetCard, f: RetrievalFilters) -> bool:
        if f.local_only and not card.local:
            return True
        if f.verified_only and not card.verified:
            return True
        if f.granularity and card.granularity and card.granularity != f.granularity:
            # declared granularity mismatch is a hard exclusion only when both
            # sides declare; unknown granularity stays in the pool
            return True
        if f.licenses_allow and card.license not in f.licenses_allow:
            return True
        if f.data_types_allow and card.data_type not in f.data_types_allow:
            return True
        return False


def _cos(a: Optional[List[float]], b: Optional[List[float]]) -> float:
    if not a or not b:
        return 0.0
    from app.services.data_fabric.retrieval.embeddings import cosine

    return cosine(a, b)


_SERVICE: Optional[RetrievalService] = None
_SERVICE_LOCK = threading.Lock()


def get_retrieval_service() -> RetrievalService:
    global _SERVICE
    with _SERVICE_LOCK:
        if _SERVICE is None:
            _SERVICE = RetrievalService()
        return _SERVICE


__all__ = [
    "RetrievalService",
    "RetrievalResponse",
    "RetrievalHit",
    "RetrievalFilters",
    "get_retrieval_service",
]
