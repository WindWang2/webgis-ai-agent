"""用户数据上传 API 路由"""
import asyncio
import logging
import shutil
import uuid
from pathlib import Path
from typing import List, Optional

import ijson
from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Response, UploadFile
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import settings
from app.core.auth import authorize_session_write, get_current_user, verify_session_owner
from app.lib.geojson_serializer import serialize_geojson
from app.tools._utils import async_db_session
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


def _load_geojson_features(path: Path) -> list:
    """流式解析 GeoJSON 的 features —— 同步 CPU+IO 密集（50MB 上限），
    必须在 worker 线程执行（计算隔离不变式 1）。"""
    with open(path, "rb") as f:
        return list(ijson.items(f, "features.item"))


def _write_upload_bytes(path: Path, content: bytes) -> None:
    """同步写盘（栅格上限 200MB）—— 必须在 worker 线程执行（#592：
    与紧随其后的 parse 走 run_in_executor 同款纪律；慢盘/NFS 上内联写
    会冻结事件循环上全部并发 SSE/WS 流）。"""
    with open(path, "wb") as f:
        f.write(content)


async def _verify_session_owner(
    db, session_id: Optional[str], user_id, owner_token: Optional[str] = None
) -> None:
    """跨租户守卫：若 upload 关联了 session_id，会话必须属于调用方（审计 S42）。

    UploadRecord 无 user_id 列；通过 session_id → Conversation.user_id 解析归属。
    session_id 为 None 时（旧匿名上传）拒绝 —— 与历史匿名会话语义一致。
    SEC-08：匿名会话需转发 X-Session-Token（owner_token），与 POST /upload 对齐。
    """
    if not session_id:
        raise HTTPException(status_code=404, detail="上传记录不存在")
    await verify_session_owner(
        db, session_id, user_id=user_id, owner_token=owner_token
    )
