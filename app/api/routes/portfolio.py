"""Mission Portfolio 只读投影 API（org-scoped；零新状态真相）。"""
from __future__ import annotations

import logging
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException

from app.core.auth import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/portfolio", tags=["Mission Portfolio"])


def _org(user: Dict[str, Any]) -> str:
    from app.core import tenancy

    org = tenancy.effective_org_in_thread(user)
    if not org:
        raise HTTPException(status_code=403, detail="org_context_required")
    return org


@router.get("/summary")
def summary(user: Dict[str, Any] = Depends(get_current_user)) -> dict:
    from app.services.spatial_events.portfolio import org_summary

    return org_summary(_org(user))


@router.get("/projects")
def projects(user: Dict[str, Any] = Depends(get_current_user)) -> dict:
    from app.services.spatial_events.portfolio import project_portfolio

    org = _org(user)
    rows = project_portfolio(org)
    return {"org_id": org, "projects": rows, "count": len(rows)}


@router.get("/projects/{project_id}")
def project_detail(
    project_id: str, user: Dict[str, Any] = Depends(get_current_user)
) -> dict:
    from app.services.spatial_events.portfolio import project_detail as _detail

    org = _org(user)
    detail = _detail(org, project_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="project_not_found")
    return detail
