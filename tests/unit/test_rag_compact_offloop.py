"""
KnowledgeEngine.compact_index must run compact() off the event loop.

compact() re-embeds every active chunk via SentenceTransformer when the
deleted-ratio crosses 20%. That is seconds-of-CPU work; if it runs on
the uvicorn event loop it stalls every concurrent request until it
finishes. PR #289 already moved index_document and search off the loop
via asyncio.to_thread; this test pins the same contract for the
compaction path triggered by delete_document.
"""
import asyncio
import threading
from typing import Any, Dict, List

import pytest


class _RecordingStore:
    """Minimal VectorStoreProtocol whose compact records its thread."""

    def __init__(self, ntotal: int = 100, deleted: int = 30) -> None:
        self._ntotal = ntotal
        self._deleted = deleted
        self.compact_thread: threading.Thread | None = None
        self.compact_calls = 0

    def compact(self) -> Dict[str, Any]:
        self.compact_calls += 1
        self.compact_thread = threading.current_thread()
        return {"purged": self._deleted, "total": self._ntotal - self._deleted}

    def get_stats(self) -> Dict[str, Any]:
        return {"total_vectors": self._ntotal, "deleted_count": self._deleted}

    def mark_deleted(self, doc_id: str) -> None:
        self._deleted += 1

    # The other Protocol methods aren't exercised here.
    def embed_texts(self, texts: List[str]) -> Any: raise NotImplementedError
    def add_vectors(self, vectors, chunks_metadata) -> None: raise NotImplementedError
    def search(self, query_vector, top_k=5, user_id=None, org_id=None, is_admin=False):
        raise NotImplementedError


@pytest.mark.asyncio
async def test_compact_index_runs_off_loop():
    """compact_index must call self._store.compact in a worker thread,
    not on the asyncio loop's main thread."""
    from app.services.rag.engine import KnowledgeEngine

    store = _RecordingStore()
    engine = KnowledgeEngine(vector_store=store)

    await engine.compact_index()

    assert store.compact_calls == 1
    assert store.compact_thread is not None
    assert store.compact_thread is not threading.current_thread(), (
        "compact ran on the event loop's main thread; "
        "asyncio.to_thread wrapping is missing or bypassed"
    )


@pytest.mark.asyncio
async def test_delete_document_runs_compact_off_loop():
    """delete_document triggers compact when the deleted ratio crosses 20%.
    That compaction must also run off the loop — same thread-identity
    contract as compact_index itself."""
    from app.services.rag.engine import KnowledgeEngine, TenantContext

    # ntotal=10, deleted=2 → ratio=0.20 → compact triggers.
    store = _RecordingStore(ntotal=10, deleted=2)
    engine = KnowledgeEngine(vector_store=store)

    await engine.delete_document("doc_x", tenant=TenantContext(user_id="alice"))

    assert store.compact_calls == 1
    assert store.compact_thread is not threading.current_thread()


@pytest.mark.asyncio
async def test_delete_document_below_threshold_does_not_compact():
    """Sanity: below 20% ratio, no compaction is triggered (and therefore
    no extra thread hop)."""
    from app.services.rag.engine import KnowledgeEngine, TenantContext

    store = _RecordingStore(ntotal=100, deleted=1)  # ratio 0.01
    engine = KnowledgeEngine(vector_store=store)

    await engine.delete_document("doc_x", tenant=TenantContext(user_id="alice"))

    assert store.compact_calls == 0
