"""Data Delivery Routes — /api/v1/data-delivery（G18 WP09）。

路由只做验证与装配（鉴权 → 服务层）；重逻辑全部在
``app/services/data_delivery``。下载复用 map.py 的 fail-closed 纪律：
未授权/未完成一律 404，不泄漏交付存在性；Content-Disposition 带 UTF-8
安全文件名，响应附校验和头。取消不在此重复实现 —— 复用既有
``DELETE /api/v1/jobs/{job_id}``（两段式：DB 事实 + 进程 token）。
"""
from __future__ import annotations

import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from urllib.parse import quote

from app.core.auth import get_current_user, get_current_user_optional, get_owner_token
from app.schemas.data_delivery_schema import (
    DeliveryCapabilitiesResponse,
    DeliveryJobCreateRequest,
    DeliveryJobCreateResponse,
    DeliveryJobView,
    DeliveryPreview,
    DeliverySpec,
)
from app.services.data_delivery import (
    DeliveryError,
    get_delivery_job,
    list_delivery_jobs,
    preview_delivery,
    resolve_download,
    submit_delivery,
)
from app.schemas.data_delivery_schema import DeliveryLimitsView
from app.services.data_delivery.contracts import FORMAT_EXT
from app.services.data_delivery.source_resolver import DeliveryActor

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/data-delivery")


def _actor(user: Optional[dict], owner_token: Optional[str] = None) -> DeliveryActor:
    a = DeliveryActor.from_user_dict(user)
    a.owner_token = owner_token
    return a


def _map_error(exc: DeliveryError) -> HTTPException:
    status = exc.status_code
    if status == 404:
        return HTTPException(status_code=404, detail=exc.reason)
    return HTTPException(status_code=status,
                         detail={"code": exc.error_code, "error": exc.reason})


@router.get("/capabilities", response_model=DeliveryCapabilitiesResponse,
            tags=["Data Delivery / 数据交付"])
async def delivery_capabilities(
    _user: Optional[dict] = Depends(get_current_user_optional),
):
    """交付能力查询 —— 运行时真实驱动探测（可用/受限/不可用 + 约束说明）。

    纯静态探测（无数据访问），可选认证 —— 与地图导出能力查询同宽。
    """
    from app.services.data_delivery.contracts import (
        DATA_OBJECT_PUBLISH_BUDGET_BYTES,
        MAX_FIELDS,
        MAX_IDS,
        MAX_OUTPUT_BYTES,
        MAX_ROWS_PER_DELIVERY,
        MAX_TEMP_DISK_BYTES,
        READ_BATCH_ROWS,
        probe_all_formats,
        writer_versions,
    )

    from app.schemas.data_delivery_schema import FormatCapability

    return DeliveryCapabilitiesResponse(
        formats=[
            FormatCapability(
                format=p.format, available=p.available, constrained=p.constrained,
                reason=p.reason, notes=p.notes,
                extensions=[FORMAT_EXT[p.format]])
            for p in probe_all_formats()
        ],
        limits=DeliveryLimitsView(
            max_rows_per_delivery=MAX_ROWS_PER_DELIVERY,
            max_output_bytes=MAX_OUTPUT_BYTES,
            max_temp_disk_bytes=MAX_TEMP_DISK_BYTES,
            max_fields=MAX_FIELDS,
            max_ids=MAX_IDS,
            read_batch_rows=READ_BATCH_ROWS,
            data_object_publish_budget_bytes=DATA_OBJECT_PUBLISH_BUDGET_BYTES,
        ),
        writer_versions=writer_versions(),
        cancel_endpoint="DELETE /api/v1/jobs/{job_id}",
    )


@router.post("/preview", response_model=DeliveryPreview, tags=["Data Delivery / 数据交付"])
async def delivery_preview(
    spec: DeliverySpec,
    user: dict = Depends(get_current_user),
    owner_token: Optional[str] = Depends(get_owner_token),
):
    """交付预览：源身份/完整性、逐格式能力、损失与阻断项、需确认清单。"""
    try:
        return await preview_delivery(spec, _actor(user, owner_token))
    except DeliveryError as e:
        raise _map_error(e) from e


@router.post("/jobs", response_model=DeliveryJobCreateResponse,
             status_code=202, tags=["Data Delivery / 数据交付"])
async def create_delivery_job(
    req: DeliveryJobCreateRequest,
    user: dict = Depends(get_current_user),
    owner_token: Optional[str] = Depends(get_owner_token),
):
    """提交交付任务（服务端复检 + 损失确认强制；幂等键防重复制造输出）。"""
    try:
        result = await submit_delivery(
            req.spec, _actor(user, owner_token),
            acknowledged_losses=req.acknowledged_losses,
            project_id=req.project_id)
    except DeliveryError as e:
        raise _map_error(e) from e
    return DeliveryJobCreateResponse(**result)


@router.get("/jobs", response_model=List[DeliveryJobView],
            tags=["Data Delivery / 数据交付"])
async def list_jobs(
    project_id: Optional[str] = Query(None, max_length=128),
    limit: int = Query(50, ge=1, le=200),
    user: dict = Depends(get_current_user),
    owner_token: Optional[str] = Depends(get_owner_token),
):
    """本人可见的交付任务（server state 恢复 —— 页面刷新/项目重开后重建）。"""
    try:
        return await list_delivery_jobs(_actor(user, owner_token),
                                        project_id=project_id, limit=limit)
    except DeliveryError as e:
        raise _map_error(e) from e


@router.get("/jobs/{job_id}", response_model=DeliveryJobView,
            tags=["Data Delivery / 数据交付"])
async def get_job(
    job_id: str,
    user: dict = Depends(get_current_user),
    owner_token: Optional[str] = Depends(get_owner_token),
):
    """交付任务状态 + 完成后的完整回执。"""
    try:
        return await get_delivery_job(job_id, _actor(user, owner_token))
    except DeliveryError as e:
        raise _map_error(e) from e


@router.get("/jobs/{job_id}/download", tags=["Data Delivery / 数据交付"])
async def download_delivery(
    job_id: str,
    member: Optional[str] = Query(None, max_length=255),
    user: dict = Depends(get_current_user_optional),
    owner_token: Optional[str] = Depends(get_owner_token),
):
    """受控下载：授权（creator/会话/项目/owner token）→ receipt 成员校验。"""
    if user is None and not owner_token:
        raise HTTPException(status_code=401, detail="Authentication required")
    try:
        path, mime, filename, sha256 = await resolve_download(
            job_id, member, _actor(user, owner_token))
    except DeliveryError as e:
        raise _map_error(e) from e
    headers = {
        "Content-Disposition":
            f"attachment; filename*=UTF-8''{quote(filename, safe='')}",
        "X-Content-Type-Options": "nosniff",
    }
    if sha256:
        headers["X-Content-Sha256"] = sha256
    return FileResponse(path, media_type=mime, headers=headers)
