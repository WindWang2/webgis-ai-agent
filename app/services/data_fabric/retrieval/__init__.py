"""ads-v2 semantic dataset retrieval (DS2, ADR-0172).

Retrieval over the source registry's declared datasets and known local assets:

- **Dataset cards** (`cards.py`) — searchable projections of the registry
  (title/description/keywords/fields/coverage/freshness/license/verified).
- **Hybrid retrieval** (`keyword.py` + `embeddings.py`) — a deterministic
  BM25 keyword index is ALWAYS the base (never vector-only); an optional
  embedding provider (reusing the existing RAG infra) adds a semantic signal
  when available. No provider → keyword-only with ``degraded=True``.
- **Ranking** (`ranker.py`) — relevance × coverage × freshness × cost ×
  trust with provisional weights (DS8 calibrates from measurements); every
  hit carries the contributing-factor breakdown (reason) and a confidence;
  low confidence triggers clarification instead of a silent first pick.

Namespace discipline: everything here is acquisition-side retrieval ("找什么
数据"); the cartography line's intent understanding stays untouched.
"""
from app.services.data_fabric.retrieval.service import (
    RetrievalHit,
    RetrievalResponse,
    RetrievalService,
    RetrievalFilters,
    get_retrieval_service,
)

__all__ = [
    "RetrievalHit",
    "RetrievalResponse",
    "RetrievalService",
    "RetrievalFilters",
    "get_retrieval_service",
]
