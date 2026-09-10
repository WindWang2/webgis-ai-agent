"""
Unit and regression tests for SEC-04: Batch re-embedding during FAISS vector store compaction.

Verifies:
1. FaissVectorStore.compact batches texts in chunks of batch_size rather than
   passing the entire active corpus in a single unbatched call (preventing OOM).
2. The compacted FAISS index and metadata accurately retain all active chunks
   without data loss or position mismatch.
3. Deleted chunks are purged, active chunks are re-indexed and searchable.
"""
import numpy as np

from app.services.rag.faiss_store import FaissVectorStore


def _fake_vectors(n: int, dim: int = 8) -> np.ndarray:
    """Deterministic normalized vectors for testing."""
    rng = np.random.default_rng(seed=42)
    vecs = rng.random((n, dim)).astype(np.float32)
    norms = np.linalg.norm(vecs, axis=1, keepdims=True)
    return vecs / np.maximum(norms, 1e-9)


def test_compact_batches_reembedding(tmp_path, monkeypatch):
    """Verify compaction splits active chunks into batches of specified batch_size."""
    store = FaissVectorStore(index_dir=str(tmp_path / "vectors"))

    # Ingest 15 chunks across 3 documents
    chunks = []
    for i in range(15):
        doc_id = f"doc_{i % 3}"
        chunks.append({
            "id": f"chk_{i:02d}",
            "document_id": doc_id,
            "title": f"Document {doc_id}",
            "content": f"Content of chunk {i:02d} for compaction testing",
            "user_id": "test_user",
            "org_id": None,
        })

    vectors = _fake_vectors(len(chunks))
    store.add_vectors(vectors, chunks)

    # Soft delete doc_0 (which has 5 chunks: 0, 3, 6, 9, 12)
    store.mark_deleted("doc_0")

    # Spy on embed_texts calls to capture batch sizes
    batch_sizes = []

    def spy_embed_texts(self, texts):
        batch_sizes.append(len(texts))
        return _fake_vectors(len(texts))

    monkeypatch.setattr(FaissVectorStore, "embed_texts", spy_embed_texts)

    # Run compaction with batch_size = 4 (10 active chunks remaining: 4 + 4 + 2)
    result = store.compact(batch_size=4)

    assert result["purged"] == 5
    assert result["total"] == 10
    assert batch_sizes == [4, 4, 2], f"Expected batches [4, 4, 2], got {batch_sizes}"

    # Verify index and metadata consistency
    meta = store.load_metadata()
    remaining_chunks = meta["chunks"]
    assert len(remaining_chunks) == 10

    # Ensure all remaining chunks are active and properly indexed
    remaining_ids = [ch["id"] for ch in remaining_chunks]
    for i, ch in enumerate(remaining_chunks):
        assert ch["index"] == i
        assert not ch.get("deleted", False)
        assert ch["document_id"] != "doc_0"

    # Verify searching works correctly after compaction
    query_vec = _fake_vectors(1)
    results = store.search(query_vec, top_k=10, user_id="test_user")
    assert len(results) == 10
    assert all(r["id"] in remaining_ids for r in results)


def test_compact_default_batch_size_without_data_loss(tmp_path, monkeypatch):
    """Verify compaction with default batch_size=64 preserves all active chunks."""
    store = FaissVectorStore(index_dir=str(tmp_path / "vectors_default"))

    # Create 80 chunks across 2 documents
    chunks = []
    for i in range(80):
        doc_id = "doc_keep" if i >= 10 else "doc_delete"
        chunks.append({
            "id": f"chk_{i:03d}",
            "document_id": doc_id,
            "title": f"Document {doc_id}",
            "content": f"Text content for vector chunk number {i}",
            "user_id": None,
            "org_id": None,
        })

    vectors = _fake_vectors(len(chunks))
    store.add_vectors(vectors, chunks)

    # Mark doc_delete (10 chunks) as deleted
    store.mark_deleted("doc_delete")

    recorded_batches = []

    def spy_embed(self, texts):
        recorded_batches.append(len(texts))
        return _fake_vectors(len(texts))

    monkeypatch.setattr(FaissVectorStore, "embed_texts", spy_embed)

    # 70 active chunks with default batch_size 64 -> 64 + 6
    result = store.compact()

    assert result["purged"] == 10
    assert result["total"] == 70
    assert recorded_batches == [64, 6]

    meta = store.load_metadata()
    assert len(meta["chunks"]) == 70
    for i, ch in enumerate(meta["chunks"]):
        assert ch["index"] == i
        assert ch["document_id"] == "doc_keep"
