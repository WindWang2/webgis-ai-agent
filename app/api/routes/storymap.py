"""StoryMap 编排路由（ADR-0196 §5.2）——编译与离线导出两个端点。

鉴权面（SEC-07）：无状态编译/导出也强制 ``get_current_user``（避免匿名
面被用作模板/渲染算力与 spec 探测入口）；会话路径挂
``require_owned_session``（SEC-08 同源纪律）。
"""
from __future__ import annotations

import logging
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user, require_owned_session
from app.core.database import get_async_db
from app.lib.storymap.export_packager import (
    build_story_bundle,
    render_standalone_html,
)
from app.lib.storymap.spec import StoryMapSpec, assert_json_depth
from app.lib.storymap.story_compiler import compile_story_map
from app.models.db_model import Conversation
from app.schemas.storymap_schema import StoryCompileRequest, StoryExportRequest
from app.services.storymap.story_compiler import compile_for_session

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/storymap", tags=["StoryMap"])


@router.post("/compile", response_model=StoryMapSpec)
async def compile_storymap(
    req: StoryCompileRequest,
    _user: dict = Depends(get_current_user),
) -> StoryMapSpec:
    """证据链/消息 → StoryMapSpec（无状态；叙事编排的权威入口）。"""
    try:
        if req.trace is not None:
            assert_json_depth(req.trace)
        if req.messages is not None:
            assert_json_depth(req.messages)
        return compile_story_map(
            trace=req.trace,
            messages=req.messages,
            session_id=req.session_id,
            turn_id=req.turn_id,
            title=req.title,
        )
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/sessions/{session_id}/compile", response_model=StoryMapSpec)
async def compile_session_storymap(
    session_id: str,
    conv: Conversation = Depends(require_owned_session),
    db: AsyncSession = Depends(get_async_db),
) -> StoryMapSpec:
    """会话消息 → StoryMapSpec（所有权守卫路径）。"""
    try:
        return await compile_for_session(db, conv)  # type: ignore[return-value]
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/export")
async def export_storymap(
    req: StoryExportRequest,
    _user: dict = Depends(get_current_user),
) -> Any:
    """StoryMapSpec → 自包含 StoryBundle（json dict 或 html 单文件）。"""
    try:
        assert_json_depth(req.spec)
        assert_json_depth(req.layers)
        if req.mapspec is not None:
            assert_json_depth(req.mapspec)
        spec = StoryMapSpec.model_validate(req.spec)
        bundle: Dict[str, Any] = build_story_bundle(
            spec, layers=req.layers, mapspec=req.mapspec
        )
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if req.format == "html":
        return HTMLResponse(
            render_standalone_html(bundle),
            headers={
                "Content-Disposition": 'attachment; filename="storymap-bundle.html"',
            },
        )
    return bundle
