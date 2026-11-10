"""字段计算域 API 路由（G19 / WP09）—— 与数据准备域同款会话归属纪律。

所有端点以已校验 session_id 为命名空间；会话不存在/不属于调用方一律 404
（不泄露存在性）。业务本体全部委托 ``field_compute.service``，路由只做
鉴权、typed 错误映射与有界响应塑形。
"""
from __future__ import annotations

from typing import Any, Dict, NoReturn, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query

from app.api.routes.data_preparation import _require_session
from app.core.auth import get_current_user_optional_with_version
from app.schemas.field_compute_schema import (
    CatalogResponse, DescribeRequest, DescribeResponse,
    DiscardResponse, ExecuteRequest, PlanListResponse, PlanOut, PlanRequest,
    PlanResponse, PreviewResponse, ProjectResultsResponse, ReceiptResponse,
    ResultResponse,
)
from app.services.data_preparation.field_compute.contracts import (
    FUNCTION_SET_VERSION, IR_VERSION, MAX_PREVIEW_ROWS, MAX_ROW_OPS, MAX_ROWS,
    CompileError, ExecError,
)
from app.services.data_preparation.field_compute.functions import function_help
from app.services.data_preparation.field_compute.parser import render_expression
from app.services.data_preparation.field_compute import service

router = APIRouter()

_EXEC_STATUS: Dict[str, int] = {
    "PLAN_NOT_FOUND": 404, "PLAN_REJECTED": 409, "STALE_PLAN": 409,
    "SNAPSHOT_CHANGED": 409, "INPUT_REF_MISSING": 404,
    "PAYLOAD_UNSUPPORTED": 400, "ROW_LIMIT_EXCEEDED": 422,
    "STORE_FAILED": 500, "WRITE_FAILED": 500, "CANCELLED": 409,
    "ABORTED_BY_ROW_ERROR": 422, "PERSISTENCE_FAILED": 500,
    "OWNER_MISMATCH": 403,
}


def _raise_typed(exc: Exception) -> NoReturn:
    if isinstance(exc, CompileError):
        raise HTTPException(status_code=422, detail=exc.to_dict()) from exc
    if isinstance(exc, ExecError):
        raise HTTPException(status_code=_EXEC_STATUS.get(exc.code, 400),
                            detail=exc.to_dict()) from exc
    raise HTTPException(status_code=400, detail=str(exc)[:200]) from exc


def _plan_out(plan: Any, data_shape: str = "") -> PlanOut:
    return PlanOut(
        plan_id=plan.plan_id,
        compute_fingerprint=plan.compute_fingerprint,
        state=plan.state, input_ref=plan.input_ref,
        content_hash=plan.anchor.content_hash,
        data_shape=data_shape,
        columns=[{"name": c.name,
                  "expression": c.expression.canonical(),
                  "formula": render_expression(c.expression),
                  "declared_unit": c.declared_unit}
                 for c in plan.columns],
        error_policy=plan.error_policy, timezone=plan.timezone,
    )


@router.get("/field-compute/catalog", response_model=CatalogResponse)
async def catalog() -> CatalogResponse:
    """函数集与限制（只读契约；不需要会话）。"""
    return {
        "function_set_version": FUNCTION_SET_VERSION,
        "ir_version": IR_VERSION,
        "functions": function_help(),
        "scope_modes": ["all_rows", "selection", "predicate"],
        "error_policies": ["null", "abort"],
        "limits": {"max_rows": MAX_ROWS, "max_ops_per_row": MAX_ROW_OPS,
                   "preview_rows": MAX_PREVIEW_ROWS},
    }


@router.post("/field-compute/describe", response_model=DescribeResponse)
async def describe(
    req: DescribeRequest,
    owner_token: Optional[str] = Header(None, alias="X-Session-Token"),
    _user: dict = Depends(get_current_user_optional_with_version),
) -> DescribeResponse:
    await _require_session(req.session_id, _user, owner_token)
    try:
        return await service.describe_source(req.session_id, req.source_ref)
    except (CompileError, ExecError) as exc:
        _raise_typed(exc)


