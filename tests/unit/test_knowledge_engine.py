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


# ─── Negative-path tests (review §3 missing edge cases) ──────────────────


@pytest.mark.asyncio
async def test_index_document_empty_content_returns_error(knowledge_engine):
    """Empty or whitespace-only content returns an error, not an empty index.

    Pins engine.py:47-48 - the guard short-circuits before chunking/embedding.
    """
    result = await knowledge_engine.index_document(
        title="Empty Doc",
        content="   ",
        file_type="text",
        tenant=TenantContext(user_id="usr_1"),
    )
    assert "error" in result
    assert "empty" in result["error"].lower()


@pytest.mark.asyncio
async def test_index_document_whitespace_markdown_returns_error(knowledge_engine):
    """Markdown with only whitespace content also hits the empty guard."""
    result = await knowledge_engine.index_document(
        title="Empty MD",
        content="\n\n  \n",
        file_type="markdown",
        tenant=TenantContext(user_id="usr_1"),
    )
    assert "error" in result


@pytest.mark.asyncio
async def test_search_defense_in_depth_tenant_filter_drops_cross_tenant():
    """The engine's post-store tenant filter drops cross-tenant results that
    slip through the store's own search.

    The review flagged that MockFaissStore inherits the real FaissVectorStore.search
    (which pre-filters by tenant), so the engine's defense-in-depth filter
    (engine.py:138-145) is dead in tests. This test uses a mock store that
    deliberately returns a cross-tenant row, proving the engine filter catches it.
    """
    from unittest.mock import MagicMock

    # A store whose search ignores tenant args and returns a cross-tenant row.
    cross_tenant_store = MagicMock()
    cross_tenant_store.embed_texts = MockFaissStore().embed_texts
    cross_tenant_store.search.return_value = [
        {
            "content": "Alice's secret data",
            "document_id": "doc_alice",
            "user_id": "usr_alice",
            "org_id": "org_alpha",
            "deleted": False,
        }
    ]

    engine = KnowledgeEngine(vector_store=cross_tenant_store)

    # Bob searches - the store returns Alice's row, but the engine's
    # defense-in-depth filter (engine.py:138-145) must drop it.
    bob_tenant = TenantContext(user_id="usr_bob", org_id="org_beta")
    results = await engine.search("secret data", tenant=bob_tenant, top_k=5)

    assert len(results) == 0, (
        "engine's defense-in-depth tenant filter should drop cross-tenant rows "
        "that slip through the store's own search"
    )
