"""G47 Dorling cartogram 作者工作台 HTTP 面（UI 与 Pi 工具共用同一领域服务）。

会话归属由 ``require_owned_session`` 守卫；应用走与用户/agent 完全相同的
MapSpec 生命周期引擎（CAS + checkpoint + durable undo 栈）。导出从**冻结布局**
按需生成，下载前同样校验会话归属 —— ID/hash 不是权限。
"""
from __future__ import annotations

from typing import Any, Dict, Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response
from pydantic import BaseModel, Field

from app.models.db_model import Conversation
from app.schemas.cartogram_schema import (
    CartogramApplyReceipt,
    CartogramInspectResponse,
    CartogramJobStatusResponse,
    CartogramLayoutDetailResponse,
    CartogramLayoutListResponse,
    CartogramPlanReceipt,
)
from app.services.auth_history_bridge import require_owned_session
from app.services.cartogram_authoring import (
    AuthoringError,
    apply_layout,
    error_payload,
    export_layout,
    get_layout,
    http_status,
    inspect_layer,
    layout_status,
    list_layouts,
    parse_plan,
    plan_layout,
    start_layout,
)

router = APIRouter(prefix="/cartogram", tags=["Dorling 圆面积 cartogram G47"])


def _fail(err: AuthoringError) -> HTTPException:
    return HTTPException(status_code=http_status(err), detail=error_payload(err))


async def _owned(owner_token: Optional[str], conv: Conversation) -> Optional[str]:
    return owner_token or getattr(conv, "owner_token", None)


class PlanBody(BaseModel):
    plan: Dict[str, Any]


class ApplyBody(BaseModel):
    layout_ref: str = Field(min_length=5, max_length=200)
    expected_revision: int = Field(ge=0)
    allow_diagnostic: bool = False
    acknowledge_stale_source: bool = False
    client_mutation_id: Optional[str] = Field(default=None, max_length=128)


@router.get("/sessions/{session_id}/layers/{layer_id}/inspect", response_model=CartogramInspectResponse)
async def inspect(
    session_id: str,
    layer_id: str,
    owner_token: Optional[str] = Header(None, alias="X-Session-Token"),
    conv: Conversation = Depends(require_owned_session),
):
    try:
        return await inspect_layer(
            session_id, layer_id, owner_token=await _owned(owner_token, conv))
    except AuthoringError as err:
        raise _fail(err) from err


@router.post("/sessions/{session_id}/layers/{layer_id}/plan", response_model=CartogramPlanReceipt)
async def plan(
    session_id: str,
    layer_id: str,
    body: PlanBody,
    owner_token: Optional[str] = Header(None, alias="X-Session-Token"),
    conv: Conversation = Depends(require_owned_session),
):
    try:
        parsed = parse_plan({**body.plan, "layer_id": layer_id})
        return await plan_layout(
            session_id, layer_id, parsed,
            owner_token=await _owned(owner_token, conv))
    except AuthoringError as err:
        raise _fail(err) from err


@router.post("/sessions/{session_id}/layers/{layer_id}/run", response_model=CartogramPlanReceipt)
async def run(
    session_id: str,
    layer_id: str,
    body: PlanBody,
    owner_token: Optional[str] = Header(None, alias="X-Session-Token"),
    conv: Conversation = Depends(require_owned_session),
):
    try:
        parsed = parse_plan({**body.plan, "layer_id": layer_id})
        return await start_layout(
            session_id, layer_id, parsed,
            owner_token=await _owned(owner_token, conv))
    except AuthoringError as err:
        raise _fail(err) from err


@router.post("/sessions/{session_id}/layers/{layer_id}/apply", response_model=CartogramApplyReceipt)
async def apply(
    session_id: str,
    layer_id: str,
    body: ApplyBody,
    owner_token: Optional[str] = Header(None, alias="X-Session-Token"),
    conv: Conversation = Depends(require_owned_session),
):
    try:
        return await apply_layout(
            session_id, layer_id,
            layout_ref=body.layout_ref,
            expected_revision=body.expected_revision,
            allow_diagnostic=body.allow_diagnostic,
            acknowledge_stale_source=body.acknowledge_stale_source,
            origin="user", actor="dorling_cartogram_panel",
            mutation_id=(f"g47:{body.client_mutation_id}"
                         if body.client_mutation_id else None))
    except AuthoringError as err:
        raise _fail(err) from err


@router.get("/sessions/{session_id}/jobs/{job_id}", response_model=CartogramJobStatusResponse)
async def job_status(
    session_id: str,
    job_id: int,
    conv: Conversation = Depends(require_owned_session),
):
    try:
        return await layout_status(session_id, job_id)
    except AuthoringError as err:
        raise _fail(err) from err


@router.get("/sessions/{session_id}/layouts", response_model=CartogramLayoutListResponse)
async def layouts(
    session_id: str,
    limit: int = Query(default=20, ge=1, le=50),
    conv: Conversation = Depends(require_owned_session),
):
    try:
        return await list_layouts(session_id, limit=limit)
    except AuthoringError as err:
        raise _fail(err) from err


@router.get("/sessions/{session_id}/layouts/{layout_ref}", response_model=CartogramLayoutDetailResponse)
async def layout_detail(
    session_id: str,
    layout_ref: str,
    full: bool = Query(default=False),
    conv: Conversation = Depends(require_owned_session),
):
    try:
        return await get_layout(session_id, layout_ref, full=full)
    except AuthoringError as err:
        raise _fail(err) from err


@router.get("/sessions/{session_id}/layouts/{layout_ref}/export")
async def layout_export(
    session_id: str,
    layout_ref: str,
    format: str = Query(default="svg", pattern="^(svg|png|pdf)$"),
    dpi: int = Query(default=96, ge=48, le=600),
    conv: Conversation = Depends(require_owned_session),
):
    try:
        data, media, filename = await export_layout(
            session_id, layout_ref, format, dpi=dpi)
    except AuthoringError as err:
        raise _fail(err) from err
    return Response(
        content=data, media_type=media,
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}",
            "Cache-Control": "no-store",
        })
