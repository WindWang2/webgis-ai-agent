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
        # #545（#485 的 add 侧镜像）：向量已在 engine.index_document 提交，
        # 而 DB 行没写进去 —— 若不补偿，这些向量成为永久可检索孤儿：
        #   - 无 DB 行 → API delete_document 的所有权检查查不到 → 删不掉
        #   - 无 deleted 标记 → compact 永远不会清理
        # 补偿用与删除同源的幂等原语（engine.delete_document → mark_deleted），
        # 只在向量确实已提交的路径执行（本 except 仅在 index_document 成功之后）。
        try:
            await engine.delete_document(doc_id, tenant=tenant)
            logger.warning(
                "[RAG] add_document compensation: soft-deleted orphan vectors for %s", doc_id
            )
        except Exception as comp_e:
            # 补偿也失败：向量仍然可检索。显式标记，绝不静默成功。
            logger.error(
                "[RAG] add_document compensation FAILED for %s — orphan vectors remain "
                "retrievable: %s", doc_id, comp_e,
                exc_info=True,
            )
            return {
                "error": f"document indexed but metadata persistence failed: {e}",
                "orphan_possible": True,
            }
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

    #485 删除顺序与补偿语义（vector-first）：

    1. 只读所有权校验（不改任何状态）。
    2. **先**清理向量索引（engine.delete_document：软删标记 + 可能的压缩）。
       - 失败 → 记日志并返回 False，**DB 行原样保留**，调用方重试即可恢复
         （owner_check 仍能找到行，重新走一遍同样的流程）。
    3. 向量清理成功后**再**删除 DB 行（Chunk → Document）。
       - 此时若 DB 删除失败 → 返回 False；搜索结果已不含该文档（向量已软删），
         DB 行仍在，重试依然安全：FaissVectorStore.mark_deleted 对已清理的
         document_id 是幂等 no-op，不会破坏状态。

    旧实现的顺序相反（先删 DB 行、后清理向量且不受保护）：向量清理一旦失败，
    向量永久孤儿化，且重试会因 owner_check 查不到行而直接 False —— 不可恢复。
    """
    from sqlalchemy import delete, select

    from app.models.knowledge_base import Chunk, Document
    from app.services.rag.engine import TenantContext, get_knowledge_engine
    from app.tools._utils import async_db_session

    # ── 1. 只读所有权校验 ────────────────────────────────────────────────
    # Delete is creator-only (docstring: "仅删除属于自己的文档"). The
    # previous org-scoped check let any member of an org delete another
    # member's document — inconsistent with templates/projects, which
    # are creator-or-admin. org_id stays on the TenantContext below for
    # the vector-store cleanup, but it must NOT broaden the SQL guard.
    if user_id is None:
        # No authenticated creator identity -> cannot authorize delete.
        return False

    try:
        async with async_db_session() as db:
            owner_check = select(Document.id).where(
                Document.id == document_id, Document.creator_id == user_id
            )
            existing = (await db.execute(owner_check)).scalar_one_or_none()
            if existing is None:
                logger.warning(
                    "[RAG] delete denied: doc %s not owned by user %s org %s",
                    document_id, user_id, org_id,
                )
                return False
    except Exception as e:
        logger.error(f"[RAG] delete_document owner check failed: {e}", exc_info=True)
        return False

    # ── 2. 向量清理先行：失败则 DB 行保留，可重试 ─────────────────────────
    tenant = TenantContext(user_id=user_id, org_id=str(org_id) if org_id is not None else None)
    engine = get_knowledge_engine()
    try:
        vector_ok = await engine.delete_document(document_id, tenant=tenant)
    except Exception as e:
        # DB rows are untouched — the caller can retry the whole delete.
        logger.error(
            f"[RAG] delete_document vector cleanup failed for {document_id}; "
            f"DB rows kept for retry: {e}",
            exc_info=True,
        )
        return False
    if vector_ok is False:
        logger.warning(
            "[RAG] delete_document vector cleanup returned False for %s; DB rows kept for retry",
            document_id,
        )
        return False

    # ── 3. DB 行删除（向量已清理；失败可安全重试，mark_deleted 幂等）──────
    try:
        async with async_db_session() as db:
            await db.execute(delete(Chunk).where(Chunk.document_id == document_id))
            await db.execute(delete(Document).where(Document.id == document_id))
    except Exception as e:
        logger.error(
            f"[RAG] delete_document DB cleanup failed for {document_id} after vector "
            f"cleanup succeeded; retry is safe (mark_deleted is idempotent): {e}",
            exc_info=True,
        )
        return False

    return True


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
        # #484：count 与 items 必须共享同一租户过滤 —— 之前 count 是全表
        # `count(Document)`，导致分页 total 按全局文档数计算（翻页越界），
        # 并向每个租户泄露全平台文档总量。过滤条件单源化为一个 where 列表，
        # 两边引用同一份，杜绝再次漂移。
        tenant_filters = []
        if org_id is not None:
            tenant_filters.append(Document.org_id == org_id)
        elif user_id is not None:
            tenant_filters.append(Document.creator_id == user_id)

        count_stmt = select(func.count()).select_from(Document)
        if tenant_filters:
            count_stmt = count_stmt.where(*tenant_filters)
        total = (await db.execute(count_stmt)).scalar_one()

        stmt = select(Document).order_by(Document.created_at.desc()).offset(offset).limit(limit)
        if tenant_filters:
            stmt = stmt.where(*tenant_filters)
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