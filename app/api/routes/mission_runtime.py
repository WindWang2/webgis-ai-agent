"""Mission Runtime diagnostics / lifecycle REST (ADR-0197) — additive, thin.

Org-scoped reads; create/start/suspend/resume/cancel require auth.
Does not expose GIS payloads — refs and bounded diagnostics only.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.core.scopes import require_scope
from app.services.mission_runtime.service import (
    get_mission_runtime,
    mission_runtime_enabled,
)
from app.services.mission_runtime.store import FencingError, TransitionRejected

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/mission-runtime", tags=["Mission Runtime"])


def _org(user: Dict[str, Any]) -> str:
    from app.core import tenancy
    return tenancy.effective_org_in_thread(user)


def _uid(user: Dict[str, Any]) -> str:
    from app.core.auth import actor_ids
    uid, _org_id = actor_ids(user)
    return uid or ""


def _is_admin(user: Dict[str, Any]) -> bool:
    return isinstance(user, dict) and user.get("role") == "admin"


def _scope_user(user: Dict[str, Any]) -> Optional[str]:
    """非 admin 的 owner 谓词；admin 传 None（org 内全量，保持运维行为）。"""
    return None if _is_admin(user) else _uid(user)


class CreateMissionRequest(BaseModel):
    root_goal: str = Field(default="", max_length=2000)
    project_id: Optional[str] = None
    session_id: str = ""
    quota: Dict[str, float] = Field(default_factory=dict)


class WorkerRequest(BaseModel):
    worker_id: str = Field(..., min_length=1, max_length=128)


@router.get("/health")
def mission_health() -> dict:
    return {"enabled": mission_runtime_enabled(), "schema": "mission.v1"}


@router.post("/missions")
def create_mission(
    body: CreateMissionRequest,
    user: Dict[str, Any] = Depends(require_scope("mission:write")),
) -> dict:
    if not mission_runtime_enabled():
        raise HTTPException(status_code=503, detail="mission_runtime_disabled")
    svc = get_mission_runtime()
    rec = svc.create(
        org_id=_org(user),
        user_id=_uid(user),
        project_id=body.project_id,
        root_goal=body.root_goal,
        session_id=body.session_id,
        quota=body.quota or None,
    )
    return rec.model_dump(mode="json")


@router.get("/missions/{mission_id}")
def get_mission(
    mission_id: str,
    user: Dict[str, Any] = Depends(require_scope("mission:read")),
) -> dict:
    svc = get_mission_runtime()
    rec = svc.store.get_mission(
        mission_id, org_id=_org(user), user_id=_scope_user(user)
    )
    if rec is None:
        raise HTTPException(status_code=404, detail="mission_not_found")
    return rec.model_dump(mode="json")


@router.get("/missions/{mission_id}/diagnostics")
def mission_diagnostics(
    mission_id: str,
    user: Dict[str, Any] = Depends(require_scope("mission:read")),
) -> dict:
    svc = get_mission_runtime()
    # org + owner filter via get first
    if svc.store.get_mission(
        mission_id, org_id=_org(user), user_id=_scope_user(user)
    ) is None:
        raise HTTPException(status_code=404, detail="mission_not_found")
    return svc.diagnostics(mission_id, org_id=_org(user)).model_dump(mode="json")


@router.post("/missions/{mission_id}/start")
def start_mission(
    mission_id: str,
    body: WorkerRequest,
    user: Dict[str, Any] = Depends(require_scope("mission:write")),
) -> dict:
    return _lifecycle(mission_id, body.worker_id, user, "start")


@router.post("/missions/{mission_id}/suspend")
def suspend_mission(
    mission_id: str,
    body: WorkerRequest,
    user: Dict[str, Any] = Depends(require_scope("mission:write")),
) -> dict:
    return _lifecycle(mission_id, body.worker_id, user, "suspend")


@router.post("/missions/{mission_id}/resume")
def resume_mission(
    mission_id: str,
    body: WorkerRequest,
    user: Dict[str, Any] = Depends(require_scope("mission:write")),
) -> dict:
    svc = get_mission_runtime()
    if svc.store.get_mission(
        mission_id, org_id=_org(user), user_id=_scope_user(user)
    ) is None:
        raise HTTPException(status_code=404, detail="mission_not_found")
    try:
        return svc.resume(mission_id, worker_id=body.worker_id, org_id=_org(user))
    except (FencingError, TransitionRejected) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/missions/{mission_id}/cancel")
def cancel_mission(
    mission_id: str,
    body: WorkerRequest,
    user: Dict[str, Any] = Depends(require_scope("mission:write")),
) -> dict:
    return _lifecycle(mission_id, body.worker_id, user, "cancel")


def _lifecycle(mission_id: str, worker_id: str, user: Dict[str, Any], op: str) -> dict:
    if not mission_runtime_enabled():
        raise HTTPException(status_code=503, detail="mission_runtime_disabled")
    svc = get_mission_runtime()
    if svc.store.get_mission(
        mission_id, org_id=_org(user), user_id=_scope_user(user)
    ) is None:
        raise HTTPException(status_code=404, detail="mission_not_found")
    try:
        fn = getattr(svc, op)
        rec = fn(mission_id, worker_id=worker_id, org_id=_org(user))
        return rec.model_dump(mode="json")
    except (FencingError, TransitionRejected) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
