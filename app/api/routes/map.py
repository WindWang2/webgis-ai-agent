"""
地图服务路由 — 遗留管理接口 (Legacy Admin)

⚠️ 架构对齐声明 (Agent Philosophy Alignment):
这些路由属于传统 CRUD 管理接口，不经过 Agent CNS。
在 "Agent is Everything" 架构下，所有地图操作应通过 AI 对话链路完成。
这些端点仅保留用于内部调试/运维，不对前端用户暴露。
"""

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional
from datetime import datetime, timezone

from app.core.auth import get_current_user

router = APIRouter()


class MapExtent(BaseModel):
    """地图范围"""
    xmin: float
    ymin: float
    xmax: float
    ymax: float
    crs: str = "EPSG:4326"


class MapInfo(BaseModel):
    """地图信息"""
    id: str
    name: str
    description: Optional[str] = None
    extent: Optional[MapExtent] = None
    created_at: Optional[datetime] = None


@router.get("/maps", tags=["内部管理"], deprecated=True)
def list_maps(
    limit: int = 100,
    offset: int = 0,
    _user: dict = Depends(get_current_user)
):
    """
    [已废弃] 获取地图列表 — 请通过 AI 对话使用 `inventory_layers` 工具替代
    """
    return {
        "total": 0,
        "limit": limit,
        "offset": offset,
        "maps": [],
        "_deprecation_notice": "此接口为遗留管理接口，不经过 Agent CNS。请通过对话指令操作地图。",
    }


@router.get("/maps/{map_id}", tags=["内部管理"], deprecated=True)
def get_map(map_id: str, _user: dict = Depends(get_current_user)):
    """
    [已废弃] 获取地图详情 — 请通过 AI 对话查询
    """
    raise HTTPException(status_code=404, detail=f"地图 {map_id} 不存在")


@router.get("/layers", tags=["内部管理"], deprecated=True)
def list_layers(_user: dict = Depends(get_current_user)):
    """
    [已废弃] 获取图层列表 — 请通过 AI 对话使用 `inventory_layers` 工具替代
    """
    return {
        "layers": [],
        "_deprecation_notice": "此接口为遗留管理接口。请通过对话指令操作图层。",
    }
