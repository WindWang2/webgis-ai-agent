"""
地图服务路由 — 遗留管理接口 (Legacy Admin)

⚠️ 架构对齐声明 (Agent Philosophy Alignment):
这些路由属于传统 CRUD 管理接口，不经过 Agent CNS。
在 "Agent is Everything" 架构下，所有地图操作应通过 AI 对话链路完成。
这些端点仅保留用于内部调试/运维，不对前端用户暴露。
"""

from fastapi import APIRouter, HTTPException, Depends, UploadFile, File, Request
from pydantic import BaseModel
from typing import Optional
from datetime import datetime
import os
import uuid
import time
from fastapi.responses import FileResponse
from app.core.config import settings
from app.core.auth import get_current_user

router = APIRouter()

EXPORT_DIR = os.path.join(settings.DATA_DIR, "exports")
os.makedirs(EXPORT_DIR, exist_ok=True)


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

# ==================== 智能制图与高清导出接口 (Agent Cartography Workflow) ====================

@router.post("/export", tags=["地图制图"])
async def upload_map_export(
    file: UploadFile = File(...),
    # Optional metadata if we want to store titles
    title: Optional[str] = None
):
    """
    接收来自前端的 Canvas 合成结果并持久化，返回可供下载访问的链接。
    由 Agent 指令 `export_thematic_map` 触发。
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="未提供文件名")
        
    ext = os.path.splitext(file.filename)[1]
    if ext.lower() not in ['.png', '.jpg', '.jpeg']:
        ext = '.png'
        
    filename = f"map_export_{int(time.time())}_{uuid.uuid4().hex[:6]}{ext}"
    filepath = os.path.join(EXPORT_DIR, filename)

    try:
        content = await file.read()
        with open(filepath, "wb") as f:
            f.write(content)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"保存导出图失败: {str(e)}")

    # Return the URL path
    download_url = f"/api/v1/export/download/{filename}"
    return {
        "success": True,
        "filename": filename,
        "url": download_url,
        "message": "地图制品已成功保存"
    }

@router.get("/export/download/{filename}", tags=["地图制图"])
def download_map_export(filename: str):
    """提取生成的精美专题地图成果"""
    # Security: Ensure the user cannot traverse outside EXPORT_DIR
    safe_filename = os.path.basename(filename)
    filepath = os.path.join(EXPORT_DIR, safe_filename)
    if not os.path.exists(filepath):
        raise HTTPException(status_code=404, detail="地图文件不存在或已过期失效")
        
    # Content-Disposition inline allows previewing in browser/chat
    return FileResponse(
        filepath, 
        media_type="image/png" if filename.endswith('.png') else "image/jpeg",
        filename=filename,
        headers={"Content-Disposition": f'inline; filename="{filename}"'}
    )
