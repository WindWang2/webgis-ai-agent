"""Goal Repair Routes — /api/v1/goal-repair（G42 UI 面）。

路由只做验证与装配（鉴权 → 服务层）；重逻辑全部在
``app/services/gis_harness/goal_repair_execution``。鉴权复用任务中心同款
``verify_session_owner``（404 语义不泄漏存在性；attempt 归属会话再验，
R30 跨租户拒绝）。取消是既有 durable 两阶段取消的薄投影。
"""
from __future__ import annotations

import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.core.auth import get_current_user_optional, get_owner_token
from app.services.gis_harness.goal_repair_execution import (
    GoalRepairErrorCode,
    build_repair_preview,
    cancel_repair_attempt,
    get_attempt_status,
    start_repair_attempt,
)
from app.services.gis_harness.goal_repair_execution.actions import (
    RepairOwnerContext,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/goal-repair", tags=["Goal Repair / 目标补全"])

_ERROR_STATUS = {
    "PLAN_NOT_FOUND": 404,
    "GOAL_SATISFIED": 409,
    "SESSION_BUSY": 409,
    "ACTIVE_ATTEMPT_EXISTS": 409,
    "NO_PROGRESS": 409,
    "BUDGET_DENIED": 402,
    "GOAL_SUPERSEDED": 409,
    "REVISION_DRIFT": 409,
    "ATTEMPT_NOT_FOUND": 404,
    "ATTEMPT_FINISHED": 409,
    "INVALID_ACTION_IDS": 422,
    "PERMISSION_DENIED": 403,
}


def _http_error(exc: GoalRepairErrorCode) -> HTTPException:
    status = _ERROR_STATUS.get(exc.code, 400)
    detail: dict = {"code": exc.code, "error": exc.message}
    if exc.detail:
        detail["detail"] = exc.detail
    return HTTPException(status_code=status, detail=detail)


async def _owned_session(
    session_id: str,
    user: Optional[dict],
    owner_token: Optional[str],
) -> None:
    """会话归属守卫（404 语义；跨租户/未授权一律不可见，R30）。"""
    from app.core.database import async_db_session
    from app.services.auth_history_bridge import verify_session_owner

    user = user or {}
    uid = user.get("user_id") or user.get("id")
    if uid in (None, "", "anonymous"):
        uid = None
    async with async_db_session() as db:
        await verify_session_owner(
            db, session_id, user_id=str(uid) if uid else None,
            owner_token=owner_token)


def _owner_ctx(
    session_id: str,
    user: Optional[dict],
    owner_token: Optional[str],
    project_id: Optional[str] = None,
) -> RepairOwnerContext:
    user = user or {}
    uid = user.get("user_id") or user.get("id")
    if uid in (None, "", "anonymous"):
        uid = None
    return RepairOwnerContext(
        session_id=session_id,
        user_id=str(uid) if uid else None,
        org_id=user.get("org_id"),
        project_id=project_id,
        owner_token=owner_token,
    )


class AttemptStartRequest(BaseModel):
    """start 请求体 —— 只含有界选择（action id / 损失确认码）。

    服务端重建其余一切（owner、tool、参数、权限均不可自报，R07/R08）。
    """

    action_ids: Optional[List[str]] = Field(
        default=None, max_length=8,
        description="选择执行的预览动作 id 子集；缺省 = 全部可执行动作")
    acknowledged_losses: Optional[List[str]] = Field(
        default=None, max_length=8,
        description="交付格式损失确认码（必须覆盖预览披露的全部义务）")
    project_id: Optional[str] = Field(default=None, max_length=128)


@router.get("/sessions/{session_id}/preview")
async def preview_goal_repair(
    session_id: str,
    _user: Optional[dict] = Depends(get_current_user_optional),
    owner_token: Optional[str] = Depends(get_owner_token),
):
    """版本化补全预览（零副作用；无计划/satisfied → typed 语义错误）。"""
    await _owned_session(session_id, _user, owner_token)
    try:
        preview = await build_repair_preview(
            session_id, ctx=_owner_ctx(session_id, _user, owner_token))
    except GoalRepairErrorCode as exc:
        raise _http_error(exc) from exc
    return preview


@router.post("/sessions/{session_id}/attempts", status_code=202)
async def start_goal_repair(
    session_id: str,
    body: AttemptStartRequest,
    _user: Optional[dict] = Depends(get_current_user_optional),
    owner_token: Optional[str] = Depends(get_owner_token),
):
    """启动一次有界补全（幂等：活跃 attempt 复用；busy 语义见 409 code）。"""
    await _owned_session(session_id, _user, owner_token)
    ctx = _owner_ctx(session_id, _user, owner_token,
                     project_id=body.project_id)
    try:
        view = await start_repair_attempt(
            session_id,
            selected_action_ids=body.action_ids,
            acknowledged_losses=body.acknowledged_losses,
            ctx=ctx,
            entry="ui",
            session_busy_check=True)
    except GoalRepairErrorCode as exc:
        raise _http_error(exc) from exc
    return view


@router.get("/attempts/{attempt_id}")
async def get_goal_repair_attempt(
    attempt_id: str,
    _user: Optional[dict] = Depends(get_current_user_optional),
    owner_token: Optional[str] = Depends(get_owner_token),
):
    """attempt 状态视图（含 waiting_delivery/runner 丢失的惰性对账）。"""
    from app.services.gis_harness.goal_repair_execution.store import (
        ensure_attempt as ensure_view,
    )

    try:
        view = ensure_view(attempt_id)
    except GoalRepairErrorCode as exc:
        raise _http_error(exc) from exc
    # attempt 归属会话再验（跨租户不泄漏存在性，R30）。
    await _owned_session(str(view.get("session_id") or ""), _user, owner_token)
    try:
        return await get_attempt_status(attempt_id)
    except GoalRepairErrorCode as exc:
        raise _http_error(exc) from exc


@router.post("/attempts/{attempt_id}/cancel")
async def cancel_goal_repair(
    attempt_id: str,
    _user: Optional[dict] = Depends(get_current_user_optional),
    owner_token: Optional[str] = Depends(get_owner_token),
):
    """两阶段取消（幂等；已终态 = no-op 披露）。"""
    from app.services.gis_harness.goal_repair_execution.store import (
        ensure_attempt as ensure_view,
    )

    try:
        view = ensure_view(attempt_id)
    except GoalRepairErrorCode as exc:
        raise _http_error(exc) from exc
    await _owned_session(str(view.get("session_id") or ""), _user, owner_token)
    ctx = _owner_ctx(str(view.get("session_id") or ""), _user, owner_token)
    try:
        return await cancel_repair_attempt(attempt_id, ctx=ctx)
    except GoalRepairErrorCode as exc:
        raise _http_error(exc) from exc


__all__ = ["router"]
