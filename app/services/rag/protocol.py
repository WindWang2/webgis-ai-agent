"""
Vector Store Protocol interface.
"""
from typing import Any, Dict, List, Protocol, runtime_checkable


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
        self, query_vector: Any, top_k: int = 5
    ) -> List[Dict[str, Any]]:
        """Search top-k most similar chunks using a query vector."""
        ...

    def get_stats(self) -> Dict[str, Any]:
        """Return index statistics (total_vectors, index_dim, etc.)."""
        ...
