"""
RAG 检索增强生成服务 - 基于 FAISS 的本地向量搜索与知识库管理。
深度模块适配层，将向量存储委托给 FaissVectorStore，分块委托给 chunker。
"""
import logging
import uuid
from typing import Any, Optional

from app.services.rag.chunker import split_into_chunks
from app.services.rag.faiss_store import FaissVectorStore

logger = logging.getLogger(__name__)

# Single default vector store instance for application usage
_default_store = FaissVectorStore()


def _get_faiss_index(dim: int = 384):
    """Backward compatibility helper returning underlying FAISS index."""
    return _default_store._get_index(dim)


def _get_embedding_model():
    """Backward compatibility helper returning underlying embedding model."""
    return _default_store._get_embedding_model()


def _load_metadata() -> dict:
    """Backward compatibility helper for metadata loading."""
    return _default_store.load_metadata()


async def add_document(
    title: str,
    content: str,
    file_type: str = "text",
    user_id: Optional[str] = None,
    org_id: Optional[Any] = None,
) -> dict[str, Any]:
    """
    添加文档到知识库，执行全文嵌入（委托给 KnowledgeEngine），
    并持久化 Document/Chunk 元数据行（list_documents 依赖该表）。
    """
    from datetime import datetime, timezone

    from app.models.knowledge_base import Chunk, Document
    from app.services.rag.engine import TenantContext, get_knowledge_engine
    from app.tools._utils import async_db_session

    tenant = TenantContext(user_id=user_id, org_id=str(org_id) if org_id is not None else None)
    engine = get_knowledge_engine()
    result = await engine.index_document(
        title=title, content=content, file_type=file_type, tenant=tenant
    )
    if "error" in result:
        return result

    doc_id = result["document_id"]
    chunk_records = result.get("chunks", [])

    try:
        async with async_db_session() as db:
            doc = Document(
                id=doc_id,
                title=title,
                content=content[:1000] if content else "",
                file_type=file_type,
                chunk_count=len(chunk_records),
                status="completed",
                creator_id=user_id,
                org_id=org_id,
                indexed_at=datetime.now(timezone.utc),
            )
            db.add(doc)

            for ch in chunk_records:
                db.add(
                    Chunk(
                        id=str(uuid.uuid4()),
                        document_id=doc_id,
                        content=ch["content"],
                        chunk_index=ch["chunk_index"],
                        start_char=ch.get("start_char"),
                        end_char=ch.get("end_char"),
                    )
                )
    except Exception as e:
        # Vectors are already indexed; surface the failure rather than reporting
        # success for a document that will never appear in list_documents.
        logger.error(f"[RAG] add_document metadata persistence failed: {e}", exc_info=True)
        return {"error": f"document indexed but metadata persistence failed: {e}"}

    return {
        "document_id": doc_id,
        "title": title,
        "chunk_count": len(chunk_records),
        "status": "completed",
    }


async def semantic_search(
    query: str,
    top_k: int = 5,
    document_id: Optional[str] = None,
    user_id: Optional[str] = None,
    org_id: Optional[Any] = None,
) -> list[dict[str, Any]]:
    """
    语义向量相似度搜索（委托给 KnowledgeEngine）。
    """
    from app.services.rag.engine import TenantContext, get_knowledge_engine
    tenant = TenantContext(user_id=user_id, org_id=str(org_id) if org_id is not None else None)
    engine = get_knowledge_engine()
    return await engine.search(query=query, tenant=tenant, top_k=top_k, document_id=document_id)


async def delete_document(
    document_id: str,
    user_id: Optional[str] = None,
    org_id: Optional[Any] = None,
) -> bool:
    """
    删除指定文档的所有chunk和相关向量。
    审计 S41：先校验所有权，非本人/本组织的文档拒绝删除。
    """
    from sqlalchemy import delete, select

    from app.models.knowledge_base import Chunk, Document
    from app.services.rag.engine import TenantContext, get_knowledge_engine
    from app.tools._utils import async_db_session

    try:
        async with async_db_session() as db:
            owner_check = select(Document).where(Document.id == document_id)
            if org_id is not None:
                owner_check = owner_check.where(Document.org_id == org_id)
            elif user_id is not None:
                owner_check = owner_check.where(Document.creator_id == user_id)
            existing = (await db.execute(owner_check)).scalar_one_or_none()
            if existing is None:
                logger.warning(
                    "[RAG] delete denied: doc %s not owned by user %s org %s",
                    document_id, user_id, org_id,
                )
                return False

            await db.execute(delete(Chunk).where(Chunk.document_id == document_id))
            await db.execute(delete(Document).where(Document.id == document_id))
    except Exception as e:
        logger.error(f"[RAG] delete_document failed: {e}", exc_info=True)
        return False

    tenant = TenantContext(user_id=user_id, org_id=str(org_id) if org_id is not None else None)
    engine = get_knowledge_engine()
    return await engine.delete_document(document_id, tenant=tenant)


async def list_documents(
    limit: int = 50,
    offset: int = 0,
    user_id: Optional[str] = None,
    org_id: Optional[int] = None,
) -> dict[str, Any]:
    """列出知识库文档"""
    from sqlalchemy import func, select

    from app.models.knowledge_base import Document
    from app.tools._utils import async_db_session

    async with async_db_session() as db:
        count_stmt = select(func.count()).select_from(Document)
        total_result = await db.execute(count_stmt)
        total = total_result.scalar_one()

        stmt = select(Document).order_by(Document.created_at.desc()).offset(offset).limit(limit)
        if org_id is not None:
            stmt = stmt.where(Document.org_id == org_id)
        elif user_id is not None:
            stmt = stmt.where(Document.creator_id == user_id)
        result = await db.execute(stmt)
        items = result.scalars().all()
        return {
            "total": total,
            "items": [
                {
                    "id": d.id,
                    "title": d.title,
                    "file_type": d.file_type,
                    "chunk_count": d.chunk_count,
                    "status": d.status,
                    "created_at": d.created_at.isoformat() if d.created_at else None,
                }
                for d in items
            ],
        }


async def retrieve_context(
    query: str,
    top_k: int = 3,
    user_id: Optional[str] = None,
    org_id: Optional[int] = None,
) -> str:
    """
    为对话引擎提供的上下文检索接口。
    """
    results = await semantic_search(
        query, top_k=top_k, user_id=user_id, org_id=org_id
    )
    if not results:
        return ""

    ctx_parts = [f"[{r['score']:.2f}] {r['content']}" for r in results]
    return "\n\n---\n\n".join(ctx_parts)


__all__ = [
    "add_document",
    "semantic_search",
    "delete_document",
    "list_documents",
    "retrieve_context",
    "split_into_chunks",
]