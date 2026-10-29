"""数据准备域 API 路由（M5；契约 §5 —— 7 端点，上传同款鉴权纪律）。

归属语义：所有端点以已校验 session_id 为命名空间（ref 是会话域能力）；
会话不存在/不属于调用方一律 404（不泄露存在性，与 uploads 纪律一致）。
"""
from __future__ import annotations

import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy import select

from app.core.auth import (
    authorize_session_write,
    get_current_user_optional_with_version,
)
from app.models.db_model import Conversation
from app.schemas.data_preparation_schema import (
    ExecuteResponse,
    FieldBindingRequest,
    FieldBindingResponse,
    FieldProposalOut,
    InspectRequest,
    InspectResponse,
    PlanOut,
    PlanRequest,
    PlanResponse,
    PreviewResponse,
    ReceiptOut,
    ReceiptSummaryOut,
    RoleCandidateOut,
    SampleFeatureOut,
)
from app.services.data_preparation.executor import (
    ExecError,
    execute_plan,
    find_receipt,
    dry_run,
)
from app.services.data_preparation.plan import (
    PlanValidationError,
    build_plan,
    discard_plan,
    get_plan,
    list_plans,
    save_plan,
)
from app.services.data_preparation.service import (
    BindError,
    InspectError,
    bind_field_roles,
    inspect_source,
)
from app.services.session_data import session_data_manager
from app.tools._utils import async_db_session

logger = logging.getLogger(__name__)
router = APIRouter()

# ExecError.code → HTTP 状态（typed 4xx，绝不 500 裸抛）。
_EXEC_STATUS = {
    "PLAN_NOT_FOUND": 404,
    "PLAN_DISCARDED": 409,
    "STALE_INPUT": 409,
    "INPUT_REF_MISSING": 404,
    "PAYLOAD_UNSUPPORTED": 400,
    "DROP_FRACTION_EXCEEDED": 422,
    "STORE_FAILED": 500,
}


async def _require_session(
    session_id: Optional[str],
    user: dict,
    owner_token: Optional[str],
) -> str:
    """会话归属守卫（与 POST /upload 同规则：首写允许、既存须 user/token）。"""
    if not session_id:
        raise HTTPException(status_code=400, detail="session_id is required")
    async with async_db_session() as db:
        conv = (await db.execute(
            select(Conversation).where(Conversation.id == session_id)
        )).scalar_one_or_none()
    if not authorize_session_write(conv, user.get("user_id"), owner_token):
        raise HTTPException(status_code=404, detail="Session not found")
    return session_id


def _proposal_out(p) -> FieldProposalOut:
    return FieldProposalOut(
        field=p.field, dtype=p.dtype,
        candidates=[RoleCandidateOut(role=c.role, confidence=c.confidence,
                                     reasons=list(c.reasons))
                    for c in p.candidates],
        rejected=[[r, code] for r, code in p.rejected],
        needs_clarification=p.needs_clarification)


def _receipt_out(r) -> ReceiptOut:
    return ReceiptOut(
        input_ref=r.input_ref, geometry_type=r.geometry_type, srs=r.srs,
        crs_source=r.crs_source, axis_order_guessed=r.axis_order_guessed,
        coordinate_range=r.coordinate_range, encoding=r.encoding,
        time_fields=list(r.time_fields), tz_status=r.tz_status,
        pk_field=r.pk_field, pk_status=r.pk_status,
        row_count=r.row_count, sample_rows_scanned=r.sample_rows_scanned,
        findings=[{"code": f.code, "severity": f.severity.value,
                   "message_zh": f.message_zh, "evidence": f.evidence,
                   "suggestion": f.suggestion.value, "field": f.field}
                  for f in r.findings],
        blocked=r.blocked)


@router.post("/data-preparation/inspect", response_model=InspectResponse)
async def inspect(
    req: InspectRequest,
    owner_token: Optional[str] = Header(None, alias="X-Session-Token"),
    _user: dict = Depends(get_current_user_optional_with_version),
):
    """检查数据源：空间收据 + 字段角色候选 + 有界样本（≤50 行）。"""
    await _require_session(req.session_id, _user, owner_token)
    try:
        result = await inspect_source(
            req.session_id, req.source_ref, user_hints=req.user_hints)
    except InspectError as exc:
        status = 404 if exc.code == "REF_MISSING" else 400
        raise HTTPException(status_code=status,
                            detail=f"{exc.code}: {exc.detail}") from exc
    receipt: ReceiptOut = _receipt_out(result["receipt"])
    return InspectResponse(
        input_ref=result["input_ref"],
        input_fingerprint=result["input_fingerprint"],
        receipt=receipt,
        proposals=[_proposal_out(p) for p in result["proposals"]],
        sample=[SampleFeatureOut(**s) for s in result["sample"]],
        sample_truncated=result["sample_truncated"],
    )


