"""Series Production REST 面 —— 系列出版生产提交与 job 查询（WP5）。

- ``POST /series-production/submit``  提交 durable job（幂等键 = 冻结计划
  digest；body: series_id + spec_version?）；
- ``GET  /series-production/jobs/{job_id}``  job 状态 + 逐成员结果摘要 +
  manifest 文件引用。

鉴权：读/写都需要登录；提交侧校验系列可见性（map_series 权威）。
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.core.auth import actor_ids, get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/series-production", tags=["系列出版生产"])


class SeriesProductionSubmitRequest(BaseModel):
    series_id: str = Field(min_length=1, max_length=36)
    spec_version: Optional[int] = Field(default=None, ge=1)


def _require_user(user: dict) -> tuple[Optional[str], object, Optional[str]]:
    user_id, org_id = actor_ids(user if isinstance(user, dict) else None)
    if not user_id:
        raise HTTPException(status_code=401, detail="authentication required")
    role = user.get("role") if isinstance(user, dict) else None
    return user_id, org_id, role


@router.post("/submit")
async def submit_series_production(
    body: SeriesProductionSubmitRequest, user: dict = Depends(get_current_user)
) -> dict:
    from app.core.database import SessionLocal

    from app.services.map_series.errors import MapSeriesError
    from app.services.map_series.freeze import build_freeze_plan
    from app.services.map_series.store import get_series, map_series_visible
    from app.services.series_production.service import submit_series_production
    from app.services.series_production.submit import freeze_plan_from_series_payload

    owner, org_id, _role = _require_user(user)
    with SessionLocal() as db:
        series = get_series(db, body.series_id)
        if not map_series_visible(series, owner, org_id):
            raise HTTPException(status_code=404, detail="map series not found")
    try:
        with SessionLocal() as db:
            freeze_payload = await build_freeze_plan(
                db, body.series_id, spec_version=body.spec_version,
                user_id=str(owner),
            )
    except MapSeriesError as exc:
        raise HTTPException(status_code=400, detail=str(exc)[:240])
    unresolvable = freeze_payload.get("unresolvable_member_ids") or []
    if unresolvable:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "MEMBERS_UNRESOLVABLE",
                "message": (
                    f"{len(unresolvable)} 个成员会话不可解析（无权威 MapSpec）"
                ),
                "member_ids": [str(x)[:40] for x in unresolvable[:8]],
            },
        )
    plan = freeze_plan_from_series_payload(freeze_payload)
    result = submit_series_production(
        plan, freeze_payload=freeze_payload,
        series_name=str(getattr(series, "name", "") or ""),
    )
    return {
        "success": True,
        "status": result.get("status", "submitted"),
        "job_id": result.get("job_id"),
        "idempotent_reuse": result.get("idempotent_reuse", False),
        "member_count": len(plan.members),
        "freeze_plan_digest": plan.plan_digest(),
    }


@router.get("/jobs/{job_id}")
async def get_series_production_job(
    job_id: int, user: dict = Depends(get_current_user)
) -> dict:
    from app.core.database import SessionLocal

    from app.services.jobs.store import DurableJobStore

    _require_user(user)
    with SessionLocal() as db:
        record = DurableJobStore.get_sync(db, int(job_id))
        if record is None:
            raise HTTPException(status_code=404, detail="job not found")
        result = getattr(record, "result", None)
        params = getattr(record, "parameters", None)
        return {
            "success": True,
            "job": {
                "id": record.id,
                "status": record.status,
                "task_type": record.task_type,
                "progress": getattr(record, "progress", None),
                "series_id": (params or {}).get("series_id") if isinstance(params, dict) else None,
                "freeze_plan_digest": (params or {}).get("freeze_plan_digest")
                if isinstance(params, dict) else None,
            },
            "result": result if isinstance(result, dict) else None,
        }


__all__ = ["router"]
