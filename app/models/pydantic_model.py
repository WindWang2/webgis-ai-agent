"""Pydantic 数据验证模型"""
from pydantic import BaseModel, Field
from typing import Optional, List, Any

class LayerBase(BaseModel):
    """图层基础字段"""
    name: str = Field(..., description="图层名称")
    description: Optional[str] = Field(None, description="描述")
    category: Optional[str] = Field(None, description="分类")

class LayerCreate(LayerBase):
    """创建图层请求"""
    layer_type: str = Field(..., description="图层类型: vector/raster/tile")
    geometry_type: Optional[str] = Field(None, description="几何类型")
    source_format: Optional[str] = Field(None, description="数据格式")
    source_url: Optional[str] = Field(None, description="数据源地址")
    
class LayerUpdate(BaseModel):
    """更新图层请求"""
    name: Optional[str] = None
    description: Optional[str] = None
    category: Optional[str] = None
    status: Optional[str] = None
    
class LayerResponse(LayerBase):
    """图层响应"""
    id: int
    layer_type: str
    crs: str
    feature_count: int = 0
    status: str = "pending"
    class Config:
        from_attributes = True

class LayerListResponse(BaseModel):
    """图层列表响应"""
    total: int
    items: List[LayerResponse]

class TaskCreate(BaseModel):
    """创建任务请求"""
    task_type: str = Field(..., description="任务类型: buffer/intersect/clip/union/statistic/other")
    layer_id: int = Field(..., description="目标图层ID")
    parameters: dict = Field(default_factory=dict, description="任务参数")
    description: Optional[str] = Field(None, description="任务描述")

class TaskResponse(BaseModel):
    """任务响应"""
    id: int
    task_type: str
    status: str
    progress: int = 0
    class Config:
        from_attributes = True
        
__all__ = ["LayerCreate", "LayerUpdate", "LayerResponse", "LayerListResponse", "TaskCreate", "TaskResponse"]