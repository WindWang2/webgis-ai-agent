"""
Vector Store Protocol interface.
"""
from typing import Any, Dict, List, Optional, Protocol, runtime_checkable


@runtime_checkable
class VectorStoreProtocol(Protocol):
    """Protocol defining the interface for vector index & metadata storage."""

    def embed_texts(self, texts: List[str]) -> Any:
        """Encode text strings into normalized vector embeddings."""
        ...

    def add_vectors(
        self, vectors: Any, chunks_metadata: List[Dict[str, Any]]
    ) -> None:
        """Add embedding vectors and associated chunk metadata to the index."""
        ...

    def search(
        self,
        query_vector: Any,
        top_k: int = 5,
        user_id: Optional[str] = None,
        org_id: Optional[str] = None,
        is_admin: bool = False,
    ) -> List[Dict[str, Any]]:
        """Search top-k most similar chunks using a query vector with optional tenant filtering."""
        ...

    def mark_deleted(self, doc_id: str) -> None:
        """Mark document chunks as deleted."""
        ...

    def compact(self) -> Dict[str, Any]:
        """Compact index by purging deleted vectors and rebuilding index."""
        ...

    def get_stats(self) -> Dict[str, Any]:
        """Return index statistics (total_vectors, index_dim, etc.)."""
        ...
