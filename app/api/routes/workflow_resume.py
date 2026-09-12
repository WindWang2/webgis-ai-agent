"""Project-level workflow resume 路由（Harness V5 — ADR-0118 决策 D8）。

- 写锚点：``POST /chat/sessions/{session_id}/workflow-resume-anchor``
  （require_owned_session —— 与其它 session 写通道同门）。
- 恢复：``POST /chat/workflow-resume/{anchor_id}``（服务层强制
  anchor.user_id == 当前用户；匿名锚点不可恢复 —— fail-closed）。
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import (
    get_async_db,
    get_current_user,
    get_current_user_optional,
    require_owned_session,
)
from app.schemas.workflow_resume_schema import (
    ResumeAnchorResponse,
    WorkflowResumeResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post(
    "/chat/sessions/{session_id}/workflow-resume-anchor",
    response_model=ResumeAnchorResponse,
)
async def create_workflow_resume_anchor(
    session_id: str,
    conversation=Depends(require_owned_session),
    db: AsyncSession = Depends(get_async_db),
    _user: dict = Depends(get_current_user_optional),
) -> ResumeAnchorResponse:
    """把当前 session 的可恢复事实存为项目级锚点（DB 持久）。

    review R2 Q9（既定语义，不改行为）：匿名可建锚（user_id=None 落库，
    建锚只记录指针不授权读）—— 但匿名不可恢复（恢复端走 get_current_user
    强鉴权 + 服务层 user_id 一致比对，None 一律 PermissionError）。
    """
    from app.services.gis_harness.resume_anchor import save_anchor

    user_id = _user.get("user_id") if isinstance(_user, dict) else None
    project_id = str(getattr(conversation, "project_id", "") or "") or None
    try:
        result = await save_anchor(
            db, session_id=session_id, user_id=user_id,
            project_id=project_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except Exception:  # noqa: BLE001
        logger.exception("[ResumeAnchor] save failed session=%s", session_id)
        raise HTTPException(status_code=500, detail="failed to save resume anchor")
    return ResumeAnchorResponse(
        anchor_id=result["anchor_id"],
        trace_last_seq=result["anchor"].get("trace_last_seq", 0),
        ref_count=len(result["anchor"].get("ref_ids") or []),
    )


@router.post(
    "/chat/workflow-resume/{anchor_id}",
    response_model=WorkflowResumeResponse,
)
async def resume_workflow_from_anchor(
    anchor_id: str,
    db: AsyncSession = Depends(get_async_db),
    user: dict = Depends(get_current_user),
) -> WorkflowResumeResponse:
    """从锚点恢复新 session（旧 session 过期不影响锚点有效性）。"""
    from app.services.gis_harness.resume_anchor import resume_from_anchor

    user_id = user.get("user_id") if isinstance(user, dict) else None
    try:
        return WorkflowResumeResponse(
            **await resume_from_anchor(db, anchor_id=anchor_id, user_id=user_id)
        )
    except LookupError:
        raise HTTPException(status_code=404, detail="resume anchor not found")
    except PermissionError:
        raise HTTPException(
            status_code=403, detail="resume anchor is not owned by current user"
        )
    except Exception:  # noqa: BLE001
        logger.exception("[ResumeAnchor] resume failed anchor=%s", anchor_id)
        raise HTTPException(status_code=500, detail="failed to resume workflow")
