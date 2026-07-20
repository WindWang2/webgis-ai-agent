"""用户数据上传 API 路由"""
import asyncio
import json
import logging
import uuid
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import select, func

from app.core.config import settings
from app.core.auth import get_current_user
from app.tools._utils import async_db_session
from app.models.upload import UploadRecord
from app.models.db_model import Conversation
from app.services.history_service_async import AsyncHistoryService
from app.services.data_parser import (
    MAX_RASTER_SIZE,
    MAX_VECTOR_SIZE,
    ParseError,
    RASTER_FORMATS,
    VECTOR_FORMATS,
    get_upload_dir,
    parse_raster,
    parse_vector,
    save_meta,
)

logger = logging.getLogger(__name__)
router = APIRouter()


async def _verify_session_owner(db, session_id: Optional[str], user_id) -> None:
    """跨租户守卫：若 upload 关联了 session_id，会话必须属于调用方（审计 S42）。

    UploadRecord 无 user_id 列；通过 session_id → Conversation.user_id 解析归属。
    session_id 为 None 时（旧匿名上传）允许 —— 与历史匿名会话语义一致。
    """
    if not session_id:
        return
    conv = await AsyncHistoryService(db).get_session(session_id, user_id=user_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Session not found")


# ==================== 响应模型 ====================

class UploadResponse(BaseModel):
    """单文件上传响应"""
    id: int
    original_name: str
    file_type: str
    format: str
    crs: str
    geometry_type: Optional[str]
    feature_count: int
    bbox: Optional[List[float]]
    file_size: int
    message: str = "上传成功"


class UploadListResponse(BaseModel):
    """上传列表响应"""
    total: int
    uploads: List[UploadResponse]


class ErrorResponse(BaseModel):
    detail: str


# ==================== 上传接口 ====================

@router.post("/upload", response_model=UploadResponse)
async def upload_files(
    files: List[UploadFile] = File(..., description="GIS 数据文件（支持多文件上传）"),
    session_id: Optional[str] = Form(None, description="关联的会话 ID"),
    _user: dict = Depends(get_current_user),
):
    """
    上传 GIS 数据文件

    支持格式:
    - 矢量: .geojson, .json, .shp (zip), .kml, .gpkg, .csv (含经纬度列)
    - 栅格: .tif, .tiff

    限制: 矢量文件 50MB, 栅格文件 200MB
    """
    if not files:
        raise HTTPException(status_code=400, detail="请选择至少一个文件")

    # 只处理第一个文件（多文件上传可扩展）
    file = files[0]
    # —— 文件名清洗：剥离任何路径分隔符与 .. 防穿越 ——
    raw_name = file.filename or "unknown"
    filename = Path(raw_name).name
    if not filename or filename.startswith("."):
        raise HTTPException(status_code=400, detail="非法文件名")
    ext = Path(filename).suffix.lower()

    # 检查格式
    if ext not in VECTOR_FORMATS and ext not in RASTER_FORMATS:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的文件格式: {ext}。"
                   f"支持的矢量格式: {', '.join(sorted(VECTOR_FORMATS))}；"
                   f"栅格格式: {', '.join(sorted(RASTER_FORMATS))}",
        )

    # 读取文件内容
    content = await file.read()
    file_size = len(content)

    # 检查大小
    if ext in RASTER_FORMATS and file_size > MAX_RASTER_SIZE:
        raise HTTPException(
            status_code=400,
            detail=f"栅格文件大小 {file_size / 1024 / 1024:.1f}MB 超过限制 200MB",
        )
    if ext in VECTOR_FORMATS and file_size > MAX_VECTOR_SIZE:
        raise HTTPException(
            status_code=400,
            detail=f"矢量文件大小 {file_size / 1024 / 1024:.1f}MB 超过限制 50MB",
        )

    # 创建上传目录
    # 完整 uuid hex (32 字符, 128 位熵) — 旧的 [:12] 仅 48 位，公网静态 mount 下可枚举
    upload_id = uuid.uuid4().hex
    upload_dir = get_upload_dir(settings.DATA_DIR, upload_id)

    # 写入临时文件 — 二次防御：解析后必须仍在 upload_dir 之下
    temp_path = upload_dir / filename
    try:
        resolved = temp_path.resolve()
        upload_root = Path(upload_dir).resolve()
        if upload_root not in resolved.parents:
            raise HTTPException(status_code=400, detail="路径越界")
        with open(temp_path, "wb") as f:
            f.write(content)
    except OSError as e:
        logger.error(f"文件保存失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="文件保存失败")

    # 解析文件 — parse_vector / parse_raster 内含 gpd.read_file / rasterio.open
    # 这些是同步 CPU+IO 操作，常常需要数秒。直接在 async def 里调用会阻塞整个
    # uvicorn 事件循环，所有其它请求停滞（审计 B1 / V2.0 计算隔离不变式）。
    # 走 run_in_executor 把工作扔到默认 threadpool。
    loop = asyncio.get_running_loop()
    try:
        if ext in RASTER_FORMATS:
            meta = await loop.run_in_executor(None, parse_raster, temp_path, upload_dir, upload_id)
        else:
            meta = await loop.run_in_executor(None, parse_vector, temp_path, upload_dir, upload_id)
    except ParseError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except (OSError, RuntimeError) as e:
        logger.error(f"文件解析异常: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="文件解析失败")

    # 保存元信息
    save_meta(upload_dir, meta)

    # 写入数据库
    try:
        async with async_db_session() as db:
            record = UploadRecord(
                filename=meta.get("output_path", str(upload_dir / filename)),
                original_name=filename,
                file_type=meta["file_type"],
                format=meta["format"],
                crs=meta.get("crs", "EPSG:4326"),
                geometry_type=meta.get("geometry_type"),
                feature_count=meta.get("feature_count", 0),
                bbox=meta.get("bbox"),
                file_size=file_size,
                session_id=session_id,
            )
            db.add(record)
            await db.flush()
            await db.refresh(record)
    except (OSError, RuntimeError) as e:
        logger.error(f"数据库写入失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="记录保存失败")

    return UploadResponse(
        id=record.id,
        original_name=record.original_name,
        file_type=record.file_type,
        format=record.format,
        crs=record.crs,
        geometry_type=record.geometry_type,
        feature_count=record.feature_count,
        bbox=record.bbox,
        file_size=record.file_size,
    )


# ==================== 查询接口 ====================

@router.get("/uploads", response_model=UploadListResponse)
async def list_uploads(_user: dict = Depends(get_current_user),
    session_id: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
):
    """获取上传文件列表

    审计 S42：session_id 缺省时之前返回全局最近 100 条 —— 任何登录用户能拉到
    他人上传文件名、bbox（常含真实位置 PII）。现在要求 session_id 必填且校验归属。
    """
    if not session_id:
        raise HTTPException(
            status_code=400,
            detail="session_id 为必填，避免跨租户泄漏",
        )
    async with async_db_session() as db:
        await _verify_session_owner(db, session_id, _user.get("user_id"))
        stmt = select(UploadRecord).where(UploadRecord.session_id == session_id).order_by(UploadRecord.upload_time.desc())
        count_stmt = select(func.count()).select_from(stmt.subquery())
        total_result = await db.execute(count_stmt)
        total = total_result.scalar_one()
        result = await db.execute(stmt.offset(offset).limit(limit))
        records = result.scalars().all()

    return UploadListResponse(
        total=total,
        uploads=[
            UploadResponse(
                id=r.id,
                original_name=r.original_name,
                file_type=r.file_type,
                format=r.format,
                crs=r.crs,
                geometry_type=r.geometry_type,
                feature_count=r.feature_count,
                bbox=r.bbox,
                file_size=r.file_size,
            )
            for r in records
        ],
    )


@router.get("/uploads/{upload_id}", response_model=UploadResponse)
async def get_upload(upload_id: int, _user: dict = Depends(get_current_user)):
    """获取单个上传文件的详情

    审计 S42：upload_id 是顺序整数易枚举；通过 record.session_id 解析归属。
    """
    async with async_db_session() as db:
        result = await db.execute(select(UploadRecord).where(UploadRecord.id == upload_id))
        record = result.scalar_one_or_none()
        if not record:
            raise HTTPException(status_code=404, detail="上传记录不存在")
        await _verify_session_owner(db, record.session_id, _user.get("user_id"))

    return UploadResponse(
        id=record.id,
        original_name=record.original_name,
        file_type=record.file_type,
        format=record.format,
        crs=record.crs,
        geometry_type=record.geometry_type,
        feature_count=record.feature_count,
        bbox=record.bbox,
        file_size=record.file_size,
    )


@router.get("/uploads/{upload_id}/geojson")
async def get_upload_geojson(upload_id: int, _user: dict = Depends(get_current_user)):
    """获取上传文件的 GeoJSON 数据（用于地图渲染）"""
    async with async_db_session() as db:
        result = await db.execute(select(UploadRecord).where(UploadRecord.id == upload_id))
        record = result.scalar_one_or_none()
        if not record:
            raise HTTPException(status_code=404, detail="上传记录不存在")
        await _verify_session_owner(db, record.session_id, _user.get("user_id"))

    if record.file_type != "vector":
        raise HTTPException(status_code=400, detail="该文件不是矢量数据")

    geojson_path = Path(record.filename)
    if not geojson_path.exists():
        raise HTTPException(status_code=404, detail="GeoJSON 文件不存在")

    resolved = geojson_path.resolve()
    data_root = Path(settings.DATA_DIR).resolve()
    if data_root not in resolved.parents and resolved != data_root:
        raise HTTPException(status_code=400, detail="非法文件路径")

    with open(geojson_path, "r", encoding="utf-8") as f:
        return json.load(f)


@router.delete("/uploads/{upload_id}")
async def delete_upload(upload_id: int, _user: dict = Depends(get_current_user)):
    """删除上传记录及文件"""
    async with async_db_session() as db:
        result = await db.execute(select(UploadRecord).where(UploadRecord.id == upload_id))
        record = result.scalar_one_or_none()
        if not record:
            raise HTTPException(status_code=404, detail="上传记录不存在")
        # 审计 S42：删除前必须确认归属 —— 否则任何用户可枚举整数 id 删除他人数据。
        await _verify_session_owner(db, record.session_id, _user.get("user_id"))

        file_path = Path(record.filename)
        await db.delete(record)

    # File cleanup AFTER DB commit succeeds
    upload_dir = file_path.parent
    if upload_dir.exists():
        import shutil
        shutil.rmtree(upload_dir, ignore_errors=True)

    return {"success": True, "message": "已删除"}
