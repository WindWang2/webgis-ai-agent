"""
RAG (Retrieval-Augmented Generation) package.
"""
from app.services.rag.protocol import VectorStoreProtocol
from app.services.rag.chunker import split_into_chunks
from app.services.rag.faiss_store import FaissVectorStore

__all__ = ["VectorStoreProtocol", "split_into_chunks", "FaissVectorStore"]
