"""PostgreSQL + PostGIS 数据库模型
T008: PostGIS数据库设计与初始化 - 核心表结构设计、PostGIS扩展配置、索引优化"""
from datetime import datetime
from typing import Optional
from sqlalchemy import (
    Column, Integer, String, Text, DateTime, Boolean, Float,
    BigInteger, ForeignKey, Index, CheckConstraint, UniqueConstraint
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship, declarative_base
Base = declarative_base()
class Organization(Base):
    __tablename__ = "organizations"
    id = Column(Integer, primary_key=True)
    name = Column(String(255), nullable=False)
    slug = Column(String(100), unique=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    org_id = Column(Integer, ForeignKey("organizations.id"))
    username = Column(String(100), nullable=False, unique=True)
    email = Column(String(255), unique=True, nullable=False)
    password_hash = Column(String(255))
    role = Column(String(20), default="viewer")
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
class Layer(Base):
    """
    图层表 - 支持 Vector/Raster/Tile三种类型，包含 PostGIS 几何列
    """
    __tablename__ = "layers"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    org_id = Column(Integer, ForeignKey("organizations.id"), nullable=False)
    creator_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    name = Column(String(255), nullable=False, index=True)
    description = Column(Text)
    category = Column(String(50), index=True)
    layer_type = Column(String(20), nullable=False)  # vector, raster, tile
    geometry_type = Column(String(50))  # Point, LineString, Polygon
    source_format = Column(String(50))  # geojson, shapefile, tiff
    source_url = Column(String(1000))
    crs = Column(String(100), default="EPSG:4326")
    bounds = Column(JSONB)  # {xmin,ymin,xmax,ymax}
    feature_count = Column(BigInteger, default=0)
    properties_def = Column(JSONB)  # 属性字段定义
    style_config = Column(JSONB)
    visibility = Column(String(20), default="org")
    is_basemap = Column(Boolean, default=False)
    status = Column(String(20), default="pending")
    error_message = Column(Text)
    processing_progress = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    __table_args__ = (
        UniqueConstraint("org_id", "name", name="uq_layer_org_name"),
        Index("idx_layer_status", "status"),
        Index("idx_layer_created", "created_at"),
    )
class AnalysisTask(Base):
    """空间分析任务表 - Celery 任务状态跟踪"""
    __tablename__ = "analysis_tasks"
    id = Column(BigInteger, primary_key=True)
    org_id = Column(Integer, ForeignKey("organizations.id"), nullable=False)
    creator_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    layer_id = Column(BigInteger, ForeignKey("layers.id"))
    result_layer_id = Column(BigInteger, ForeignKey("layers.id"))
    task_type = Column(String(50), nullable=False)  # buffer, clip, intersect...
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
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
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
    permission = Column(String(20), nullable=False)  # view, edit, admin
    granted_by = Column(Integer, ForeignKey("users.id"))
    granted_at = Column(DateTime, default=datetime.utcnow)
    expires_at = Column(DateTime)
    __table_args__ = (
        UniqueConstraint("layer_id", "user_id", name="uq_permission"),
    )
def get_init_sql():
    """获取PostGIS初始化SQL"""
    return """
    CREATE EXTENSION IF NOT EXISTS postgis;
    CREATE EXTENSION IF NOT EXISTS postgis_topology;
    ALTER SYSTEM SET shared_buffers='256MB';
    ALTER SYSTEM SET work_mem='64MB';
    """
__all__ = ["Base", "Organization", "User", "Layer", "AnalysisTask", "LayerPermission", "get_init_sql"]