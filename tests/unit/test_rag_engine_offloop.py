"""
KnowledgeEngine must not block the event loop on SentenceTransformer
encoding (REVIEW-P1-5).

The /knowledge/documents POST and /knowledge/search endpoints live on the
uvicorn event loop. SentenceTransformer.encode is CPU-bound and takes
seconds for many chunks, so it has to run in a worker thread or every
other request stalls while one document uploads.

ADR-0038 dropped the run_in_executor wrapper the pre-extraction rag_service
had. Verify it is back by recording the thread that embed_texts runs on and
asserting it isn't the event loop's thread.
"""
import threading
from typing import Any, Dict, List

import pytest


class _RecordingVectorStore:
    """Minimal VectorStoreProtocol that captures which thread embed_texts runs on."""

    def __init__(self) -> None:
        self.embed_thread: threading.Thread | None = None

    def embed_texts(self, texts: List[str]) -> Any:
        self.embed_thread = threading.current_thread()
        return [[0.1] * 8 for _ in texts]

    def add_vectors(self, vectors: Any, chunks_metadata: List[Dict[str, Any]]) -> None:
        pass

    def search(self, query_vector, top_k=5, user_id=None, org_id=None, is_admin=False):
        return []

    def mark_deleted(self, doc_id: str) -> None:
        pass

    def compact(self) -> Dict[str, Any]:
        return {}

    def get_stats(self) -> Dict[str, Any]:
        return {"total_vectors": 0, "deleted_count": 0}


@pytest.mark.asyncio
async def test_index_document_runs_embed_off_loop():
    """index_document must offload embed_texts — the calling thread must NOT
    be the asyncio loop's main thread.
    """
    from app.services.rag.engine import KnowledgeEngine, TenantContext

    store = _RecordingVectorStore()
    engine = KnowledgeEngine(vector_store=store)

    result = await engine.index_document(
        title="event loop test",
        content="some content " * 50,
        tenant=TenantContext(user_id="alice"),
    )

    assert "error" not in result
    assert store.embed_thread is not None
    loop_thread = threading.current_thread()
    assert store.embed_thread is not loop_thread, (
        "embed_texts ran on the event loop's main thread; "
        "asyncio.to_thread wrapping is missing or bypassed"
    )


@pytest.mark.asyncio
async def test_search_runs_embed_off_loop():
    """search() also encodes the query — must be off the loop too."""
    from app.services.rag.engine import KnowledgeEngine, TenantContext

    store = _RecordingVectorStore()
    engine = KnowledgeEngine(vector_store=store)

    await engine.search("q", tenant=TenantContext(user_id="alice"))

    assert store.embed_thread is not None
    assert store.embed_thread is not threading.current_thread()

