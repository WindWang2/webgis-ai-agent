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

    def search(self, query_vector: Any, top_k: int = 5) -> List[Dict[str, Any]]:
        """Search top-k most similar vectors."""
        idx = self._get_index()
        scores, indices = idx.search(query_vector, top_k * 2)
        meta = self.load_metadata()
        results = []
        for score, i in zip(scores[0], indices[0]):
            if i < 0 or i >= len(meta.get("chunks", [])):
                continue
            chunk_meta = dict(meta["chunks"][int(i)])
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

    def get_stats(self) -> Dict[str, Any]:
        """Return index stats."""
        idx = self._get_index()
        meta = self.load_metadata()
        chunks = meta.get("chunks", [])
        active_chunks = [c for c in chunks if not c.get("deleted")]
        return {
            "total_chunks": len(chunks),
            "active_chunks": len(active_chunks),
            "vector_count": idx.ntotal if idx else 0,
        }
