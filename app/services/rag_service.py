"""
RAG 检索增强生成服务 - 基于 FAISS 的本地向量搜索
支持: 文档嵌入、分块、相似度检索、与对话引擎集成
"""
import io
import json
import math
import os
import re
import uuid
from pathlib import Path
from typing import Any, Optional

import numpy as np
import logging

logger = logging.getLogger(__name__)

# 全局向量索引和模型缓存
_faiss_index = None
_embedding_model = None
INDEX_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data", "vectors_store")


def _get_embedding_model():
    """延迟加载 embedding 模型"""
    global _embedding_model
    if _embedding_model is None:
        try:
            from sentence_transformers import SentenceTransformer
            _embedding_model = SentenceTransformer("paraphrase-multilingual-MiniLM-L12-v2")
            logger.info("[RAG] Loaded embedding model: paraphrase-multilingual-MiniLM-L12-v2")
        except Exception as e:
            logger.error(f"[RAG] Failed to load embedding model: {e}")
            raise
    return _embedding_model


def _get_faiss_index(dim: int = 384):
    """获取或创建 FAISS 索引"""
    global _faiss_index
    if _faiss_index is None:
        try:
            import faiss
            # 使用 Inner Product (cosine similarity via normalized vectors)
            _faiss_index = faiss.IndexFlatIP(dim)
            logger.info(f"[RAG] Created FAISS index, dim={dim}")
            
            # 如果存在持久化索引则加载
            index_file = os.path.join(INDEX_DIR, "index.faiss")
            meta_file = os.path.join(INDEX_DIR, "metadata.json")
            if os.path.exists(index_file) and os.path.exists(meta_file):
                _faiss_index = faiss.read_index(index_file)
                logger.info("[RAG] Loaded existing FAISS index")
        except Exception as e:
            logger.error(f"[RAG] Failed to init FAISS: {e}")
            raise
    return _faiss_index


def _save_index():
    """持久化 FAISS 索引"""
    global _faiss_index
    if _faiss_index is not None:
        os.makedirs(INDEX_DIR, exist_ok=True)
        try:
            import faiss
            faiss.write_index(_faiss_index, os.path.join(INDEX_DIR, "index.faiss"))
            logger.info("[RAG] Saved FAISS index to disk")
        except Exception as e:
            logger.warning(f"[RAG] Failed to save index: {e}")


# ----------------------------------------------------------------------
# 文档分块策略
# ----------------------------------------------------------------------

def split_into_chunks(
    text: str,
    max_tokens: int = 512,
    overlap: int = 50
) -> list[dict[str, Any]]:
    """
    将文本分割为多个chunk，支持重叠滑动窗口。
    
    Args:
        text: 输入文本
        max_tokens: 每个chunk的最大token数
        overlap: 相邻chunk的重叠token数
        
    Returns:
        [{content, start_char, end_char, chunk_index}, ...]
    """
    chunks_size = max_tokens * 4  # 粗略估计: 1 token ≈ 4 chars
    overlap_chars = overlap * 4
    
    chunks_size = min(chunk_size, len(text))
    if chunk_size <= 0:
        return []
    
    chunks_list = []
    start = 0
    idx = 0
    
    while start < len(text):
        end = start + chunk_size
        chunk_text = text[start:end]
        
        # 尝试在自然断点处优化边界
        if idx > 0:
            # 寻找最近的分隔符
            for sep in ["\n\n", "\n", ". ", "。"]:
                last_sep = chunk_text.rfind(sep)
                if last_sep > chunk_size // 2:
                    end = start + last_sep + len(sep)
                    chunk_text = text[start:end]
                    break
        
        chunk_list.append({
            "content": chunk_text.strip(),
            "start_char": start,
            "end_char": end,
            "chunk_index": idx,
        })
        
        # 滑动窗口移动
        start = end - overlap_chars
        if start >= len(text):
            break
        idx += 1
    
    return chunk_list


# ----------------------------------------------------------------------
# 核心 RAG 操作
# ----------------------------------------------------------------------

