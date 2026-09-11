"""Data Lifecycle V9 REST 面 —— 统一生命周期策略引擎 + data-gc 闭环。

端点（前缀 /api/v1/data-lifecycle；新路由文件，不改既有路由签名）：

- ``GET  /objects``                  统一登记视图分页（kind 过滤）；
- ``POST /assess``                   全景评估（枚举五机制 → 登记分级）；
- ``GET  /policies``                 策略列表（含默认种子）；
- ``PUT  /policies/{name}``          策略 upsert（admin；observe-only 类
                                     拒绝删除动作）；
- ``POST /gc/plans``                 创建 GC 计划（dry-run 树 → 待审批）；
- ``GET  /gc/plans`` / ``GET /gc/plans/{id}``   计划查询；
- ``POST /gc/plans/{id}/approve|reject|cancel`` 审批转移（admin）；
- ``POST /gc/plans/{id}/execute``    执行（durable job；admin）；
- ``POST /gc/plans/{id}/rollback``   staging 回滚（admin）；
- ``POST /gc/plans/{id}/purge``      观察期后物理删（admin，显式）。

鉴权纪律（与 data-gc plan/execute 同款）：读路径 optional；任何状态变更
强制认证；审批/执行/回滚/物理删要求 admin。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.core.auth import (
    get_current_user,
    get_current_user_optional,
    require_admin,
)
from app.schemas.pagination import Page, clamp_pagination

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/data-lifecycle", tags=["数据生命周期 V9"])


# ── 请求模型 ─────────────────────────────────────────────────────────


class AssessRequest(BaseModel):
    persist: bool = True


class PolicyUpsertRequest(BaseModel):
    kind: str
    action: str = "observe"
    tier_thresholds: Optional[Dict[str, Any]] = None
    staging_hours: int = Field(default=72, ge=0, le=24 * 30)
    enabled: bool = False
    params: Optional[Dict[str, Any]] = None


class GcPlanCreateRequest(BaseModel):
    kinds: Optional[List[str]] = None
    tiers: Optional[List[str]] = None


# ── helpers ──────────────────────────────────────────────────────────


def _plan_view(plan: Any) -> Dict[str, Any]:
    return {
        "id": plan.id,
        "status": plan.status,
        "scope": plan.scope,
        "candidate_count": plan.candidate_count,
        "candidate_bytes": plan.candidate_bytes,
        "staging_expires_at": plan.staging_expires_at.isoformat()
        if plan.staging_expires_at else None,
        "created_by": plan.created_by,
        "approved_by": plan.approved_by,
        "executed_by": plan.executed_by,
        "job_id": plan.job_id,
        "result": plan.result,
        "plan_tree": plan.plan_tree,
        "created_at": plan.created_at.isoformat() if plan.created_at else None,
    }


def _map_gc_error(exc: Exception) -> HTTPException:
    from app.services.data_lifecycle.gc_plan import GcPlanError

    if isinstance(exc, GcPlanError):
        text = str(exc)
        code = 400 if "只支持 observe" in text else 409
        return HTTPException(status_code=code, detail=text)
    raise exc


# ── 对象 / 评估 ──────────────────────────────────────────────────────


@router.get("/objects")
def list_lifecycle_objects(
    limit: Optional[int] = None,
    offset: Optional[int] = None,
    kind: Optional[str] = None,
    tier: Optional[str] = None,
    _user: dict = Depends(get_current_user_optional),
) -> Page[Dict[str, Any]]:
    from app.core.database import SessionLocal
    from app.models.data_lifecycle import LifecycleObject

    limit_n, offset_n = clamp_pagination(limit, offset)
    with SessionLocal() as db:
        stmt = select(LifecycleObject).order_by(
            LifecycleObject.kind, LifecycleObject.last_used_at.desc()
        )
        count_stmt = select(LifecycleObject)
        if kind:
            stmt = stmt.where(LifecycleObject.kind == kind)
            count_stmt = count_stmt.where(LifecycleObject.kind == kind)
        if tier:
            stmt = stmt.where(LifecycleObject.tier == tier)
            count_stmt = count_stmt.where(LifecycleObject.tier == tier)
        total = len(db.execute(count_stmt).scalars().all())
        rows = db.execute(stmt.limit(limit_n).offset(offset_n)).scalars().all()
        items = [
            {
                "id": r.id,
                "kind": r.kind,
                "object_id": r.object_id,
                "owner_scope": r.owner_scope,
                "byte_size": r.byte_size,
                "tier": r.tier,
                "last_used_at": r.last_used_at.isoformat() if r.last_used_at else None,
                "first_seen_at": r.first_seen_at.isoformat() if r.first_seen_at else None,
                "info": r.info,
            }
            for r in rows
        ]
    return Page(items=items, total=total, limit=limit_n, offset=offset_n,
                has_more=(offset_n + limit_n) < total)


@router.post("/assess")
def run_assess(
    body: AssessRequest,
    user: dict = Depends(get_current_user),
) -> dict:
    from app.core.database import SessionLocal
    from app.services.data_lifecycle.policy import assess

    with SessionLocal() as db:
        summary = assess(db, persist=body.persist)
    return {"success": True, "summary": summary}


# ── 策略 ─────────────────────────────────────────────────────────────


@router.get("/policies")
def list_policies(_user: dict = Depends(get_current_user_optional)) -> dict:
    from app.core.database import SessionLocal
    from app.models.data_lifecycle import LifecyclePolicy
    from app.services.data_lifecycle.policy import ensure_defaults

    with SessionLocal() as db:
        ensure_defaults(db)
        rows = db.query(LifecyclePolicy).order_by(LifecyclePolicy.name).all()
        return {
            "success": True,
            "policies": [
                {
                    "name": p.name,
                    "kind": p.kind,
                    "action": p.action,
                    "tier_thresholds": p.tier_thresholds,
                    "staging_hours": p.staging_hours,
                    "enabled": p.enabled,
                    "params": p.params,
                }
                for p in rows
            ],
        }


@router.put("/policies/{name}")
def upsert_policy(
    name: str,
    body: PolicyUpsertRequest,
    _admin: dict = Depends(require_admin),
) -> dict:
    from app.core.database import SessionLocal
    from app.models.data_lifecycle import (
        LIFECYCLE_ACTIONS,
        LIFECYCLE_OBJECT_KINDS,
        LifecyclePolicy,
    )
    from app.services.data_lifecycle.gc_plan import OBSERVE_ONLY_KINDS

    if body.kind not in LIFECYCLE_OBJECT_KINDS:
        raise HTTPException(status_code=400, detail=f"未知对象类 '{body.kind}'")
    if body.action not in LIFECYCLE_ACTIONS:
        raise HTTPException(status_code=400, detail=f"未知动作 '{body.action}'")
    if body.kind in OBSERVE_ONLY_KINDS and body.action != "observe":
        raise HTTPException(
            status_code=400,
            detail=f"kind '{body.kind}' 在 V9 只支持 observe（真实删除走 lakehouse 自有 GC 保护面）",
        )
    with SessionLocal() as db:
        row = db.query(LifecyclePolicy).filter_by(name=name).one_or_none()
        if row is None:
            row = LifecyclePolicy(name=name[:64], kind=body.kind)
            db.add(row)
        row.kind = body.kind
        row.action = body.action
        row.tier_thresholds = body.tier_thresholds
        row.staging_hours = body.staging_hours
        row.enabled = body.enabled
        row.params = body.params
        db.commit()
        return {"success": True, "name": row.name, "action": row.action,
                "enabled": row.enabled}


# ── GC 计划闭环 ──────────────────────────────────────────────────────


@router.post("/gc/plans")
def create_plan(
    body: GcPlanCreateRequest,
    user: dict = Depends(get_current_user),
) -> dict:
    from app.core.database import SessionLocal
    from app.services.data_lifecycle.gc_plan import create_gc_plan

    try:
        with SessionLocal() as db:
            plan = create_gc_plan(
                db,
                kinds=body.kinds,
                tiers=body.tiers,
                created_by=(user.get("user_id") if isinstance(user, dict) else None),
            )
            return {"success": True, "plan": _plan_view(plan)}
    except Exception as exc:  # noqa: BLE001 — 映射状态机/词表错误
        raise _map_gc_error(exc)


@router.get("/gc/plans")
def list_plans(
    limit: Optional[int] = None,
    offset: Optional[int] = None,
    status: Optional[str] = None,
    _user: dict = Depends(get_current_user_optional),
) -> Page[Dict[str, Any]]:
    from app.core.database import SessionLocal
    from app.models.data_lifecycle import GcPlan

    limit_n, offset_n = clamp_pagination(limit, offset)
    with SessionLocal() as db:
        stmt = select(GcPlan).order_by(GcPlan.created_at.desc())
        count_stmt = select(GcPlan)
        if status:
            stmt = stmt.where(GcPlan.status == status)
            count_stmt = count_stmt.where(GcPlan.status == status)
        total = len(db.execute(count_stmt).scalars().all())
        rows = db.execute(stmt.limit(limit_n).offset(offset_n)).scalars().all()
    return Page(
        items=[_plan_view(r) for r in rows], total=total,
        limit=limit_n, offset=offset_n, has_more=(offset_n + limit_n) < total,
    )


@router.get("/gc/plans/{plan_id}")
def get_plan_detail(
    plan_id: str,
    _user: dict = Depends(get_current_user_optional),
) -> dict:
    from app.core.database import SessionLocal
    from app.services.data_lifecycle.gc_plan import get_plan

    with SessionLocal() as db:
        plan = get_plan(db, plan_id)
        if plan is None:
            raise HTTPException(status_code=404, detail="Plan not found")
        return {"success": True, "plan": _plan_view(plan)}


def _admin_action(plan_id: str, action: str, actor: Optional[str]):
    from app.core.database import SessionLocal
    from app.services.data_lifecycle.gc_plan import (
        GcPlanError,
        get_plan,
        transition,
    )

    with SessionLocal() as db:
        plan = get_plan(db, plan_id)
        if plan is None:
            raise HTTPException(status_code=404, detail="Plan not found")
        try:
            transition(plan, action, actor=actor)
        except GcPlanError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        db.commit()
        return {"success": True, "plan": _plan_view(plan)}


@router.post("/gc/plans/{plan_id}/approve")
def approve_plan(plan_id: str, admin: dict = Depends(require_admin)) -> dict:
    return _admin_action(plan_id, "approve",
                         admin.get("user_id") if isinstance(admin, dict) else None)


@router.post("/gc/plans/{plan_id}/reject")
def reject_plan(plan_id: str, admin: dict = Depends(require_admin)) -> dict:
    return _admin_action(plan_id, "reject",
                         admin.get("user_id") if isinstance(admin, dict) else None)


@router.post("/gc/plans/{plan_id}/cancel")
def cancel_plan(plan_id: str, admin: dict = Depends(require_admin)) -> dict:
    return _admin_action(plan_id, "cancel",
                         admin.get("user_id") if isinstance(admin, dict) else None)


@router.post("/gc/plans/{plan_id}/execute")
def execute_plan_endpoint(plan_id: str, admin: dict = Depends(require_admin)) -> dict:
    """执行穿 durable job（eager 模式下同步完成；结果回 plan.result）。"""
    from app.core.database import SessionLocal
    from app.services.data_lifecycle.gc_plan import GcPlanError, get_plan
    from app.services.data_lifecycle.jobs import execute_gc_plan_task
    from app.services.jobs.submit import submit_durable_job

    with SessionLocal() as db:
        plan = get_plan(db, plan_id)
        if plan is None:
            raise HTTPException(status_code=404, detail="Plan not found")
        if plan.status != "approved":
            raise HTTPException(
                status_code=409,
                detail=f"plan 状态为 '{plan.status}'，仅 approved 可执行",
            )
    try:
        result = submit_durable_job(
            celery_task=execute_gc_plan_task,
            task_type="data_lifecycle_gc_execute",
            display_name="数据生命周期 GC 计划执行",
            params={"plan_id": plan_id},
            task_kwargs={"plan_id": plan_id,
                         "actor": admin.get("user_id") if isinstance(admin, dict) else None},
            idempotent=False,
        )
    except GcPlanError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"success": True, **result}


@router.post("/gc/plans/{plan_id}/rollback")
def rollback_plan_endpoint(plan_id: str, admin: dict = Depends(require_admin)) -> dict:
    from app.core.database import SessionLocal
    from app.services.data_lifecycle.gc_plan import (
        GcPlanError,
        get_plan,
        rollback_plan,
    )

    with SessionLocal() as db:
        plan = get_plan(db, plan_id)
        if plan is None:
            raise HTTPException(status_code=404, detail="Plan not found")
        try:
            result = rollback_plan(
                db, plan,
                actor=admin.get("user_id") if isinstance(admin, dict) else None,
            )
        except GcPlanError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        return {"success": True, "plan": _plan_view(plan), "result": result}


@router.post("/gc/plans/{plan_id}/purge")
def purge_plan_endpoint(plan_id: str, admin: dict = Depends(require_admin)) -> dict:
    """观察期后的物理删（显式；返回 409 若观察期未到）。"""
    import datetime as _dt

    from app.core.database import SessionLocal
    from app.services.data_lifecycle.gc_plan import get_plan, purge_staging

    with SessionLocal() as db:
        plan = get_plan(db, plan_id)
        if plan is None:
            raise HTTPException(status_code=404, detail="Plan not found")
        if plan.status not in ("done", "failed"):
            raise HTTPException(status_code=409,
                                detail=f"plan 状态为 '{plan.status}'，仅终态可 purge")
        if plan.staging_expires_at and plan.staging_expires_at.replace(
                tzinfo=_dt.timezone.utc) > _dt.datetime.now(_dt.timezone.utc):
            raise HTTPException(
                status_code=409,
                detail=f"staging 观察期未到（{plan.staging_expires_at.isoformat()}）",
            )
        result = purge_staging(plan)
        return {"success": True, "result": result}


__all__ = ["router"]