@router.post("/data-preparation/plan", response_model=PlanResponse)
async def create_plan(
    req: PlanRequest,
    owner_token: Optional[str] = Header(None, alias="X-Session-Token"),
    _user: dict = Depends(get_current_user_optional_with_version),
):
    """构建准备计划（服务端校验步与收据事实匹配；不执行）。"""
    await _require_session(req.session_id, _user, owner_token)
    axis_evidence = None
    crs_source = ""
    warnings: List[str] = []
    try:
        result = await inspect_source(req.session_id, req.source_ref)
    except InspectError as exc:
        status = 404 if exc.code == "REF_MISSING" else 400
        raise HTTPException(status_code=status,
                            detail=f"{exc.code}: {exc.detail}") from exc
    from app.services.data_preparation.vocabulary import FINDING_AXIS_ORDER_SUSPECT
    for f in result["receipt"].findings:
        if f.code == FINDING_AXIS_ORDER_SUSPECT:
            axis_evidence = dict(f.evidence)
        if f.code in ("crs_missing", "crs_suspicious"):
            crs_source = crs_source or "assumed"
    if result["receipt"].crs_source:
        crs_source = result["receipt"].crs_source
    elif result["receipt"].srs:
        crs_source = "declared"

    steps = [dict(s) for s in (req.steps or [])]
    if req.auto_steps:
        # 仅自动补两类安全步：证据齐备的越界行过滤 + 高置信角色的映射。
        # 过滤闸取 0.2（auto 路径的现实坏坐标尾量级；仍受 DROP_FRACTION
        # 硬闸 typed 保护 —— 更脏的数据必须人工显式给步并说明）。
        from app.services.data_preparation.plan import FilterRowsStep
        has_filter = any(s.get("type") == "filter_rows" for s in steps)
        if not has_filter and any(
                f.code == "impossible_coordinates"
                for f in result["receipt"].findings):
            steps.append(FilterRowsStep(
                condition_code="impossible_coordinates",
                max_drop_frac=0.2).model_dump())
            warnings.append("auto: 追加 impossible_coordinates 过滤步（≤20% 硬闸）")
        for p in result["proposals"]:
            if p.top is None or p.needs_clarification:
                continue
            if any(s.get("type") == "field_mapping" and s.get("field") == p.field
                   for s in steps):
                continue
            from app.services.data_preparation.plan import FieldMappingStep
            steps.append(FieldMappingStep(
                role=p.top.role, field=p.field, source="proposal").model_dump())

    fields = list((result["profile"].vector.fields
                   if result["profile"].vector is not None else {}).keys())
    try:
        plan = build_plan(
            session_id=req.session_id,
            owner=str(_user.get("user_id") or "anonymous"),
            input_ref=req.source_ref,
            input_fingerprint=result["input_fingerprint"],
            steps=steps,
            axis_suspect_evidence=axis_evidence,
            crs_source=crs_source,
            available_fields=fields)
    except PlanValidationError as exc:
        raise HTTPException(status_code=422,
                            detail=f"{exc.reason_code}: {exc.detail}") from exc
    await save_plan(plan)
    return PlanResponse(
        plan=PlanOut(plan_id=plan.plan_id, plan_hash=plan.plan_hash,
                     state=plan.state, input_ref=plan.input_ref,
                     input_fingerprint=plan.input_fingerprint,
                     steps=[s.canonical() for s in plan.steps],
                     mutates_data=plan.mutates_data),
        warnings=warnings)


async def _load_owned_plan(plan_id: str, session_id: str):
    plan = await get_plan(session_id, plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="plan not found")
    return plan


@router.post("/data-preparation/plan/{plan_id}/preview", response_model=PreviewResponse)
async def preview_plan(
    plan_id: str,
    session_id: str = Query(...),
    owner_token: Optional[str] = Header(None, alias="X-Session-Token"),
    _user: dict = Depends(get_current_user_optional_with_version),
):
    """dry-run 预演（采样 ≤2000 行；不落任何 store）。"""
    await _require_session(session_id, _user, owner_token)
    from app.services.data_preparation.executor import _exec_lock

    plan = await _load_owned_plan(plan_id, session_id)
    if plan.state == "discarded":
        raise HTTPException(status_code=409, detail="PLAN_DISCARDED")
    # 与 execute 同一把 (session, plan_hash) 锁 + 锁内重读状态：preview 的
    # load→dry-run→save 窗口内 execute 完成时，旧副本回存会把 executed 打回
    # previewed 再被 discard（R2-3 竞态）—— 锁内重读后按终态拒绝。
    async with _exec_lock(session_id, plan.plan_hash):
        plan = await _load_owned_plan(plan_id, session_id) or plan
        if plan.state in ("executed", "verified", "rolled_back"):
            raise HTTPException(
                status_code=409,
                detail=f"PLAN_ALREADY_{plan.state.upper()}")
        payload = await session_data_manager.get(session_id, plan.input_ref)
        if payload is None:
            raise HTTPException(status_code=404, detail="input ref missing")
        try:
            result = dry_run(payload, plan)
        except ExecError as exc:
            raise HTTPException(status_code=_EXEC_STATUS.get(exc.code, 400),
                                detail=f"{exc.code}: {exc.detail}") from exc
        plan.state = "previewed"
        await save_plan(plan)
    return PreviewResponse(
        plan_id=result["plan_id"], sampled_rows=result["sampled_rows"],
        truncated=result["truncated"],
        estimated_rows_after=result["estimated_rows_after"],
        quality_delta_sample=result["quality_delta_sample"],
        steps=[{"type": s.get("type"), "data_changed": s.get("data_changed", False),
                "detail": {k: v for k, v in s.items()
                           if k not in ("type", "data_changed")}}
               for s in result["steps"]],
        degraded=result["degraded"])


