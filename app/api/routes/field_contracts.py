"""G44 业务字段合同 REST 面 —— 草稿/评估/激活/退役/违规分页/观测对照。

鉴权：全部挂 require_owned_session（跨会话/租户统一 404 不泄存在）。
actor 由本路由服务端裁决（owner 身份），绝不采信请求体。激活/退役是 owner
显式动作 —— Pi 工具面不提供（F28）。
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.models.db_model import Conversation
from app.schemas.field_contracts_schema import (
    ContractCreateRequest,
    ContractReviseRequest,
)
from app.services.auth_history_bridge import require_owned_session
from app.services.field_contracts import service as fc_service
from app.services.field_contracts.enforcement import contract_binding_status
from app.services.field_contracts.service import FieldContractError

router = APIRouter(prefix="/chat", tags=["AI对话"])


def _actor(conv: Conversation) -> str:
    return getattr(conv, "owner_user_id", None) or getattr(conv, "owner_token", None) or "owner"


def _http_error(exc: FieldContractError) -> HTTPException:
    status = 404
    if exc.code in (
        "contract_not_found", "assessment_not_found",
    ):
        status = 404
    elif exc.code in (
        "invalid_document", "dataset_not_eligible", "dataset_not_editable",
        "dataset_not_found", "dataset_too_large_to_assess",
        "contract_not_revisionable", "contract_not_assessable",
        "contract_not_activatable", "no_activatable_assessment",
        "assessment_stale", "draft_exists", "revision_conflict",
        "contract_not_active", "invalid_cursor",
    ):
        status = 400
    elif exc.code in ("activation_race", "retire_race"):
        status = 409
    return HTTPException(status_code=status, detail={"code": exc.code, "message": str(exc), "detail": exc.detail})


class ActivateRequest(BaseModel):
    pass


@router.get("/sessions/{session_id}/field-contracts")
async def list_contracts(
    session_id: str,
    ref_id: Optional[str] = Query(default=None),
    _conv: Conversation = Depends(require_owned_session),
) -> Dict[str, Any]:
    return {"contracts": await fc_service.list_contracts(session_id, ref_id)}


@router.post("/sessions/{session_id}/field-contracts")
async def create_contract(
    session_id: str,
    req: ContractCreateRequest,
    _conv: Conversation = Depends(require_owned_session),
) -> Dict[str, Any]:
    try:
        return await fc_service.create_contract(
            session_id,
            ref_id=req.ref_id,
            document=req.document.model_dump(),
            actor=_actor(_conv),
        )
    except FieldContractError as exc:
        raise _http_error(exc) from exc


@router.get("/sessions/{session_id}/field-contracts/datasets/{ref_id:path}/observe")
async def observe_fields(
    session_id: str,
    ref_id: str,
    _conv: Conversation = Depends(require_owned_session),
) -> Dict[str, Any]:
    try:
        return await fc_service.observe_fields(session_id, ref_id)
    except FieldContractError as exc:
        raise _http_error(exc) from exc


@router.get("/sessions/{session_id}/field-contracts/datasets/{ref_id:path}/binding")
async def get_binding_status(
    session_id: str,
    ref_id: str,
    _conv: Conversation = Depends(require_owned_session),
) -> Dict[str, Any]:
    return await contract_binding_status(session_id, ref_id)


@router.get("/sessions/{session_id}/field-contracts/{contract_id}")
async def get_contract(
    session_id: str,
    contract_id: str,
    _conv: Conversation = Depends(require_owned_session),
) -> Dict[str, Any]:
    try:
        return await fc_service.get_contract(session_id, contract_id)
    except FieldContractError as exc:
        raise _http_error(exc) from exc


@router.put("/sessions/{session_id}/field-contracts/{contract_id}")
async def revise_contract(
    session_id: str,
    contract_id: str,
    req: ContractReviseRequest,
    _conv: Conversation = Depends(require_owned_session),
) -> Dict[str, Any]:
    try:
        return await fc_service.revise_contract(
            session_id, contract_id,
            expected_revision=req.expected_revision,
            document=req.document.model_dump(),
            actor=_actor(_conv),
        )
    except FieldContractError as exc:
        raise _http_error(exc) from exc


@router.post("/sessions/{session_id}/field-contracts/{contract_id}/assess")
async def start_assessment(
    session_id: str,
    contract_id: str,
    _conv: Conversation = Depends(require_owned_session),
) -> Dict[str, Any]:
    try:
        return await fc_service.start_assessment(
            session_id, contract_id, actor=_actor(_conv), background=True
        )
    except FieldContractError as exc:
        raise _http_error(exc) from exc


@router.post("/sessions/{session_id}/field-contracts/{contract_id}/activate")
async def activate_contract(
    session_id: str,
    contract_id: str,
    _conv: Conversation = Depends(require_owned_session),
) -> Dict[str, Any]:
    """owner 激活（完整零违反收据 + 编辑锁内指纹复核 + DB CAS）。"""
    try:
        return await fc_service.activate_contract(
            session_id, contract_id, actor=_actor(_conv)
        )
    except FieldContractError as exc:
        raise _http_error(exc) from exc


@router.post("/sessions/{session_id}/field-contracts/{contract_id}/retire")
async def retire_contract(
    session_id: str,
    contract_id: str,
    _conv: Conversation = Depends(require_owned_session),
) -> Dict[str, Any]:
    try:
        return await fc_service.retire_contract(
            session_id, contract_id, actor=_actor(_conv)
        )
    except FieldContractError as exc:
        raise _http_error(exc) from exc


@router.get("/sessions/{session_id}/field-contracts-assessments/{assessment_id}")
async def get_assessment(
    session_id: str,
    assessment_id: str,
    _conv: Conversation = Depends(require_owned_session),
) -> Dict[str, Any]:
    try:
        return await fc_service.get_assessment(session_id, assessment_id)
    except FieldContractError as exc:
        raise _http_error(exc) from exc


@router.get("/sessions/{session_id}/field-contracts-assessments/{assessment_id}/findings")
async def assessment_findings(
    session_id: str,
    assessment_id: str,
    cursor: Optional[str] = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    code: Optional[str] = Query(default=None),
    _conv: Conversation = Depends(require_owned_session),
) -> Dict[str, Any]:
    try:
        return await fc_service.assessment_findings_page(
            session_id, assessment_id, cursor=cursor, limit=limit, code=code
        )
    except FieldContractError as exc:
        raise _http_error(exc) from exc
