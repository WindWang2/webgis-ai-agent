"""WP09/WP10 — 时序地图动画媒体 REST 面（UI 与 Pi 共用同一 service）。

会话归属由 ``verify_session_owner`` 证明；成果下载沿用既有
``/api/v1/export/download/{filename}`` 的 ``.owner`` 侧车 + 签名 URL
fail-closed 鉴权。路由不返回媒体字节，只返回有界摘要和授权 ref。
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import (
    get_current_user_optional_with_version, get_owner_token,
)
from app.core.database import get_async_db
from app.services.temporal_media import contracts as C
from app.services.temporal_media import service as media_service
from app.services.temporal_media.store import list_jobs

router = APIRouter(prefix="/temporal-media", tags=["时序媒体交付 (G35)"])


async def _verify(db: AsyncSession, session_id: str, user: dict,
                  owner_token: Optional[str]) -> None:
    if not session_id:
        raise HTTPException(status_code=422, detail={"code": "MISSING_SESSION_ID",
                                                    "message": "session_id 必填"})
    from app.services.auth_history_bridge import verify_session_owner
    try:
        await verify_session_owner(
            db, session_id,
            user_id=user.get("user_id") or None,
            owner_token=owner_token or None,
        )
    except Exception as exc:  # noqa: BLE001 — 不泄漏会话存在性
        raise HTTPException(status_code=403, detail={
            "code": "session_not_owned", "message": "会话不属于当前用户"}) from exc


def _raise(exc: C.TemporalMediaError) -> None:
    raise HTTPException(status_code=exc.status_code, detail=exc.to_payload())


async def _call(fn: Any, args: Any, principal: str) -> Dict[str, Any]:
    try:
        return await fn(args, principal)
    except C.TemporalMediaError as exc:
        _raise(exc)
    return {"success": False, "code": "UNEXPECTED_MEDIA_FAILURE"}


@router.get("/capabilities")
async def get_capabilities(
    session_id: str = Query(...),
    expected_revision: Optional[int] = Query(None),
    _user: dict = Depends(get_current_user_optional_with_version),
    owner_token: Optional[str] = Depends(get_owner_token),
    db: AsyncSession = Depends(get_async_db),
) -> Dict[str, Any]:
    """真实能力合同（encoder/限制/依赖缺失 + 当前 temporal 绑定支持矩阵）。"""
    await _verify(db, session_id, _user, owner_token)
    args = C.CapabilitiesArgs(session_id=session_id,
                              expected_revision=expected_revision)
    return await _call(media_service.capabilities, args,
                       media_service._principal(None, session_id))


@router.post("/plan")
async def post_plan(
    body: C.MediaPlanArgs,
    _user: dict = Depends(get_current_user_optional_with_version),
    owner_token: Optional[str] = Depends(get_owner_token),
    db: AsyncSession = Depends(get_async_db),
) -> Dict[str, Any]:
    """预览冻结计划（返回 plan_hash；提交必须用同一 hash）。"""
    await _verify(db, body.session_id, _user, owner_token)
    return await _call(media_service.preview, body,
                       media_service._principal(None, body.session_id))


@router.post("/submit")
async def post_submit(
    body: C.SubmitArgs,
    _user: dict = Depends(get_current_user_optional_with_version),
    owner_token: Optional[str] = Depends(get_owner_token),
    db: AsyncSession = Depends(get_async_db),
) -> Dict[str, Any]:
    """提交后台任务；返回 pending，不冒充完成。"""
    await _verify(db, body.session_id, _user, owner_token)
    return await _call(media_service.submit, body,
                       media_service._principal(None, body.session_id))


@router.get("/jobs")
async def get_jobs(
    session_id: str = Query(...),
    _user: dict = Depends(get_current_user_optional_with_version),
    owner_token: Optional[str] = Depends(get_owner_token),
    db: AsyncSession = Depends(get_async_db),
) -> Dict[str, Any]:
    """重开页面后从服务器恢复本会话任务列表（owner 隔离）。"""
    await _verify(db, session_id, _user, owner_token)
    principal = media_service._principal(None, session_id)
    return {"success": True, "jobs": list_jobs(principal)}


@router.get("/jobs/{job_id}")
async def get_job_status(
    job_id: str,
    session_id: str = Query(...),
    _user: dict = Depends(get_current_user_optional_with_version),
    owner_token: Optional[str] = Depends(get_owner_token),
    db: AsyncSession = Depends(get_async_db),
) -> Dict[str, Any]:
    await _verify(db, session_id, _user, owner_token)
    args = C.JobRefArgs(session_id=session_id, job_id=job_id)
    return await _call(media_service.status, args,
                       media_service._principal(None, session_id))


@router.delete("/jobs/{job_id}")
async def cancel_job(
    job_id: str,
    session_id: str = Query(...),
    _user: dict = Depends(get_current_user_optional_with_version),
    owner_token: Optional[str] = Depends(get_owner_token),
    db: AsyncSession = Depends(get_async_db),
) -> Dict[str, Any]:
    await _verify(db, session_id, _user, owner_token)
    args = C.JobRefArgs(session_id=session_id, job_id=job_id)
    return await _call(media_service.cancel, args,
                       media_service._principal(None, session_id))


@router.get("/jobs/{job_id}/result")
async def get_job_result(
    job_id: str,
    session_id: str = Query(...),
    refresh_urls: bool = Query(True),
    _user: dict = Depends(get_current_user_optional_with_version),
    owner_token: Optional[str] = Depends(get_owner_token),
    db: AsyncSession = Depends(get_async_db),
) -> Dict[str, Any]:
    await _verify(db, session_id, _user, owner_token)
    args = C.ResultArgs(session_id=session_id, job_id=job_id,
                        refresh_urls=refresh_urls)
    return await _call(media_service.result, args,
                       media_service._principal(None, session_id))


__all__ = ["router"]
