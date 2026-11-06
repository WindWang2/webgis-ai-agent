"""Mission Runtime diagnostics / lifecycle REST (ADR-0197) — additive, thin.

Org-scoped reads; create/start/suspend/resume/cancel require auth.
Does not expose GIS payloads — refs and bounded diagnostics only.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator

from app.core.scopes import require_scope
from app.services.mission_runtime.service import (
    get_mission_runtime,
    mission_runtime_enabled,
)
from app.services.mission_runtime.store import FencingError, TransitionRejected

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/mission-runtime", tags=["Mission Runtime"])


def _server_worker_id(mission_id: str, user: Dict[str, Any]) -> str:
    """API lease identity derives from the authenticated principal, never a body."""
    import hashlib
    return "api:" + hashlib.sha256(f"{_uid(user)}:{mission_id}".encode()).hexdigest()[:40]


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
    quota: Dict[Annotated[str, Field(max_length=64)], Annotated[float, Field(ge=0, allow_inf_nan=False)]] = Field(default_factory=dict, max_length=16)

    @field_validator("quota")
    @classmethod
    def _known_dimensions(cls, value: Dict[str, float]) -> Dict[str, float]:
        from app.services.mission_runtime.quota_policy import MISSION_CEILINGS
        if any(key not in MISSION_CEILINGS for key in value):
            raise ValueError("Unknown mission quota dimension")
        return value


class WorkerRequest(BaseModel):
    worker_id: str = Field(default="", max_length=128)


@router.get("/health")
def mission_health() -> dict:
    return {"enabled": mission_runtime_enabled(), "schema": "mission.v1"}


def _assert_create_bindings(user: Dict[str, Any], *, session_id: str, project_id: Optional[str]) -> None:
    """#1415：create 时校验 session/project 归属（防把他人 scope 记到自己 mission）。"""
    from app.core.database import SessionLocal

    uid = _uid(user)
    sid = (session_id or "").strip()
    if sid:
        from app.models.db_model import Conversation

        with SessionLocal() as db:
            conv = db.get(Conversation, sid)
            if conv is None or not uid or str(conv.user_id) != str(uid):
                raise HTTPException(status_code=404, detail="session_not_found")
    pid = (project_id or "").strip() if project_id else ""
    if pid:
        from app.services.project_service import ProjectService
        from app.core.auth import actor_ids

        user_id, org_id = actor_ids(user)
        with SessionLocal() as db:
            project = ProjectService.get_project_with_auth(
                db, pid, user_id=user_id, org_id=org_id
            )
        if project is None:
            raise HTTPException(status_code=404, detail="project_not_found")


@router.post("/missions")
def create_mission(
    body: CreateMissionRequest,
    user: Dict[str, Any] = Depends(require_scope("mission:write")),
) -> dict:
    if not mission_runtime_enabled():
        raise HTTPException(status_code=503, detail="mission_runtime_disabled")
    _assert_create_bindings(
        user, session_id=body.session_id, project_id=body.project_id
    )
    svc = get_mission_runtime()
    org_id = _org(user)
    try:
        quota = svc.effective_quota(org_id, body.quota)
    except Exception as exc:
        logger.exception("Mission quota policy unavailable")
        raise HTTPException(status_code=503, detail="Mission quota policy unavailable") from exc
    rec = svc.create(
        org_id=org_id,
        user_id=_uid(user),
        project_id=body.project_id,
        root_goal=body.root_goal,
        session_id=body.session_id,
        quota=quota,
    )
    return rec.model_dump(mode="json", exclude={"lease_owner"})


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
    return rec.model_dump(mode="json", exclude={"lease_owner"})


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
    return svc.diagnostics(mission_id, org_id=_org(user)).model_dump(mode="json", exclude={"lease_owner"})


@router.post("/missions/{mission_id}/start")
def start_mission(
    mission_id: str,
    body: WorkerRequest,
    user: Dict[str, Any] = Depends(require_scope("mission:write")),
) -> dict:
    return _lifecycle(mission_id, _server_worker_id(mission_id, user), user, "start")


@router.post("/missions/{mission_id}/suspend")
def suspend_mission(
    mission_id: str,
    body: WorkerRequest,
    user: Dict[str, Any] = Depends(require_scope("mission:write")),
) -> dict:
    return _lifecycle(mission_id, _server_worker_id(mission_id, user), user, "suspend")


@router.post("/missions/{mission_id}/resume")
def resume_mission(
    mission_id: str,
    body: WorkerRequest,
    user: Dict[str, Any] = Depends(require_scope("mission:write")),
) -> dict:
    if not mission_runtime_enabled():
        raise HTTPException(status_code=503, detail="mission_runtime_disabled")
    svc = get_mission_runtime()
    if svc.store.get_mission(
        mission_id, org_id=_org(user), user_id=_scope_user(user)
    ) is None:
        raise HTTPException(status_code=404, detail="mission_not_found")
    try:
        result = svc.resume(mission_id, worker_id=_server_worker_id(mission_id, user), org_id=_org(user))
        result.pop("lease_owner", None)
        return result
    except (FencingError, TransitionRejected) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/missions/{mission_id}/cancel")
def cancel_mission(
    mission_id: str,
    body: WorkerRequest,
    user: Dict[str, Any] = Depends(require_scope("mission:write")),
) -> dict:
    return _lifecycle(mission_id, _server_worker_id(mission_id, user), user, "cancel")


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
        return rec.model_dump(mode="json", exclude={"lease_owner"})
    except (FencingError, TransitionRejected) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
