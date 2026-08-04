"""
Fast unit tests for KnowledgeEngine and TenantContext multi-tenant isolation.
Uses deterministic mock embeddings for fast, offline execution.
"""
import numpy as np
import pytest
from app.services.rag.engine import KnowledgeEngine, TenantContext, set_active_knowledge_engine
from app.services.rag.faiss_store import FaissVectorStore


class MockFaissStore(FaissVectorStore):
    """Subclass of FaissVectorStore with mock vector embeddings for offline testing."""

    def embed_texts(self, texts: list[str]) -> np.ndarray:
        # Generate deterministic 384-dim normalized mock vectors
        num_texts = len(texts)
        vecs = np.zeros((num_texts, 384), dtype=np.float32)
        for i, t in enumerate(texts):
            val = float(hash(t) % 100) / 100.0
            vecs[i, :] = val
        # Normalize
        norms = np.linalg.norm(vecs, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return (vecs / norms).astype(np.float32)


@pytest.fixture
def temp_faiss_store(tmp_path):
    store = MockFaissStore(index_dir=str(tmp_path / "vectors"))
    return store


@pytest.fixture
def knowledge_engine(temp_faiss_store):
    engine = KnowledgeEngine(vector_store=temp_faiss_store)
    set_active_knowledge_engine(engine)
    return engine


@pytest.mark.asyncio
async def test_index_and_search_document(knowledge_engine):
    tenant = TenantContext(user_id="usr_101", org_id="org_1")
    res = await knowledge_engine.index_document(
        title="GIS Spatial Methods",
        content="Spatial analysis calculates density and buffer zones for GIS layers.",
        file_type="text",
        tenant=tenant,
    )
    assert res.get("status") == "indexed"
    assert "document_id" in res

    # Search with matching tenant
    results = await knowledge_engine.search("buffer zones", tenant=tenant, top_k=5)
    assert len(results) >= 1
    assert results[0].get("title") == "GIS Spatial Methods"


@pytest.mark.asyncio
async def test_multi_tenant_isolation(knowledge_engine):
    tenant_a = TenantContext(user_id="usr_alice", org_id="org_alpha")
    tenant_b = TenantContext(user_id="usr_bob", org_id="org_beta")

    # Index document for Alice
    await knowledge_engine.index_document(
        title="Alice Confidential GIS",
        content="Confidential GIS survey data for Alpha organization.",
        tenant=tenant_a,
    )

    # Bob searches -> should receive empty results
    bob_results = await knowledge_engine.search("Confidential GIS", tenant=tenant_b)
    assert len(bob_results) == 0

    # Alice searches -> should receive results
    alice_results = await knowledge_engine.search("Confidential GIS", tenant=tenant_a)
    assert len(alice_results) >= 1
    assert alice_results[0]["title"] == "Alice Confidential GIS"


@pytest.mark.asyncio
async def test_soft_delete_and_compaction(knowledge_engine):
    tenant = TenantContext(user_id="usr_admin", is_admin=True)

    idx_res = await knowledge_engine.index_document(
        title="Temporary Document",
        content="This document will be deleted and compacted.",
        tenant=tenant,
    )
    doc_id = idx_res["document_id"]

    # Soft delete
    ok = await knowledge_engine.delete_document(doc_id, tenant=tenant)
    assert ok is True

    # Search after delete -> should not return deleted doc
    results = await knowledge_engine.search("Temporary Document", tenant=tenant)
    assert len(results) == 0

    # Manual compaction
    compact_res = await knowledge_engine.compact_index()
    assert "purged" in compact_res
