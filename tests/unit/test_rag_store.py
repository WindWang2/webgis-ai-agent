"""
Unit tests for RAG vector store deep module and chunker.
"""
import pytest
import os
import tempfile
from app.services.rag.chunker import split_into_chunks, split_markdown_sections
from app.services.rag.protocol import VectorStoreProtocol
from app.services.rag.faiss_store import FaissVectorStore


def test_vector_store_protocol_conformance():
    """Verify FaissVectorStore satisfies VectorStoreProtocol."""
    store = FaissVectorStore()
    assert isinstance(store, VectorStoreProtocol)


def test_split_into_chunks_basic():
    text = "Hello world. " * 100
    chunks = split_into_chunks(text, max_tokens=20, overlap=5)
    assert len(chunks) > 0
    assert "content" in chunks[0]
    assert "chunk_index" in chunks[0]


def test_split_markdown_sections():
    md = "# Title\n\nIntro text\n\n## Section 1\nContent 1\n\n## Section 2\nContent 2"
    sections = split_markdown_sections(md)
    assert len(sections) == 3
    assert "Section 1" in sections[1]
    assert "Section 2" in sections[2]


def test_faiss_vector_store_in_temp_dir():
    with tempfile.TemporaryDirectory() as tmpdir:
        store = FaissVectorStore(index_dir=tmpdir)
        meta = store.load_metadata()
        assert meta == {"chunks": []}

        # Save metadata
        store.save_metadata({"chunks": [{"document_id": "doc1", "title": "Test"}]})
        reloaded = store.load_metadata()
        assert len(reloaded["chunks"]) == 1
        assert reloaded["chunks"][0]["document_id"] == "doc1"
