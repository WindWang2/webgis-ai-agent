"""Server-owned session authority for HTTP workflow evidence and continuation."""
from __future__ import annotations

import uuid
from typing import Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.db_model import Conversation, User
from app.models.project import WorkflowRun


async def create_workflow_session(user_id: Optional[str]) -> str:
    """Use normal history admission/eviction; never accept a client session ID."""
    from app.core.database import async_db_session
    from app.services.history_service_async import AsyncHistoryService

    if not user_id or user_id == "anonymous":
        raise HTTPException(status_code=401, detail="Authentication required")
    async with async_db_session() as db:
        user = await db.get(User, user_id)
        if user is None or not user.is_active:
            raise HTTPException(status_code=401, detail="User unavailable")
        sid = f"workflow-{uuid.uuid4().hex}"
        conversation, created = await AsyncHistoryService(db).get_or_create_conversation_with_created(
            sid, user_id=user_id,
        )
        if not created or conversation.user_id != user_id:
            raise HTTPException(status_code=503, detail="Workflow session unavailable")
    return str(sid)


def continuation_session(db: Session, prior: Optional[WorkflowRun], *, user_id: Optional[str]) -> str:
    """Reconstruction must read the original live owner's actual evidence scope."""
    if prior is None:
        raise HTTPException(status_code=404, detail="Run not found")
    sid = prior.session_id
    if not sid:
        raise ValueError("prior run has no persistent evidence session; a full rerun is required")
    conversation = db.get(Conversation, sid)
    user = db.get(User, user_id) if user_id else None
    if (user is None or not user.is_active or conversation is None
            or not user_id or conversation.user_id != user_id):
        raise HTTPException(status_code=404, detail="Run evidence unavailable")
    return str(sid)


def require_legacy_rerun_owner(db: Session, prior: Optional[WorkflowRun], *, project_id: Optional[str], user_id: Optional[str]) -> None:
    """A NULL legacy scope has no private-session owner proof; only its live project owner may start afresh."""
    from app.models.project import Project

    project = db.get(Project, project_id) if project_id else None
    user = db.get(User, user_id) if user_id else None
    if (prior is None or prior.project_id != project_id or project is None
            or project.status == "deleted" or user is None or not user.is_active
            or not user_id or project.owner_id != user_id):
        raise HTTPException(status_code=404, detail="Run evidence unavailable")
