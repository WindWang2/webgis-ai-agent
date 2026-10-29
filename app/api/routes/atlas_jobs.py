"""Atlas batch jobs API —— 批量图册任务提交与详情（owner-scoped）。

- ``POST /api/v1/atlas/jobs``：验证会话归属 → committed spec 存在性 +
  dry-run 规划（typed 400，提交即知策略是否成立）→ durable job（幂等）。
- ``GET /api/v1/atlas/jobs/{job_id}``：JobView + 页级状态 + 产物下载 URL
  （属主判定与 /tasks/jobs 同款：user/owner_token 三元组，非属主 404）。
- 取消/重试复用 ``/tasks/jobs/{id}/cancel|retry``（既有端点，不改）。

安全：与 jobs.py 同纪律 —— 归属不成立一律 404（不泄露存在性）；提交
必须通过 ``verify_session_owner``（会话是权威 spec 与数据的归属域）。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user_optional, get_owner_token
from app.core.database import get_async_db
from app.lib.cartography.mapspec_schema import MapSpecSchemaError
from app.services.auth_history_bridge import verify_session_owner
from app.services.jobs.store import DurableJobStore, JobNotFound
from app.services.jobs.views import job_from_record

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/atlas/jobs", tags=["地图制图"])


class AtlasSubmitRequest(BaseModel):
    """提交请求（atlas 策略字段复用既有 AtlasRequestPolicy 形状）。"""

    session_id: str = Field(min_length=1, max_length=255)
    atlas: Dict[str, Any] = Field(default_factory=dict)
    """AtlasPolicy 投影：driver/layerId/categoryProperty/featuresPerPage/
    pageBudget/includeCover/atlasTitle（camelCase 与 vector-pdf 请求一致）。"""
    classification_scope: str = Field(default="shared", pattern="^(shared|local)$")
    target_dpi: int = Field(default=300, ge=72, le=600)
    title: str = Field(default="", max_length=160)
    simulated_data: bool = False


class AtlasSubmitResponse(BaseModel):
    success: bool = True
    job_id: str
    status: str
    idempotent_reuse: bool = False
    message: str = ""
    plan: Optional[Dict[str, Any]] = None
    poll_url: str = ""


class AtlasJobDetailResponse(BaseModel):
    job: Dict[str, Any]
    pages: list = Field(default_factory=list)
    artifacts: Dict[str, Any] = Field(default_factory=dict)


_ALLOWED_POLICY_KEYS = {
    "driver", "layerId", "categoryProperty", "featuresPerPage",
    "pageBudget", "includeCover", "atlasTitle",
}


def _policy_from_request(raw: Dict[str, Any]) -> Dict[str, Any]:
    """camelCase 请求投影 → AtlasPolicy 构造键（严格转换；缺省与 vector-pdf 同值）。

    R1-P2：类型强转/未知键/伪布尔一律 typed 400（``atlas_policy_invalid``），
    不静默吞也不 500。
    """
    if not isinstance(raw, dict):
        raise HTTPException(
            status_code=400,
            detail={"code": "atlas_policy_invalid", "message": "atlas 必须是对象"})
    unknown = set(raw.keys()) - _ALLOWED_POLICY_KEYS
    if unknown:
        raise HTTPException(
            status_code=400,
            detail={"code": "atlas_policy_invalid",
                    "message": f"atlas 含未知键: {', '.join(sorted(unknown))}"})
    def _strict_int(key: str, default: int) -> int:
        """R2-B：仅接受真 int（bool/float/数字串一律 400；键缺席用默认）。"""
        if key not in raw:
            return default
        val = raw.get(key)
        if isinstance(val, bool) or not isinstance(val, int):
            raise HTTPException(
                status_code=400,
                detail={"code": "atlas_policy_invalid",
                        "message": f"{key} 必须是整数"})
        return val

    features_per_page = _strict_int("featuresPerPage", 50)
    page_budget = _strict_int("pageBudget", 20)
    include_cover_raw = raw.get("includeCover")
    if include_cover_raw is not None and not isinstance(include_cover_raw, bool):
        raise HTTPException(
            status_code=400,
            detail={"code": "atlas_policy_invalid",
                    "message": "includeCover 必须是布尔值"})
    return {
        "driver": str(raw.get("driver") or "frames"),
        "layer_id": str(raw.get("layerId") or ""),
        "category_property": str(raw.get("categoryProperty") or ""),
        "features_per_page": features_per_page,
        "page_budget": page_budget,
        "include_cover": bool(include_cover_raw),
        "atlas_title": str(raw.get("atlasTitle") or ""),
    }


@router.post("", response_model=AtlasSubmitResponse)
async def submit_atlas_export_job(
    body: AtlasSubmitRequest,
    db: AsyncSession = Depends(get_async_db),
    user: dict = Depends(get_current_user_optional),
    owner_token: Optional[str] = Depends(get_owner_token),
) -> AtlasSubmitResponse:
    """提交批量图册 durable job（幂等；重复提交复用同一 job 行）。"""
    user_id = (user or {}).get("user_id") if (user or {}).get("user_id") not in (None, "anonymous") else None
    # R1-P2：产物下载走属主（user_id）通道 —— 匿名提交会产出无人可下载的
    # PDF，诚实拒绝（401 typed）而不是静默半成品。
    if user_id is None:
        raise HTTPException(
            status_code=401,
            detail={"code": "atlas_owner_required",
                    "message": "批量图册导出需要已认证用户（产物下载走属主通道）"})
    await verify_session_owner(
        db, body.session_id, user_id=user_id, owner_token=owner_token)

    from app.services.atlas_batch.submit import (
        build_atlas_job_spec,
        dry_run_plan,
        submit_atlas_job,
    )

    try:
        spec = build_atlas_job_spec(
            session_id=body.session_id,
            atlas_policy=_policy_from_request(body.atlas),
            classification_scope=body.classification_scope,
            target_dpi=body.target_dpi,
            title=body.title,
            simulated_data=body.simulated_data,
        )
    except MapSpecSchemaError as ex:
        raise HTTPException(
            status_code=400,
            detail={"code": getattr(ex, "code", "atlas_policy_invalid"),
                    "message": str(ex)})

    # committed spec 存在性 + dry-run 规划（纯规划无渲染；R1-P2：与执行面
    # 同口径先水合 ref: 载体源，否则 ref 形态 spec 会被误判无页/页数失真。
    # R2-A：async 路由直接 await async 水合本体 —— run_sync 桥在运行 loop
    # 内会 RuntimeError→500）
    from app.services.mapspec_store import mapspec_store
    from app.services.publication_export import hydrate_ref_sources

    document = await mapspec_store.get_mapspec(body.session_id)
    if not isinstance(document, dict):
        raise HTTPException(
            status_code=404,
            detail={"code": "atlas_spec_missing",
                    "message": f"会话 {body.session_id} 无已提交的权威 MapSpec"})
    try:
        plan_summary = dry_run_plan(
            spec, await hydrate_ref_sources(document, body.session_id))
    except MapSpecSchemaError as ex:
        raise HTTPException(
            status_code=400,
            detail={"code": getattr(ex, "code", "atlas_plan_invalid"),
                    "message": str(ex)})

    # 归属经 JobOrigin 上下文注入（submit_durable_job 消费 → creator_id/
    # owner_token 列）：REST 提交的 job 才能被同一 user/owner_token 的
    # /atlas/jobs/{id} 与任务中心读到（get_for_owner 只认这两元组）。
    from app.services.jobs.context import JobOrigin, use_origin

    origin = JobOrigin(
        session_id=body.session_id,
        owner_id=user_id,
        owner_token=owner_token,
    )
    with use_origin(origin):
        result = submit_atlas_job(spec, origin_session_id=body.session_id)
    return AtlasSubmitResponse(
        job_id=str(result.get("job_id") or ""),
        status=str(result.get("status") or "submitted"),
        idempotent_reuse=bool(result.get("idempotent_reuse")),
        message=str(result.get("message") or ""),
        plan=plan_summary,
        poll_url=f"/api/v1/tasks/jobs/{result.get('job_id')}",
    )


@router.get("/{job_id}", response_model=AtlasJobDetailResponse)
async def get_atlas_export_job(
    job_id: str,
    db: AsyncSession = Depends(get_async_db),
    user: dict = Depends(get_current_user_optional),
    owner_token: Optional[str] = Depends(get_owner_token),
) -> AtlasJobDetailResponse:
    """图册 job 详情（页级状态 + 产物链接；非属主 404 不泄露存在性）。"""
    user = user or {}
    owner_id = user.get("user_id") if user.get("user_id") not in (None, "anonymous") else None
    try:
        record = await DurableJobStore.get_for_owner(
            db, job_id, owner_id=owner_id, owner_token=owner_token)
    except JobNotFound:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    view = job_from_record(record)
    summary = record.result_summary if isinstance(record.result_summary, dict) else {}
    artifacts = summary.get("artifacts") if isinstance(summary.get("artifacts"), dict) else {}
    pdf = artifacts.get("pdf")
    manifest = artifacts.get("manifest")
    return AtlasJobDetailResponse(
        job=view.model_dump(),
        pages=view.page_status or [],
        artifacts={
            "pdf": pdf,
            "manifest": manifest,
            "pdf_url": f"/api/v1/export/download/{pdf}" if pdf else None,
            "manifest_url": f"/api/v1/export/download/{manifest}" if manifest else None,
            "lineage_recorded": bool(summary.get("lineage_recorded")),
        },
    )
