"""
FAISS Vector Store implementation of VectorStoreProtocol.
"""
import fcntl
import json
import logging
import os
from typing import Any, Dict, List, Optional
import numpy as np

logger = logging.getLogger(__name__)

INDEX_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))),
    "data",
    "vectors_store",
)


class FaissVectorStore:
    """Encapsulates FAISS index management, metadata storage, and file locks."""

    def __init__(self, index_dir: str = INDEX_DIR):
        self.index_dir = index_dir
        self._index = None
        self._model = None

    def _get_embedding_model(self):
        """Lazy load sentence transformer model."""
        if self._model is None:
            try:
                from sentence_transformers import SentenceTransformer
                self._model = SentenceTransformer("paraphrase-multilingual-MiniLM-L12-v2")
                logger.info("[RAG] Loaded embedding model: paraphrase-multilingual-MiniLM-L12-v2")
            except Exception as e:
                logger.error(f"[RAG] Failed to load embedding model: {e}")
                raise
        return self._model

    def embed_texts(self, texts: List[str]) -> np.ndarray:
        """Encode text strings into normalized vector embeddings."""
        model = self._get_embedding_model()
        vectors = model.encode(texts, normalize_embeddings=True)
        return np.array(vectors, dtype=np.float32)

    def _get_index(self, dim: int = 384):
        """Get or initialize FAISS index."""
        if self._index is None:
            try:
                import faiss
                self._index = faiss.IndexFlatIP(dim)
                index_file = os.path.join(self.index_dir, "index.faiss")
                meta_file = os.path.join(self.index_dir, "metadata.json")
                if os.path.exists(index_file) and os.path.exists(meta_file):
                    self._index = faiss.read_index(index_file)
                    logger.info("[RAG] Loaded existing FAISS index")
            except Exception as e:
                logger.error(f"[RAG] Failed to init FAISS: {e}")
                raise
        return self._index

    def save_index(self) -> None:
        """Persist FAISS index to disk."""
        if self._index is not None:
            os.makedirs(self.index_dir, exist_ok=True)
            try:
                import faiss
                faiss.write_index(self._index, os.path.join(self.index_dir, "index.faiss"))
                logger.info("[RAG] Saved FAISS index to disk")
            except Exception as e:
                logger.warning(f"[RAG] Failed to save index: {e}")

    def load_metadata(self) -> Dict[str, Any]:
        """Load metadata with shared read lock."""
        meta_file = os.path.join(self.index_dir, "metadata.json")
        if not os.path.exists(meta_file):
            return {"chunks": []}
        try:
            with open(meta_file, "r", encoding="utf-8") as f:
                fcntl.flock(f.fileno(), fcntl.LOCK_SH)
                try:
                    return json.load(f)
                finally:
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        except Exception as e:
            logger.warning(f"[RAG] Failed to load metadata: {e}")
            return {"chunks": []}

    def save_metadata(self, meta: Dict[str, Any]) -> None:
        """Save metadata with exclusive write lock."""
        os.makedirs(self.index_dir, exist_ok=True)
        meta_file = os.path.join(self.index_dir, "metadata.json")
        try:
            with open(meta_file, "w", encoding="utf-8") as f:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX)
                try:
                    json.dump(meta, f, ensure_ascii=False, indent=2)
                finally:
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        except Exception as e:
            logger.warning(f"[RAG] Failed to save metadata: {e}")

    def add_vectors(self, vectors: Any, chunks_metadata: List[Dict[str, Any]]) -> None:
        """Add embedding vectors and associated metadata."""
        idx = self._get_index(vectors.shape[1])
        idx.add(vectors)

        meta = self.load_metadata()
        base_idx = len(meta.get("chunks", []))
        for i, ch_meta in enumerate(chunks_metadata):
            ch_meta["index"] = base_idx + i
            meta.setdefault("chunks", []).append(ch_meta)
        self.save_metadata(meta)
        self.save_index()

    def search(
        self,
        query_vector: Any,
        top_k: int = 5,
        user_id: Optional[str] = None,
        org_id: Optional[str] = None,
        is_admin: bool = False,
    ) -> List[Dict[str, Any]]:
        """Search top-k most similar vectors with optional tenant filtering."""
        idx = self._get_index()
        fetch_count = min(top_k * 4, idx.ntotal) if idx.ntotal > 0 else top_k
        scores, indices = idx.search(query_vector, fetch_count)
        meta = self.load_metadata()
        results = []
        for score, i in zip(scores[0], indices[0]):
            if i < 0 or i >= len(meta.get("chunks", [])):
                continue
            chunk_meta = dict(meta["chunks"][int(i)])

            if chunk_meta.get("deleted", False):
                continue

            # Tenant filtering check
            if not is_admin and (user_id or org_id):
                c_user = chunk_meta.get("user_id")
                c_org = chunk_meta.get("org_id")
                if c_user and user_id and c_user != user_id:
                    continue
                if c_org and org_id and c_org != org_id:
                    continue

            chunk_meta["score"] = float(score)
            results.append(chunk_meta)
            if len(results) >= top_k:
                break
        return results

    def mark_deleted(self, document_id: str) -> None:
        """Mark chunks of document as deleted in metadata."""
        meta = self.load_metadata()
        for ch in meta.get("chunks", []):
            if ch.get("document_id") == document_id:
                ch["deleted"] = True
        self.save_metadata(meta)

    def compact(self) -> Dict[str, Any]:
        """Compact index by purging deleted chunks and rebuilding FAISS index."""
        import faiss
        meta = self.load_metadata()
        chunks = meta.get("chunks", [])
        active_chunks = [ch for ch in chunks if not ch.get("deleted", False)]
        purged_count = len(chunks) - len(active_chunks)

        if purged_count == 0:
            return {"purged": 0, "total": len(active_chunks)}

        # Update metadata indices
        for new_idx, ch in enumerate(active_chunks):
            ch["index"] = new_idx

        meta["chunks"] = active_chunks
        self.save_metadata(meta)

        # Rebuild FAISS index from active chunks if texts exist
        if active_chunks:
            texts = [ch.get("content", ch.get("text", "")) for ch in active_chunks if ch.get("content") or ch.get("text")]
            if texts:
                vectors = self.embed_texts(texts)
                self._index = faiss.IndexFlatIP(vectors.shape[1])
                self._index.add(vectors)
                self.save_index()
        else:
            self._index = None
            index_path = os.path.join(self.index_dir, "index.faiss")
            if os.path.exists(index_path):
                os.remove(index_path)

        logger.info(f"[FaissVectorStore] Compacted index: purged {purged_count} deleted vectors")
        return {"purged": purged_count, "total": len(active_chunks)}

    def get_stats(self) -> Dict[str, Any]:
        """Return index statistics (total_vectors, index_dim, deleted_count)."""
        idx = self._get_index()
        meta = self.load_metadata()
        chunks = meta.get("chunks", [])
        deleted_count = sum(1 for ch in chunks if ch.get("deleted", False))

        return {
            "total_vectors": idx.ntotal,
            "dimension": idx.d,
            "total_chunks": len(chunks),
            "deleted_count": deleted_count,
        }
