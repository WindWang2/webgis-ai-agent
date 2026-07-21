"""
PostgreSQL + PostGIS 数据库模型
B011 Fix: 使用统一的 Base 单例，避免重复定义冲突
"""
from datetime import datetime, timezone
from sqlalchemy import (
    Column, Integer, String, Text, DateTime, Boolean, BigInteger, ForeignKey, Index, UniqueConstraint, JSON, CheckConstraint
)
from sqlalchemy.orm import relationship
from app.core.database import Base

class Organization(Base):
    """组织机构表"""
    __tablename__ = "organizations"
    
    id = Column(Integer, primary_key=True)
    name = Column(String(255), nullable=False)
    slug = Column(String(100), unique=True, nullable=False)
    description = Column(Text)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

class User(Base):
    """用户表"""
    __tablename__ = "users"
    
    id = Column(String(255), primary_key=True)
    org_id = Column(Integer, ForeignKey("organizations.id", ondelete="CASCADE"))
    username = Column(String(100), nullable=False, unique=True)
    email = Column(String(255), unique=True, nullable=False)
    password_hash = Column(String(255))
    full_name = Column(String(255))
    avatar_url = Column(String(500))
    role = Column(String(20), default="viewer")
    is_active = Column(Boolean, default=True)
    email_verified = Column(Boolean, default=False)
    last_login = Column(DateTime)
    login_count = Column(Integer, default=0)
    token_version = Column(Integer, nullable=False, default=0, server_default="0")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    
    organization = relationship("Organization", backref="users", lazy="selectin")

    __table_args__ = (
        CheckConstraint("role IN ('viewer', 'editor', 'admin')", name="ck_user_role"),
    )

class Layer(Base):
    """图层表 - 支持 Vector/Raster/Tile 三种类型"""
    __tablename__ = "layers"
    
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    org_id = Column(Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    creator_id = Column(String(255), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    name = Column(String(255), nullable=False, index=True)
    description = Column(Text)
    category = Column(String(50), index=True)
    layer_type = Column(String(20), nullable=False)
    geometry_type = Column(String(50))
    source_format = Column(String(50))
    source_url = Column(String(1000))
    crs = Column(String(100), default="EPSG:4326")
    bounds = Column(JSON)
    feature_count = Column(BigInteger, default=0)
    properties_def = Column(JSON)
    style_config = Column(JSON)
    visibility = Column(String(20), default="org")
    is_basemap = Column(Boolean, default=False)
    status = Column(String(20), default="pending")
    error_message = Column(Text)
    processing_progress = Column(Integer, default=0)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    
    __table_args__ = (
        UniqueConstraint("org_id", "name", name="uq_layer_org_name"),
        Index("idx_layer_status", "status"),
        Index("idx_layer_created", "created_at"),
        Index("idx_layer_org_status", "org_id", "status"),
        Index("idx_layer_org_category_status", "org_id", "category", "status"),
        CheckConstraint("layer_type IN ('vector', 'raster', 'tile')", name="ck_layer_type"),
        CheckConstraint("visibility IN ('org', 'public', 'private')", name="ck_layer_visibility"),
        CheckConstraint("status IN ('pending', 'processing', 'ready', 'error')", name="ck_layer_status"),
    )
    
    organization = relationship("Organization", backref="layers", lazy="selectin")
    creator = relationship("User", backref="layers", lazy="selectin")

class AnalysisTask(Base):
    """空间分析任务表"""
    __tablename__ = "analysis_tasks"
    
    id = Column(BigInteger, primary_key=True)
    org_id = Column(Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    creator_id = Column(String(255), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    layer_id = Column(BigInteger, ForeignKey("layers.id", ondelete="SET NULL"), nullable=True)
    result_layer_id = Column(BigInteger, ForeignKey("layers.id", ondelete="SET NULL"), nullable=True)
    task_type = Column(String(50), nullable=False)
    parameters = Column(JSON, nullable=False)
    celery_task_id = Column(String(100), unique=True)
    status = Column(String(20), default="pending", index=True)
    progress = Column(Integer, default=0)
    progress_message = Column(String(255))
    result_summary = Column(JSON)
    error_trace = Column(Text)
    retry_count = Column(Integer, default=0)
    max_retries = Column(Integer, default=3)
    queued_at = Column(DateTime)
    started_at = Column(DateTime)
    completed_at = Column(DateTime)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    
    __table_args__ = (
        Index("idx_task_status", "status"),
        Index("idx_task_org_status", "org_id", "status"),
        Index("idx_task_org_type_status", "org_id", "task_type", "status"),
        CheckConstraint("status IN ('pending', 'queued', 'running', 'completed', 'failed', 'cancelled')", name="ck_task_status"),
        CheckConstraint("progress >= 0 AND progress <= 100", name="ck_task_progress"),
    )

class LayerPermission(Base):
    """图层权限细粒度控制"""
    __tablename__ = "layer_permissions"
    
    id = Column(Integer, primary_key=True)
    layer_id = Column(BigInteger, ForeignKey("layers.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(String(255), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    permission = Column(String(20), nullable=False)
    granted_by = Column(String(255), ForeignKey("users.id", ondelete="SET NULL"))
    granted_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    expires_at = Column(DateTime)
    
    __table_args__ = (
        UniqueConstraint("layer_id", "user_id", name="uq_permission"),
        CheckConstraint("permission IN ('read', 'write', 'admin')", name="ck_permission"),
    )
    
    layer = relationship("Layer", backref="permissions", lazy="selectin")
    user = relationship("User", foreign_keys=[user_id], lazy="selectin")

def get_init_sql():
    """获取 PostGIS 初始化 SQL"""
    return """
    CREATE EXTENSION IF NOT EXISTS postgis;
    CREATE EXTENSION IF NOT EXISTS postgis_topology;
    """

class Conversation(Base):
    __tablename__ = "conversations"

    id = Column(String(255), primary_key=True)
    # Nullable：兼容历史匿名会话；新认证会话写入 users.id；查询时按 owner 过滤
    user_id = Column(String(255), ForeignKey("users.id"), nullable=True, index=True)
    title = Column(String(200), default="新对话")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    messages = relationship("Message", back_populates="conversation", cascade="all, delete-orphan")

class Message(Base):
    __tablename__ = "messages"

    id = Column(Integer, primary_key=True, autoincrement=True)
    conversation_id = Column(String(255), ForeignKey("conversations.id", ondelete="CASCADE"))
    role = Column(String(20), nullable=False)  # user / assistant / tool
    content = Column(Text, default="")
    reasoning_content = Column(Text, nullable=True)  # reasoning/thinking process
    tool_calls = Column(JSON, nullable=True)  # FC tool calls
    tool_call_id = Column(String(255), nullable=True)
    tool_result = Column(JSON, nullable=True)  # tool execution result
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    conversation = relationship("Conversation", back_populates="messages")

    __table_args__ = (
        CheckConstraint("role IN ('user', 'assistant', 'tool')", name="ck_message_role"),
    )

__all__ = ["Base", "Organization", "User", "Layer", "AnalysisTask", "LayerPermission", "Conversation", "Message", "get_init_sql"]