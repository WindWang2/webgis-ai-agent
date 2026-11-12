"""字段计算域 API 路由（G19 / WP09）—— 与数据准备域同款会话归属纪律。

所有需会话端点以已校验 session_id 为命名空间；会话不存在/不属于调用方
一律 404（不泄露存在性）。业务本体全部委托 ``field_compute.service``，
路由只做鉴权、typed 错误映射与有界响应塑形。

独立评审修复：
- 全部契约模型带 ``FieldCompute`` 前缀，避免与 data_preparation_schema 的
  短名重名触发 FastAPI OpenAPI 全局消歧、改写既有端点 ``$ref``；
- ``owner`` 一律由服务端认证上下文注入，忽略 body 覆盖（跨 UI/Pi 互操作）；
- 新增 ``cancel`` 端点，让前端取消真正联动服务端协作取消。
"""
from __future__ import annotations

from typing import Any, Dict, NoReturn, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query

from app.api.routes.data_preparation import _require_session
from app.core.auth import get_current_user_optional_with_version
from app.schemas.field_compute_schema import (
    FieldComputeCancelResponse, FieldComputeCatalogResponse,
    FieldComputeDescribeRequest, FieldComputeDescribeResponse,
    FieldComputeDiscardResponse, FieldComputeExecuteRequest,
    FieldComputePlanListResponse, FieldComputePlanOut,
    FieldComputePlanRequest, FieldComputePlanResponse,
    FieldComputePreviewResponse, FieldComputeProjectResultsResponse,
    FieldComputeReceiptResponse, FieldComputeResultResponse,
)
from app.services.data_preparation.field_compute import service
from app.services.data_preparation.field_compute.contracts import (
    FUNCTION_SET_VERSION, IR_VERSION, MAX_PREVIEW_ROWS, MAX_ROW_OPS, MAX_ROWS,
    CompileError, ExecError,
)
from app.services.data_preparation.field_compute.functions import function_help
from app.services.data_preparation.field_compute.parser import render_expression

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


def _plan_out(plan: Any, data_shape: str = "") -> FieldComputePlanOut:
    return FieldComputePlanOut(
        plan_id=plan.plan_id,
        compute_fingerprint=plan.compute_fingerprint,
        state=plan.state, input_ref=plan.input_ref,
        content_hash=plan.anchor.content_hash,
        data_shape=data_shape, owner=plan.owner,
        columns=[{"name": c.name,
                  "expression": c.expression.canonical(),
                  "formula": render_expression(c.expression),
                  "declared_unit": c.declared_unit}
                 for c in plan.columns],
        error_policy=plan.error_policy, timezone=plan.timezone,
    )


def _injected_owner(user: dict) -> str:
    """owner 只取自认证上下文；客户端 body 不得覆盖。"""
    return str(user.get("user_id") or "anonymous")


@router.get("/field-compute/catalog", response_model=FieldComputeCatalogResponse)
async def catalog() -> FieldComputeCatalogResponse:
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


@router.post("/field-compute/describe", response_model=FieldComputeDescribeResponse)
async def describe(
    req: FieldComputeDescribeRequest,
    owner_token: Optional[str] = Header(None, alias="X-Session-Token"),
    _user: dict = Depends(get_current_user_optional_with_version),
) -> FieldComputeDescribeResponse:
    await _require_session(req.session_id, _user, owner_token)
    try:
        return await service.describe_source(req.session_id, req.source_ref)
    except (CompileError, ExecError) as exc:
        _raise_typed(exc)


