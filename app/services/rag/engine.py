"""
KnowledgeEngine - Deep RAG Knowledge Retrieval & Vector Indexing Engine.
Enforces TenantContext multi-tenant security isolation and index compaction.
"""
import logging
import uuid
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from app.services.rag.chunker import split_into_chunks, split_markdown_sections
from app.services.rag.protocol import VectorStoreProtocol

logger = logging.getLogger(__name__)


@dataclass
class TenantContext:
    """Domain object capturing caller multi-tenant access control credentials."""

    user_id: Optional[str] = None
    org_id: Optional[str] = None
    is_admin: bool = False


_active_engine: Optional["KnowledgeEngine"] = None


class KnowledgeEngine:
    """Deep Knowledge Retrieval & Vector Indexing Engine."""

    def __init__(self, vector_store: Optional[VectorStoreProtocol] = None):
        if vector_store is None:
            from app.services.rag.faiss_store import FaissVectorStore
            self._store = FaissVectorStore()
        else:
            self._store = vector_store

    async def index_document(
        self,
        title: str,
        content: str,
        file_type: str = "text",
        tenant: Optional[TenantContext] = None,
    ) -> Dict[str, Any]:
        """Process, chunk, embed, and index a document into the vector store."""
        if not content.strip():
            return {"error": "Document content is empty"}

        if file_type == "markdown":
            sections = split_markdown_sections(content)
            chunk_list = [{"content": sec.strip(), "chunk_index": i} for i, sec in enumerate(sections) if sec.strip()]
        else:
            chunk_list = split_into_chunks(content)

        if not chunk_list:
            return {"error": "No valid text chunks generated from document"}

        texts = [c["content"] if isinstance(c, dict) else str(c) for c in chunk_list]
        embeddings = self._store.embed_texts(texts)
        doc_id = f"doc_{uuid.uuid4().hex[:12]}"

        user_id = tenant.user_id if tenant else None
        org_id = tenant.org_id if tenant else None

        chunks_meta = [
            {
                "id": f"chk_{uuid.uuid4().hex[:12]}",
                "document_id": doc_id,
                "title": title,
                "content": text,
                "file_type": file_type,
                "user_id": user_id,
                "org_id": org_id,
            }
            for text in texts
        ]

        self._store.add_vectors(embeddings, chunks_meta)
        logger.info(f"KnowledgeEngine: indexed doc_id={doc_id} title='{title}' chunks={len(chunk_list)}")
        return {
            "document_id": doc_id,
            "title": title,
            "chunks_count": len(chunk_list),
            "status": "indexed",
        }

    async def search(
        self,
        query: str,
        tenant: Optional[TenantContext] = None,
        top_k: int = 5,
        document_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Perform semantic search with tenant-aware candidate filtering."""
        if not query.strip():
            return []

        query_vectors = self._store.embed_texts([query])

        user_id = tenant.user_id if tenant else None
        org_id = tenant.org_id if tenant else None
        is_admin = tenant.is_admin if tenant else False

        results = self._store.search(
            query_vectors,
            top_k=top_k * 2,  # Over-fetch for candidate filtering
            user_id=user_id,
            org_id=org_id,
            is_admin=is_admin,
        )

        filtered = []
        for r in results:
            if r.get("deleted", False):
                continue
            if document_id and r.get("document_id") != document_id:
                continue

            # Additional tenant check defense-in-depth
            if not is_admin and (user_id or org_id):
                doc_user = r.get("user_id")
                doc_org = r.get("org_id")
                if doc_user and user_id and doc_user != user_id:
                    continue
                if doc_org and org_id and doc_org != org_id:
                    continue

            filtered.append(r)
            if len(filtered) >= top_k:
                break

        return filtered

    async def delete_document(
        self, doc_id: str, tenant: Optional[TenantContext] = None
    ) -> bool:
        """Mark document as deleted and trigger compaction if threshold exceeded."""
        self._store.mark_deleted(doc_id)
        logger.info(f"KnowledgeEngine: soft-deleted doc_id={doc_id}")

        stats = self._store.get_stats()
        deleted_count = stats.get("deleted_count", 0)
        total_vectors = stats.get("total_vectors", 0)

        if total_vectors > 0 and (deleted_count / total_vectors) >= 0.20:
            logger.info("KnowledgeEngine: deleted ratio exceeds 20%, executing compact()")
            self.compact_index()

        return True

    def compact_index(self) -> Dict[str, Any]:
        """Compact underlying vector index."""
        if hasattr(self._store, "compact"):
            return self._store.compact()
        return {}


def get_knowledge_engine() -> KnowledgeEngine:
    """Return active KnowledgeEngine instance."""
    global _active_engine
    if _active_engine is None:
        _active_engine = KnowledgeEngine()
    return _active_engine


def set_active_knowledge_engine(engine: KnowledgeEngine) -> None:
    """Override active knowledge engine (for testing)."""
    global _active_engine
    _active_engine = engine
