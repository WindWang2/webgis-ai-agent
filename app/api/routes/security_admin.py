"""V9 安全管理面路由（ADR-0139 P5/P7）：

- ``GET  /admin/orgs/{org_id}/quota``         配额快照（admin:read）
- ``PUT  /admin/orgs/{org_id}/quota``         配额配置覆盖（admin:write）
- ``GET  /admin/orgs/{org_id}/quota/usage``   用量投影（admin:read）
- ``GET  /admin/orgs/{org_id}/audit``         组织审计事件查询（admin:read）

鉴权双层：``require_scope``（OAuth scope 声明层）+ ``require_admin``
（role 实时权威守卫）。admin 动作与配额越限均落审计（fail-open）。
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import require_admin
from app.core.database import get_async_db
from app.core.scopes import require_scope
from app.services import audit as audit_service
from app.services import org_quota

router = APIRouter(prefix="/admin", tags=["Security Admin (V9)"])


def _trace_id(request: Request) -> Optional[str]:
    return audit_service.trace_id_from_header(request.headers.get("traceparent"))


class OrgQuotaUpdate(BaseModel):
    """配额覆盖（NULL/缺省 = 跟随全局默认；非负整数）。"""

    max_storage_bytes: Optional[int] = Field(default=None, ge=0)
    max_concurrent_tasks: Optional[int] = Field(default=None, ge=0)
    rate_limit_per_min: Optional[int] = Field(default=None, ge=0)


@router.get("/orgs/{org_id}/quota")
async def get_org_quota(
    org_id: str,
    _scope: dict = Depends(require_scope("admin:read")),
    _admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
) -> Dict[str, Any]:
    limits = await org_quota.effective_limits(db, org_id)
    return {
        "org_id": org_id,
        "max_storage_bytes": limits.max_storage_bytes,
        "max_concurrent_tasks": limits.max_concurrent_tasks,
        "rate_limit_per_min": limits.rate_per_min,
        "defaults": {
            "max_storage_bytes": org_quota.DEFAULT_MAX_STORAGE_BYTES,
            "max_concurrent_tasks": org_quota.DEFAULT_MAX_CONCURRENT_TASKS,
            "rate_limit_per_min": org_quota.DEFAULT_RATE_PER_MIN,
        },
    }


@router.put("/orgs/{org_id}/quota")
async def set_org_quota(
    org_id: str,
    body: OrgQuotaUpdate,
    request: Request,
    scope_user: dict = Depends(require_scope("admin:write")),
    _admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
) -> Dict[str, Any]:
    """per-org 配额覆盖（upsert；admin 动作落审计）。"""
    from app.models.db_model import OrgQuota

    actor = scope_user.get("user_id")
    row = (await db.execute(
        _select_org_quota(org_id)
    )).scalar_one_or_none()
    if row is None:
        row = OrgQuota(org_id=org_id, updated_by=actor)
        db.add(row)
    row.max_storage_bytes = body.max_storage_bytes
    row.max_concurrent_tasks = body.max_concurrent_tasks
    row.rate_limit_per_min = body.rate_limit_per_min
    row.updated_by = actor
    from datetime import datetime, timezone

    row.updated_at = datetime.now(timezone.utc)
    await db.commit()

    await audit_service.record_audit(
        db,
        action=audit_service.ACTION_ADMIN_QUOTA_SET,
        actor_id=actor,
        org_id=org_id,
        target_type="org_quota",
        target_id=org_id,
        detail={
            "max_storage_bytes": body.max_storage_bytes,
            "max_concurrent_tasks": body.max_concurrent_tasks,
            "rate_limit_per_min": body.rate_limit_per_min,
        },
        trace_id=_trace_id(request),
    )
    return {"success": True, "org_id": org_id}


async def _select_org_quota(org_id: str):
    from sqlalchemy import select

    from app.models.db_model import OrgQuota

    return select(OrgQuota).where(OrgQuota.org_id == org_id)


@router.get("/orgs/{org_id}/quota/usage")
async def get_org_quota_usage(
    org_id: str,
    _scope: dict = Depends(require_scope("admin:read")),
    _admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
) -> Dict[str, Any]:
    return await org_quota.usage_snapshot(db, org_id)


@router.get("/orgs/{org_id}/audit")
async def query_org_audit(
    org_id: str,
    request: Request,
    actor_id: Optional[str] = None,
    action: Optional[str] = None,
    limit: int = 100,
    before_id: Optional[int] = None,
    scope_user: dict = Depends(require_scope("admin:read")),
    _admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
) -> Dict[str, Any]:
    """组织审计事件查询（id 降序、有界 ≤500）。"""
    events = await audit_service.query_audit(
        db, org_id=org_id, actor_id=actor_id, action=action,
        limit=limit, before_id=before_id,
    )
    await audit_service.record_audit(
        db,
        action=audit_service.ACTION_ADMIN_AUDIT_QUERY,
        actor_id=scope_user.get("user_id"),
        org_id=org_id,
        target_type="audit_events",
        trace_id=_trace_id(request),
    )
    return {"org_id": org_id, "events": events,
            "limit": max(1, min(int(limit), 500))}


@router.get("/audit")
async def query_global_audit(
    request: Request,
    org_id: Optional[str] = None,
    actor_id: Optional[str] = None,
    action: Optional[str] = None,
    limit: int = 100,
    before_id: Optional[int] = None,
    _scope: dict = Depends(require_scope("admin:read")),
    _admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
) -> Dict[str, Any]:
    """跨组织审计查询（全 admin 面；org_id 可选过滤）。"""
    events = await audit_service.query_audit(
        db, org_id=org_id, actor_id=actor_id, action=action,
        limit=limit, before_id=before_id,
    )
    return {"events": events, "limit": max(1, min(int(limit), 500))}