async def add_document(
    title: str,
    content: str,
    file_type: str = "text"
) -> dict[str, Any]:
    """
    添加文档到知识库，执行全文嵌入。
    
    Args:
        title: 文档标题
        content: 文档全文
        file_type: 文档类型
        
    Returns:
        {document_id, chunk_count, status}
    """
    from datetime import datetime, timezone
    from sqlalchemy.orm import Session
    from app.core.database import SessionLocal
    from app.models.knowledge_base import Document, Chunk
    
    doc_id = str(uuid.uuid4())
    
    # 解析content
    if file_type == "markdown":
        # Markdown 特殊处理：按 ## 标题分段
        sections_chunks = _split_markdown_sections(content)
        chunks_list = []
        pos = 0
        for i, sec in enumerate(section_chunk):
            chunk_list.append({
                "content": sec.strip(),
                "start_char": pos,
                "end_char": pos + len(sec),
                "chunk_index": i,
            })
            pos += len(sec)
    else:
        # 默认分块策略
        chunk_list = split_into_chunks(content)
    
    if not chunk_list:
        return {"error": "Empty content"}
    
    # 保存到数据库
    db: Session = SessionLocal()
    try:
        doc = Document(
            id=doc_id,
            title=title,
            content=content[:1000] if content else "",
            file_type=file_type,
            chunk_count=len(chunk_list),
            status="indexing",
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
        
        db.commit()
        
        # 生成 embeddings 并建立向量索引
        embed_model = _get_embedding_model()
        texts = [ch["content"] for ch in chunk_list]
        vectors = embed_model.encode(texts, normalize_embeddings=True)
        
        # L2归一化转为一维数组
        vectors = np.array(vectors, dtype=np.float32)
        
        faiss_idx = _get_faiss_index(vectors.shape[1])
        faiss_idx.add(vectors)
        
        # 元数据保存
        _save_metadata(doc_id, title, len(chunk_list), texts)
        _save_index()
        
        doc.status = "completed"
        doc.indexed_at = datetime.now(timezone.utc)
        db.commit()
        
        return {
            "document_id": doc_id,
            "title": title,
            "chunk_count": len(chunk_list),
            "status": "completed"
        }
        
    except Exception as e:
        logger.error(f"[RAG] add_document failed: {e}", exc_info=True)
        db.rollback()
        return {"error": str(e)}
    finally:
        db.close()


def _split_markdown_section(text: str) -> list[str]:
    """将 Markdown 按 ## 标题分割成章节"""
    parts = re.split(r"(?=^##\s+.+$)", text, flags=re.MULTILINE)
    return [p.strip() for p in parts if p.strip()]


async def semantic_search(
    query: str,
    top_k: int = 5,
    document_id: Optional[str] = None
) -> list[dict[str, Any]]:
    """
    语义向量相似度搜索。
    
    Args:
        query: 查询文本
        top_k: 返回top结果数
        document_id: 可选限定文档
        
    Returns:
        [{document_id, chunk_id, content, score}, ...]
    """
    try:
        embed_model = _get_embedding_model()
        query_vec = embed_model.encode([query], normalize_embeddings=True)
        query_vec = np.array(query_vec, dtype=np.float32)
        
        faiss_idx = _get_faiss_index()
        scores, indices = faiss_idx.search(query_vec, top_k * 2)  # 多搜一些后面过滤
        
        # 读取元数据
        meta = _load_metadata()
        results = []
        
        for score, idx in zip(scores[0], indices[0]):
            if idx < 0 or idx >= len(meta.get("chunks", [])):
                continue
            
            chunk_meta = meta["chunks"][int(idx)]
            if document_id and chunk_meta.get("document_id") != document_id:
                continue
            
            results.append({
                "document_id": chunk_meta.get("document_id"),
                "chunk_id": chunk_meta.get("chunk_id"),
                "content": chunk_meta.get("content", "")[:500],
                "score": float(score),
            })
            
            if len(results) >= top_k:
                break
        
        return results
        
    except Exception as e:
        logger.error(f"[RAG] semantic_search failed: {e}", exc_info=True)
        return []


async def delete_document(document_id: str) -> bool:
    """
    删除指定文档的所有chunk和相关向量。
    
    Note: FAISS 目前无法真正删除向量，仅标记删除。
    实际可通过重建索引实现完全删除。此处简化处理返回成功。
    """
    from sqlalchemy.orm import Session
    from app.core.database import SessionLocal
    from app.models.knowledge_base import Document, Chunk
    
    db: Session = SessionLocal()
    try:
        db.query(Chunk).filter(Chunk.document_id == document_id).delete()
        db.query(Document).filter(Document.id == document_id).delete()
        db.commit()
        
        # 更新元数据标记删除
        _mark_deleted(document_id)
        
        return True
    except Exception as e:
        logger.error(f"[RAG] delete_document failed: {e}", exc_info=True)
        return False
    finally:
        db.close()


async def list_documents(
    limit: int = 50,
    offset: int = 0
) -> dict[str, Any]:
    """列出知识库文档"""
    from sqlalchemy.orm import Session
    from app.core.database import SessionLocal
    from app.models.knowledge_base import Document
    
    db: Session = SessionLocal()
    try:
        total = db.query(Document).count()
        items = (
            db.query(Document)
            .order_by(Document.created_at.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )
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
            ]
        }
    finally:
        db.close()


# ----------------------------------------------------------------------
# 元数据持久化 (配合 FAISS 使用)
# ----------------------------------------------------------------------

def _get_meta_path() -> str:
    os.makedirs(INDEX_DIR, exist_ok=True)
    return os.path.join(INDEX_DIR, "metadata.json")


def _save_metadata(doc_id: str, title: str, chunk_count: int, texts: list[str]):
    """保存向量对应元数据"""
    meta_path = _get_meta_path()
    meta = _load_metadata()
    
    base_idx = len(meta.get("chunks", []))
    for i, text in enumerate(texts):
        meta.setdefault("chunks", []).append({
            "index": base_idx + i,
            "document_id": doc_id,
            "chunk_id": f"{doc_id}_chunk_{i}",
            "title": title,
            "content": text,
        })
    
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)


def _load_metadata() -> dict:
    meta_path = _get_meta_path()
    if os.path.exists(meta_path):
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _mark_deleted(doc_id: str):
    """标记已删除的文档"""
    meta = _load_metadata()
    if "deleted" in meta:
        meta["deleted"].append(doc_id)
    else:
        meta["deleted"] = [doc_id]
    
    with open(_get_meta_path(), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False)


# ----------------------------------------------------------------------
# 对话引擎集成钩子
# ----------------------------------------------------------------------

async def retrieve_context(query: str, top_k: int = 3) -> str:
    """
    为对话引擎提供的上下文检索接口。
    返回拼接的上下文字符串供LLM使用。
    """
    results = await semantic_search(query, top_k=top_k)
    if not results:
        return ""
    
    ctx_parts = []
    for r in results:
        ctx_parts.append(f"[{r['score']:.2f}] {r['content']}")
    
    return "\n\n---\n\n".join(ctx_parts)


__all__ = [
    "add_document",
    "semantic_search",
    "delete_document",
    "list_documents",
    "retrieve_context",
]