"""G45 坐标操作工作台 HTTP 面（UI 与 Pi 工具共用 coordinate_operations.service）。

会话归属由 ``require_owned_session`` 守卫（G32/G33/G38 同款）；源解析的逐源
授权在 service 内经 G18 resolve_source 复核。错误统一 typed payload。
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query

from app.models.db_model import Conversation
from app.schemas.coordinate_operations_schema import (
    DescribeSourceBody,
    ExecuteBody,
    ListOperationsBody,
    LockBody,
    PlanBody,
    PreviewBody,
    UpdatePlanBody,
)
from app.services.auth_history_bridge import require_owned_session
from app.services.coordinate_operations import service
from app.services.coordinate_operations.contracts import (
    CoordinateOperationError,
    http_status,
)

router = APIRouter(prefix="/coord-ops", tags=["坐标操作工作台 G45"])


def _fail(err: CoordinateOperationError) -> HTTPException:
    return HTTPException(status_code=http_status(err), detail=err.payload())


def _actor(conv: Conversation, owner_token: Optional[str],
           session_id: str = "") -> dict:
    return {
        "user_id": str(getattr(conv, "user_id", "") or "") or None,
        "org_id": getattr(conv, "org_id", None),
        "owner_token": owner_token or getattr(conv, "owner_token", None),
        "trusted_session_id": session_id,
    }


def _source_dict(body_source) -> dict:
    return body_source.model_dump(exclude_none=True)


# ─── 源与 CRS ───────────────────────────────────────────────────────────────

@router.post("/sessions/{session_id}/describe-source")
async def describe_source(
    session_id: str,
    body: DescribeSourceBody,
    owner_token: Optional[str] = Header(None, alias="X-Session-Token"),
    conv: Conversation = Depends(require_owned_session),
):
    try:
        return await service.describe_source(
            session_id, _source_dict(body.source),
            _actor(conv, owner_token, session_id))
    except CoordinateOperationError as err:
        raise _fail(err) from err


@router.get("/crs/lookup")
async def crs_lookup(
    q: str = Query(..., min_length=1, max_length=256),
):
    try:
        return service.lookup_crs(q)
    except CoordinateOperationError as err:
        raise _fail(err) from err


@router.post("/sessions/{session_id}/operations")
async def list_operations(
    session_id: str,
    body: ListOperationsBody,
    owner_token: Optional[str] = Header(None, alias="X-Session-Token"),
    conv: Conversation = Depends(require_owned_session),
):
    try:
        return await service.list_operations(
            session_id, _source_dict(body.source), body.target_crs,
            body.aoi.model_dump() if body.aoi else None,
            _actor(conv, owner_token, session_id))
    except CoordinateOperationError as err:
        raise _fail(err) from err


# ─── 计划 CRUD（CAS）+ 锁定 ────────────────────────────────────────────────

@router.post("/sessions/{session_id}/plans")
async def create_plan(
    session_id: str,
    body: PlanBody,
    owner_token: Optional[str] = Header(None, alias="X-Session-Token"),
    conv: Conversation = Depends(require_owned_session),
):
    try:
        return await service.create_plan(
            session_id, body.plan.model_dump(exclude_none=True),
            actor=_actor(conv, owner_token, session_id))
    except CoordinateOperationError as err:
        raise _fail(err) from err


@router.get("/sessions/{session_id}/plans")
async def list_plans(
    session_id: str,
    limit: int = Query(50, ge=1, le=100),
    include_archived: bool = Query(False),
    conv: Conversation = Depends(require_owned_session),
):
    try:
        return await service.list_plans(
            session_id, limit=limit, include_archived=include_archived)
    except CoordinateOperationError as err:
        raise _fail(err) from err


@router.get("/sessions/{session_id}/plans/{plan_id}")
async def get_plan(
    session_id: str,
    plan_id: str,
    conv: Conversation = Depends(require_owned_session),
):
    try:
        return await service.get_plan(session_id, plan_id)
    except CoordinateOperationError as err:
        raise _fail(err) from err


@router.patch("/sessions/{session_id}/plans/{plan_id}")
async def update_plan(
    session_id: str,
    plan_id: str,
    body: UpdatePlanBody,
    owner_token: Optional[str] = Header(None, alias="X-Session-Token"),
    conv: Conversation = Depends(require_owned_session),
):
    try:
        return await service.update_plan(
            session_id, plan_id, body.plan.model_dump(exclude_none=True),
            expected_revision=body.expected_revision,
            actor=_actor(conv, owner_token, session_id))
    except CoordinateOperationError as err:
        raise _fail(err) from err


@router.delete("/sessions/{session_id}/plans/{plan_id}")
async def archive_plan(
    session_id: str,
    plan_id: str,
    expected_revision: int = Query(..., ge=1),
    conv: Conversation = Depends(require_owned_session),
):
    try:
        return await service.archive_plan(
            session_id, plan_id, expected_revision=expected_revision)
    except CoordinateOperationError as err:
        raise _fail(err) from err


@router.post("/sessions/{session_id}/plans/{plan_id}/lock")
async def lock_plan(
    session_id: str,
    plan_id: str,
    body: LockBody,
    owner_token: Optional[str] = Header(None, alias="X-Session-Token"),
    conv: Conversation = Depends(require_owned_session),
):
    try:
        return await service.lock_plan(
            session_id, plan_id, body.candidate_id,
            expected_revision=body.expected_revision,
            actor=_actor(conv, owner_token, session_id))
    except CoordinateOperationError as err:
        raise _fail(err) from err


# ─── 预览 / 执行 ───────────────────────────────────────────────────────────

@router.post("/sessions/{session_id}/plans/{plan_id}/preview")
async def preview_plan(
    session_id: str,
    plan_id: str,
    body: PreviewBody,
    owner_token: Optional[str] = Header(None, alias="X-Session-Token"),
    conv: Conversation = Depends(require_owned_session),
):
    try:
        return await service.preview_plan(
            session_id, plan_id,
            expected_revision=body.expected_revision,
            actor=_actor(conv, owner_token, session_id),
            sample_rows=body.sample_rows)
    except CoordinateOperationError as err:
        raise _fail(err) from err


@router.post("/sessions/{session_id}/plans/{plan_id}/execute")
async def execute_plan(
    session_id: str,
    plan_id: str,
    body: ExecuteBody,
    owner_token: Optional[str] = Header(None, alias="X-Session-Token"),
    conv: Conversation = Depends(require_owned_session),
):
    try:
        return await service.execute_plan(
            session_id, plan_id,
            expected_revision=body.expected_revision,
            actor=_actor(conv, owner_token, session_id),
            background=body.background)
    except CoordinateOperationError as err:
        raise _fail(err) from err


# ─── 运行 ──────────────────────────────────────────────────────────────────

@router.get("/sessions/{session_id}/runs")
async def list_runs(
    session_id: str,
    plan_id: Optional[str] = Query(None, max_length=64),
    limit: int = Query(50, ge=1, le=100),
    conv: Conversation = Depends(require_owned_session),
):
    try:
        return await service.list_runs(
            session_id, plan_id=plan_id, limit=limit)
    except CoordinateOperationError as err:
        raise _fail(err) from err


@router.get("/sessions/{session_id}/runs/{run_id}")
async def inspect_run(
    session_id: str,
    run_id: str,
    conv: Conversation = Depends(require_owned_session),
):
    try:
        return await service.inspect_run(session_id, run_id)
    except CoordinateOperationError as err:
        raise _fail(err) from err


@router.get("/sessions/{session_id}/runs/{run_id}/results")
async def run_results(
    session_id: str,
    run_id: str,
    cursor: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    conv: Conversation = Depends(require_owned_session),
):
    try:
        return await service.run_result_page(
            session_id, run_id, cursor, limit)
    except CoordinateOperationError as err:
        raise _fail(err) from err


@router.post("/sessions/{session_id}/runs/{run_id}/cancel")
async def cancel_run(
    session_id: str,
    run_id: str,
    conv: Conversation = Depends(require_owned_session),
):
    try:
        return await service.cancel_run(session_id, run_id)
    except CoordinateOperationError as err:
        raise _fail(err) from err


@router.post("/sessions/{session_id}/runs/{run_id}/display-ref")
async def display_ref(
    session_id: str,
    run_id: str,
    owner_token: Optional[str] = Header(None, alias="X-Session-Token"),
    conv: Conversation = Depends(require_owned_session),
):
    """WGS84 显示副本重铸（地图层挂载；主成果独立，C21/C22）。"""
    try:
        return await service.display_ref_for_run(
            session_id, run_id, _actor(conv, owner_token, session_id))
    except CoordinateOperationError as err:
        raise _fail(err) from err


@router.post("/sessions/{session_id}/recover")
async def recover(
    session_id: str,
    conv: Conversation = Depends(require_owned_session),
):
    """孤儿 run/staging 收敛（C29/C38）。"""
    try:
        return await service.recover(session_id)
    except CoordinateOperationError as err:
        raise _fail(err) from err
