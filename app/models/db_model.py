"""
PostgreSQL + PostGIS 数据库模型
B011 Fix: 使用统一的 Base 单例，避免重复定义冲突
"""
from datetime import datetime, timezone
from typing import Optional
from sqlalchemy import (
    Column, Integer, String, Text, DateTime, Boolean, Float,
    BigInteger, ForeignKey, Index, UniqueConstraint, JSON
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship
from app.models.sqlalchemy_base import get_base

Base = get_base()

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
    
    id = Column(Integer, primary_key=True)
    org_id = Column(Integer, ForeignKey("organizations.id"))
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
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    
    organization = relationship("Organization", backref="users")

class Layer(Base):
    """图层表 - 支持 Vector/Raster/Tile 三种类型"""
    __tablename__ = "layers"
    
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    org_id = Column(Integer, ForeignKey("organizations.id"), nullable=False)
    creator_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    name = Column(String(255), nullable=False, index=True)
    description = Column(Text)
    category = Column(String(50), index=True)
    layer_type = Column(String(20), nullable=False)
    geometry_type = Column(String(50))
    source_format = Column(String(50))
    source_url = Column(String(1000))
    crs = Column(String(100), default="EPSG:4326")
    bounds = Column(JSONB)
    feature_count = Column(BigInteger, default=0)
    properties_def = Column(JSONB)
    style_config = Column(JSONB)
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
    )
    
    organization = relationship("Organization", backref="layers")
    creator = relationship("User", backref="layers")

class AnalysisTask(Base):
    """空间分析任务表"""
    __tablename__ = "analysis_tasks"
    
    id = Column(BigInteger, primary_key=True)
    org_id = Column(Integer, ForeignKey("organizations.id"), nullable=False)
    creator_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    layer_id = Column(BigInteger, ForeignKey("layers.id"))
    result_layer_id = Column(BigInteger, ForeignKey("layers.id"))
    task_type = Column(String(50), nullable=False)
    parameters = Column(JSONB, nullable=False)
    celery_task_id = Column(String(100), unique=True, index=True)
    status = Column(String(20), default="pending", index=True)
    progress = Column(Integer, default=0)
    progress_message = Column(String(255))
    result_summary = Column(JSONB)
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
        Index("idx_task_celery", "celery_task_id"),
    )

class LayerPermission(Base):
    """图层权限细粒度控制"""
    __tablename__ = "layer_permissions"
    
    id = Column(Integer, primary_key=True)
    layer_id = Column(BigInteger, ForeignKey("layers.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    permission = Column(String(20), nullable=False)
    granted_by = Column(Integer, ForeignKey("users.id"))
    granted_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    expires_at = Column(DateTime)
    
    __table_args__ = (
        UniqueConstraint("layer_id", "user_id", name="uq_permission"),
    )
    
    layer = relationship("Layer", backref="permissions")
    user = relationship("User", foreign_keys=[user_id])

def get_init_sql():
    """获取 PostGIS 初始化 SQL"""
    return """
    CREATE EXTENSION IF NOT EXISTS postgis;
    CREATE EXTENSION IF NOT EXISTS postgis_topology;
    """

__all__ = ["Base", "Organization", "User", "Layer", "AnalysisTask", "LayerPermission", "get_init_sql"]