"""用户数据上传 API 路由"""
import json
import logging
import uuid
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.upload import UploadRecord
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
    filename = file.filename or "unknown"
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
    upload_id = uuid.uuid4().hex[:12]
    upload_dir = get_upload_dir(settings.DATA_DIR, upload_id)

    # 写入临时文件
    temp_path = upload_dir / filename
    try:
        with open(temp_path, "wb") as f:
            f.write(content)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"文件保存失败: {e}")

    # 解析文件
    try:
        if ext in RASTER_FORMATS:
            meta = parse_raster(temp_path, upload_dir, upload_id)
        else:
            meta = parse_vector(temp_path, upload_dir, upload_id)
    except ParseError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"文件解析异常: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"文件解析失败: {e}")

    # 保存元信息
    save_meta(upload_dir, meta)

    # 写入数据库
    db = SessionLocal()
    try:
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
        db.commit()
        db.refresh(record)
    except Exception as e:
        db.rollback()
        logger.error(f"数据库写入失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="记录保存失败")
    finally:
        db.close()

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
async def list_uploads(
    session_id: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
):
    """获取上传文件列表"""
    db = SessionLocal()
    try:
        query = db.query(UploadRecord).order_by(UploadRecord.upload_time.desc())
        if session_id:
            query = query.filter(UploadRecord.session_id == session_id)
        total = query.count()
        records = query.offset(offset).limit(limit).all()
    finally:
        db.close()

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
async def get_upload(upload_id: int):
    """获取单个上传文件的详情"""
    db = SessionLocal()
    try:
        record = db.query(UploadRecord).filter(UploadRecord.id == upload_id).first()
    finally:
        db.close()

    if not record:
        raise HTTPException(status_code=404, detail="上传记录不存在")

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
async def get_upload_geojson(upload_id: int):
    """获取上传文件的 GeoJSON 数据（用于地图渲染）"""
    db = SessionLocal()
    try:
        record = db.query(UploadRecord).filter(UploadRecord.id == upload_id).first()
    finally:
        db.close()

    if not record:
        raise HTTPException(status_code=404, detail="上传记录不存在")

    if record.file_type != "vector":
        raise HTTPException(status_code=400, detail="该文件不是矢量数据")

    geojson_path = Path(record.filename)
    if not geojson_path.exists():
        raise HTTPException(status_code=404, detail="GeoJSON 文件不存在")

    with open(geojson_path, "r", encoding="utf-8") as f:
        return json.load(f)


@router.delete("/uploads/{upload_id}")
async def delete_upload(upload_id: int):
    """删除上传记录及文件"""
    db = SessionLocal()
    try:
        record = db.query(UploadRecord).filter(UploadRecord.id == upload_id).first()
        if not record:
            raise HTTPException(status_code=404, detail="上传记录不存在")

        # 删除文件目录
        file_path = Path(record.filename)
        upload_dir = file_path.parent
        if upload_dir.exists():
            import shutil
            shutil.rmtree(upload_dir, ignore_errors=True)

        db.delete(record)
        db.commit()
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"删除失败: {e}")
    finally:
        db.close()

    return {"success": True, "message": "已删除"}
