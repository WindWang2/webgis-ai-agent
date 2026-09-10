"""
Unit and regression tests for SEC-02: Fail-closed multi-tenant scoping in FAISS RAG retrieval.

Verifies:
1. Anonymous/unauthenticated queries (user_id=None, org_id=None) fail closed:
   only public chunks (c_user=None, c_org=None) can be returned.
2. Tenant A cannot see Tenant B's chunks or documents.
3. Users without organization context cannot access organization-scoped documents.
4. Admin queries can see all chunks across tenants.
5. Consistent behavior across FaissVectorStore and KnowledgeEngine.
"""
import numpy as np
import pytest

from app.services.rag.engine import KnowledgeEngine, TenantContext
from app.services.rag.faiss_store import FaissVectorStore


def _fake_vectors(n: int, dim: int = 8) -> np.ndarray:
    """Deterministic vectors for tests so real embedding model is not needed."""
    rng = np.random.default_rng(seed=123)
    vecs = rng.random((n, dim)).astype(np.float32)
    # L2 normalize so cosine similarity via dot product is well-behaved
    norms = np.linalg.norm(vecs, axis=1, keepdims=True)
    return vecs / np.maximum(norms, 1e-9)


@pytest.fixture
def patch_embed(monkeypatch):
    """Stub FaissVectorStore.embed_texts for offline testing."""
    def fake(self, texts):
        return _fake_vectors(len(texts))

    monkeypatch.setattr(FaissVectorStore, "embed_texts", fake)


def test_faiss_store_tenant_scoping_fail_closed(tmp_path, patch_embed):
    """FaissVectorStore.search must strictly enforce fail-closed tenant scoping."""
    store = FaissVectorStore(index_dir=str(tmp_path / "vectors"))

    chunks = [
        # Chunk 0: public doc
        {"id": "chk_pub", "document_id": "doc_pub", "title": "Public Guide", "content": "public info", "user_id": None, "org_id": None},
        # Chunk 1: Alice personal doc
        {"id": "chk_alice", "document_id": "doc_alice", "title": "Alice Secrets", "content": "alice private", "user_id": "alice", "org_id": None},
        # Chunk 2: OrgX shared doc
        {"id": "chk_orgx", "document_id": "doc_orgx", "title": "OrgX Policy", "content": "orgx shared", "user_id": None, "org_id": "org_x"},
        # Chunk 3: Bob in OrgX
        {"id": "chk_bob", "document_id": "doc_bob", "title": "Bob Notes", "content": "bob in orgx", "user_id": "bob", "org_id": "org_x"},
        # Chunk 4: Charlie in OrgY
        {"id": "chk_charlie", "document_id": "doc_charlie", "title": "Charlie Dossier", "content": "charlie in orgy", "user_id": "charlie", "org_id": "org_y"},
    ]

    vectors = _fake_vectors(len(chunks))
    store.add_vectors(vectors, chunks)

    # Use identical query vector to match all chunks
    query_vec = vectors[0:1]

    # 1. Anonymous search (user_id=None, org_id=None) -> only public chunks
    anon_results = store.search(query_vec, top_k=10, user_id=None, org_id=None, is_admin=False)
    anon_ids = {r["id"] for r in anon_results}
    assert anon_ids == {"chk_pub"}, f"Anonymous query leaked private chunks: {anon_ids}"

    # 2. Alice searching (user_id='alice', org_id=None) -> sees public and Alice's own doc
    alice_results = store.search(query_vec, top_k=10, user_id="alice", org_id=None, is_admin=False)
    alice_ids = {r["id"] for r in alice_results}
    assert "chk_pub" in alice_ids
    assert "chk_alice" in alice_ids
    assert "chk_orgx" not in alice_ids, "Alice without org_id must not see OrgX documents"
    assert "chk_bob" not in alice_ids, "Alice must not see Bob's documents"
    assert "chk_charlie" not in alice_ids, "Alice must not see Charlie's documents"

    # 3. Bob searching in OrgX (user_id='bob', org_id='org_x') -> sees public, OrgX shared, and Bob in OrgX
    bob_results = store.search(query_vec, top_k=10, user_id="bob", org_id="org_x", is_admin=False)
    bob_ids = {r["id"] for r in bob_results}
    assert "chk_pub" in bob_ids
    assert "chk_orgx" in bob_ids
    assert "chk_bob" in bob_ids
    assert "chk_alice" not in bob_ids, "Tenant Bob in OrgX must not see Alice's chunks"
    assert "chk_charlie" not in bob_ids, "Tenant Bob in OrgX must not see OrgY chunks"

    # 4. Charlie searching in OrgY (user_id='charlie', org_id='org_y') -> sees public and Charlie's doc
    charlie_results = store.search(query_vec, top_k=10, user_id="charlie", org_id="org_y", is_admin=False)
    charlie_ids = {r["id"] for r in charlie_results}
    assert "chk_pub" in charlie_ids
    assert "chk_charlie" in charlie_ids
    assert "chk_alice" not in charlie_ids
    assert "chk_orgx" not in charlie_ids
    assert "chk_bob" not in charlie_ids

    # 5. Org-level query for OrgX (user_id=None, org_id='org_x')
    orgx_results = store.search(query_vec, top_k=10, user_id=None, org_id="org_x", is_admin=False)
    orgx_ids = {r["id"] for r in orgx_results}
    assert "chk_pub" in orgx_ids
    assert "chk_orgx" in orgx_ids
    assert "chk_alice" not in orgx_ids
    assert "chk_bob" not in orgx_ids
    assert "chk_charlie" not in orgx_ids

    # 6. Admin query -> sees all chunks
    admin_results = store.search(query_vec, top_k=10, user_id=None, org_id=None, is_admin=True)
    admin_ids = {r["id"] for r in admin_results}
    assert admin_ids == {"chk_pub", "chk_alice", "chk_orgx", "chk_bob", "chk_charlie"}


