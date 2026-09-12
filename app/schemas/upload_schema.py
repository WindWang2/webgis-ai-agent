"""数据上传子系统契约模型（V9 契约基石，ADR-0138）。

从 `app/api/routes/upload.py` 内联迁出；路由文件只 import。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict


class UploadResponse(BaseModel):
    """单文件上传响应

    V4 增量字段（全部带默认值 —— 既有契约不变）：
    - ``crs`` 放宽为可空：解析器未确认 CRS（CSV 未声明）时如实为 null，
      不再谎称 confirmed EPSG:4326（``crs_source`` 披露证据来源）；
    - ``deduplicated``：同会话同内容幂等导入命中，返回既有 upload_id；
    - ``warnings`` / ``ignored_files`` / ``meta``：诚实披露（多文件丢弃、
      CRS 假设、栅格 nodata/overviews 等）；
    - ``session_ref`` / ``profile_summary`` / ``quality``：V3 摄入管线
      增值车道（opt-in）的有界产物；失败不阻断上传（``ref_registration_error``）。
    """

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "id": 42,
                    "original_name": "aoi.csv",
                    "file_type": "csv",
                    "format": "csv",
                    "crs": None,
                    "geometry_type": "Point",
                    "feature_count": 128,
                    "bbox": [120.0, 30.0, 121.0, 31.0],
                    "file_size": 20480,
                    "message": "上传成功",
                    "crs_source": None,
                    "deduplicated": False,
                    "ignored_files": [],
                    "warnings": [],
                    "meta": None,
                    "session_ref": None,
                    "profile_summary": None,
                    "quality": None,
                    "ref_registration_error": None,
                }
            ]
        }
    )

    id: int
    original_name: str
    file_type: str
    format: str
    crs: Optional[str]
    geometry_type: Optional[str]
    feature_count: int
    bbox: Optional[List[float]]
    file_size: int
    message: str = "上传成功"
    # ---- V4 additive ----
    crs_source: Optional[str] = None
    deduplicated: bool = False
    ignored_files: List[str] = []
    warnings: List[str] = []
    meta: Optional[Dict[str, Any]] = None
    session_ref: Optional[str] = None
    profile_summary: Optional[Dict[str, Any]] = None
    quality: Optional[Dict[str, Any]] = None
    ref_registration_error: Optional[str] = None


class UploadListResponse(BaseModel):
    """上传列表响应。"""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [{"total": 1, "uploads": []}]
        }
    )

    total: int
    uploads: List[UploadResponse]


class ErrorResponse(BaseModel):
    """通用错误体（兼容保留）。"""

    detail: str


class UploadDeleteResponse(BaseModel):
    """DELETE /uploads/{upload_id} 返回。"""

    model_config = ConfigDict(
        json_schema_extra={"examples": [{"success": True, "message": "已删除"}]}
    )

    success: bool
    message: str
