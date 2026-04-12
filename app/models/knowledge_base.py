"""
向量知识库数据模型 - 基于 FAISS 的本地向量存储
Document: 文档元信息
Chunk: 文档分块
Embedding: 向量索引 (由 FAISS 管理，此表仅用于元数据追踪)
"""
import os
import uuid
from datetime import datetime, timezone
from sqlalchemy import (
    Column, String, Text, Integer, DateTime, Index, ForeignKey
)
from app.core.database import Base


class Document(Base):
    """知识库文档表"""
    __tablename__ = "knowledge_documents"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    title = Column(String(500), nullable=False)
    content = Column(Text, nullable=True)  # 原始内容摘要
    file_type = Column(String(50), nullable=True)  # txt/markdown/pdf/etc
    file_path = Column(String(1000), nullable=True)  # 文件存储路径
    chunk_count = Column(Integer, default=0)
    status = Column(String(20), default="pending")  # pending/indexing/completed/failed
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    indexed_at = Column(DateTime, nullable=True)

    __table_args__ = (
        Index("idx_document_status", "status"),
    )


class Chunk(Base):
    """文档分块表 - 存储每个文本片段"""
    __tablename__ = "knowledge_chunks"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    document_id = Column(
        String(36),
        ForeignKey("knowledge_documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )
    content = Column(Text, nullable=False)  # 分块文本内容
    chunk_index = Column(Integer, nullable=False)  # 在原文档中的顺序
    start_char = Column(Integer, nullable=True)  # 原文起始位置
    end_char = Column(Integer, nullable=True)  # 原文结束位置
    
    __table_args__ = (
        Index("idx_chunk_document", "document_id"),
    )


__all__ = ["Document", "Chunk"]