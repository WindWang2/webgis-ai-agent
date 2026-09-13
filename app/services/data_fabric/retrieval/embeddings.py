"""Optional embedding signal for hybrid retrieval (DS2, ADR-0172).

The keyword BM25 index is always the base. When an embedding provider is
available, query and card texts are embedded and cosine similarity merges
into the relevance score. When unavailable the retrieval runs keyword-only
and flags ``degraded=True`` — honest, never a silent capability drop.

Providers:
- ``FaissEmbeddingProvider`` — reuses the existing RAG infra
  (``app.services.rag.faiss_store.FaissVectorStore.embed_texts``); lazily
  imported and guarded (model load failures → unavailable, matching the
  test-suite's offline embedding guard).
- Tests inject a deterministic fake via ``register_provider``.
"""
from __future__ import annotations

import math
from typing import List, Optional, Protocol


class EmbeddingProvider(Protocol):
    def embed(self, texts: List[str]) -> List[List[float]]: ...


class FaissEmbeddingProvider:
    """Adapter over the existing RAG embedding seam (lazy, guarded).

    Instantiates a throwaway ``FaissVectorStore`` for its embedding model only
    (no index dir interaction). Model load failures → unavailable (degraded
    keyword-only mode), matching the suite's offline-embedding guard.
    """

    def __init__(self) -> None:
        self._store = None

    def _ensure(self):
        if self._store is None:
            from app.services.rag.faiss_store import FaissVectorStore

            self._store = FaissVectorStore(index_dir=":memory-embedding-only:")
        return self._store

    def available(self) -> bool:
        try:
            self._ensure()._get_embedding_model()
            return True
        except Exception:  # noqa: BLE001 — offline/unavailable → keyword-only
            return False

    def embed(self, texts: List[str]) -> List[List[float]]:
        store = self._ensure()
        vecs = store.embed_texts(texts)
        return [v.tolist() for v in vecs]


class KeywordOnlyProvider:
    """Degenerate provider: always unavailable (used explicitly in tests)."""

    def available(self) -> bool:
        return False

    def embed(self, texts: List[str]):  # pragma: no cover — never called
        raise RuntimeError("keyword-only provider cannot embed")


_PROVIDER: Optional[EmbeddingProvider] = None


def register_provider(provider: Optional[EmbeddingProvider]) -> None:
    """Install (or clear with None) the process-level embedding provider."""
    global _PROVIDER
    _PROVIDER = provider


def get_provider() -> Optional[EmbeddingProvider]:
    """Default provider policy (bounded, no network):

    - an explicitly registered provider always wins;
    - otherwise probe the RAG infra **only** when ``RAG_EMBEDDING_OFFLINE`` is
      on — that mode constructs the model with ``local_files_only=True`` and
      fails bounded (seconds) instead of hanging on an HF download;
    - otherwise return None → retrieval runs keyword-only with
      ``degraded=True`` (honest capability disclosure).
    """
    global _PROVIDER
    if _PROVIDER is None:
        try:
            from app.core.config import settings

            if settings.RAG_EMBEDDING_OFFLINE:
                candidate = FaissEmbeddingProvider()
                if candidate.available():
                    _PROVIDER = candidate
        except Exception:  # noqa: BLE001 — genuinely unavailable
            _PROVIDER = None
    return _PROVIDER


def cosine(a: List[float], b: List[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    num = sum(x * y for x, y in zip(a, b))
    da = math.sqrt(sum(x * x for x in a))
    db = math.sqrt(sum(y * y for y in b))
    if da == 0.0 or db == 0.0:
        return 0.0
    return max(0.0, min(1.0, num / (da * db)))


__all__ = [
    "EmbeddingProvider",
    "FaissEmbeddingProvider",
    "KeywordOnlyProvider",
    "register_provider",
    "get_provider",
    "cosine",
]
