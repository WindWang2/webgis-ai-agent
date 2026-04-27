"""
地图导出路由 — 智能制图工作流

导出接口由 Agent 指令 `export_thematic_map` 触发，
接收前端 Canvas 合成结果并持久化。
"""

from fastapi import APIRouter, HTTPException, UploadFile, File
from typing import Optional
import os
import uuid
import time
from fastapi.responses import FileResponse
from app.core.config import settings

router = APIRouter()

EXPORT_DIR = os.path.join(settings.DATA_DIR, "exports")
os.makedirs(EXPORT_DIR, exist_ok=True)

MAX_EXPORT_SIZE = 50 * 1024 * 1024  # 50 MB


@router.post("/export", tags=["地图制图"])
async def upload_map_export(
    file: UploadFile = File(...),
    title: Optional[str] = None
):
    """接收来自前端的 Canvas 合成结果并持久化，返回可供下载访问的链接。"""
    if not file.filename:
        raise HTTPException(status_code=400, detail="未提供文件名")

    ext = os.path.splitext(file.filename)[1]
    if ext.lower() not in ['.png', '.jpg', '.jpeg']:
        ext = '.png'

    filename = f"map_export_{int(time.time())}_{uuid.uuid4().hex[:6]}{ext}"
    filepath = os.path.join(EXPORT_DIR, filename)

    try:
        content = await file.read(MAX_EXPORT_SIZE + 1)
        if len(content) > MAX_EXPORT_SIZE:
            raise HTTPException(status_code=413, detail="文件过大，上限 50MB")
        with open(filepath, "wb") as f:
            f.write(content)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"保存导出图失败: {str(e)}")

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
    safe_filename = os.path.basename(filename)
    filepath = os.path.join(EXPORT_DIR, safe_filename)
    if not os.path.exists(filepath):
        raise HTTPException(status_code=404, detail="地图文件不存在或已过期失效")

    return FileResponse(
        filepath,
        media_type="image/png" if safe_filename.endswith('.png') else "image/jpeg",
        headers={"Content-Disposition": f'inline; filename="{safe_filename}"'}
    )
