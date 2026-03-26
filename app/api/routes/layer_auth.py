"""图层管理 API 路由 - 集成JWT认证和数据隔离"""
from fastapi import APIRouter, HTTPException, Query, Depends
from sqlalchemy.orm import Session
from typing import Optional, List
from app.core.config import settings
from app.core.auth import get_current_user, filter_by_owner
from app.models.pydantic_models import (
    LayerCreate, LayerUpdate, LayerResponse, LayerListResponse,
    TaskCreate, TaskResponse, TaskListResponse
)
from app.services.layer_service import LayerService, TaskService
from app.models.user_model import User
from app.db.session import get_db
from app.models.api_response import ApiResponse
router = APIRouter()

@router.post("/layer", response_model=ApiResponse)
def create_layer(
    layer_data: dict,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """创建图层 - 需认证，自动绑定创建者"""
    svc = LayerService(db)
    layer = svc.create_with_owner(layer_data, current_user.id, current_user.org_id)
    return ApiResponse.ok(data={"id": layer.id, "name": layer.name})

@router.get("/layer", response_model=ApiResponse)
def list_layers(
    limit: int = Query(50, le=200),
    offset: int = Query(0, ge=0),
    category: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """图层列表 - 数据隔离:用户只看自己和组织的图层"""
    svc = LayerService(db)
    layers, total = svc.list_for_user(current_user.id, current_user.org_id, limit, offset, category)
    return ApiResponse.ok(data={
        "total": total,
        "items": [{"id": l.id, "name": l.name, "layer_type": l.layer_type} for l in layer]
    })

@router.get("/layer/{layer_id}", response_model=ApiResponse)
def get_layer(layer_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """获取图层详情 - 权限检查"""
    svc = LayerService(db)
    layer = svc.get_accessible(layer_id, current_user.id, current_user.role, current_user.org_id)
    if not layer:
        return ApiResponse.fail(code="NOT_FOUND", message="无权访问")
    return ApiResponse.ok(data={"id": layer.id, "name": layer.name, "feature_count": layer.feature_count})

@router.put("/layer/{layer_id}", response_model=ApiResponse)
def update_layer(layer_id: int, data: dict, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """更新图层 - 需所有者或编辑权限"""
    svc = LayerService(db)
    layer = svc.update_if_owned(layer_id, data, current_user.id, current_user.role)
    if not layer:
        return ApiResponse.fail(code="NOT_FOUND_OR_PERMISSION_DENIED", message="无权操作")
    return ApiResponse.ok(message="更新成功")

@router.delete("/layer/{layer_id}", response_model=ApiResponse)
def delete_layer(layer_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """删除图层 - 需所有者或管理员"""
    svc = LayerService(db)
    if svc.delete_if_owned(layer_id, current_user.id, current_user.role):
        return ApiResponse.ok(message="删除成功")
    return ApiResponse.fail(code="NOT_FOUND_OR_PERMISSION_DENIED", message="无权删除")

# 空间查询端点保持原有逻辑，由services层做权限过滤
@router.get("/layer/{layer_id}/geojson", response_model=ApiResponse)
def export_geojson(layer_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """导出GeoJSON - 权限检查"""
    svc = LayerService(db)
    layer = svc.get_accessible(layer_id, current_user.id, current_user.role, current_user.org_id)
    if not layer:
        return ApiResponse.fail(code="NOT_FOUND", message="无权访问")
    geojson = svc.export_as_geojson(layer_id)
    return ApiResponse.ok(data=geojson)

__all__ = ["router"]