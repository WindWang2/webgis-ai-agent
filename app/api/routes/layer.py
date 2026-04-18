"""
图层管理 API 路由

⚠️ 架构对齐声明 (Agent Philosophy Alignment):
- GET /layers/data/{ref_id}：核心 Fetch-on-Demand 端点，Agent 执行链路的一部分，必须保留。
- 其余 CRUD 路由（POST/PUT/DELETE /layers, GET /tasks 等）属于传统管理接口，
  不经过 Agent CNS。在 "Agent is Everything" 架构下标记为 deprecated。
"""

from fastapi import APIRouter, HTTPException, Query, Depends
from sqlalchemy.orm import Session
from typing import Optional, List

from app.core.config import settings
from app.core.auth import get_current_user as auth_get_current_user
from app.models.pydantic_models import (
    LayerCreate, LayerUpdate, LayerResponse, LayerListResponse,
    TaskCreate, TaskResponse, TaskListResponse,
    SuccessResponse, ErrorResponse
)
from app.models.db_model import User
from app.services.layer_service import LayerService, TaskService
from app.core.database import get_db
from app.services.session_data import session_data_manager

router = APIRouter()


# ==================== 数据获取 API ====================

@router.get("/layers/data/{ref_id}", tags=["图层数据"])
def get_session_layer_data(
    ref_id: str,
    session_id: str = Query(..., description="会话 ID"),
):
    """
    通过引用 ID 获取会话缓存中的大数据对象（如分析产生的 GeoJSON）
    用于 Fetch-on-Demand 链路。
    """
    data = session_data_manager.get(session_id, ref_id)
    if not data:
        raise HTTPException(status_code=404, detail="数据已过期或不存在")
    return data


# ==================== 图层 CRUD ====================

@router.post("/layers", response_model=LayerResponse, tags=["内部管理"], deprecated=True)
def create_layer(
    layer: LayerCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(auth_get_current_user)
):
    """
    [已废弃] 创建新图层 — Agent 通过工具链自动创建和管理图层，无需手动 CRUD。
    """
    layer_service = LayerService(db)
    created_layer = layer_service.create(layer, owner_id=current_user.id)
    return created_layer


@router.get("/layers", response_model=LayerListResponse, tags=["内部管理"], deprecated=True)
def list_layers(
    limit: int = Query(100, le=1000, description="每页数量"),
    offset: int = Query(0, ge=0, description="偏移量"),
    layer_type: Optional[str] = Query(None, description="按类型筛选"),
    is_public: Optional[bool] = Query(None, description="按公开状态筛选"),
    search: Optional[str] = Query(None, description="搜索关键词"),
    db: Session = Depends(get_db)
):
    """
    [已废弃] 获取图层列表 — 请通过 AI 对话使用 `inventory_layers` 工具替代
    """
    layer_service = LayerService(db)
    layers, total = layer_service.list_all(
        limit=limit,
        offset=offset,
        layer_type=layer_type,
        is_public=is_public,
        search=search
    )
    
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "layers": layers
    }


@router.get("/layers/{layer_id}", response_model=LayerResponse, tags=["内部管理"], deprecated=True)
def get_layer(
    layer_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(auth_get_current_user)
):
    """[已废弃] 获取单个图层详情"""
    layer_service = LayerService(db)
    layer = layer_service.get(layer_id)
    
    if not layer:
        raise HTTPException(status_code=404, detail="图层不存在")
    
    # 权限检查
    if not layer.is_public and layer.owner_id != current_user.id:
        if not layer_service.check_permission(layer_id, current_user.id, "read"):
            raise HTTPException(status_code=403, detail="无权限访问")
    
    return layer


@router.put("/layers/{layer_id}", response_model=LayerResponse, tags=["内部管理"], deprecated=True)
def update_layer(
    layer_id: int,
    layer_update: LayerUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(auth_get_current_user)
):
    """[已废弃] 更新图层信息 — Agent 通过 `update_layer_appearance` / `alias_layer` 工具操作"""
    layer_service = LayerService(db)
    
    # 权限检查
    layer = layer_service.get(layer_id)
    if not layer:
        raise HTTPException(status_code=404, detail="图层不存在")
    
    if layer.owner_id != current_user.id:
        if not layer_service.check_permission(layer_id, current_user.id, "write"):
            raise HTTPException(status_code=403, detail="无权限修改")
    
    updated_layer = layer_service.update(layer_id, layer_update)
    return updated_layer


@router.delete("/layers/{layer_id}", response_model=SuccessResponse, tags=["内部管理"], deprecated=True)
def delete_layer(
    layer_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(auth_get_current_user)
):
    """[已废弃] 删除图层 — Agent 通过 `set_layer_status` 控制图层显隐"""
    layer_service = LayerService(db)
    layer = layer_service.get(layer_id)
    
    if not layer:
        raise HTTPException(status_code=404, detail="图层不存在")
    
    if layer.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="无权限删除")
    
    layer_service.delete(layer_id)
    return {"success": True, "message": "图层已删除"}


# ==================== 空间分析任务 ====================

