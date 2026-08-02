# 38. KnowledgeEngine & Multi-Tenant Vector Isolation

Date: 2026-08-02

## Status

Accepted

## Context

Prior to this decision, RAG document indexing and semantic vector searching were managed via procedurally scattered functions in `app/services/rag_service.py`. Vector chunk metadata ignored `user_id` and `org_id` parameters passed by API routes, bypassing multi-tenant security isolation. Furthermore, vector index deletion relied solely on metadata flags without a mechanism to compact the FAISS index.

## Decisions

1. **KnowledgeEngine Deep Module**: Created `KnowledgeEngine` in `app/services/rag/engine.py` encapsulating document chunking, embedding generation, tenant filtering, and vector compaction.
2. **TenantContext Domain Object**: Introduced `TenantContext` (`user_id`, `org_id`, `is_admin`) capturing security credentials and enforcing strict candidate filtering during `KnowledgeEngine.search()` and `VectorStoreProtocol.search()`.
3. **Index Compaction Seam**: Added `compact()` to `VectorStoreProtocol` and `FaissVectorStore`, automatically rebuilding the vector index when deleted vector ratio exceeds 20%.
4. **Backward Compatibility**: Converted `app/services/rag_service.py` into a thin shim delegating to `get_knowledge_engine()`.

## Consequences

- **Security**: Strict multi-tenant isolation guaranteed across all RAG vector searches.
- **Locality**: Storage locks, tenant security, and FAISS index compaction concentrate within `KnowledgeEngine`.
- **Testability**: `KnowledgeEngine` accepts injected `VectorStoreProtocol` instances for zero-disk-I/O test doubles.