@router.post("/data-preparation/plan/{plan_id}/execute", response_model=ExecuteResponse)
async def execute(
    plan_id: str,
    session_id: str = Query(...),
    owner_token: Optional[str] = Header(None, alias="X-Session-Token"),
    _user: dict = Depends(get_current_user_optional_with_version),
):
    """幂等执行计划 → prepared ref + 收据（原件不可变）。"""
    await _require_session(session_id, _user, owner_token)
    plan = await _load_owned_plan(plan_id, session_id)
    try:
        receipt = await execute_plan(plan)
    except ExecError as exc:
        raise HTTPException(status_code=_EXEC_STATUS.get(exc.code, 400),
                            detail=f"{exc.code}: {exc.detail}") from exc
    return ExecuteResponse(receipt=ReceiptSummaryOut(
        plan_id=receipt.plan_id, plan_hash=receipt.plan_hash,
        input_ref=receipt.input_ref, output_ref=receipt.output_ref,
        output_fingerprint=receipt.output_fingerprint,
        row_before=receipt.row_before, row_after=receipt.row_after,
        quality_before=receipt.quality_before, quality_after=receipt.quality_after,
        degraded=list(receipt.degraded),
        user_confirmations=[dict(c) for c in receipt.user_confirmations]))


@router.get("/data-preparation/plans")
async def plans(
    session_id: str = Query(...),
    owner_token: Optional[str] = Header(None, alias="X-Session-Token"),
    _user: dict = Depends(get_current_user_optional_with_version),
):
    """列出会话内计划（有界 ≤64）。"""
    await _require_session(session_id, _user, owner_token)
    records = await list_plans(session_id)
    return {"plans": [PlanOut(
        plan_id=p.plan_id, plan_hash=p.plan_hash, state=p.state,
        input_ref=p.input_ref, input_fingerprint=p.input_fingerprint,
        steps=[s.canonical() for s in p.steps],
        mutates_data=p.mutates_data).model_dump() for p in records]}


@router.delete("/data-preparation/plan/{plan_id}")
async def discard(
    plan_id: str,
    session_id: str = Query(...),
    owner_token: Optional[str] = Header(None, alias="X-Session-Token"),
    _user: dict = Depends(get_current_user_optional_with_version),
):
    """作废未执行计划（executed/verified 不可作废 —— 走产物删除面）。"""
    await _require_session(session_id, _user, owner_token)
    try:
        plan = await discard_plan(session_id, plan_id)
    except PlanValidationError as exc:
        raise HTTPException(status_code=409,
                            detail=f"{exc.reason_code}: {exc.detail}") from exc
    if plan is None:
        raise HTTPException(status_code=404, detail="plan not found")
    return {"plan_id": plan.plan_id, "state": plan.state}


@router.post("/data-preparation/bindings/fields", response_model=FieldBindingResponse)
async def bind_fields(
    req: FieldBindingRequest,
    owner_token: Optional[str] = Header(None, alias="X-Session-Token"),
    _user: dict = Depends(get_current_user_optional_with_version),
):
    """用户确认角色写回 descriptor（版本化；address 等无对应角色诚实跳过）。"""
    await _require_session(req.session_id, _user, owner_token)
    try:
        result = await bind_field_roles(
            req.session_id, req.source_ref, req.roles)
    except BindError as exc:
        status = 404 if exc.code == "REF_MISSING" else 400
        raise HTTPException(status_code=status,
                            detail=f"{exc.code}: {exc.detail}") from exc
    return FieldBindingResponse(**result)


@router.get("/data-preparation/plan/{plan_id}/receipt")
async def plan_receipt(
    plan_id: str,
    session_hash: str = Query(..., description="plan_hash（收据幂等键）"),
    session_id: str = Query(...),
    owner_token: Optional[str] = Header(None, alias="X-Session-Token"),
    _user: dict = Depends(get_current_user_optional_with_version),
):
    """查执行收据（幂等键查询；未执行 → 404 诚实披露）。"""
    await _require_session(session_id, _user, owner_token)
    plan = await _load_owned_plan(plan_id, session_id)
    receipt = await find_receipt(session_id, session_hash)
    if receipt is None or receipt.plan_id != plan.plan_id:
        raise HTTPException(status_code=404, detail="receipt not found")
    return ReceiptSummaryOut(
        plan_id=receipt.plan_id, plan_hash=receipt.plan_hash,
        input_ref=receipt.input_ref, output_ref=receipt.output_ref,
        output_fingerprint=receipt.output_fingerprint,
        row_before=receipt.row_before, row_after=receipt.row_after,
        quality_before=receipt.quality_before, quality_after=receipt.quality_after,
        degraded=list(receipt.degraded),
        user_confirmations=[dict(c) for c in receipt.user_confirmations]).model_dump()