@router.post("/field-compute/plan", response_model=FieldComputePlanResponse)
async def create_plan(
    req: FieldComputePlanRequest,
    owner_token: Optional[str] = Header(None, alias="X-Session-Token"),
    _user: dict = Depends(get_current_user_optional_with_version),
) -> FieldComputePlanResponse:
    await _require_session(req.session_id, _user, owner_token)
    try:
        created = await service.create_plan({
            "session_id": req.session_id,
            "source_ref": req.source_ref,
            "owner": _injected_owner(_user),   # 忽略 req.owner
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
    return FieldComputePlanResponse(
        plan=_plan_out(plan, created["data_shape"]),
        data_shape=created["data_shape"],
        column_order=list(created["column_order"]),
        scope_summary=created["scope_summary"],
        geometry=created["geometry"],
        estimated_ops=created["estimated_ops"],
        warnings=created["warnings"],
    )


@router.post("/field-compute/plan/{plan_id}/preview",
             response_model=FieldComputePreviewResponse)
async def preview_plan(
    plan_id: str,
    session_id: str = Query(...),
    generation: int = Query(0, ge=0),
    owner_token: Optional[str] = Header(None, alias="X-Session-Token"),
    _user: dict = Depends(get_current_user_optional_with_version),
) -> FieldComputePreviewResponse:
    await _require_session(session_id, _user, owner_token)
    try:
        return await service.preview(plan_id, session_id, generation=generation)
    except (CompileError, ExecError) as exc:
        _raise_typed(exc)


@router.post("/field-compute/plan/{plan_id}/execute",
             response_model=FieldComputeReceiptResponse)
async def execute_plan(
    plan_id: str,
    req: FieldComputeExecuteRequest,
    session_id: str = Query(...),
    owner_token: Optional[str] = Header(None, alias="X-Session-Token"),
    _user: dict = Depends(get_current_user_optional_with_version),
) -> FieldComputeReceiptResponse:
    await _require_session(session_id, _user, owner_token)
    actor = {"user_id": _user.get("user_id"), "owner_token": owner_token,
             "org_id": _user.get("org_id")}
    try:
        if req.background:
            # G23：后台 durable job —— 立即返回句柄（任务中心/状态工具查询）
            out = await service.execute_background(
                plan_id, session_id, project_id=req.project_id,
                owner=_injected_owner(_user),
                expected_fingerprint=req.expected_fingerprint, actor=actor)
            if "receipt" in out:  # 幂等短路：收据即答
                return {"receipt": out["receipt"].to_bounded_dict()}
            return out
        receipt = await service.execute(
            plan_id, session_id, project_id=req.project_id,
            owner=_injected_owner(_user),
            expected_fingerprint=req.expected_fingerprint, actor=actor)
    except (CompileError, ExecError) as exc:
        _raise_typed(exc)
    return {"receipt": receipt.to_bounded_dict()}


@router.post("/field-compute/plan/{plan_id}/cancel",
             response_model=FieldComputeCancelResponse)
async def cancel_plan(
    plan_id: str,
    session_id: str = Query(...),
    owner_token: Optional[str] = Header(None, alias="X-Session-Token"),
    _user: dict = Depends(get_current_user_optional_with_version),
) -> FieldComputeCancelResponse:
    """请求服务端协作取消（执行循环每批检查；已发布成果不受影响）。

    只对**正在执行**的计划登记取消：计划不存在 → typed 404；计划已结束或
    尚未开始 → ``cancelled=false``（不留下会污染后续重提的陈旧取消键）。
    """
    await _require_session(session_id, _user, owner_token)
    try:
        await service.get_plan(session_id, plan_id)   # 不存在 → PLAN_NOT_FOUND
        accepted = service.request_cancel(session_id, plan_id)
    except (CompileError, ExecError) as exc:
        _raise_typed(exc)
    return FieldComputeCancelResponse(plan_id=plan_id, cancelled=accepted)


@router.get("/field-compute/plans", response_model=FieldComputePlanListResponse)
async def plans(
    session_id: str = Query(...),
    owner_token: Optional[str] = Header(None, alias="X-Session-Token"),
    _user: dict = Depends(get_current_user_optional_with_version),
) -> FieldComputePlanListResponse:
    await _require_session(session_id, _user, owner_token)
    records = await service.list_plans(session_id)
    return FieldComputePlanListResponse(plans=[_plan_out(p) for p in records])


@router.get("/field-compute/result", response_model=FieldComputeResultResponse)
async def result(
    session_id: str = Query(...),
    output_ref: str = Query(...),
    owner_token: Optional[str] = Header(None, alias="X-Session-Token"),
    _user: dict = Depends(get_current_user_optional_with_version),
) -> FieldComputeResultResponse:
    await _require_session(session_id, _user, owner_token)
    try:
        return await service.get_result(session_id, output_ref)
    except (CompileError, ExecError) as exc:
        _raise_typed(exc)


@router.get("/field-compute/project-results",
            response_model=FieldComputeProjectResultsResponse)
async def project_results(
    session_id: str = Query(...),
    project_id: str = Query(...),
    owner_token: Optional[str] = Header(None, alias="X-Session-Token"),
    _user: dict = Depends(get_current_user_optional_with_version),
) -> FieldComputeProjectResultsResponse:
    await _require_session(session_id, _user, owner_token)
    return {"items": await service.list_project_results(session_id, project_id)}


@router.delete("/field-compute/plan/{plan_id}",
               response_model=FieldComputeDiscardResponse)
async def discard_plan(
    plan_id: str,
    session_id: str = Query(...),
    owner_token: Optional[str] = Header(None, alias="X-Session-Token"),
    _user: dict = Depends(get_current_user_optional_with_version),
) -> FieldComputeDiscardResponse:
    await _require_session(session_id, _user, owner_token)
    try:
        plan = await service.discard_plan(session_id, plan_id)
    except CompileError as exc:
        _raise_typed(exc)
    if plan is None:
        raise HTTPException(status_code=404, detail="plan not found")
    return FieldComputeDiscardResponse(plan_id=plan.plan_id, state=plan.state)
