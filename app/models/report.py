"""
报告数据模型 - 支持从会话历史生成 PDF/HTML/Markdown 报告
"""
from datetime import datetime, timezone
from sqlalchemy import (
    Column, String, Integer, Text, DateTime, Index
)
from sqlalchemy.dialects.postgresql import UUID
from app.core.database import Base


class Report(Base):
    """分析报告表"""
    __tablename__ = "reports"

    id = Column(String(36), primary_key=True)
    session_id = Column(String(255), nullable=False, index=True)
    title = Column(String(500), nullable=False, default="分析报告")
    format = Column(String(20), nullable=False, default="pdf")
    status = Column(String(20), nullable=False, default="pending", index=True)
    file_path = Column(Text, nullable=True)
    file_size = Column(Integer, nullable=True)
    share_code = Column(String(32), unique=True, nullable=True, index=True)
    share_expires_at = Column(DateTime, nullable=True)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        Index("idx_report_session", "session_id"),
        Index("idx_report_status", "status"),
        Index("idx_report_share", "share_code"),
    )


__all__ = ["Report"]
