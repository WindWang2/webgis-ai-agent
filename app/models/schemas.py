"""
数据模型定义
"""

from datetime import datetime
from typing import Optional, List
from sqlalchemy import Column, Integer, String, Text, DateTime, Boolean, JSON, ForeignKey
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


class Layer(Base):
    """地图图层模型"""
    __tablename__ = "layers"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), nullable=False, index=True)
    description = Column(Text, nullable=True)
    layer_type = Column(String(50), nullable=False)  # vector, raster, tile
    geometry_type = Column(String(50), nullable=True)  # Point, LineString, Polygon, etc.
    
    # 数据源配置
    source_url = Column(String(500), nullable=True)
    source_format = Column(String(50), nullable=True)  # shapefile, geojson, tiff, etc.
    
    # 元数据
    crs = Column(String(100), nullable=True, default="EPSG:4326")  # 坐标系
    extent = Column(JSON, nullable=True)  # 空间范围 {xmin, ymin, xmax, ymax}
    attributes = Column(JSON, nullable=True)  # 属性字段列表
    
    # 权限控制
    owner_id = Column(Integer, nullable=True)
    is_public = Column(Boolean, default=True)
    permission = Column(String(20), default="read")  # read, write, admin
    
    # 状态
    is_active = Column(Boolean, default=True)
    
    # 时间戳
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # 关系
    tasks = relationship("AnalysisTask", back_populates="layer")


class AnalysisTask(Base):
    """空间分析任务模型"""
    __tablename__ = "analysis_tasks"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(String(100), unique=True, nullable=False)  # Celery task ID
    layer_id = Column(Integer, ForeignKey("layers.id"), nullable=True)
    
    # 任务信息
    task_type = Column(String(100), nullable=False)  # buffer, clip, intersect, etc.
    parameters = Column(JSON, nullable=True)  # 任务参数
    status = Column(String(50), default="pending")  # pending, running, completed, failed
    progress = Column(Integer, default=0)  # 0-100
    
    # 结果
    result = Column(JSON, nullable=True)
    error_message = Column(Text, nullable=True)
    
    # 重试
    retry_count = Column(Integer, default=0)
    max_retries = Column(Integer, default=3)
    
    # 时间戳
    created_at = Column(DateTime, default=datetime.utcnow)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    
    # 关系
    layer = relationship("Layer", back_populates="tasks")


class UserRole(Base):
    """用户角色模型"""
    __tablename__ = "user_roles"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=False, index=True)
    resource_type = Column(String(50), nullable=False)  # layer, project, etc.
    resource_id = Column(Integer, nullable=False)
    role = Column(String(20), nullable=False)  # owner, editor, viewer
    created_at = Column(DateTime, default=datetime.utcnow)
