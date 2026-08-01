"""
RAG 检索增强生成服务 - 基于 FAISS 的本地向量搜索与知识库管理。
深度模块适配层，将向量存储委托给 FaissVectorStore，分块委托给 chunker。
"""
import asyncio
import logging
import uuid
from typing import Any, Optional

import numpy as np

from app.services.rag.chunker import split_into_chunks, split_markdown_sections
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


def _save_metadata(meta: dict) -> None:
    """Backward compatibility helper for metadata saving."""
    _default_store.save_metadata(meta)


def _mark_deleted(doc_id: str) -> None:
    """Backward compatibility helper for marking deleted docs."""
    _default_store.mark_deleted(doc_id)


def _split_markdown_section(text: str) -> list[str]:
    """Backward compatibility helper for markdown splitting."""
    return split_markdown_sections(text)


async def add_document(
    title: str,
    content: str,
    file_type: str = "text",
    user_id: Optional[str] = None,
    org_id: Optional[int] = None,
) -> dict[str, Any]:
    """
    添加文档到知识库，执行全文嵌入。
    """
    from datetime import datetime, timezone
    from app.models.knowledge_base import Chunk, Document
    from app.tools._utils import async_db_session

    doc_id = str(uuid.uuid4())

    if file_type == "markdown":
        sections_chunks = split_markdown_sections(content)
        chunks_list = []
        pos = 0
        for i, sec in enumerate(sections_chunks):
            chunks_list.append({
                "content": sec.strip(),
                "start_char": pos,
                "end_char": pos + len(sec),
                "chunk_index": i,
            })
            pos += len(sec)
        chunk_list = chunks_list
    else:
        chunk_list = split_into_chunks(content)

    if not chunk_list:
        return {"error": "Empty content"}

    try:
        async with async_db_session() as db:
            doc = Document(
                id=doc_id,
                title=title,
                content=content[:1000] if content else "",
                file_type=file_type,
                chunk_count=len(chunk_list),
                status="indexing",
                creator_id=user_id,
                org_id=org_id,
            )
            db.add(doc)

            for ch in chunk_list:
                chunk = Chunk(
                    id=str(uuid.uuid4()),
                    document_id=doc_id,
                    content=ch["content"],
                    chunk_index=ch["chunk_index"],
                    start_char=ch.get("start_char"),
                    end_char=ch.get("end_char"),
                )
                db.add(chunk)

            texts = [ch["content"] for ch in chunk_list]
            loop = asyncio.get_running_loop()
            vectors = await loop.run_in_executor(
                None, lambda: _default_store.embed_texts(texts)
            )

            chunks_meta = [
                {
                    "document_id": doc_id,
                    "chunk_id": f"{doc_id}_chunk_{i}",
                    "title": title,
                    "content": text,
                }
                for i, text in enumerate(texts)
            ]

            _default_store.add_vectors(vectors, chunks_meta)

            doc.status = "completed"
            doc.indexed_at = datetime.now(timezone.utc)

        return {
            "document_id": doc_id,
            "title": title,
            "chunk_count": len(chunk_list),
            "status": "completed",
        }

    except Exception as e:
        logger.error(f"[RAG] add_document failed: {e}", exc_info=True)
        return {"error": str(e)}


async def semantic_search(
    query: str,
    top_k: int = 5,
    document_id: Optional[str] = None,
    user_id: Optional[str] = None,
    org_id: Optional[int] = None,
) -> list[dict[str, Any]]:
    """
    语义向量相似度搜索。
    """
    try:
        query_vec = _default_store.embed_texts([query])

        raw_results = _default_store.search(query_vec, top_k=top_k * 2)

        results = []
        for ch_meta in raw_results:
            if ch_meta.get("deleted"):
                continue
            if document_id and ch_meta.get("document_id") != document_id:
                continue

            results.append({
                "document_id": ch_meta.get("document_id"),
                "chunk_id": ch_meta.get("chunk_id"),
                "content": ch_meta.get("content", "")[:500],
                "score": float(ch_meta.get("score", 0.0)),
            })

            if len(results) >= top_k:
                break

        return results

    except Exception as e:
        logger.error(f"[RAG] semantic_search failed: {e}", exc_info=True)
        return []


async def delete_document(
    document_id: str,
    user_id: Optional[str] = None,
    org_id: Optional[int] = None,
) -> bool:
    """
    删除指定文档的所有chunk和相关向量。
    """
    from sqlalchemy import delete, select
    from app.models.knowledge_base import Chunk, Document
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
                    document_id,
                    user_id,
                    org_id,
                )
                return False
            await db.execute(delete(Chunk).where(Chunk.document_id == document_id))
            await db.execute(delete(Document).where(Document.id == document_id))

            _default_store.mark_deleted(document_id)

        return True
    except Exception as e:
        logger.error(f"[RAG] delete_document failed: {e}", exc_info=True)
        return False


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