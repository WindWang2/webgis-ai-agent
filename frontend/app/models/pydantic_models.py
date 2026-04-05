"""
Pydantic 数据模型（用于 API 请求/响应验证）
"""

from datetime import datetime
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field


# ==================== 图层相关 ====================

class LayerBase(BaseModel):
    """图层基础模型"""
    name: str = Field(..., description="图层名称")
    description: Optional[str] = Field(None, description="图层描述")
    layer_type: str = Field(..., description="图层类型：vector, raster, tile")
    geometry_type: Optional[str] = Field(None, description="几何类型")
    source_url: Optional[str] = Field(None, description="数据源 URL")
    source_format: Optional[str] = Field(None, description="数据源格式")
    crs: Optional[str] = Field("EPSG:4326", description="坐标系")
    extent: Optional[Dict[str, float]] = Field(None, description="空间范围")
    attributes: Optional[List[Dict[str, Any]]] = Field(None, description="属性字段")
    is_public: bool = Field(True, description="是否公开")
    permission: str = Field("read", description="权限级别")


class LayerCreate(LayerBase):
    """创建图层请求模型"""
    pass


class LayerUpdate(BaseModel):
    """更新图层请求模型"""
    name: Optional[str] = None
    description: Optional[str] = None
    source_url: Optional[str] = None
    is_public: Optional[bool] = None
    permission: Optional[str] = None
    is_active: Optional[bool] = None


class LayerResponse(LayerBase):
    """图层响应模型"""
    id: int
    owner_id: Optional[int] = None
    is_active: bool = True
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class LayerListResponse(BaseModel):
    """图层列表响应模型"""
    total: int
    limit: int
    offset: int
    layers: List[LayerResponse]


# ==================== 空间分析任务 ====================

class TaskBase(BaseModel):
    """任务基础模型"""
    task_type: str = Field(..., description="任务类型：buffer, clip, intersect, dissolve, etc.")
    layer_id: Optional[int] = Field(None, description="关联图层 ID")
    parameters: Dict[str, Any] = Field(..., description="任务参数")


class TaskCreate(TaskBase):
    """创建任务请求模型"""
    pass


class TaskResponse(BaseModel):
    """任务响应模型"""
    id: int
    task_id: str
    layer_id: Optional[int]
    task_type: str
    parameters: Dict[str, Any]
    status: str
    progress: int
    result: Optional[Dict[str, Any]]
    error_message: Optional[str]
    retry_count: int
    created_at: datetime
    started_at: Optional[datetime]
    completed_at: Optional[datetime]

    class Config:
        from_attributes = True


class TaskListResponse(BaseModel):
    """任务列表响应模型"""
    total: int
    limit: int
    offset: int
    tasks: List[TaskResponse]


# ==================== 权限相关 ====================

class PermissionCheck(BaseModel):
    """权限检查请求"""
    user_id: int
    resource_type: str
    resource_id: int
    required_permission: str


class PermissionResponse(BaseModel):
    """权限检查响应"""
    allowed: bool
    role: Optional[str] = None


# ==================== 通用响应 ====================

class SuccessResponse(BaseModel):
    """通用成功响应"""
    success: bool = True
    message: str = "操作成功"
    data: Optional[Any] = None


class ErrorResponse(BaseModel):
    """通用错误响应"""
    success: bool = False
    message: str
    code: Optional[str] = None