@router.post("/field-compute/plan", response_model=PlanResponse)
async def create_plan(
    req: PlanRequest,
    owner_token: Optional[str] = Header(None, alias="X-Session-Token"),
    _user: dict = Depends(get_current_user_optional_with_version),
) -> PlanResponse:
    await _require_session(req.session_id, _user, owner_token)
    try:
        created = await service.create_plan({
            "session_id": req.session_id,
            "source_ref": req.source_ref,
            "owner": req.owner or str(_user.get("user_id") or "anonymous"),
            "columns": [c.model_dump() for c in req.columns],
            "scope": req.scope,
            "field_units": req.field_units,
            "timezone": req.timezone,
            "date_format": req.date_format,
            "error_policy": req.error_policy,
        })
    except (CompileError, ExecError) as exc:
        _raise_typed(exc)
    plan = created["plan"]
    return PlanResponse(
        plan=_plan_out(plan, created["data_shape"]),
        data_shape=created["data_shape"],
        column_order=list(created["column_order"]),
        scope_summary=created["scope_summary"],
        geometry=created["geometry"],
        estimated_ops=created["estimated_ops"],
        warnings=created["warnings"],
    )


@router.post("/field-compute/plan/{plan_id}/preview", response_model=PreviewResponse)
async def preview_plan(
    plan_id: str,
    session_id: str = Query(...),
    generation: int = Query(0, ge=0),
    owner_token: Optional[str] = Header(None, alias="X-Session-Token"),
    _user: dict = Depends(get_current_user_optional_with_version),
) -> PreviewResponse:
    await _require_session(session_id, _user, owner_token)
    try:
        return await service.preview(plan_id, session_id, generation=generation)
    except (CompileError, ExecError) as exc:
        _raise_typed(exc)


@router.post("/field-compute/plan/{plan_id}/execute", response_model=ReceiptResponse)
async def execute_plan(
    plan_id: str,
    req: ExecuteRequest,
    session_id: str = Query(...),
    owner_token: Optional[str] = Header(None, alias="X-Session-Token"),
    _user: dict = Depends(get_current_user_optional_with_version),
) -> ReceiptResponse:
    await _require_session(session_id, _user, owner_token)
    try:
        receipt = await service.execute(
            plan_id, session_id, project_id=req.project_id,
            owner=req.owner or str(_user.get("user_id") or ""),
            expected_fingerprint=req.expected_fingerprint)
    except (CompileError, ExecError) as exc:
        _raise_typed(exc)
    return {"receipt": receipt.to_bounded_dict()}


@router.get("/field-compute/plans", response_model=PlanListResponse)
async def plans(
    session_id: str = Query(...),
    owner_token: Optional[str] = Header(None, alias="X-Session-Token"),
    _user: dict = Depends(get_current_user_optional_with_version),
) -> PlanListResponse:
    await _require_session(session_id, _user, owner_token)
    records = await service.list_plans(session_id)
    return PlanListResponse(plans=[_plan_out(p) for p in records])


@router.get("/field-compute/result", response_model=ResultResponse)
async def result(
    session_id: str = Query(...),
    output_ref: str = Query(...),
    owner_token: Optional[str] = Header(None, alias="X-Session-Token"),
    _user: dict = Depends(get_current_user_optional_with_version),
) -> ResultResponse:
    await _require_session(session_id, _user, owner_token)
    try:
        return await service.get_result(session_id, output_ref)
    except (CompileError, ExecError) as exc:
        _raise_typed(exc)


@router.get("/field-compute/project-results", response_model=ProjectResultsResponse)
async def project_results(
    session_id: str = Query(...),
    project_id: str = Query(...),
    owner_token: Optional[str] = Header(None, alias="X-Session-Token"),
    _user: dict = Depends(get_current_user_optional_with_version),
) -> ProjectResultsResponse:
    await _require_session(session_id, _user, owner_token)
    return {"items": await service.list_project_results(session_id, project_id)}


@router.delete("/field-compute/plan/{plan_id}", response_model=DiscardResponse)
async def discard_plan(
    plan_id: str,
    session_id: str = Query(...),
    owner_token: Optional[str] = Header(None, alias="X-Session-Token"),
    _user: dict = Depends(get_current_user_optional_with_version),
) -> DiscardResponse:
    await _require_session(session_id, _user, owner_token)
    try:
        plan = await service.discard_plan(session_id, plan_id)
    except CompileError as exc:
        _raise_typed(exc)
    if plan is None:
        raise HTTPException(status_code=404, detail="plan not found")
    return DiscardResponse(plan_id=plan.plan_id, state=plan.state)
