"""raster_sampling routes — G39 采样工作台 HTTP 面。

全部端点要求会话归属（``require_owned_session``）；owner_token 经
X-Session-Token 注入，用于 refs 的每次读取鉴权（工具面走
trusted_dispatch 单通道，见 raster_sampling_tools）。
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query

from app.models.db_model import Conversation
from app.core.auth import get_current_user_optional
from app.schemas.raster_sampling_schema import (
    ExecuteIn,
    PlanCreateIn,
    PlanReviseIn,
    PreviewIn,
)
from app.services.auth_history_bridge import require_owned_session
from app.services.raster_sampling import service
from app.services.raster_sampling.contracts import RasterSamplingError

router = APIRouter()


def _fail(exc: RasterSamplingError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.to_payload())


async def _owned(session_id: str, owner_token: Optional[str],
                 conv: Conversation) -> str:
    return owner_token or getattr(conv, "owner_token", None)


def _user_owner(user: Optional[dict]) -> dict:
    user = user or {}
    return {
        "user_id": str(user.get("id") or "") or None,
        "owner_token": None,
        "org_id": user.get("org_id"),
    }


@router.get("/raster-sampling/describe", tags=["栅格采样"])
async def describe(
    session_id: str = Query(..., min_length=8, max_length=128),
    owner_token: Optional[str] = Header(None, alias="X-Session-Token"),
    _conv: Conversation = Depends(require_owned_session),
):
    try:
        return await service.describe_sources(
            session_id, await _owned(session_id, owner_token, _conv))
    except RasterSamplingError as exc:
        raise _fail(exc) from exc


@router.post("/raster-sampling/plan", tags=["栅格采样"])
async def create_plan(
    body: PlanCreateIn,
    session_id: str = Query(..., min_length=8, max_length=128),
    user: Optional[dict] = Depends(get_current_user_optional),
    owner_token: Optional[str] = Header(None, alias="X-Session-Token"),
    _conv: Conversation = Depends(require_owned_session),
):
    try:
        return await service.create_plan(
            session_id=session_id,
            owner_token=await _owned(session_id, owner_token, _conv),
            name=body.name,
            mode=body.mode,
            points_ref=body.points_ref,
            selection=(body.selection.model_dump()
                       if body.selection is not None else None),
            lines_ref=body.lines_ref,
            line_feature_id=body.line_feature_id,
            station_crs=body.station_crs,
            distance_mode=body.distance_mode,
            station_step=body.station_step,
            include_vertices=body.include_vertices,
            method=body.method,
            nodata_policy=body.nodata_policy,
            sources=[s.model_dump() for s in body.sources],
            project_id=body.project_id,
            owner=str((user or {}).get("id") or ""),
        )
    except RasterSamplingError as exc:
        raise _fail(exc) from exc


@router.post("/raster-sampling/plan/{plan_id}/revise", tags=["栅格采样"])
async def revise_plan(
    plan_id: str,
    body: PlanReviseIn,
    session_id: str = Query(..., min_length=8, max_length=128),
    owner_token: Optional[str] = Header(None, alias="X-Session-Token"),
    _conv: Conversation = Depends(require_owned_session),
):
    try:
        return await service.revise_plan(
            session_id=session_id,
            plan_id=plan_id,
            expected_revision=body.expected_revision,
            owner_token=await _owned(session_id, owner_token, _conv),
            updates=body.model_dump(exclude={"session_id", "expected_revision"}),
        )
    except RasterSamplingError as exc:
        raise _fail(exc) from exc


@router.get("/raster-sampling/plan/{plan_id}", tags=["栅格采样"])
async def get_plan(
    plan_id: str,
    session_id: str = Query(..., min_length=8, max_length=128),
    _conv: Conversation = Depends(require_owned_session),
):
    try:
        return await service.get_plan_payload(session_id, plan_id)
    except RasterSamplingError as exc:
        raise _fail(exc) from exc


@router.get("/raster-sampling/plans", tags=["栅格采样"])
async def list_plans(
    session_id: str = Query(..., min_length=8, max_length=128),
    _conv: Conversation = Depends(require_owned_session),
):
    return await service.list_plans_payload(session_id)


@router.post("/raster-sampling/plan/{plan_id}/preview", tags=["栅格采样"])
async def preview(
    plan_id: str,
    body: PreviewIn,
    session_id: str = Query(..., min_length=8, max_length=128),
    owner_token: Optional[str] = Header(None, alias="X-Session-Token"),
    _conv: Conversation = Depends(require_owned_session),
):
    try:
        return await service.preview(
            session_id=session_id,
            plan_id=plan_id,
            owner_token=await _owned(session_id, owner_token, _conv),
            expected_revision=body.expected_revision,
            limit=body.limit,
        )
    except RasterSamplingError as exc:
        raise _fail(exc) from exc


@router.post("/raster-sampling/plan/{plan_id}/execute", tags=["栅格采样"])
async def execute(
    plan_id: str,
    body: ExecuteIn,
    session_id: str = Query(..., min_length=8, max_length=128),
    user: Optional[dict] = Depends(get_current_user_optional),
    owner_token: Optional[str] = Header(None, alias="X-Session-Token"),
    _conv: Conversation = Depends(require_owned_session),
):
    try:
        return await service.execute(
            session_id=session_id,
            plan_id=plan_id,
            owner_token=await _owned(session_id, owner_token, _conv),
            expected_revision=body.expected_revision,
            background=body.background,
            actor=_user_owner(user),
            project_id=body.project_id,
        )
    except RasterSamplingError as exc:
        raise _fail(exc) from exc


@router.get("/raster-sampling/plan/{plan_id}/status", tags=["栅格采样"])
async def status(
    plan_id: str,
    session_id: str = Query(..., min_length=8, max_length=128),
    owner_token: Optional[str] = Header(None, alias="X-Session-Token"),
    _conv: Conversation = Depends(require_owned_session),
):
    try:
        return await service.status(
            session_id=session_id, plan_id=plan_id,
            owner_token=await _owned(session_id, owner_token, _conv))
    except RasterSamplingError as exc:
        raise _fail(exc) from exc


@router.get("/raster-sampling/results", tags=["栅格采样"])
async def results(
    session_id: str = Query(..., min_length=8, max_length=128),
    plan_id: str = Query(""),
    execution_id: str = Query(""),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=500),
    include_curves: bool = Query(True),
    source_ref: str = Query(""),
    band: Optional[int] = Query(None),
    valid: Optional[bool] = Query(None),
    owner_token: Optional[str] = Header(None, alias="X-Session-Token"),
    _conv: Conversation = Depends(require_owned_session),
):
    if not plan_id and not execution_id:
        raise HTTPException(status_code=422, detail={
            "code": "INVALID_PLAN", "detail": "plan_id or execution_id required"})
    try:
        return await service.results(
            session_id=session_id, plan_id=plan_id, execution_id=execution_id,
            owner_token=await _owned(session_id, owner_token, _conv),
            page=page, per_page=per_page, include_curves=include_curves,
            source_ref=source_ref, band=band, valid=valid)
    except RasterSamplingError as exc:
        raise _fail(exc) from exc


@router.post("/raster-sampling/plan/{plan_id}/cancel", tags=["栅格采样"])
async def cancel(
    plan_id: str,
    session_id: str = Query(..., min_length=8, max_length=128),
    _conv: Conversation = Depends(require_owned_session),
):
    try:
        return await service.cancel(session_id=session_id, plan_id=plan_id)
    except RasterSamplingError as exc:
        raise _fail(exc) from exc