@pytest.mark.asyncio
async def test_knowledge_engine_search_tenant_scoping_fail_closed(tmp_path, patch_embed):
    """KnowledgeEngine.search must enforce fail-closed multi-tenant scoping end-to-end."""
    store = FaissVectorStore(index_dir=str(tmp_path / "engine_vectors"))
    engine = KnowledgeEngine(vector_store=store)

    # Ingest documents with different tenant contexts
    await engine.index_document(
        title="Public Doc",
        content="public documentation content",
        tenant=None,  # anonymous / public
    )
    await engine.index_document(
        title="Alice Doc",
        content="alice personal data",
        tenant=TenantContext(user_id="alice"),
    )
    await engine.index_document(
        title="Bob OrgX Doc",
        content="bob private organizational data",
        tenant=TenantContext(user_id="bob", org_id="org_x"),
    )
    await engine.index_document(
        title="Charlie OrgY Doc",
        content="charlie proprietary research data",
        tenant=TenantContext(user_id="charlie", org_id="org_y"),
    )

    # 1. Anonymous search with tenant=None
    anon_res = await engine.search("data", top_k=10, tenant=None)
    anon_titles = {r["title"] for r in anon_res}
    assert anon_titles == {"Public Doc"}, f"Anonymous caller leaked: {anon_titles}"

    # 2. Anonymous search with TenantContext(user_id=None, org_id=None)
    anon_res_ctx = await engine.search("data", top_k=10, tenant=TenantContext(user_id=None, org_id=None))
    anon_titles_ctx = {r["title"] for r in anon_res_ctx}
    assert anon_titles_ctx == {"Public Doc"}

    # 3. Alice search
    alice_res = await engine.search("data", top_k=10, tenant=TenantContext(user_id="alice"))
    alice_titles = {r["title"] for r in alice_res}
    assert "Public Doc" in alice_titles
    assert "Alice Doc" in alice_titles
    assert "Bob OrgX Doc" not in alice_titles
    assert "Charlie OrgY Doc" not in alice_titles

    # 4. Bob search in OrgX
    bob_res = await engine.search("data", top_k=10, tenant=TenantContext(user_id="bob", org_id="org_x"))
    bob_titles = {r["title"] for r in bob_res}
    assert "Public Doc" in bob_titles
    assert "Bob OrgX Doc" in bob_titles
    assert "Alice Doc" not in bob_titles
    assert "Charlie OrgY Doc" not in bob_titles

    # 5. Charlie search in OrgY
    charlie_res = await engine.search("data", top_k=10, tenant=TenantContext(user_id="charlie", org_id="org_y"))
    charlie_titles = {r["title"] for r in charlie_res}
    assert "Public Doc" in charlie_titles
    assert "Charlie OrgY Doc" in charlie_titles
    assert "Alice Doc" not in charlie_titles
    assert "Bob OrgX Doc" not in charlie_titles

    # 6. Admin search
    admin_res = await engine.search("data", top_k=10, tenant=TenantContext(is_admin=True))
    admin_titles = {r["title"] for r in admin_res}
    assert admin_titles == {"Public Doc", "Alice Doc", "Bob OrgX Doc", "Charlie OrgY Doc"}