@router.post("/layers/{layer_id}/tasks", response_model=TaskResponse, tags=["空间分析"])
def create_analysis_task(
    layer_id: int,
    task: TaskCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(auth_get_current_user)
):
    """
    创建空间分析任务
    
    - **task_type**: 任务类型（buffer, clip, intersect, dissolve, etc.）
    - **parameters**: 任务参数（根据任务类型不同）
    
    示例参数:
    - buffer: {"distance": 100, "units": "meters"}
    - clip: {"clip_layer_id": 2}
    """
    layer_service = LayerService(db)
    
    # 检查图层权限
    if not layer_service.check_permission(layer_id, current_user.id, "write"):
        raise HTTPException(status_code=403, detail="无权限执行分析")
    
    task_service = TaskService(db)
    created_task = task_service.create_task(
        task_type=task.task_type,
        parameters=task.parameters,
        layer_id=layer_id
    )
    
    # TODO: 触发 Celery 任务
    # from app.tasks.analysis import run_analysis
    # celery_task = run_analysis.delay(created_task.task_id)
    
    return created_task


@router.get("/tasks", response_model=TaskListResponse, tags=["空间分析"])
def list_tasks(
    limit: int = Query(100, le=1000),
    offset: int = Query(0, ge=0),
    status: Optional[str] = Query(None, description="任务状态"),
    layer_id: Optional[int] = Query(None, description="图层 ID"),
    db: Session = Depends(get_db)
):
    """获取分析任务列表"""
    task_service = TaskService(db)
    tasks, total = task_service.list_tasks(
        limit=limit,
        offset=offset,
        status=status,
        layer_id=layer_id
    )
    
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "tasks": tasks
    }


@router.get("/tasks/{task_id}", response_model=TaskResponse, tags=["空间分析"])
def get_task(
    task_id: str,
    db: Session = Depends(get_db)
):
    """获取任务详情和状态"""
    task_service = TaskService(db)
    task = task_service.get_task(task_id)
    
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    
    return task


@router.post("/tasks/{task_id}/retry", response_model=TaskResponse, tags=["空间分析"])
def retry_task(
    task_id: str,
    db: Session = Depends(get_db)
):
    """重试失败的任务"""
    task_service = TaskService(db)
    task = task_service.get_task(task_id)
    
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    
    if task.status != "failed":
        raise HTTPException(status_code=400, detail="仅失败任务可重试")
    
    if task.retry_count >= task.max_retries:
        raise HTTPException(status_code=400, detail="已超过最大重试次数")
    
    task_service.increment_retry(task_id)
    task_service.update_task_status(task_id, "pending")
    
    # TODO: 重新触发 Celery 任务
    # from app.tasks.analysis import run_analysis
    # run_analysis.delay(task_id)
    
    return task


@router.get("/tasks/{task_id}/progress", tags=["空间分析"])
def get_task_progress(
    task_id: str,
    db: Session = Depends(get_db)
):
    """获取任务进度（用于 SSE 实时推送）"""
    task_service = TaskService(db)
    task = task_service.get_task(task_id)
    
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    
    return {
        "task_id": task_id,
        "status": task.status,
        "progress": task.progress,
        "result": task.result,
        "error_message": task.error_message
    }


# ==================== 元数据接口 ====================

@router.get("/layers/{layer_id}/metadata", tags=["元数据"])
def get_layer_metadata(
    layer_id: int,
    db: Session = Depends(get_db)
):
    """
    获取图层元数据
    
    返回详细的图层信息，包括：
    - 空间范围
    - 属性字段
    - 坐标系
    - 数据源信息
    """
    layer_service = LayerService(db)
    layer = layer_service.get(layer_id)
    
    if not layer:
        raise HTTPException(status_code=404, detail="图层不存在")
    
    return {
        "id": layer.id,
        "name": layer.name,
        "description": layer.description,
        "layer_type": layer.layer_type,
        "geometry_type": layer.geometry_type,
        "crs": layer.crs,
        "extent": layer.extent,
        "attributes": layer.attributes,
        "source_format": layer.source_format,
        "created_at": layer.created_at.isoformat(),
        "updated_at": layer.updated_at.isoformat()
    }


@router.get("/layer-types", tags=["元数据"])
def get_layer_types():
    """获取支持的图层类型列表"""
    return {
        "layer_types": [
            {"type": "vector", "description": "矢量图层", "formats": ["shapefile", "geojson", "gpx", "kml"]},
            {"type": "raster", "description": "栅格图层", "formats": ["tiff", "jpg", "png", "dem"]},
            {"type": "tile", "description": "瓦片图层", "formats": ["xyz", "wmts", "tms"]}
        ],
        "analysis_types": [
            {"type": "buffer", "description": "缓冲区分析"},
            {"type": "clip", "description": "裁剪分析"},
            {"type": "intersect", "description": "相交分析"},
            {"type": "dissolve", "description": "融合分析"},
            {"type": "union", "description": "联合分析"},
            {"type": "spatial_join", "description": "空间连接"}
        ]
    }
